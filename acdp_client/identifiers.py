"""Identifier-hygiene checks (RFC-ACDP-0002 §3.1).

``origin_registry`` (and a registry's own authority) is a **bare DNS
hostname** — lowercase ASCII LDH labels, no port, no scheme, no ``did:``
prefix. The wire-convention tightening in the RFC (commit ``05bab36``,
``body-001``/``body-002`` fixtures) makes this normative because a
``host:port`` or ``did:web:host`` value changes the ``content_hash``
preimage and breaks federation routing.

These helpers let the playground assert that a retrieved body's
``origin_registry`` is well-formed before trusting it for cross-registry
resolution.
"""

from __future__ import annotations

import re

# One DNS label: LDH (letters/digits/hyphen), no leading/trailing hyphen,
# 1–63 chars. Hostnames are lowercased before matching.
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

# The reserved tenant sentinel — the silent column default for untenanted
# rows on both the registry and the control plane.
RESERVED_TENANT = "default"


def is_valid_authority(host: str) -> bool:
    """True iff ``host`` is a bare DNS hostname (no port/scheme/DID/uppercase).

    Validation is strict and case-sensitive: ``Registry.Example`` is
    rejected because authorities are minted lowercase. Trailing dots,
    ports, schemes, and ``did:`` forms are all rejected.
    """
    if not host or host != host.lower():
        return False
    if ":" in host or "/" in host or host.startswith("did:"):
        return False
    if host.endswith(".") or ".." in host:
        return False
    if len(host) > 253:
        return False
    return all(_LABEL.match(label) for label in host.split("."))


def validate_origin_registry(value: str) -> None:
    """Raise ``ValueError`` unless ``value`` is a conformant authority."""
    if not is_valid_authority(value):
        raise ValueError(
            f"origin_registry must be a bare DNS hostname "
            f"(no port/scheme/did:/uppercase): {value!r}"
        )


def is_reserved_tenant(tenant: str | None) -> bool:
    """True iff ``tenant`` is the reserved ``default`` sentinel."""
    return tenant == RESERVED_TENANT


def reject_reserved_tenant(tenant: str | None) -> None:
    """Raise ``ValueError`` if ``tenant`` explicitly asserts the reserved sentinel.

    ``default`` is the silent column default for untenanted rows. Asserting it
    via ``X-Tenant-Id`` or a signed ``tenant`` claim would alias the entire
    untenanted bucket — a cross-boundary read/write. Both siblings now reject
    it server-side (registry ``reject_reserved_tenant``, acdp-registry-core
    ``c988ea4`` → 400 ``schema_violation``; control-plane ``AuthGuard``, #50 →
    403 ``not_authorized``). We mirror the rule client-side so a caller who
    sets ``tenant_id="default"`` fails fast locally with a clear message
    instead of a confusing server rejection. Untenanted access stays reachable
    only through the *absence* of an assertion (``None``), which passes through
    untouched.
    """
    if is_reserved_tenant(tenant):
        raise ValueError(
            f"{RESERVED_TENANT!r} is a reserved tenant sentinel and cannot be "
            "asserted via X-Tenant-Id or a token claim; omit the tenant "
            "entirely for untenanted access"
        )


__all__ = [
    "RESERVED_TENANT",
    "is_reserved_tenant",
    "is_valid_authority",
    "reject_reserved_tenant",
    "validate_origin_registry",
]
