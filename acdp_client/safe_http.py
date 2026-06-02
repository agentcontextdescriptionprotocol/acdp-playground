"""Consumer-side SSRF guard for following ``data_refs[].location`` URLs.

A pure-Python mirror of the enforcement that landed in the Rust SDK
(``acdp-rs/src/safe_http.rs``, PR #29). The Rust policy lives in the
core ``RegistryClient`` — which the playground's ``httpx``-based
:class:`acdp_client.client.AcdpClient` does **not** use — so a consumer
following an external ``data_refs[].location`` would otherwise reach a
private/loopback/IMDS address with no protection at all.

This module screens that fetch the way RFC-ACDP-0008 §4.9 (cross-ref
RFC-ACDP-0006 §7) requires, and is validated against the RFC's own
conformance fixtures (``data-ref-ssrf-00{1..5}-*.json``):

* **HTTPS-only** — ``http://`` and other schemes are refused without
  connecting.
* **IP-range filtering on the resolved address(es)** — loopback,
  RFC-1918, link-local/IMDS (incl. ``169.254.169.254``), ULA, multicast,
  IPv4-mapped/compatible IPv6, and the NAT64 well-known prefix.
* **Mixed-answer rejection** — if DNS returns several addresses and *any*
  is forbidden, the *whole* resolution is rejected (never connect to the
  surviving public address).
* **Same-authority redirects only** — a redirect target must match the
  origin on scheme + host + *effective port*; a host-only match is
  non-conformant. At most :pyattr:`SsrfPolicy.max_redirects` follows.
* **Response-size + timeout caps.**

The DNS resolver is injectable so the unit tests exercise the filter
without real sockets.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpx

# A resolver maps a hostname to the list of textual IP addresses DNS
# would return. Injectable so tests never touch the network.
Resolver = Callable[[str], list[str]]


class SsrfError(RuntimeError):
    """A fetch was refused by the SSRF guard.

    ``reason`` is a stable machine-readable token so callers/tests can
    assert *why* a fetch was blocked.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


class DataRefHashMismatch(RuntimeError):
    """Fetched bytes did not match ``data_ref.content_hash``.

    Mirrors the registry/RFC ``data_ref_hash_mismatch`` code, but this is
    a *consumer* fetch-time check (RFC-ACDP-0008 §4.9): the body itself
    stays valid; the referenced data is simply unverifiable.
    """


# ── forbidden ranges ───────────────────────────────────────────────────────

# NAT64 well-known prefix (RFC 6052) — routes can reach IMDS via a NAT64
# gateway, so block it explicitly.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

_FORBIDDEN_V4 = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. IMDS
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
]

_FORBIDDEN_V6 = [
    ipaddress.ip_network("fc00::/7"),  # ULA
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
    ipaddress.ip_network("::/128"),  # unspecified
]

_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")


@dataclass(frozen=True)
class SsrfPolicy:
    """Knobs for the consumer fetch guard. The default is production-safe."""

    allow_http: bool = False
    reject_ip_literals: bool = True
    allow_loopback: bool = False
    allow_private: bool = False
    max_redirects: int = 3
    max_bytes: int = 1_048_576  # 1 MB (RFC-ACDP-0006 §7)
    connect_timeout: float = 5.0
    total_timeout: float = 30.0

    @classmethod
    def production(cls) -> "SsrfPolicy":
        return cls()

    @classmethod
    def allow_test_loopback(cls) -> "SsrfPolicy":
        """Permit loopback (for a local registry/data stack) but still
        block RFC-1918 + link-local/IMDS — mirrors the Rust SDK's
        ``SsrfPolicy::allow_test_loopback``.
        """
        return cls(allow_loopback=True, allow_http=True, reject_ip_literals=False)


