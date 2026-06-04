"""A tested pure-Python reference for RFC 8785 (JCS) canonicalization.

Real ACDP canonicalization happens in the Rust SDK (``acdp-rs``). This
module is a *reference* the playground uses to show — and assert — the
wire form the protocol requires, in particular the ECMAScript
Number::toString rules (RFC 8785 §3.2.2.3, cross-referenced from
RFC-ACDP-0001 §5.2) that are the single most common conformance failure:

* negative zero canonicalizes to ``0`` (Python's ``json.dumps`` does not);
* the decimal band ``[1e-6, 1e21)`` renders without an exponent;
* magnitudes ``>= 1e21`` / ``<= 1e-7`` use exponential form with an
  un-padded, signed exponent (``1e+21``, ``1e-7``);
* integers up to ``2**53`` render exactly with no decimal point.

Validated against the RFC's own conformance vectors
(``schemas/conformance/can-011-jcs-numeric-vectors.json``) in
``tests/test_jcs_vectors.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_EXP_RE = re.compile(r"[eE]")


def ecma_number_to_string(value: float | int) -> str:
    """Render ``value`` per the ECMAScript Number::toString algorithm.

    This is the function RFC 8785 §3.2.2.3 normatively references. It
    operates on the IEEE-754 double value, so ``1.0`` and ``1`` both
    render as ``1`` and ``-0.0`` renders as ``0``.
    """
    if isinstance(value, bool):  # bool is an int subclass — guard first
        raise TypeError("bool is not a JSON number")

    # Exact integers within the double-exact range render with full digits.
    if isinstance(value, int):
        if -(2**53) <= value <= 2**53:
            return str(value)
        value = float(value)

    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not valid JSON numbers")

    if value == 0:  # covers +0.0 and -0.0
        return "0"

    negative = value < 0
    s, n = _shortest_digits_and_point(abs(value))
    body = _format_ecma(s, n)
    return f"-{body}" if negative else body


def _shortest_digits_and_point(x: float) -> tuple[str, int]:
    """Return ``(s, n)`` where ``x = 0.s * 10**n`` with ``s`` the shortest
    significant-digit string (no leading/trailing zeros).

    Python's ``repr`` already yields the shortest decimal that round-trips
    to the same double; we just normalise its placement.
    """
    r = repr(x)
    if _EXP_RE.search(r):
        mant, exp_text = _EXP_RE.split(r)
        exp = int(exp_text)
    else:
        mant, exp = r, 0
    if "." in mant:
        intpart, frac = mant.split(".")
    else:
        intpart, frac = mant, ""
    raw = intpart + frac
    point_pos = len(intpart) + exp  # digits to the left of the point in `raw`
    stripped = raw.lstrip("0")
    point_pos -= len(raw) - len(stripped)
    s = stripped.rstrip("0") or "0"
    return s, point_pos


def _format_ecma(s: str, n: int) -> str:
    """Apply the ECMAScript placement rules to digits ``s`` at point ``n``."""
    k = len(s)
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    # Exponential form. Exponent is n-1, sign always present, no padding.
    exp = n - 1
    sign = "+" if exp >= 0 else "-"
    mant = s if k == 1 else s[0] + "." + s[1:]
    return f"{mant}e{sign}{abs(exp)}"


def _canon_string(value: str) -> str:
    # RFC 8785 string serialization matches json.dumps' escaping for the
    # cases the playground exercises (ASCII keys, simple values).
    return json.dumps(value, ensure_ascii=False)


def canonicalize(obj: Any) -> str:
    """Serialize ``obj`` to its JCS (RFC 8785) canonical string."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return ecma_number_to_string(obj)
    if isinstance(obj, str):
        return _canon_string(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in obj) + "]"
    if isinstance(obj, dict):
        # Keys sorted by UTF-16 code units (RFC 8785 §3.2.3).
        items = sorted(obj.items(), key=lambda kv: str(kv[0]).encode("utf-16-be"))
        return "{" + ",".join(f"{_canon_string(str(k))}:{canonicalize(v)}" for k, v in items) + "}"
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def content_hash(obj: Any) -> str:
    """Return ``sha256:<hex>`` over the JCS canonical bytes of ``obj``."""
    digest = hashlib.sha256(canonicalize(obj).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = ["canonicalize", "content_hash", "ecma_number_to_string"]
