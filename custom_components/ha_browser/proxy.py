"""HA Browser — proxy + ad-blocking core.

Pure standard library, no Home Assistant imports, so it can be unit
tested offline. The HA views (views.py) only fetch bytes and call
these helpers.

How the browser works
---------------------
Most sites refuse to load inside an iframe (X-Frame-Options / CSP
frame-ancestors), so the integration fetches pages server-side and
serves them from Home Assistant's own origin. While doing that it:

* strips the headers that forbid framing (X-Frame-Options, CSP),
* rewrites every link, image, script, stylesheet, form and media URL
  so the next click also goes through the proxy,
* removes resources served from known ad/tracker domains,
* removes whole page elements whose id/class looks like an ad slot,
* injects a small script that blocks ad/tracker requests the page
  makes on its own (fetch/XHR/dynamic tags) and reports how many
  ads/trackers were blocked back to the browser bar.

Limits, stated honestly (see README): JavaScript-heavy apps, sites
that need a login, video streaming and websockets mostly do not work
well through a simple proxy. This is a *light* browser for reading
pages, news and docs — not a replacement for a desktop browser.
"""

from __future__ import annotations

import ipaddress
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlparse

PROXY_PATH = "/api/ha_browser/proxy"
WS_RELAY_PATH = "/api/ha_browser/ws"
MAX_BODY_BYTES = 10 * 1024 * 1024  # never proxy more than 10 MB at once

# ---------------------------------------------------------------------------
# Ad / tracker blocklist (curated, domain-suffix matching)
# ---------------------------------------------------------------------------
# Well-known ad networks, trackers and analytics. Subdomains match too
# (ads.example.com is blocked when example.com is listed). The list is
# deliberately conservative: only domains whose whole business is ads,
# tracking or analytics, so ordinary content CDNs are never blocked.
BLOCKED_DOMAINS = frozenset(
    {
        # Google ads / analytics
        "doubleclick.net",
        "googlesyndication.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googletagservices.com",
        "googleadservices.com",
        "analytics.google.com",
        "ads.google.com",
        "pagead2.googlesyndication.com",
        "adservice.google.com",
        "adservice.google.co.jp",
        # Meta
        "facebook.net",
        "connect.facebook.net",
        "graph.facebook.com",
        # Amazon ads
        "amazon-adsystem.com",
        "aax.amazon-adsystem.com",
        # Microsoft / LinkedIn ads
        "ads.linkedin.com",
        "bat.bing.com",
        "clarity.ms",
        # Ad networks
        "taboola.com",
        "outbrain.com",
        "criteo.com",
        "criteo.net",
        "adnxs.com",
        "adsrvr.org",
        "rubiconproject.com",
        "pubmatic.com",
        "openx.net",
        "moatads.com",
        "adsafeprotected.com",
        "scorecardresearch.com",
        "quantserve.com",
        "quantcast.com",
        "advertising.com",
        "adtechus.com",
        "2mdn.net",
        "casalemedia.com",
        "bidswitch.net",
        "lijit.com",
        "mathtag.com",
        "bluekai.com",
        "krxd.net",
        "demdex.net",
        "everesttech.net",
        "rlcdn.com",
        "agkn.com",
        "addthis.com",
        "addthisedge.com",
        "sharethis.com",
        "taboolaapis.com",
        "zedo.com",
        "tribalfusion.com",
        "adform.net",
        "adroll.com",
        "simpli.fi",
        "spotxchange.com",
        "spotx.tv",
        "teads.tv",
        "smartadserver.com",
        "admixer.net",
        "admatic.com.tr",
        # Trackers / analytics / session replay
        "hotjar.com",
        "hotjar.io",
        "mixpanel.com",
        "segment.com",
        "segment.io",
        "fullstory.com",
        "logrocket.com",
        "amplitude.com",
        "heap.io",
        "mouseflow.com",
        "crazyegg.com",
        "luckyorange.com",
        "inspectlet.com",
        "smartlook.com",
        "yandex.ru",  # yandex metrica
        "mc.yandex.ru",
        "metrika.yandex.ru",
        "newrelic.com",
        "nr-data.net",
        "sentry.io",
        "bugsnag.com",
        "intercom.io",
        "intercomcdn.com",
        "drift.com",
        "zendesk.com",
        "zopim.com",
        "optimizely.com",
        "vwo.com",
        "abtasty.com",
        "clicktale.com",
        "clicktale.net",
        "contentsquare.net",
        "piano.io",
        "tinypass.com",
        "chartbeat.com",
        "chartbeat.net",
        "parsely.com",
        "pingdom.net",
        "alexametrics.com",
        "imrworldwide.com",
        "nielsen.com",
        "moat.com",
        "doubleverify.com",
        "iasds01.com",
        "eyeota.net",
        "lotame.com",
        "exelator.com",
        "turn.com",
        "adobedtm.com",
        "omtrdc.net",
        "everestads.net",
        "fwmrm.net",
        "freewheel.tv",
        "freewheel.com",
        "innovid.com",
        "flashtalking.com",
        "sizmek.com",
        "mediamath.com",
        "thetradedesk.com",
        "ads-twitter.com",
        "analytics.twitter.com",
        "ads.pinterest.com",
        "ads.tiktok.com",
        "analytics.tiktok.com",
        "snap.licdn.com",
        "px.ads.linkedin.com",
        "trk.pinterest.com",
        "ct.pinterest.com",
        "analytics.yahoo.com",
        "ads.yahoo.com",
        "advertising.yahoo.com",
        "amplify.outbrain.com",
        "odb.outbrain.com",
        "widgets.outbrain.com",
        "recommendations.taboola.com",
    }
)

