"""HTTP + WebSocket reverse proxy for HA Browser.

Auth is the unguessable session token in the URL path (iframes cannot send
bearer headers). Every response is served with a CSP ``sandbox`` directive so
proxied content gets an opaque origin and can never read Home Assistant's
storage or tokens, even when the URL is opened directly in a browser tab.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from urllib.parse import urlsplit

import aiohttp
from aiohttp import WSMsgType, web
from yarl import URL

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import CONF_REQUIRE_ADMIN, DOMAIN, MAX_REWRITE_BYTES, PROXY_BASE
from .rewrite import Rewriter
from .security import PolicyError

_LOGGER = logging.getLogger(__name__)

_SANDBOX = (
    "sandbox allow-scripts allow-forms allow-popups allow-modals "
    "allow-downloads allow-pointer-lock allow-presentation"
)
_OUR_HEADERS = {
    "Content-Security-Policy": _SANDBOX,
    "Referrer-Policy": "no-referrer",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Expose-Headers": "*",
}

_DROP_REQ = {
    "host", "cookie", "origin", "referer", "content-length", "accept-encoding",
    "connection", "keep-alive", "proxy-authorization", "proxy-authenticate",
    "te", "trailer", "transfer-encoding", "upgrade", "forwarded", "via",
    "x-real-ip",
}
_DROP_REQ_PREFIX = ("sec-fetch-", "sec-websocket-", "x-forwarded-", "x-ha-", "cf-")
_PASS_RESP = {
    "content-type", "cache-control", "etag", "last-modified", "expires",
    "content-disposition", "content-range", "accept-ranges", "content-language",
    "pragma",
}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_BODYLESS = {204, 304}


def _page(status: int, title: str, detail: str) -> web.Response:
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html_lib.escape(title)}</title>"
        "<style>body{font:16px system-ui,sans-serif;margin:12vh auto;max-width:34rem;"
        "padding:0 1.5rem;color:#333}h1{font-size:1.4rem}code{background:#eee;"
        "padding:.1em .3em;border-radius:3px;word-break:break-all}</style>"
        f"<h1>{html_lib.escape(title)}</h1><p>{html_lib.escape(detail)}</p>"
    )
    return web.Response(
        status=status, body=body.encode(), content_type="text/html",
        charset="utf-8", headers=_OUR_HEADERS,
    )


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([^\s;]+)", ctype, re.I)
    enc = m[1].strip("\"'") if m else None
    if not enc:
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        enc = m2[1].decode("ascii", "ignore") if m2 else "utf-8"
    try:
        return body.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", "replace")


def _close_code(code: int | None) -> int:
    if code and (1000 <= code <= 1003 or 1007 <= code <= 1011 or 3000 <= code <= 4999):
        return code
    return 1000


class BrowserProxyView(HomeAssistantView):
    """/api/ha_browser/s/{token}/{scheme}/{authority}/{tail}"""

    url = PROXY_BASE + "/{token}/{scheme}/{authority}/{tail:.*}"
    extra_urls = [PROXY_BASE + "/{token}/{scheme}/{authority}"]
    name = "api:ha_browser:proxy"
    requires_auth = False  # token in the path authenticates
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def _handle(self, request: web.Request, token, scheme, authority, tail=""):
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime")
        if runtime is None:
            return _page(503, "HA Browser is not loaded", "Enable the integration and retry.")

        # Never raise HTTPUnauthorized: HA would count it towards IP bans.
        sess = runtime.manager.get(token)
        user = await self.hass.auth.async_get_user(sess.user_id) if sess else None
        if (
            sess is None or user is None or not user.is_active
            or (runtime.options[CONF_REQUIRE_ADMIN] and not user.is_admin)
        ):
            return _page(401, "Session expired", "Reload the HA Browser panel to start a new session.")

        if request.method == "OPTIONS":
            h = dict(_OUR_HEADERS)
            h["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"
            h["Access-Control-Allow-Headers"] = request.headers.get(
                "Access-Control-Request-Headers", "*"
            )
            h["Access-Control-Max-Age"] = "600"
            return web.Response(status=204, headers=h)

        if scheme not in ("http", "https") or "@" in authority:
            return _page(400, "Bad address", "Only http:// and https:// addresses are supported.")
        try:
            parts = urlsplit(f"//{authority}")
            host, _port = parts.hostname, parts.port
            if not host:
                raise ValueError
            runtime.manager.policy.check_host(host)
        except ValueError:
            return _page(400, "Bad address", f"Invalid host: {authority}")
        except PolicyError as err:
            return _page(403, "Blocked by HA Browser", f"{authority}: {err}")

        # Rebuild upstream URL from the raw request path so encoding is preserved.
        raw_path, _, query = request.raw_path.partition("?")
        segs = raw_path.split("/", 7)  # '', api, ha_browser, s, token, scheme, auth, rest
        rest = segs[7] if len(segs) > 7 else ""
        upstream = f"{scheme}://{authority}/{rest}"
        if query:
            upstream += "?" + query
        try:
            url = URL(upstream, encoded=True)
        except ValueError:
            return _page(400, "Bad address", "Could not parse URL.")

        headers = {}
        for k, v in request.headers.items():
            lk = k.lower()
            if lk in _DROP_REQ or lk.startswith(_DROP_REQ_PREFIX):
                continue
            headers[k] = v
        headers["Referer"] = f"{scheme}://{authority}/"
        if "Origin" in request.headers:
            headers["Origin"] = f"{scheme}://{authority}"

        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_ws(request, sess, url, scheme, authority, headers)

        body = await request.read() if request.body_exists else None
        try:
            async with sess.http.request(
                request.method, url, headers=headers, data=body or None,
                allow_redirects=False,
            ) as resp:
                return await self._relay(request, resp, runtime, token, str(url))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.debug("Upstream error for %s: %s", url, err)
            return _page(502, "Could not reach site", f"{authority}: {err}")

    get = post = put = patch = delete = head = options = _handle

    # ------------------------------------------------------------------
    async def _relay(self, request, resp, runtime, token, doc_url):
        prefix = f"{PROXY_BASE}/{token}"
        rw = Rewriter(prefix, doc_url)
        out = dict(_OUR_HEADERS)
        for k, v in resp.headers.items():
            if k.lower() in _PASS_RESP:
                out[k] = v
        loc = resp.headers.get("Location")
        if loc:
            out["Location"] = rw.proxify(loc)

        ctype = resp.headers.get("Content-Type", "")
        mime = ctype.split(";")[0].strip().lower()
        rewritable = (
            (mime in _HTML_TYPES or mime == "text/css")
            and request.method != "HEAD"
            and resp.status not in _BODYLESS
        )

        chunks: list[bytes] = []
        if rewritable:
            size, overflow = 0, False
            async for chunk in resp.content.iter_chunked(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_REWRITE_BYTES:
                    overflow = True
                    break
            if not overflow:
                text = _decode(b"".join(chunks), ctype)
                if mime == "text/css":
                    text = rw.css(text)
                else:
                    text = rw.html(text, runtime.shim_js, doc_url)
                out.pop("ETag", None)
                out["Content-Type"] = f"{mime}; charset=utf-8"
                return web.Response(body=text.encode("utf-8"), status=resp.status, headers=out)

        # Streaming passthrough.
        if "Content-Encoding" not in resp.headers and "Content-Length" in resp.headers:
            out["Content-Length"] = resp.headers["Content-Length"]
        stream = web.StreamResponse(status=resp.status, headers=out)
        await stream.prepare(request)
        if request.method != "HEAD" and resp.status not in _BODYLESS:
            try:
                for chunk in chunks:
                    await stream.write(chunk)
                async for chunk in resp.content.iter_chunked(65536):
                    await stream.write(chunk)
            except (ConnectionResetError, aiohttp.ClientError):
                pass
        try:
            await stream.write_eof()
        except (ConnectionResetError, aiohttp.ClientError):
            pass
        return stream

    # ------------------------------------------------------------------
    async def _proxy_ws(self, request, sess, url, scheme, authority, headers):
        protocols = [
            p.strip()
            for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
            if p.strip()
        ]
        try:
            upstream = await sess.http.ws_connect(
                url, protocols=protocols, headers=headers,
                origin=f"{scheme}://{authority}",
                autoclose=False, autoping=False, max_msg_size=0,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            return _page(502, "WebSocket failed", f"{authority}: {err}")

        server = web.WebSocketResponse(
            protocols=[upstream.protocol] if upstream.protocol else (),
            autoclose=False, autoping=False, max_msg_size=0,
        )
        await server.prepare(request)

        async def pump(src, dst):
            async for msg in src:
                if msg.type == WSMsgType.TEXT:
                    await dst.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await dst.send_bytes(msg.data)
                elif msg.type == WSMsgType.PING:
                    await dst.ping(msg.data)
                elif msg.type == WSMsgType.PONG:
                    await dst.pong(msg.data)
                else:
                    break

        tasks = [
            asyncio.create_task(pump(server, upstream)),
            asyncio.create_task(pump(upstream, server)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await upstream.close(code=_close_code(server.close_code))
                await server.close(code=_close_code(upstream.close_code))
            except Exception:  # noqa: BLE001 - best effort on teardown
                pass
        return server
