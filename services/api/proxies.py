"""The client's IP address behind the dashboard's nginx (M11).

nginx overwrites X-Forwarded-For with the address it saw (`$remote_addr`), so the header
is the client's address only when the request really came through nginx. Anyone else
can write any X-Forwarded-For they like: the header is believed only when the TCP peer
is a trusted proxy (`MF_TRUSTED_PROXIES`: IPs, CIDRs, or hostnames such as the compose
service name, resolved and cached for 30 s), and then only its last entry, the one the
proxy itself wrote. uvicorn runs with --no-proxy-headers, so this is the only place a
forwarded address is ever believed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Iterable

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class TrustedProxies:
    def __init__(self, entries: Iterable[str], ttl_s: float = 30.0) -> None:
        self.networks: list[Network] = []
        self.hostnames: list[str] = []
        for e in entries:
            try:
                self.networks.append(ipaddress.ip_network(e, strict=False))
            except ValueError:
                self.hostnames.append(e)
        self.ttl_s = ttl_s
        self._resolved: tuple[float, set[str]] = (0.0, set())

    async def _hosts(self) -> set[str]:
        expires, addrs = self._resolved
        if self.hostnames and time.monotonic() >= expires:
            loop, addrs = asyncio.get_running_loop(), set()
            for host in self.hostnames:
                try:
                    for info in await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                        addrs.add(str(info[4][0]))
                except OSError:
                    pass  # not up yet: trust nobody under that name until it resolves
            self._resolved = (time.monotonic() + self.ttl_s, addrs)
        return addrs

    async def trusts(self, peer: str | None) -> bool:
        if not peer:
            return False
        try:
            ip = ipaddress.ip_address(peer)
        except ValueError:
            return False
        if any(ip in n for n in self.networks):
            return True
        return peer in await self._hosts()

    async def client_ip(self, peer: str | None, forwarded_for: str | None) -> str:
        """The proxy's X-Forwarded-For entry if the peer is a trusted proxy, else the peer."""
        if forwarded_for and await self.trusts(peer):
            last = forwarded_for.split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(last))
            except ValueError:
                pass
        return peer or "unknown"
