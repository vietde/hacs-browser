"""HA Browser — setup.

Registers the browser panel, the proxy and the config endpoint, and
owns one dedicated HTTP client session (its own cookie jar) for the
proxy so the browser's cookies never leak into Home Assistant's own
requests.

The panel is a custom element (panel.js), registered through
`panel_custom` exactly like HA Terminal — NOT a plain iframe panel.
v0.1.0 used an iframe panel pointing at an authenticated page and
that is why the sidebar showed 401: Home Assistant only accepts a
Bearer header or a signed `?authSig=` query parameter, and a plain
iframe navigation sends neither (nor does the frontend add one).
The custom element receives the `hass` object and signs every proxy
URL itself with the `auth/sign_path` websocket command before
loading it into its iframe.
"""

from __future__ import annotations

from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN, PANEL_ICON, PANEL_TITLE, PANEL_URL_PATH
from .views import AssetView, BrowserConfigView, ProxyView


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Browser from a config entry."""
    session = async_create_clientsession(
        hass,
        headers={"User-Agent": "HA-Browser/0.1.1 (Home Assistant)"},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"session": session}

    hass.http.register_view(AssetView())
    hass.http.register_view(BrowserConfigView(entry))
    hass.http.register_view(ProxyView(entry))
    await panel_custom.async_register_panel(
        hass,
        PANEL_URL_PATH,
        "ha-browser-panel",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url="/api/ha_browser/assets/panel.js",
        require_admin=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload HA Browser."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
