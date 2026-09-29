"""Outbound URL safety checks for the homepage scraper.

The inbound webhook accepts an arbitrary ``website`` from the caller, so every
URL the scraper fetches (including each redirect hop and ``robots.txt``) is
checked here first. Hostnames are resolved and *every* returned address must be
publicly routable; loopback, private (RFC 1918 / ULA), link-local (including the
cloud metadata address 169.254.169.254), CGNAT, multicast, reserved, and
unspecified addresses are rejected, as are IPv6 forms that embed such an IPv4
address (IPv4-mapped, 6to4, Teredo, NAT64).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

ALLOWED_SCHEMES = {"http", "https"}
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class UnsafeURLError(ValueError):
    """Raised when a URL must not be fetched."""


async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # Strip a zone id-free scope and check any embedded IPv4 as well.
        embedded = _embedded_ipv4(ip)
        if embedded is not None and not is_public_ip(embedded):
            return False
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_unspecified
    )


async def validate_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    allow_private: bool = False,
) -> None:
    """Raise ``UnsafeURLError`` unless ``url`` is http(s) and resolves only to public IPs."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme {scheme or '(none)'!r} is not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    if allow_private:
        return

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("invalid port") from exc

    try:
        literal: IPAddress | None = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [literal]
    else:
        try:
            resolved = await (resolver or default_resolver)(host, port)
        except (OSError, UnicodeError) as exc:
            raise UnsafeURLError(f"could not resolve host {host!r}") from exc
        if not resolved:
            raise UnsafeURLError(f"could not resolve host {host!r}")
        addresses = [ipaddress.ip_address(addr.split("%", 1)[0]) for addr in resolved]

    for address in addresses:
        if not is_public_ip(address):
            raise UnsafeURLError(f"host {host!r} resolves to non-public address {address}")
