"""URL, HTML and CSS rewriting for the HA Browser proxy.

Proxied URL layout:  {prefix}/{scheme}/{host[:port]}/{path}?{query}
Only root-relative, protocol-relative and absolute URLs need rewriting;
path-relative URLs keep working because the path layout mirrors the origin.
Dynamic URLs created by page JavaScript are handled by shim.js.
"""
from __future__ import annotations

import json
import re
from urllib.parse import SplitResult, urljoin, urlsplit

_SKIP = re.compile(r"^(?:data|blob|javascript|about|mailto|tel|sms|file|chrome|view-source):", re.I)
_HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

URL_ATTRS = frozenset(
    {
        "href", "src", "action", "poster", "formaction", "background", "cite",
        "longdesc", "manifest", "data", "data-src", "data-href", "data-url",
        "xlink:href",
    }
)

_TAG_RE = re.compile(r"<[a-zA-Z][^\s/>]*(?:\"[^\"]*\"|'[^']*'|[^'\">])*>")
_ATTR_RE = re.compile(
    r"""(?<![\w:.\-])(?P<name>[a-zA-Z_:][\w:.\-]*)(?P<eq>\s*=\s*)"""
    r"""(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^\s"'=<>`]+))"""
)
_STYLE_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
_CSS_URL = re.compile(r"""url\(\s*(?P<q>["']?)(?P<u>[^"')]*?)(?P=q)\s*\)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(?P<q>["'])(?P<u>[^"']+)(?P=q)""", re.I)
_SRCSET = re.compile(r"(^|,)(\s*)([^\s,]+)")
_REFRESH = re.compile(r"^(\s*\d+\s*;\s*url\s*=\s*)(.+)$", re.I)
_BASE_TAG = re.compile(r"<base\b[^>]*>", re.I)
_BASE_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_CSP_META = re.compile(
    r"""<meta\b[^>]*http-equiv\s*=\s*["']?content-security-policy[^>]*>""", re.I
)
_HEAD_OPEN = re.compile(r"<head(?:\s[^>]*)?>", re.I)
_HTML_OPEN = re.compile(r"<html(?:\s[^>]*)?>", re.I)
_DOCTYPE = re.compile(r"^\s*<!doctype[^>]*>", re.I)


def to_proxy_url(prefix: str, parts: SplitResult) -> str:
    """Build the proxied path for an absolute http(s) URL."""
    netloc = parts.netloc.rpartition("@")[2]
    out = f"{prefix}/{parts.scheme}/{netloc}{parts.path or '/'}"
    if parts.query:
        out += "?" + parts.query
    if parts.fragment:
        out += "#" + parts.fragment
    return out


def build_shim_tag(shim_js: str, prefix: str, url: str, base: str) -> str:
    """Inline <script> carrying the client-side shim and its config."""
    cfg = json.dumps({"prefix": prefix, "url": url, "base": base}).replace("</", "<\\/")
    return f"<script>{shim_js}({cfg});</script>"


class Rewriter:
    """Rewrites content fetched from `base` so it works under `prefix`."""

    def __init__(self, prefix: str, base: str) -> None:
        self.prefix = prefix
        self.base = base
        self.force_abs = False

    # -- single URL ---------------------------------------------------
    def proxify(self, value: str) -> str:
        v = value.strip()
        if (
            not v
            or v.startswith("#")
            or "\\" in v  # escaped quotes etc. from JS strings: not a real URL
            or _SKIP.match(v)
            or v.startswith(self.prefix + "/")
        ):
            return value
        if self.force_abs or v.startswith("/") or _HAS_SCHEME.match(v):
            try:
                parts = urlsplit(urljoin(self.base, v))
            except ValueError:
                return value
            if parts.scheme in ("http", "https") and parts.hostname:
                return to_proxy_url(self.prefix, parts)
        return value

    # -- CSS ----------------------------------------------------------
    def css(self, text: str) -> str:
        text = _CSS_URL.sub(
            lambda m: f"url({m['q']}{self.proxify(m['u'])}{m['q']})", text
        )
        return _CSS_IMPORT.sub(
            lambda m: f"@import {m['q']}{self.proxify(m['u'])}{m['q']}", text
        )

    def srcset(self, value: str) -> str:
        return _SRCSET.sub(
            lambda m: f"{m[1]}{m[2]}{self.proxify(m[3])}", value
        )

    # -- HTML ---------------------------------------------------------
    def _attr(self, m: re.Match) -> str:
        name = m["name"]
        lname = name.lower()
        if m["dq"] is not None:
            q, val = '"', m["dq"]
        elif m["sq"] is not None:
            q, val = "'", m["sq"]
        else:
            q, val = '"', m["uq"]
        if lname in URL_ATTRS:
            new = self.proxify(val)
        elif lname in ("srcset", "imagesrcset"):
            new = self.srcset(val)
        elif lname == "style":
            new = self.css(val)
        elif lname == "integrity":
            return ""  # rewritten resources would fail SRI checks
        elif lname == "content" and _REFRESH.match(val):
            new = _REFRESH.sub(lambda r: r[1] + self.proxify(r[2]), val)
        else:
            return m.group(0)
        if new == val:
            return m.group(0)  # untouched: preserve original quoting byte-for-byte
        return f"{name}{m['eq']}{q}{new}{q}"

    def html(self, text: str, shim_js: str, doc_url: str) -> str:
        text = text.lstrip("\ufeff")
        m = _BASE_TAG.search(text)
        if m:
            h = _BASE_HREF.search(m.group(0))
            if h:
                href = h[1] if h[1] is not None else (h[2] if h[2] is not None else h[3])
                self.base = urljoin(self.base, href)
                self.force_abs = True
            text = text[: m.start()] + text[m.end():]
        text = _CSP_META.sub("", text)
        text = _TAG_RE.sub(lambda t: _ATTR_RE.sub(self._attr, t.group(0)), text)
        text = _STYLE_RE.sub(lambda s: s[1] + self.css(s[2]) + s[3], text)

        shim = build_shim_tag(shim_js, self.prefix, doc_url, self.base)
        m = _HEAD_OPEN.search(text) or _HTML_OPEN.search(text)
        if m:
            pos = m.end()
        else:
            d = _DOCTYPE.match(text)
            pos = d.end() if d else 0
        return text[:pos] + shim + text[pos:]
