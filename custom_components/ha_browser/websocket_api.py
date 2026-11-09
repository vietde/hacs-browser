"""WebSocket command the panel uses to obtain / renew a browsing session."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOME_URL,
    CONF_REQUIRE_ADMIN,
    CONF_SEARCH_URL,
    DOMAIN,
    PROXY_BASE,
)


@websocket_api.websocket_command(
    {vol.Required("type"): "ha_browser/session", vol.Optional("token"): str}
)
@websocket_api.async_response
async def ws_session(hass: HomeAssistant, connection, msg) -> None:
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "HA Browser is not loaded")
        return
    if runtime.options[CONF_REQUIRE_ADMIN] and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    mgr = runtime.manager
    sess = None
    if token := msg.get("token"):
        existing = mgr.get(token)
        if existing and existing.user_id == connection.user.id:
            sess = existing
    if sess is None:
        sess = mgr.create(connection.user.id)

    connection.send_result(
        msg["id"],
        {
            "token": sess.token,
            "prefix": f"{PROXY_BASE}/{sess.token}",
            "home_url": runtime.options[CONF_HOME_URL],
            "search_url": runtime.options[CONF_SEARCH_URL],
            "ttl": mgr.ttl,
        },
    )
