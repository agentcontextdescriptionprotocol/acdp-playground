"""ACDP registry bearer-token manager.

Implements the challenge → sign → token flow against an ACDP registry,
caches issued tokens per ``(agent_did, registry_base_url)``, refreshes
proactively before expiry, and retries once on a 401.

Wire types live in ``acdp-registry-rs/crates/acdp-registry-types/src/auth.rs``;
the canonical challenge signing input is

    acdp-registry-auth:v1:{nonce}:{agent_id}:{authority}:{expires_at}

The Rust SDK's :meth:`acdp.AcdpProducer.sign_challenge` builds the
base64 Ed25519 signature directly from that string.

Design notes
------------

* **Per-(agent, registry) cache.** Each registry signs its own JWTs
  with its own secret. A token issued by registry-a is meaningless to
  registry-b, so we key the cache accordingly.

* **Proactive refresh.** When ``now + leeway >= expires_at`` we acquire
  a new token *before* sending the next request, instead of paying for
  one 401 round-trip per refresh window. Leeway defaults to 30s which
  matches the registry's own ``token_leeway_seconds``.

* **Single-flight per key.** An ``asyncio.Lock`` per cache key ensures
  that concurrent requests trigger at most one challenge/token round
  trip per agent+registry; the rest wait for the lock and reuse the
  resulting token.

* **Reactive retry.** A second 401 after a fresh token means a real
  authorization problem (audience mismatch, revoked key, …); we
  surface that as :class:`TokenAuthError` instead of looping.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from acdp import AcdpProducer

log = logging.getLogger(__name__)


# ── exceptions ───────────────────────────────────────────────────────────


class TokenError(RuntimeError):
    """Base class for token-acquisition errors."""


class ChallengeError(TokenError):
    """The registry refused the challenge request."""


class TokenIssueError(TokenError):
    """The registry refused our signed token request."""


class TokenAuthError(TokenError):
    """A request continued to fail with 401 even after a refreshed token.

    Typically indicates a real authorization problem (audience
    mismatch, revoked key, registry rotated its JWT secret) rather
    than an expired token.
    """


# ── cached token ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CachedToken:
    """A token paired with its registry-declared unix-seconds expiry."""

    token: str
    token_type: str
    expires_at: int  # unix seconds

    def is_fresh(self, leeway_seconds: int) -> bool:
        return time.time() + leeway_seconds < self.expires_at


# ── manager ──────────────────────────────────────────────────────────────


class TokenManager:
    """Per-(agent, registry) token cache with single-flight refresh.

    Multiple agents/registries share one ``TokenManager``; callers
    pass the producer + registry base URL to :meth:`token_for`.
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        leeway_seconds: int = 30,
        timeout: float = 15.0,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self._leeway = leeway_seconds
        self._cache: dict[tuple[str, str], CachedToken] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ── public API ───────────────────────────────────────────────────────

    async def token_for(
        self,
        producer: "AcdpProducer",
        registry_base_url: str,
    ) -> CachedToken:
        """Return a fresh token for ``(producer, registry)``.

        Acquires a single-flight lock so concurrent callers reuse the
        same refreshed token rather than racing the registry. The
        returned ``CachedToken`` is guaranteed to be fresh within
        ``leeway_seconds`` of the call.
        """
        key = (producer.agent_did, registry_base_url.rstrip("/"))
        cached = self._cache.get(key)
        if cached and cached.is_fresh(self._leeway):
            return cached

        lock = await self._lock_for(key)
        async with lock:
            # Recheck under the lock — another coroutine may have refreshed.
            cached = self._cache.get(key)
            if cached and cached.is_fresh(self._leeway):
                return cached
            fresh = await self._mint(producer, key[1])
            self._cache[key] = fresh
            return fresh

    def invalidate(self, producer: "AcdpProducer", registry_base_url: str) -> None:
        """Drop the cached token, forcing a refresh on the next call.

        Used by :class:`acdp_client.AcdpClient` when a request returns 401
        even though we believed the cached token was still valid.
        """
        key = (producer.agent_did, registry_base_url.rstrip("/"))
        self._cache.pop(key, None)

    # ── internals ────────────────────────────────────────────────────────

    async def _lock_for(self, key: tuple[str, str]) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def _mint(
        self, producer: "AcdpProducer", registry_base_url: str
    ) -> CachedToken:
        """Run one full challenge → sign → token round trip."""
        # Step 1 — challenge
        ch_url = f"{registry_base_url}/auth/challenge"
        ch_req = {"agent_id": producer.agent_did}
        log.debug("token mint: challenge %s for %s", ch_url, producer.agent_did)
        ch_resp = await self._http.post(ch_url, json=ch_req)
        if not ch_resp.is_success:
            raise ChallengeError(
                f"POST {ch_url} -> {ch_resp.status_code}: {ch_resp.text[:300]}"
            )
        ch = ch_resp.json()
        try:
            nonce = ch["nonce"]
            expires_at = int(ch["expires_at"])
            signing_input = ch["signing_input"]
        except (KeyError, ValueError) as e:
            raise ChallengeError(f"malformed challenge: {e}: {ch}") from None

        # Step 2 — sign with the agent's Ed25519 key (via the SDK)
        signature = producer.sign_challenge(signing_input)

        # Step 3 — exchange signature for JWT
        tk_url = f"{registry_base_url}/auth/token"
        tk_req = {
            "agent_id": producer.agent_did,
            "key_id": producer.key_id,
            "nonce": nonce,
            "expires_at": expires_at,
            "algorithm": "ed25519",
            "signature": signature,
        }
        tk_resp = await self._http.post(tk_url, json=tk_req)
        if not tk_resp.is_success:
            raise TokenIssueError(
                f"POST {tk_url} -> {tk_resp.status_code}: {tk_resp.text[:300]}"
            )
        tk = tk_resp.json()
        try:
            cached = CachedToken(
                token=tk["token"],
                token_type=tk.get("token_type", "Bearer"),
                expires_at=int(tk["expires_at"]),
            )
        except (KeyError, ValueError) as e:
            raise TokenIssueError(f"malformed token response: {e}: {tk}") from None
        log.debug(
            "token mint: issued for %s, expires_at=%d (in %ds)",
            producer.agent_did,
            cached.expires_at,
            cached.expires_at - int(time.time()),
        )
        return cached


# ── module-level singleton (optional convenience) ────────────────────────


_default: TokenManager | None = None


def default_token_manager() -> TokenManager:
    """Return a process-wide :class:`TokenManager`.

    Most call sites should instantiate their own; the singleton is a
    convenience for short scripts and the playground's runner.
    """
    global _default
    if _default is None:
        _default = TokenManager()
    return _default
