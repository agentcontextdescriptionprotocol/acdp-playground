"""Parse HTTP ``Retry-After`` headers (RFC 9110 §10.2.3).

Thin re-export of :func:`acdp_client.retry_after.parse_retry_after` so the
playground control-plane bridge and the registry token client share one
implementation. Mirrors the control plane's own ``parseRetryAfterMs`` so
the playground reacts the same way to a cooperating, rate-limited
upstream.
"""

from __future__ import annotations

from acdp_client.retry_after import parse_retry_after

__all__ = ["parse_retry_after"]
