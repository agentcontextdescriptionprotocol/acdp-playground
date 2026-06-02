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


__all__ = ["is_valid_authority", "validate_origin_registry"]