# Domains blocked only when the URL path looks ad-related.
PATH_BLOCKED_DOMAINS = {
    "gstatic.com": re.compile(r"/(ads|pagead|doubleclick)", re.I),
    "google.com": re.compile(r"/(ads|pagead|doubleclick)", re.I),
    "googleapis.com": re.compile(r"/(ads|pagead)", re.I),
    "facebook.com": re.compile(r"/(tr|plugins/like)", re.I),
    "youtube.com": re.compile(r"/(api/stats/ads|pagead)", re.I),
}

# Elements whose id or class looks like an ad slot are removed whole.
AD_ELEMENT_RE = re.compile(
    r"(^|[\s_\-])(ad|ads|advert|advertisement|sponsored|taboola|outbrain"
    r"|popunder|popup-ad|sticky-ad|banner-ad)([\s_\-]|$)",
    re.I,
)

# Response headers that must never reach the browser: they forbid
# framing (the whole point of the proxy) or pin the page to its origin.
STRIP_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "x-content-security-policy",
    "x-webkit-csp",
    "timing-allow-origin",
}

# HTML void elements (no end tag, no children).
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# Attributes that carry a URL, per tag.
URL_ATTRS = {
    "a": ("href",),
    "link": ("href",),
    "img": ("src", "srcset"),
    "script": ("src",),
    "iframe": ("src",),
    "form": ("action",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "source": ("src", "srcset"),
    "embed": ("src",),
    "object": ("data",),
    "track": ("src",),
    "blockquote": ("cite",),
    "q": ("cite",),
    "ins": ("cite",),
    "del": ("cite",),
}


def host_of(url: str) -> str | None:
    """Lowercased hostname of a URL, or None if it has none."""
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def _domain_match(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_blocked_url(url: str) -> bool:
    """True when a URL points at a known ad/tracker domain."""
    host = host_of(url)
    if not host:
        return False
    for domain in BLOCKED_DOMAINS:
        if _domain_match(host, domain):
            return True
    pattern = PATH_BLOCKED_DOMAINS.get(host) or next(
        (p for d, p in PATH_BLOCKED_DOMAINS.items() if _domain_match(host, d)),
        None,
    )
    if pattern is not None:
        try:
            path = urlparse(url).path or ""
        except ValueError:
            path = ""
        if pattern.search(path):
            return True
    return False


def is_forbidden_target(url: str) -> bool:
    """True for URLs the proxy must never fetch (SSRF guard).

    Blocks non-http(s) schemes, localhost, loopback, link-local and
    reserved addresses. Private LAN addresses (RFC1918) and `.local`
    hosts are ALLOWED since v0.2.0: browsing internal resources
    (router, NAS, Node-RED, dashboards) is the point of the browser,
    and the proxy is admin-only behind signed HA URLs — the same
    access control hass_ingress relies on. The terminal integration
    already gives an admin a shell with full LAN access, so blocking
    the LAN here would add no safety for this household.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        return True
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_link_local or ip.is_reserved


def is_internal_target(url: str) -> bool:
    """True when a URL points at a LAN service (private IP / .local)."""
    host = host_of(url) or ""
    if host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def proxy_url_for(target: str, base_url: str | None = None, signer=None) -> str | None:
    """Wrap a page-relative or absolute URL as a proxy URL.

    Returns None when the URL is unusable (javascript:, mailto:,
    fragment-only) or points at a blocked ad/tracker domain.

    `signer`, when given, is a callable taking a proxy URL and
    returning the same URL with a valid HA auth signature appended
    (or None on failure). Signed URLs are required: the browser
    panel and the proxied pages load through plain iframe
    navigations, which carry no Authorization header, so an
    unsigned proxy URL answers 401 (see views.py). A signer that
    fails or returns None falls back to the unsigned URL.
    """
    if not target:
        return None
    target = target.strip()
    if not target or target.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    absolute = urljoin(base_url, target) if base_url else target
    if is_blocked_url(absolute):
        return None
    url = f"{PROXY_PATH}?url={quote(absolute, safe='')}"
    if signer is not None:
        try:
            signed = signer(url)
        except Exception:
            signed = None
        if signed:
            return signed
    return url


def proxy_url_for_srcset(srcset: str, base_url: str | None, signer=None) -> str:
    """Rewrite every URL inside a srcset attribute through the proxy."""
    parts = []
    for chunk in srcset.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        bits = chunk.split()
        proxied = proxy_url_for(bits[0], base_url, signer)
        if proxied is None:
            continue
        parts.append(proxied + (" " + " ".join(bits[1:]) if len(bits) > 1 else ""))
    return ", ".join(parts)


def ws_relay_url_for(target: str, base_url: str | None = None, signer=None) -> str | None:
    """Wrap a ws(s):// URL as a websocket-relay URL (unsigned unless signed).

    Returns None for unusable URLs. The relay itself is a GET with an
    Upgrade header, so a signed relay URL authenticates like any other
    signed proxy URL — but the shim cannot sign synchronously, so it
    asks the panel to sign (see _shim_script) instead of using this
    for live pages. This helper is for tests and for any server-side
    use; the live path goes through the panel.
    """
    if not target:
        return None
    target = target.strip()
    if not target:
        return None
    if "://" in target:
        absolute = target
    else:
        # Relative WS path: resolve against the base page with its
        # scheme converted http->ws / https->wss.
        try:
            parsed_base = urlparse(base_url or "")
        except ValueError:
            return None
        ws_scheme = {"http": "ws", "https": "wss"}.get(parsed_base.scheme)
        if not ws_scheme:
            return None
        absolute = urljoin(parsed_base._replace(scheme=ws_scheme).geturl(), target)
    try:
        scheme = urlparse(absolute).scheme
    except ValueError:
        return None
    if scheme not in ("ws", "wss"):
        return None
    if is_forbidden_target(absolute):
        return None
    url = f"{WS_RELAY_PATH}?url={quote(absolute, safe='')}"
    if signer is not None:
        try:
            signed = signer(url)
        except Exception:
            signed = None
        if signed:
            return signed
    return url


def extract_ws_target(raw: str | None) -> str | None:
    """Decode and validate the ?url= query value of a relay request."""
    if not raw:
        return None
    try:
        target = unquote(raw)
    except Exception:
        return None
    try:
        scheme = urlparse(target).scheme
    except ValueError:
        return None
    if scheme not in ("ws", "wss"):
        return None
    if is_forbidden_target(target):
        return None
    return target
    """Drop framing/CSP headers; keep everything else."""
    return {k: v for k, v in headers.items() if k.lower() not in STRIP_HEADERS}


_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", re.I)


def strip_headers(headers) -> dict:
    """Drop framing/CSP headers; keep everything else."""
    return {k: v for k, v in headers.items() if k.lower() not in STRIP_HEADERS}


def rewrite_css(css: str, base_url: str, signer=None) -> tuple[str, int]:
    """Rewrite url(...) references in a stylesheet through the proxy.

    Returns (rewritten_css, blocked_count).
    """
    blocked = 0

    def _repl(match: re.Match) -> str:
        nonlocal blocked
        quote_char, raw = match.group(1), match.group(2)
        proxied = proxy_url_for(raw, base_url, signer)
        if proxied is None:
            blocked += 1
            return "url(about:blank)"
        return f"url({quote_char}{proxied}{quote_char})"

    return _CSS_URL_RE.sub(_repl, css), blocked


def _shim_script(blocked_domains: frozenset, server_blocked: int, base_url: str) -> str:
    """Client-side shim, injected into every proxied HTML page.

    Three jobs, in order of importance for internal apps:
    1. Route the page's own requests through the proxy. The page is
       served from Home Assistant's origin, so a relative fetch/XHR/
       WebSocket URL would otherwise hit Home Assistant itself
       instead of the site it came from. Every such request is
       resolved against the page's ORIGINAL base URL and routed
       through the proxy — and because proxy URLs must be signed,
       the shim asks the sidebar panel to sign them (the panel owns
       the HA connection; the page cannot sign for itself). The panel
       answers with ha-browser-signed / ha-browser-sign-error.
    2. Block ads/trackers the page loads on its own (fetch/XHR/
       dynamic tags) and report the running blocked count to the
       browser bar.
    3. Make XMLHttpRequest work through the proxy at all: the shim
       replaces it with a fetch-backed implementation, because a
       native XHR cannot wait for a signature. Async only — sync XHR
       is deprecated and throws.
    """
    domains_json = json.dumps(sorted(blocked_domains))
    base_json = json.dumps(base_url)
    proxy_json = json.dumps(PROXY_PATH)
    relay_json = json.dumps(WS_RELAY_PATH)
    return (
        "<script>(function(){"
        "var BLOCKED=" + domains_json + ";"
        "var BASE=" + base_json + ";"
        "var PROXY=" + proxy_json + ";"
        "var WSRELAY=" + relay_json + ";"
        f"var count={server_blocked};"
        # --- adblock helpers
        "function blocked(u){try{var h=new URL(u,location.href).hostname.toLowerCase();"
        "for(var i=0;i<BLOCKED.length;i++){if(h===BLOCKED[i]||h.endsWith('.'+BLOCKED[i]))return true;}"
        "}catch(e){}return false;}"
        "function report(){try{window.parent.postMessage({type:'ha-browser-blocked',count:count},location.origin);}catch(e){}}"
        # --- signing: ask the sidebar panel (it owns the HA connection)
        "var _seq=0;var _pending={};"
        "function sign(path){return new Promise(function(res,rej){"
        "if(window.parent===window){rej(new Error('no panel'));return;}"
        "var id=++_seq;var done=false;"
        "var timer=setTimeout(function(){if(!done){done=true;delete _pending[id];rej(new Error('sign timeout'));}},5000);"
        "_pending[id]={res:function(v){if(!done){done=true;clearTimeout(timer);res(v);}},"
        "rej:function(e){if(!done){done=true;clearTimeout(timer);rej(e);}}};"
        "try{window.parent.postMessage({type:'ha-browser-sign',id:id,path:path},location.origin);}"
        "catch(e){delete _pending[id];clearTimeout(timer);rej(e);}});}"
        "window.addEventListener('message',function(ev){if(ev.origin!==location.origin)return;"
        "var d=ev.data||{};"
        "if(d.type==='ha-browser-signed'&&_pending[d.id])_pending[d.id].res(d.signed);"
        "else if(d.type==='ha-browser-sign-error'&&_pending[d.id])_pending[d.id].rej(new Error('sign failed'));});"
        # --- URL routing: proxy/HA URLs pass through, the rest resolve
        # against the page's ORIGINAL base and go through the proxy.
        "function isProxyPath(u){return typeof u==='string'&&(u.indexOf(PROXY)===0||u.indexOf(WSRELAY)===0);}"
        "function resolve(u){if(isProxyPath(u))return u;"
        "try{return new URL(u,BASE).href;}catch(e){return null;}}"
        "function sameOrigin(u){try{return new URL(u,location.href).origin===location.origin;}catch(e){return false;}}"
        "function proxyFor(abs){return PROXY+'?url='+encodeURIComponent(abs);}"
        # --- fetch
        "var ofetch=window.fetch.bind(window);"
        "window.fetch=function(input,init){"
        "var raw=typeof input==='string'?input:(input&&input.url);"
        "var abs=resolve(raw);if(!abs)return ofetch.apply(null,arguments);"
        "if(isProxyPath(abs)||sameOrigin(abs))return ofetch.apply(null,arguments);"
        "if(blocked(abs)){count++;report();return Promise.reject(new Error('blocked by HA Browser'));}"
        "return sign(proxyFor(abs)).then(function(signed){"
        "if(typeof input==='string')return ofetch(signed,init);"
        "try{return ofetch(new Request(signed,input));}catch(e){return ofetch(signed,init);}"
        "}).catch(function(){return ofetch.apply(null,arguments);});};"
        # --- XMLHttpRequest, fetch-backed so it can wait for a signature
        "var NativeXHR=window.XMLHttpRequest;"
        "function FakeXHR(){this._headers={};this.readyState=0;this.status=0;this.statusText='';"
        "this.responseText='';this.response='';this.responseType='';this._aborted=false;"
        "this.onreadystatechange=null;this.onload=null;this.onerror=null;this._listeners={};}"
        "FakeXHR.prototype.open=function(method,url,async){"
        "if(async===false)throw new Error('Synchronous XHR is not supported by HA Browser');"
        "this._method=method;this._url=url;this.readyState=1;this._fire('readystatechange');};"
        "FakeXHR.prototype.setRequestHeader=function(k,v){this._headers[k]=v;};"
        "FakeXHR.prototype.getResponseHeader=function(){return null;};"
        "FakeXHR.prototype.getAllResponseHeaders=function(){return '';};"
        "FakeXHR.prototype.addEventListener=function(t,fn){(this._listeners[t]=this._listeners[t]||[]).push(fn);};"
        "FakeXHR.prototype.removeEventListener=function(t,fn){var a=this._listeners[t]||[];var i=a.indexOf(fn);if(i>=0)a.splice(i,1);};"
        "FakeXHR.prototype._fire=function(t,ev){var h=this['on'+t];"
        "if(typeof h==='function'){try{h.call(this,ev||{});}catch(e){}}"
        "var self=this;(this._listeners[t]||[]).forEach(function(fn){try{fn.call(self,ev||{});}catch(e){}});};"
        "FakeXHR.prototype.abort=function(){this._aborted=true;this.readyState=0;};"
        "FakeXHR.prototype.send=function(body){var self=this;"
        "function fail(){if(self._aborted)return;self.readyState=4;self.status=0;"
        "self._fire('readystatechange');self._fire('error');}"
        "var abs=resolve(self._url);if(!abs){fail();return;}"
        "if(isProxyPath(abs)||sameOrigin(abs)){doFetch(abs);return;}"
        "if(blocked(abs)){count++;report();fail();return;}"
        "sign(proxyFor(abs)).then(doFetch,function(){doFetch(abs);});"
        "function doFetch(url){if(self._aborted)return;"
        "var headers=new Headers();for(var k in self._headers)headers.append(k,self._headers[k]);"
        "ofetch(url,{method:self._method,headers:headers,body:(body===undefined?null:body)}).then(function(resp){"
        "if(self._aborted)return;self.status=resp.status;self.statusText=resp.statusText;"
        "self.readyState=2;self._fire('readystatechange');"
        "if(self.responseType==='json')return resp.text().then(function(t){try{return JSON.parse(t);}catch(e){return t;}});"
        "return resp.text();}).then(function(data){if(self._aborted)return;"
        "if(typeof data==='string'){self.responseText=data;self.response=data;}"
        "else{self.response=data;self.responseText='';}"
        "self.readyState=4;self._fire('readystatechange');self._fire('load');}).catch(fail);}};"
        "window.XMLHttpRequest=FakeXHR;"
        # --- WebSocket: deferred until the panel signs the relay URL
        "var NativeWS=window.WebSocket;"
        "function DeferredWS(url,protocols){var self=this;"
        "this.url=url;this.protocol='';this._binaryType='blob';this.readyState=0;this.bufferedAmount=0;"
        "this.onopen=null;this.onmessage=null;this.onclose=null;this.onerror=null;"
        "this._listeners={};this._queue=[];this._real=null;this._closed=false;"
        "var abs=resolve(url);"
        "function fail(){self.readyState=3;self._fire('error',{});self._fire('close',{code:1006,wasClean:false});}"
        "if(!abs){setTimeout(fail,0);return;}"
        "if(blocked(abs)){count++;report();setTimeout(fail,0);return;}"
        "if(isProxyPath(abs)||sameOrigin(abs)){connect(abs);return;}"
        "sign(WSRELAY+'?url='+encodeURIComponent(abs)).then(connect,function(){fail();});"
        "function connect(target){if(self._closed)return;var real;"
        "try{real=new NativeWS(target,protocols);}catch(e){fail();return;}"
        "self._real=real;try{real.binaryType=self.binaryType;}catch(e){}"
        "real.onopen=function(ev){self.readyState=1;try{self.protocol=real.protocol||'';}catch(e){}"
        "self._fire('open',ev);var q=self._queue;self._queue=[];"
        "q.forEach(function(d){try{real.send(d);}catch(e){}});};"
        "real.onmessage=function(ev){self._fire('message',ev);};"
        "real.onclose=function(ev){self.readyState=3;self._fire('close',ev);};"
        "real.onerror=function(ev){self._fire('error',ev);};}}"
        "DeferredWS.prototype._fire=function(t,ev){var h=this['on'+t];"
        "if(typeof h==='function'){try{h.call(this,ev||{});}catch(e){}}"
        "var self=this;(this._listeners[t]||[]).forEach(function(fn){try{fn.call(self,ev||{});}catch(e){}});};"
        "DeferredWS.prototype.send=function(data){"
        "if(this.readyState===3)throw new Error('WebSocket is closed');"
        "if(this._real&&this.readyState===1){this._real.send(data);return;}"
        "this._queue.push(data);};"
        "DeferredWS.prototype.close=function(code,reason){this._closed=true;"
        "if(this._real){try{this._real.close(code,reason);}catch(e){}}"
        "else{this.readyState=3;var self=this;"
        "setTimeout(function(){self._fire('close',{code:code||1000,wasClean:true});},0);}};"
        "DeferredWS.prototype.addEventListener=function(t,fn){(this._listeners[t]=this._listeners[t]||[]).push(fn);};"
        "DeferredWS.prototype.removeEventListener=function(t,fn){var a=this._listeners[t]||[];var i=a.indexOf(fn);if(i>=0)a.splice(i,1);};"
        "Object.defineProperty(DeferredWS.prototype,'binaryType',{"
        "get:function(){return this._binaryType||'blob';},"
        "set:function(v){this._binaryType=v;if(this._real){try{this._real.binaryType=v;}catch(e){}}},"
        "configurable:true});"
        "DeferredWS.CONNECTING=0;DeferredWS.OPEN=1;DeferredWS.CLOSING=2;DeferredWS.CLOSED=3;"
        "DeferredWS.prototype.CONNECTING=0;DeferredWS.prototype.OPEN=1;"
        "DeferredWS.prototype.CLOSING=2;DeferredWS.prototype.CLOSED=3;"
        "window.WebSocket=DeferredWS;"
        # --- dynamic tags: drop blocked ones as they appear
        "var mo=new MutationObserver(function(){"
        "document.querySelectorAll('img,script,iframe,link').forEach(function(el){"
        "var s=el.src||el.href||'';if(s&&blocked(s)){count++;el.remove();}});});"
        "mo.observe(document.documentElement,{childList:true,subtree:true});"
        "report();"
        "})();</script>"
    )


class _Rewriter(HTMLParser):
    """Rewrite a page's URLs through the proxy and strip ads."""

    def __init__(self, base_url: str, signer=None):
        super().__init__(convert_charrefs=False)
        self.base_url = base_url
        self.signer = signer
        self.out: list[str] = []
        self.blocked = 0
        self._skip_depth = 0  # >0 while inside a removed element

    def _sign(self, proxy_url: str) -> str:
        """Append an HA auth signature to a proxy URL, or return it as-is."""
        if self.signer is None:
            return proxy_url
        try:
            signed = self.signer(proxy_url)
        except Exception:
            signed = None
        return signed or proxy_url

    # -- helpers ------------------------------------------------------
    def _is_ad_element(self, attrs: dict) -> bool:
        for key in ("id", "class"):
            value = attrs.get(key, "")
            if value and AD_ELEMENT_RE.search(value):
                return True
        return False

    def _rewrite_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]] | None:
        out: list[tuple[str, str | None]] = []
        for name, value in attrs:
            if value is None:
                out.append((name, None))
                continue
            if name in ("href", "src", "action", "poster", "data", "cite"):
                proxied = proxy_url_for(value, self.base_url, self.signer)
                if proxied is None:
                    if tag in ("script", "iframe", "img", "link", "source", "video", "audio", "embed", "track", "object"):
                        # Blocked resource: drop the whole tag. The
                        # caller marks the tag for skipping.
                        return None
                    # A link/form pointing at a blocked host: still wrap
                    # it in a proxy URL so the click goes through the
                    # proxy (which answers the blocked page) instead of
                    # navigating the iframe straight to the ad site.
                    absolute = urljoin(self.base_url, value)
                    if is_blocked_url(absolute):
                        out.append((name, self._sign(
                            f"{PROXY_PATH}?url={quote(absolute, safe='')}")))
                    else:
                        # javascript:/mailto:/#fragment — leave alone.
                        out.append((name, value))
                else:
                    out.append((name, proxied))
            elif name == "srcset":
                rewritten = proxy_url_for_srcset(value, self.base_url, self.signer)
                out.append((name, rewritten))
            else:
                out.append((name, value))
        return out

    # -- parser callbacks ---------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            if tag not in VOID_ELEMENTS:
                self._skip_depth += 1
            return
        attrs_d = dict(attrs)
        if tag == "base":
            return  # we rewrite every URL ourselves; a <base> would break that
        if self._is_ad_element(attrs_d):
            self.blocked += 1
            if tag not in VOID_ELEMENTS:
                self._skip_depth = 1
            return
        rewritten = self._rewrite_attrs(tag, attrs)
        if rewritten is None:
            self.blocked += 1
            if tag not in VOID_ELEMENTS:
                self._skip_depth = 1
            return
        self.out.append(self._render_tag(tag, rewritten, close=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            return
        attrs_d = dict(attrs)
        if tag == "base":
            return
        if self._is_ad_element(attrs_d):
            self.blocked += 1
            return
        rewritten = self._rewrite_attrs(tag, attrs)
        if rewritten is None:
            self.blocked += 1
            return
        self.out.append(self._render_tag(tag, rewritten, close=True))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.out.append(data)

    def handle_comment(self, data: str) -> None:
        if self._skip_depth == 0:
            self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if self._skip_depth == 0:
            self.out.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        if self._skip_depth == 0:
            self.out.append(f"<?{data}>")

    @staticmethod
    def _render_tag(tag: str, attrs: list[tuple[str, str | None]], close: bool) -> str:
        text = "".join(
            f' {name}="{value}"' if value is not None else f" {name}"
            for name, value in attrs
        )
        if tag in VOID_ELEMENTS:
            return f"<{tag}{text}>"
        return f"<{tag}{text}{' /' if close else ''}>"


def rewrite_html(html: str, base_url: str, signer=None) -> tuple[str, int]:
    """Rewrite a page through the proxy and inject the adblock shim.

    Returns (rewritten_html, blocked_count) where blocked_count is the
    number of resources/elements removed server-side.
    """
    parser = _Rewriter(base_url, signer)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A malformed page must not kill the browser: fall back to the
        # original HTML with only the shim injected.
        return html + _shim_script(BLOCKED_DOMAINS, 0, base_url), 0
    body = "".join(parser.out)
    shim = _shim_script(BLOCKED_DOMAINS, parser.blocked, base_url)
    if "</body>" in body.lower():
        idx = body.lower().rindex("</body>")
        body = body[:idx] + shim + body[idx:]
    else:
        body = body + shim
    return body, parser.blocked


def extract_target(raw: str | None) -> str | None:
    """Decode and validate the ?url= query value of a proxy request."""
    if not raw:
        return None
    try:
        target = unquote(raw)
    except Exception:
        return None
    try:
        scheme = urlparse(target).scheme
    except ValueError:
        return None
    if scheme not in ("http", "https"):
        return None
    if is_forbidden_target(target):
        return None
    return target
