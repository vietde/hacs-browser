"""HA Browser — HTTP views.

* AssetView         — the panel's JS, served without auth (no secrets).
* BrowserConfigView — homepage + adblock settings for the panel
  (admin-only; the panel fetches it with the access token HA hands
  it, because the panel cannot read the token from localStorage in
  the HA mobile app).
* ProxyView         — fetches a target page server-side, strips the
  framing headers, rewrites it through the proxy and applies the
  adblocker. Admin-only: it makes Home Assistant fetch arbitrary
  public URLs, so it must never be open to non-admin users.

Auth design (v0.1.1 — this is what v0.1.0 got wrong)
----------------------------------------------------
Home Assistant only authenticates an HTTP request from a Bearer
`Authorization` header or a signed `?authSig=` query parameter —
there is no cookie auth (see `homeassistant.components.http.auth`,
`auth_middleware`). A plain iframe navigation sends neither, so
v0.1.0's iframe sidebar panel pointing at an authenticated
`/api/ha_browser/` page answered **401**, and the browser UI never
loaded at all. The frontend's iframe panel does not add any token
to the URL either (`ha-panel-iframe.ts` renders the configured URL
verbatim).

So the panel is a custom element now (panel.js, registered through
`panel_custom` like HA Terminal): Home Assistant hands it the `hass`
object, and it signs every proxy URL itself with the `auth/sign_path`
websocket command before setting the inner iframe's `src`. Every URL
the proxy rewrites inside a page is signed the same way, server-side,
with the requesting user's refresh token (`async_sign_path`), so
images, stylesheets and scripts load instead of 401ing too. Signed
URLs are only valid for GET/HEAD, so a form that POSTs through the
proxy still fails — that joins the honest limits in the README.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.const import KEY_HASS_REFRESH_TOKEN_ID, KEY_HASS_USER
from homeassistant.config_entries import ConfigEntry

from . import proxy as browser_proxy
from .const import CONF_ADBLOCK, CONF_HOMEPAGE, DEFAULT_ADBLOCK, DEFAULT_HOMEPAGE, DOMAIN
from .proxy import PROXY_PATH

FRONTEND_DIR = Path(__file__).parent / "frontend"

_ASSET_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
}

# Rewritten sub-resource URLs are signed for this long. Top-level
# navigations are signed by the panel for 1 hour; a page open longer
# than that reloads through a fresh signature.
SIGN_EXPIRATION = timedelta(minutes=30)

_BLOCKED_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Blocked</title><style>
body{background:#101418;color:#e6e6e6;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{text-align:center}h1{font-size:20px;margin:0 0 8px}p{color:#9aa3ab;margin:0}
</style></head><body><div><h1>Blocked by HA Browser</h1>
<p>This ad or tracker was blocked by the built-in adblocker.</p></div>
</body></html>"""


def _is_admin(request: web.Request) -> bool:
    user = request.get(KEY_HASS_USER)
    return user is not None and user.is_admin


def _signer_for(request: web.Request):
    """Return a signer for proxy URLs, or None when the request has no token.

    The auth middleware stores the authenticated user's refresh token
    id on the request whether it came in via a Bearer header or a
    signed URL, so this works for both the panel's signed top-level
    navigation and any later request carrying the token.
    """
    refresh_token_id = request.get(KEY_HASS_REFRESH_TOKEN_ID)
    if not refresh_token_id:
        return None
    hass = request.app["hass"]

    def signer(proxy_url: str) -> str | None:
        try:
            return async_sign_path(
                hass, proxy_url, SIGN_EXPIRATION, refresh_token_id=refresh_token_id
            )
        except Exception:
            return None

    return signer