def _coerce_v4(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Return the embedded IPv4 address for v4-mapped/compatible IPv6.

    ``::ffff:169.254.169.254`` and ``::169.254.169.254`` must be screened
    against the v4 ranges, not slip through as "some IPv6 address".
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        # IPv4-compatible (deprecated) ::a.b.c.d — low 32 bits, when the
        # high bits are zero and it isn't ::1/:: itself.
        if int(ip) >> 32 == 0 and int(ip) > 1:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def ip_is_forbidden(ip_text: str, policy: SsrfPolicy) -> str | None:
    """Return a reason string if ``ip_text`` is off-limits, else ``None``."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return "unparseable_ip"

    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64:
        return "nat64"

    ip = _coerce_v4(ip)

    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _LOOPBACK_V4:
            return None if policy.allow_loopback else "loopback"
        for net in _FORBIDDEN_V4:
            if ip in net:
                # CGNAT / RFC-1918 honour allow_private; the rest never do.
                if net.prefixlen in (8, 10, 12, 16) and net.network_address in (
                    ipaddress.IPv4Address("10.0.0.0"),
                    ipaddress.IPv4Address("172.16.0.0"),
                    ipaddress.IPv4Address("192.168.0.0"),
                    ipaddress.IPv4Address("100.64.0.0"),
                ):
                    if policy.allow_private:
                        return None
                return _v4_reason(net)
        return None

    # IPv6
    if ip == ipaddress.IPv6Address("::1"):
        return None if policy.allow_loopback else "loopback"
    for net in _FORBIDDEN_V6:
        if ip in net:
            if net.prefixlen == 7 and policy.allow_private:  # ULA fc00::/7
                return None
            return "ipv6_special"
    return None


def _v4_reason(net: ipaddress.IPv4Network) -> str:
    if net == ipaddress.ip_network("169.254.0.0/16"):
        return "link_local"
    if net in (ipaddress.ip_network("224.0.0.0/4"), ipaddress.ip_network("240.0.0.0/4")):
        return "multicast_or_reserved"
    return "private"


# ── URL + redirect screening ────────────────────────────────────────────────


def _is_ip_literal(host: str) -> bool:
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
        return True
    except ValueError:
        return False


def check_url(url: str, policy: SsrfPolicy) -> None:
    """Validate scheme/userinfo/host shape *before* any DNS resolution."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "http":
        if not policy.allow_http:
            raise SsrfError("forbidden_scheme", f"http:// not allowed: {url}")
    elif scheme != "https":
        raise SsrfError("forbidden_scheme", f"scheme {scheme!r} not allowed: {url}")
    if parts.username or parts.password:
        raise SsrfError("forbidden_userinfo", f"userinfo not allowed in URL: {url}")
    host = parts.hostname
    if not host:
        raise SsrfError("no_host", f"no host in URL: {url}")
    if policy.reject_ip_literals and _is_ip_literal(host):
        raise SsrfError("ip_literal", f"IP-literal host not allowed: {host}")


def _effective_port(parts) -> int:
    if parts.port is not None:
        return parts.port
    return 443 if parts.scheme.lower() == "https" else 80


def same_authority(u1: str, u2: str) -> bool:
    """True iff scheme + host (case-insensitive) + *effective port* match.

    A host-only comparison is non-conformant (RFC-ACDP-0006 §7.5): an
    attacker controlling ``host:443`` need not control an internal service
    the same host exposes on ``:8443``.
    """
    a, b = urlsplit(u1), urlsplit(u2)
    return (
        a.scheme.lower() == b.scheme.lower()
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and _effective_port(a) == _effective_port(b)
    )


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:  # pragma: no cover - network dependent
        raise SsrfError("dns_failure", f"could not resolve {host}: {e}") from e
    return [info[4][0] for info in infos]


def screen_host(host: str, policy: SsrfPolicy, *, resolver: Resolver | None = None) -> list[str]:
    """Resolve ``host`` and reject the *whole* answer set on any bad address.

    Mixed-answer rejection (RFC-ACDP-0006 §7.1): partial filtering would
    leave the forbidden address in DNS for a reconnect/rebind to exploit.
    """
    resolve = resolver or _default_resolver
    addrs: Iterable[str] = resolve(host)
    addrs = list(addrs)
    if not addrs:
        raise SsrfError("dns_failure", f"no addresses for {host}")
    for ip_text in addrs:
        reason = ip_is_forbidden(ip_text, policy)
        if reason is not None:
            raise SsrfError(
                reason,
                f"{host} resolved to a forbidden address ({ip_text}); "
                "rejecting the entire resolution",
            )
    return addrs


# ── fetch ────────────────────────────────────────────────────────────────────


async def fetch(
    url: str,
    *,
    policy: SsrfPolicy | None = None,
    resolver: Resolver | None = None,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Fetch ``url`` under the SSRF guard, returning the response bytes.

    Screens the URL + every redirect hop, enforces same-authority
    redirects with a follow cap, and bounds the response size. Raises
    :class:`SsrfError` on any policy violation.
    """
    pol = policy or SsrfPolicy.production()
    owns = client is None
    http = client or httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(pol.total_timeout, connect=pol.connect_timeout),
    )
    try:
        current = url
        for _ in range(pol.max_redirects + 1):
            check_url(current, pol)
            host = urlsplit(current).hostname or ""
            screen_host(host, pol, resolver=resolver)
            resp = await http.get(current)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise SsrfError("bad_redirect", "redirect with no Location")
                target = str(resp.url.join(location))
                if not same_authority(current, target):
                    raise SsrfError(
                        "cross_authority_redirect",
                        f"redirect {current} -> {target} crosses the authority",
                    )
                current = target
                continue
            data = resp.content
            if len(data) > pol.max_bytes:
                raise SsrfError(
                    "response_too_large",
                    f"{len(data)} bytes exceeds cap {pol.max_bytes}",
                )
            return data
        raise SsrfError("too_many_redirects", f"exceeded {pol.max_redirects} redirects")
    finally:
        if owns:
            await http.aclose()


async def fetch_data_ref(
    data_ref: dict,
    *,
    policy: SsrfPolicy | None = None,
    resolver: Resolver | None = None,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Fetch a ``data_refs[]`` entry's ``location`` under the SSRF guard.

    When the entry carries a ``content_hash`` (``sha256:<hex>``) the
    fetched bytes are verified against it; a mismatch raises
    :class:`DataRefHashMismatch` and the data MUST NOT be reported as
    verified (RFC-ACDP-0008 §4.9).
    """
    location = data_ref.get("location")
    if not location:
        raise SsrfError("no_location", "data_ref has no location to fetch")
    data = await fetch(location, policy=policy, resolver=resolver, client=client)
    expected = data_ref.get("content_hash")
    if expected:
        algo, _, hexdigest = expected.partition(":")
        if algo != "sha256":
            raise DataRefHashMismatch(f"unsupported content_hash algorithm: {algo!r}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != hexdigest:
            raise DataRefHashMismatch(
                f"data_ref content_hash mismatch: expected {hexdigest[:16]}…, "
                f"got {actual[:16]}…"
            )
    return data


__all__ = [
    "DataRefHashMismatch",
    "Resolver",
    "SsrfError",
    "SsrfPolicy",
    "check_url",
    "fetch",
    "fetch_data_ref",
    "ip_is_forbidden",
    "same_authority",
    "screen_host",
]
