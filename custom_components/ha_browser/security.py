"""Target policy (SSRF protection) for the HA Browser proxy.

Two layers:
1. ``HostPolicy.check_host`` runs before a request is made (names + IP literals).
2. ``PolicyResolver`` runs inside aiohttp's connector, so the IPs that are
   actually connected to are validated. This defeats DNS rebinding.
"""
from __future__ import annotations

import fnmatch
import ipaddress
import re
import socket
from typing import Any

from aiohttp.abc import AbstractResolver
from aiohttp.resolver import ThreadedResolver

# Never reachable, whatever the options say: unspecified, link-local (cloud
# metadata services live in 169.254.0.0/16 and fd00:ec2::/32), multicast, reserved.
_ALWAYS_BLOCKED = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::/32",
    )
]


class PolicyError(Exception):
    """Raised when a target is not allowed."""


def split_entries(value: Any) -> list[str]:
    """Split a multi-line/comma separated option into normalised entries."""
    if not value:
        return []
    parts = re.split(r"[\s,]+", value) if isinstance(value, str) else list(value)
    return [p.strip().lower() for p in parts if p and p.strip()]


def _parse(entries: list[str]):
    nets, names = [], []
    for entry in entries:
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            names.append(entry)
    return nets, names


def _name_match(host: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(host, p) for p in patterns)


class HostPolicy:
    """Decides which hosts/IPs the proxy may contact."""

    def __init__(
        self,
        *,
        allow_loopback: bool = False,
        allow_private: bool = True,
        allow_public: bool = True,
        allowed: Any = None,
        blocked: Any = None,
    ) -> None:
        self.allow_loopback = allow_loopback
        self.allow_private = allow_private
        self.allow_public = allow_public
        self._allowed_nets, self._allowed_names = _parse(split_entries(allowed))
        self._blocked_nets, self._blocked_names = _parse(split_entries(blocked))

    @property
    def _has_allowlist(self) -> bool:
        return bool(self._allowed_nets or self._allowed_names)

    def check_host(self, host: str) -> None:
        """Pre-flight check on a hostname or IP literal."""
        host = host.strip("[]").rstrip(".").lower()
        if not host:
            raise PolicyError("Empty host")
        try:
            ip = ipaddress.ip_address(host.split("%")[0])
        except ValueError:
            ip = None
        if ip is not None:
            self.check_ip(ip, None)
            return
        if _name_match(host, self._blocked_names):
            raise PolicyError(f"{host} is on the blocked list")
        # With a network-only allow list, names are validated after DNS.
        if self._allowed_names and not self._allowed_nets:
            if not _name_match(host, self._allowed_names):
                raise PolicyError(f"{host} is not on the allowed list")

    def check_ip(self, ip, hostname: str | None) -> None:
        """Check a concrete IP (optionally the one `hostname` resolved to)."""
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if any(ip in net for net in _ALWAYS_BLOCKED):
            raise PolicyError(f"{ip} is in a permanently blocked range")
        if any(ip in net for net in self._blocked_nets):
            raise PolicyError(f"{ip} is on the blocked list")

        name_ok = bool(hostname) and _name_match(hostname, self._allowed_names)
        net_ok = any(ip in net for net in self._allowed_nets)
        if self._has_allowlist and not (name_ok or net_ok):
            raise PolicyError(f"{hostname or ip} is not on the allowed list")
        if name_ok or net_ok:
            return  # explicitly trusted by the user

        if ip.is_loopback:
            if not self.allow_loopback:
                raise PolicyError("Loopback addresses are disabled")
        elif not ip.is_global:
            if not self.allow_private:
                raise PolicyError("Private network addresses are disabled")
        elif not self.allow_public:
            raise PolicyError("Public internet addresses are disabled")


class PolicyResolver(AbstractResolver):
    """aiohttp resolver that drops any address the policy forbids."""

    def __init__(self, policy: HostPolicy) -> None:
        self._policy = policy
        self._inner = ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await self._inner.resolve(host, port, family)
        allowed, last = [], None
        for item in results:
            try:
                ip = ipaddress.ip_address(item["host"].split("%")[0])
                self._policy.check_ip(ip, host.lower().rstrip("."))
                allowed.append(item)
            except (PolicyError, ValueError) as err:
                last = err
        if not allowed:
            raise OSError(f"Blocked by HA Browser policy: {last}")
        return allowed

    async def close(self) -> None:
        await self._inner.close()
