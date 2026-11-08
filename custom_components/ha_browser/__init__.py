"""HA Browser — setup.

Registers the browser page, the proxy and the sidebar panel, and owns
one dedicated HTTP client session (its own cookie jar) for the proxy
so the browser's cookies never leak into Home Assistant's own
requests. The panel is a plain iframe panel — the same kind that has
worked on the web, on mobile browsers and in the HA app since the
terminal's v0.1.3 — so nothing here depends on custom elements or
any browser-specific feature.
"""

from __future__ import annotations

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN, PANEL_ICON, PANEL_TITLE, PANEL_URL_PATH
from .views import AssetView, BrowserView, ProxyView


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Browser from a config entry."""
    session = async_create_clientsession(
        hass,
        headers={"User-Agent": "HA-Browser/0.1.0 (Home Assistant)"},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"session": session}

    hass.http.register_view(BrowserView(entry))
    hass.http.register_view(AssetView())
    hass.http.register_view(ProxyView(entry))
    frontend.async_register_built_in_panel(
        hass,
        "iframe",
        PANEL_TITLE,
        PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        config={"url": "/api/ha_browser/"},
        require_admin=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload HA Browser."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
