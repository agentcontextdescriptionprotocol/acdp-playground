"""Parse HTTP ``Retry-After`` headers (RFC 9110 §10.2.3).

Two wire forms are accepted:

* **delta-seconds** — ``Retry-After: 30`` → 30.0 seconds.
* **HTTP-date** — ``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`` → the
  number of seconds between ``now`` and that instant.

Returns a non-negative float of seconds, or ``None`` when the header is
absent or unparseable. Mirrors the control plane's own
``parseRetryAfterMs`` so the playground reacts the same way to a
cooperating, rate-limited upstream.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime


def parse_retry_after(value: str | None, *, now: float) -> float | None:
    """Return the retry delay in seconds, clamped to ``>= 0``.

    ``now`` is a unix timestamp (``time.time()``); it is required rather
    than read internally so callers stay deterministic/testable.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    # delta-seconds form (all digits).
    if value.isdigit():
        return float(value)
    # HTTP-date form.
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    delay = dt.timestamp() - now
    return max(0.0, delay)