class AssetView(HomeAssistantView):
    """Static JS for the browser panel. No secrets, no auth needed."""

    url = "/api/ha_browser/assets/{filename}"
    name = "api:ha_browser:assets"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        path = FRONTEND_DIR / filename
        if path.suffix not in _ASSET_TYPES:
            return web.Response(status=404)
        try:
            resolved = path.resolve()
        except OSError:
            return web.Response(status=404)
        if resolved.parent != FRONTEND_DIR.resolve() or not resolved.is_file():
            return web.Response(status=404)
        return web.FileResponse(resolved, headers={"Content-Type": _ASSET_TYPES[path.suffix]})


class BrowserConfigView(HomeAssistantView):
    """Homepage + adblock settings for the panel. Admin-only."""

    url = "/api/ha_browser/config"
    name = "api:ha_browser:config"
    requires_auth = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def get(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return web.Response(status=403, text="Admin only")
        return web.json_response(
            {
                "homepage": self._entry.options.get(CONF_HOMEPAGE, DEFAULT_HOMEPAGE),
                "adblock": self._entry.options.get(CONF_ADBLOCK, DEFAULT_ADBLOCK),
            }
        )


class ProxyView(HomeAssistantView):
    """Fetch a page, rewrite it through the proxy, block ads."""

    url = PROXY_PATH
    name = "api:ha_browser:proxy"
    requires_auth = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def get(self, request: web.Request) -> web.Response:
        return await self._handle(request)

    async def post(self, request: web.Request) -> web.Response:
        return await self._handle(request)

    async def _handle(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return web.Response(status=403, text="Admin only")
        target = browser_proxy.extract_target(request.query.get("url"))
        if target is None:
            return web.Response(status=400, text="Missing or invalid url")
        if browser_proxy.is_blocked_url(target):
            # A top-level navigation (address bar / link click) gets a
            # readable blocked page; a sub-resource just gets nothing.
            if request.headers.get("Sec-Fetch-Dest", "document") == "document":
                return web.Response(text=_BLOCKED_PAGE, content_type="text/html")
            return web.Response(status=204)

        session: aiohttp.ClientSession = request.app["hass"].data[DOMAIN][
            self._entry.entry_id
        ]["session"]
        try:
            body = await request.read() if request.method == "POST" else None
            headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length", "connection", "cookie")
            }
            async with session.request(
                request.method,
                target,
                data=body,
                headers=headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=30),
                max_redirects=5,
            ) as resp:
                # Capture everything needed before the response is released.
                content_type = resp.headers.get("Content-Type", "")
                final_url = str(resp.url)
                encoding = resp.get_encoding()
                resp_headers = browser_proxy.strip_headers(resp.headers)
                raw = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            return web.Response(status=502, text=f"Could not load page: {err}")
        if len(raw) > browser_proxy.MAX_BODY_BYTES:
            return web.Response(status=502, text="Page too large to proxy")

        headers = resp_headers
        # The response's own Content-Type / Content-Length must not be
        # forwarded: aiohttp raises if a Content-Type header is passed
        # together with an explicit content_type, and the length is
        # wrong anyway once the page has been rewritten.
        headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("content-type", "content-length")
        }
        adblock = self._entry.options.get(CONF_ADBLOCK, DEFAULT_ADBLOCK)
        signer = _signer_for(request)

        if "text/html" in content_type:
            try:
                text = raw.decode(encoding or "utf-8", errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            if adblock:
                text, blocked = browser_proxy.rewrite_html(text, final_url, signer)
            else:
                text, blocked = text, 0
            return web.Response(
                text=text,
                content_type="text/html",
                headers={**headers, "X-HA-Browser-Blocked": str(blocked)},
            )
        if "text/css" in content_type and adblock:
            try:
                text = raw.decode(encoding or "utf-8", errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            text, blocked = browser_proxy.rewrite_css(text, final_url, signer)
            return web.Response(
                text=text,
                content_type="text/css",
                headers={**headers, "X-HA-Browser-Blocked": str(blocked)},
            )
        return web.Response(
            body=raw,
            content_type=content_type or "application/octet-stream",
            headers=headers,
        )
