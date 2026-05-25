"""Optional bridge to the ACDP control plane.

When ``CONTROL_PLANE_URL`` is empty (the default), every method is a
no-op. When it's set, run lifecycle notifications and forwarded
registry webhooks are sent to the control plane with an HMAC-SHA256
signature in ``X-ACDP-Signature: sha256=<hex>``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from playground.config import Settings

log = logging.getLogger(__name__)


def _sign(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class ControlPlaneClient:
    """No-op when URL unset; otherwise fires-and-forgets HTTP calls."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.control_plane_url)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            async with self._lock:
                if self._http is None:
                    self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(
        self,
        path: str,
        body: bytes,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not self.enabled:
            return
        url = self._settings.control_plane_url.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if self._settings.control_plane_hmac_secret:
            headers["X-ACDP-Signature"] = _sign(
                self._settings.control_plane_hmac_secret, body
            )
        if extra_headers:
            headers.update(extra_headers)
        try:
            client = await self._client()
            r = await client.post(url, content=body, headers=headers)
            if not r.is_success:
                log.warning(
                    "control-plane %s -> %s: %s", path, r.status_code, r.text[:200]
                )
        except httpx.HTTPError as e:
            # Never let control-plane outages break a run.
            log.warning("control-plane %s failed: %s", path, e)

    # ── lifecycle events ─────────────────────────────────────────────────

    async def notify_run_started(
        self,
        run_id: str,
        scenario_id: str,
        inputs: dict[str, Any],
    ) -> None:
        payload = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "inputs": inputs,
        }
        await self._post("/runs/started", json.dumps(payload).encode("utf-8"))

    async def notify_run_complete(
        self,
        run_id: str,
        status: str,
        result: dict[str, Any] | None,
    ) -> None:
        payload = {
            "run_id": run_id,
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        await self._post(f"/runs/{run_id}/complete", json.dumps(payload).encode("utf-8"))

    # ── webhook forwarding ──────────────────────────────────────────────

    async def forward_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> None:
        """Forward a registry webhook verbatim to the control plane.

        Re-signs with the control plane's secret if configured (the
        registry's signature wouldn't validate there). Preserves
        X-ACDP-Event so the control plane sees the event type.
        """
        extra: dict[str, str] = {}
        if event := headers.get("x-acdp-event") or headers.get("X-ACDP-Event"):
            extra["X-ACDP-Event"] = event
        await self._post("/ingest/acdp", raw_body, extra_headers=extra)


_singleton: ControlPlaneClient | None = None


def get_control_plane(settings: Settings) -> ControlPlaneClient:
    global _singleton
    if _singleton is None:
        _singleton = ControlPlaneClient(settings)
    return _singleton
