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

    Blocks non-http(s) schemes, localhost/loopback/link-local/private
    IP literals and .local hosts. The browser is for the public web.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    if parsed.scheme not in ("http", "https"):
        return True
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


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


def strip_headers(headers) -> dict:
    """Drop framing/CSP headers; keep everything else."""
    return {k: v for k, v in headers.items() if k.lower() not in STRIP_HEADERS}


_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", re.I)


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


def _shim_script(blocked_domains: frozenset, server_blocked: int) -> str:
    """Client-side adblock shim, injected into every proxied HTML page.

    Wraps fetch/XHR and watches for dynamically added tags so ads the
    page loads on its own (not through a rewritten tag) are blocked
    too. Reports the running blocked count to the browser bar.
    """
    domains_json = json.dumps(sorted(blocked_domains))
    return (
        "<script>(function(){"
        "var BLOCKED=" + domains_json + ";"
        f"var count={server_blocked};"
        "function blocked(u){try{var h=new URL(u,location.href).hostname.toLowerCase();"
        "for(var i=0;i<BLOCKED.length;i++){if(h===BLOCKED[i]||h.endsWith('.'+BLOCKED[i]))return true;}"
        "}catch(e){}return false;}"
        "function report(){try{window.parent.postMessage({type:'ha-browser-blocked',count:count},location.origin);}catch(e){}}"
        "var ofetch=window.fetch.bind(window);"
        "window.fetch=function(u){if(blocked(u)){count++;report();return Promise.reject(new Error('blocked by HA Browser'));}"
        "return ofetch.apply(null,arguments);};"
        "var oopen=XMLHttpRequest.prototype.open;"
        "XMLHttpRequest.prototype.open=function(m,u){if(blocked(u)){count++;report();throw new Error('blocked by HA Browser');}"
        "return oopen.apply(this,arguments);};"
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
        return html + _shim_script(BLOCKED_DOMAINS, 0), 0
    body = "".join(parser.out)
    shim = _shim_script(BLOCKED_DOMAINS, parser.blocked)
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
    if is_forbidden_target(target):
        return None
    return target
