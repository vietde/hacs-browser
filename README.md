# HA Browser

A light web browser that lives inside Home Assistant, with a built-in
adblocker. A HACS **integration**, not an add-on — it runs in-process
in Home Assistant Core, so there is no second container and no extra
CPU/memory cost the way an add-on has.

## What it is

- A **Browser** item in the sidebar (admin only) with an address bar,
  back/forward/reload/home, and a live **"🚫 N blocked"** counter.
- Type an address or a search query; search goes to DuckDuckGo.
- Pages load through a **server-side proxy**: Home Assistant fetches
  the page and serves it from its own address. That is what makes
  framing work at all — most sites refuse to load inside an iframe,
  and the proxy strips those headers and rewrites every link, image,
  script, stylesheet and form so the next click stays in the browser.
- **Internal resources work too.** Type a LAN address — your router,
  NAS, Node-RED, a dashboard — and the browser opens it the same
  way, through the same proxy. Internal apps get a websocket relay
  and their own API calls routed back through the proxy, so live
  dashboards keep working. This is the ingress-style use
  (hass_ingress is the model): browse your own network without
  installing a heavy Firefox/Chrome add-on.
- The **built-in adblocker** works in two layers: the proxy removes
  resources from known ad/tracker domains and whole ad slots from the
  page, and an injected script blocks ad/tracker requests the page
  makes on its own (fetch, XHR, dynamically added tags).

## What it is not (honest limits)

- **Light browser, not a desktop browser.** JavaScript-heavy apps,
  sites that need a login and video streaming mostly do not work well
  through a simple proxy. It is for reading pages, news and docs —
  and for checking your own internal services, which get a websocket
  relay and proxied API calls so live dashboards keep working.
- **No logins through the proxy.** Do not sign in to anything
  important in it — the browser has its own cookie jar, but login
  flows through a proxy are unreliable and the session is shared
  with every admin who opens the panel. Home Assistant's own
  `Authorization` header is never forwarded to a target.
- **Forms that POST do not work through the proxy.** Signed URLs
  only authenticate GET requests, so a form submit answers 401.
  Search boxes that submit with GET work; login and checkout forms
  do not.
- **Not uBlock-level.** The blocklist is a curated list of ad and
  tracker domains plus ad-slot removal — good coverage, but not the
  full EasyList cosmetic filtering of a real adblocker.
- The proxy never fetches `localhost`, loopback, link-local or
  reserved addresses, and never fetches anything but `http(s)` /
  `ws(s)`. Private LAN addresses and `.local` hosts ARE allowed —
  that is the internal-browsing feature, and the proxy is admin-only
  behind signed URLs.
- Internal sites with self-signed certificates need **Verify HTTPS
  certificates** turned off in the integration options (default on).
- Some sites detect proxies or bots and refuse to load; pages over
  10 MB are refused. Pages that navigate by setting
  `location.href` in JavaScript (instead of a link or form) can
  break, and `EventSource` streams are not proxied.

## Install

1. In HACS, add this repo as a custom repository (type: integration).
2. Install **HA Browser**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **HA Browser**.
   Pick a homepage (default DuckDuckGo) and whether the adblocker is
   on (default on).
4. The **Browser** item appears in the sidebar.

## Files

- `custom_components/ha_browser/` — the integration
  (`__init__.py`, `config_flow.py`, `proxy.py` proxy + adblock core,
  `views.py` HTTP + proxy, `frontend/` browser page)
- `hacs.json` — HACS repository metadata

## Status

v0.2.0 — internal resources, ingress-style (the hass_ingress
model). The proxy now allows private LAN addresses and `.local`
hosts, and internal apps get a websocket relay plus their API calls
(fetch/XHR) routed back through the proxy with panel-signed URLs, so
live dashboards (router, Node-RED, Frigate, monitoring) keep working
without a heavy browser add-on. Also adds a **Verify HTTPS
certificates** option for self-signed internal sites, and fixes a
v0.1.1 bug the offline tests caught: `proxy.strip_headers` was
missing, so every proxied page would have crashed. Live verification
on a real Home Assistant install is still pending.

v0.1.1 — fixes the 401 the sidebar showed in v0.1.0. Home Assistant
only accepts a Bearer header or a signed `?authSig=` query parameter
(no cookie auth), and a plain iframe navigation sends neither — so
v0.1.0's iframe panel never loaded the browser page at all. The
panel is now a custom element (like HA Terminal) that signs every
proxy URL with the `auth/sign_path` websocket command, and the proxy
signs every URL it rewrites inside a page the same way, so images,
stylesheets and scripts load too. The browser chrome is now a real
omnibox-style address bar with a loading bar.
