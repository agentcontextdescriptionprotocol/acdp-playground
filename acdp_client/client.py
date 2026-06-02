"""Async httpx client for one ACDP registry.

Crypto lives in ``AcdpProducer`` (the acdp-py Rust SDK). This class
handles only HTTP: routing, headers, error handling.

Optional auth — when an :class:`acdp.AcdpProducer` and an
:class:`acdp_client.token_manager.TokenManager` are supplied, the
client transparently:

* injects ``Authorization: Bearer <token>`` on each request,
* refreshes the token proactively before expiry,
* retries a single time on a 401 (invalidating the cached token
  first) so a stale token doesn't leak into the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Literal
from urllib.parse import quote

import httpx

from acdp_client.models import (
    CURSOR_ERROR_CODES,
    Body,
    CursorError,
    FullContext,
    PublishResponse,
    SearchHit,
    SearchResponse,
)

if TYPE_CHECKING:
    from acdp_client.signing import Producer
    from acdp_client.token_manager import TokenManager

TenantHeaderMode = Literal["fallback", "always", "never"]


class AcdpHTTPError(RuntimeError):
    """Raised when a registry returns a non-2xx response."""

    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"{status} from {url}: {body[:400]}")
        self.status = status
        self.body = body
        self.url = url


def _raise_for_status(r: httpx.Response) -> None:
    if r.is_success:
        return
    raise AcdpHTTPError(r.status_code, r.text, str(r.request.url))


class AcdpClient:
    """Async httpx client for one ACDP registry.

    Construct with ``producer=...`` and ``token_manager=...`` to enable
    automatic bearer-token injection. Without those, the client behaves
    as an anonymous caller (which is fine for public-visibility
    contexts or for registries with ``anonymous_public_reads = true``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        run_id: str | None = None,
        timeout: float = 30.0,
        http: httpx.AsyncClient | None = None,
        producer: "Producer | None" = None,
        token_manager: "TokenManager | None" = None,
        tenant_id: str | None = None,
        tenant_header_mode: TenantHeaderMode = "fallback",
    ):
        self._base = base_url.rstrip("/")
        self._static_bearer = bearer_token
        self._run_id = run_id
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self._producer = producer
        self._token_manager = token_manager
        # Deployment-level tenant attribution. Per RFC-ACDP-0008 §6.4 the
        # `X-Tenant-Id` header is NEVER authoritative — it is only a
        # fallback for producer-signed (bearer-less) publishes. When a
        # bearer token is present the authoritative tenant rides in the
        # JWT `tenant` claim, so in "fallback" mode we suppress the header
        # to avoid a claim/header conflict (which the registry rejects).
        #   * fallback (default): send the header only when un-authenticated
        #   * always:             always send it (used to test conflict reject)
        #   * never:              never send it
        self._tenant_id = tenant_id
        self._tenant_header_mode: TenantHeaderMode = tenant_header_mode

    # ── lifecycle ────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AcdpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ── headers ──────────────────────────────────────────────────────────

    async def _bearer_token(self) -> str | None:
        """Resolve the bearer token for the next request.

        Order of precedence:
        1. Explicit ``bearer_token=`` constructor arg (static override).
        2. A live token from the :class:`TokenManager`, refreshed on
           demand.
        3. ``None`` — anonymous request.
        """
        if self._static_bearer:
            return self._static_bearer
        if self._producer and self._token_manager:
            cached = await self._token_manager.token_for(self._producer, self._base)
            return cached.token
        return None

    async def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        token = await self._bearer_token()
        if token:
            h["Authorization"] = f"Bearer {token}"
        if self._run_id:
            h["X-Run-Id"] = self._run_id
        tenant_header = self._tenant_header_value(authenticated=bool(token))
        if tenant_header is not None:
            h["X-Tenant-Id"] = tenant_header
        if extra:
            h.update(extra)
        return h

    def _tenant_header_value(self, *, authenticated: bool) -> str | None:
        """Decide whether to attach ``X-Tenant-Id`` to the next request.

        See the constructor for the policy rationale. The header is only
        a fallback signal; it is suppressed for authenticated requests in
        ``fallback`` mode so it can never contradict the JWT claim.
        """
        if self._tenant_id is None or self._tenant_header_mode == "never":
            return None
        if self._tenant_header_mode == "always":
            return self._tenant_id
        # fallback
        return None if authenticated else self._tenant_id

    async def _retrying(
        self,
        send: Callable[[dict[str, str]], Awaitable[httpx.Response]],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send a request once; on 401 with a managed token, invalidate
        + refresh + retry once.

        Idempotent for GET/PUT/DELETE and acceptable for POST since a 401
        means the prior call never reached the business-logic layer.
        """
        headers = await self._headers(extra_headers)
        r = await send(headers)
        if r.status_code != 401 or not (self._producer and self._token_manager):
            return r
        # Cached token rejected — drop it and try once more.
        self._token_manager.invalidate(self._producer, self._base)
        headers = await self._headers(extra_headers)
        return await send(headers)

    # ── Publish ──────────────────────────────────────────────────────────

    async def publish(
        self,
        request_json: str,
        *,
        idempotency_key: str | None = None,
    ) -> PublishResponse:
        extra = {"Idempotency-Key": idempotency_key} if idempotency_key else None

        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.post(
                f"{self._base}/contexts", content=request_json, headers=h
            )

        r = await self._retrying(send, extra_headers=extra)
        _raise_for_status(r)
        return PublishResponse.model_validate(r.json())

    # ── Retrieve ─────────────────────────────────────────────────────────

    @staticmethod
    def _encode_ctx(ctx_id: str) -> str:
        """URL-encode a ctx_id for path interpolation.

        ACDP ctx_ids look like ``acdp://<authority>/<uuid>`` — the
        embedded ``://`` and ``/`` break axum's `:ctx_id` single-segment
        capture if sent raw. ``quote(safe="")`` encodes every reserved
        character.
        """
        return quote(ctx_id, safe="")

    async def retrieve(self, ctx_id: str) -> FullContext:
        encoded = self._encode_ctx(ctx_id)

        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(f"{self._base}/contexts/{encoded}", headers=h)

        r = await self._retrying(send)
        _raise_for_status(r)
        return FullContext.model_validate(r.json())

    async def retrieve_raw(self, ctx_id: str) -> dict:
        """Return the registry's full-context JSON verbatim (unparsed).

        Used when a downstream needs the exact body bytes the registry
        produced — e.g. ``build_supersede_request`` requires the previous
        body with its registry-assigned ``ctx_id``/``created_at`` and
        *without* the explicit nulls a re-serialized model would inject.
        """
        encoded = self._encode_ctx(ctx_id)

        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(f"{self._base}/contexts/{encoded}", headers=h)

        r = await self._retrying(send)
        _raise_for_status(r)
        return r.json()

    async def retrieve_body(self, ctx_id: str) -> Body:
        encoded = self._encode_ctx(ctx_id)

        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(
                f"{self._base}/contexts/{encoded}/body", headers=h
            )

        r = await self._retrying(send)
        _raise_for_status(r)
        return Body.model_validate(r.json())

    # ── Search ───────────────────────────────────────────────────────────

    async def search(
        self,
        q: str | None = None,
        *,
        context_type: str | None = None,
        domain: str | None = None,
        agent_id: str | None = None,
        tags: list[str] | None = None,
        derived_from: str | None = None,
        visibility: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SearchResponse:
        params: dict[str, str | int] = {}
        if q is not None:
            params["q"] = q
        if context_type is not None:
            params["type"] = context_type
        if domain is not None:
            params["domain"] = domain
        if agent_id is not None:
            params["agent_id"] = agent_id
        if tags:
            params["tags"] = ",".join(tags)
        if derived_from:
            params["derived_from"] = derived_from
        if visibility is not None:
            params["visibility"] = visibility
        params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor

        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(
                f"{self._base}/contexts/search", params=params, headers=h
            )

        r = await self._retrying(send)
        self._raise_cursor_error(r)
        _raise_for_status(r)
        return SearchResponse.model_validate(r.json())

    @staticmethod
    def _raise_cursor_error(r: httpx.Response) -> None:
        """Translate a 400 + cursor error code into :class:`CursorError`.

        RFC-ACDP-0007 §4 returns ``{"error": {"code": "...", ...}}``;
        ``invalid_cursor`` / ``cursor_expired`` are surfaced as a typed
        error so paginating callers can react (restart vs abort).
        """
        if r.status_code != 400:
            return
        try:
            err = r.json().get("error") or {}
        except ValueError:
            return
        code = err.get("code")
        if code in CURSOR_ERROR_CODES:
            raise CursorError(code, err.get("message", "") or err.get("detail", ""))

    async def search_all(
        self,
        q: str | None = None,
        *,
        context_type: str | None = None,
        domain: str | None = None,
        agent_id: str | None = None,
        tags: list[str] | None = None,
        derived_from: str | None = None,
        visibility: str | None = None,
        page_size: int = 20,
        max_pages: int = 100,
    ) -> AsyncIterator[SearchHit]:
        """Yield every match across the whole paginated sequence.

        Critically (RFC-ACDP-0005 §2.3) this loop continues while
        ``next_cursor`` is present **even when a page returns zero
        matches** — a storage page whose rows were all post-filtered
        (visibility/tenant) still advances the cursor. Terminating on an
        empty page would silently drop later results.

        ``max_pages`` is a safety bound against a misbehaving registry
        that never stops returning a cursor; reaching it logs nothing
        here but is observable by the caller via the yielded count.
        """
        cursor: str | None = None
        for _ in range(max_pages):
            resp = await self.search(
                q,
                context_type=context_type,
                domain=domain,
                agent_id=agent_id,
                tags=tags,
                derived_from=derived_from,
                visibility=visibility,
                limit=page_size,
                cursor=cursor,
            )
            for hit in resp.matches:
                yield hit
            if not resp.next_cursor:
                return
            cursor = resp.next_cursor

    # ── Lineage ──────────────────────────────────────────────────────────

    async def lineage(self, lineage_id: str) -> list[FullContext]:
        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(
                f"{self._base}/lineages/{lineage_id}", headers=h
            )

        r = await self._retrying(send)
        _raise_for_status(r)
        return [FullContext.model_validate(x) for x in r.json()]

    async def current(self, lineage_id: str) -> FullContext:
        async def send(h: dict[str, str]) -> httpx.Response:
            return await self._http.get(
                f"{self._base}/lineages/{lineage_id}/current", headers=h
            )

        r = await self._retrying(send)
        _raise_for_status(r)
        return FullContext.model_validate(r.json())

    # ── Cross-registry routing ───────────────────────────────────────────

    @staticmethod
    def _authority_of(ctx_id: str) -> str:
        return ctx_id.removeprefix("acdp://").split("/", 1)[0]

    async def resolve(
        self,
        ctx_id: str,
        authority_map: dict[str, "AcdpClient"],
    ) -> FullContext:
        """Retrieve a context, routing to the registry that owns it.

        Falls back to this client when the authority is unknown (the
        registry's cross-registry resolver will forward in that case).
        """
        authority = self._authority_of(ctx_id)
        client = authority_map.get(authority, self)
        return await client.retrieve(ctx_id)

    # ── Health ───────────────────────────────────────────────────────────

    async def healthz(self) -> bool:
        try:
            r = await self._http.get(f"{self._base}/healthz", timeout=5.0)
            return r.is_success
        except httpx.HTTPError:
            return False
