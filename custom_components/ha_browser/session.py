"""Browser sessions: opaque path tokens, per-session cookie jars and clients."""
from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import client_context, client_context_no_verify

from .const import MAX_SESSIONS_PER_USER
from .security import HostPolicy, PolicyResolver


@dataclass
class BrowserSession:
    """One HA user's browsing session (shared by all their tabs)."""

    token: str
    user_id: str
    last_used: float
    http: aiohttp.ClientSession


class SessionManager:
    """Creates, validates and expires browsing sessions."""

    def __init__(
        self, hass: HomeAssistant, policy: HostPolicy, ttl: int, verify_ssl: bool
    ) -> None:
        self.hass = hass
        self.policy = policy
        self.ttl = ttl  # seconds
        self._sessions: dict[str, BrowserSession] = {}
        self._connector = aiohttp.TCPConnector(
            resolver=PolicyResolver(policy),
            ssl=client_context() if verify_ssl else client_context_no_verify(),
            limit=200,
            limit_per_host=20,
        )

    def create(self, user_id: str) -> BrowserSession:
        mine = sorted(
            (s for s in self._sessions.values() if s.user_id == user_id),
            key=lambda s: s.last_used,
        )
        while len(mine) >= MAX_SESSIONS_PER_USER:
            self._drop(mine.pop(0).token)
        token = secrets.token_urlsafe(24)
        http = aiohttp.ClientSession(
            connector=self._connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(unsafe=True),  # allow cookies for IP hosts
            timeout=aiohttp.ClientTimeout(total=None, connect=20, sock_read=120),
        )
        sess = BrowserSession(token, user_id, time.monotonic(), http)
        self._sessions[token] = sess
        return sess

    def get(self, token: str) -> BrowserSession | None:
        sess = self._sessions.get(token)
        if sess is None:
            return None
        now = time.monotonic()
        if now - sess.last_used > self.ttl:
            self._drop(token)
            return None
        sess.last_used = now
        return sess

    def _drop(self, token: str) -> None:
        sess = self._sessions.pop(token, None)
        if sess is not None:
            self.hass.async_create_task(sess.http.close())

    async def async_cleanup(self, _now: Any = None) -> None:
        cutoff = time.monotonic() - self.ttl
        for token in [t for t, s in self._sessions.items() if s.last_used < cutoff]:
            self._drop(token)

    async def async_close(self) -> None:
        for sess in list(self._sessions.values()):
            await sess.http.close()
        self._sessions.clear()
        await self._connector.close()


@dataclass
class BrowserRuntime:
    """Everything the views need for the currently loaded config entry."""

    manager: SessionManager
    options: dict[str, Any]
    shim_js: str
