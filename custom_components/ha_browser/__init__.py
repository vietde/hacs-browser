"""HA Browser: a tabbed, dynamic browser panel backed by a secure reverse proxy."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ALLOW_LOOPBACK,
    CONF_ALLOW_PRIVATE,
    CONF_ALLOW_PUBLIC,
    CONF_ALLOWED_HOSTS,
    CONF_BLOCKED_HOSTS,
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_SESSION_TTL,
    CONF_TITLE,
    CONF_VERIFY_SSL,
    DEFAULTS,
    DOMAIN,
    PANEL_ELEMENT,
    PANEL_URL_PATH,
    STATIC_URL,
    VERSION,
)
from .proxy import BrowserProxyView
from .security import HostPolicy
from .session import BrowserRuntime, SessionManager
from .websocket_api import ws_session

_DIR = Path(__file__).parent


def _read_shim() -> str:
    return (_DIR / "shim.js").read_text(encoding="utf-8").strip().rstrip(";")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {"registered": False, "runtime": None})
    opts = {**DEFAULTS, **entry.options}

    policy = HostPolicy(
        allow_loopback=opts[CONF_ALLOW_LOOPBACK],
        allow_private=opts[CONF_ALLOW_PRIVATE],
        allow_public=opts[CONF_ALLOW_PUBLIC],
        allowed=opts[CONF_ALLOWED_HOSTS],
        blocked=opts[CONF_BLOCKED_HOSTS],
    )
    manager = SessionManager(
        hass, policy, int(opts[CONF_SESSION_TTL]) * 60, opts[CONF_VERIFY_SSL]
    )
    shim_js = await hass.async_add_executor_job(_read_shim)
    data["runtime"] = BrowserRuntime(manager, opts, shim_js)

    if not data["registered"]:  # views/commands cannot be unregistered
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(_DIR / "frontend"), True)]
        )
        hass.http.register_view(BrowserProxyView(hass))
        websocket_api.async_register_command(hass, ws_session)
        data["registered"] = True

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_ELEMENT,
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{STATIC_URL}/ha-browser-panel.js?v={VERSION}",
        sidebar_title=opts[CONF_TITLE],
        sidebar_icon=opts[CONF_ICON],
        require_admin=opts[CONF_REQUIRE_ADMIN],
    )

    entry.async_on_unload(
        async_track_time_interval(hass, manager.async_cleanup, timedelta(minutes=1))
    )
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    data = hass.data[DOMAIN]
    runtime, data["runtime"] = data["runtime"], None
    if runtime:
        await runtime.manager.async_close()
    return True
