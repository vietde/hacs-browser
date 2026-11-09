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
  through a simple proxy. The YouTube, Facebook and Google PAGES
  load through it (verified against the real sites), but video
  playback is partial: media over the 10 MB proxy cap fails, so long
  or high-bitrate videos may not play. It is for reading pages, news
  and docs — and for checking your own internal services, which get
  a websocket relay and proxied API calls.
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

v0.2.2 — fixes the panel hanging on "Loading…" forever. Ngot's
screenshot showed the browser chrome loaded but DuckDuckGo never
rendered, and the HA log had zero errors — the hang was silent.
Three real proxy bugs, all found by running the real proxy against
the real sites (a new live test does exactly that): (1) the proxy
forwarded the origin's `Transfer-Encoding: chunked` while aiohttp
set its own `Content-Length` for the rewritten body — a malformed
response browsers reject or wait on forever; (2) it forwarded
`Content-Encoding: gzip` with a body aiohttp had already decoded,
so the client tried to gunzip plain text and the page never loaded;
(3) it forwarded the browser's `Accept-Encoding`, which aiohttp
cannot decode when it did not negotiate it itself. All three
headers are now stripped. The panel also stops hanging silently:
signing times out after 10 s, a page load after 45 s, the config
fetch after 10 s — each shows a readable error with a retry button
— and the panel reports its stages to the HA log (a client beacon,
like HA Terminal's) so the next failure names its stage. The live
test proves Google, YouTube and Facebook pages load through the
proxy (DuckDuckGo's own block page loads too; it refuses this test
machine's datacenter IP, not the proxy). Live verification on Ngot's
HA still pending after HACS update + restart. Ngot's screenshot
showed "Unable to load custom panel from …/api/ha_browser/assets/
panel.js" and the URL answered 500. Root cause, from the HA log:
Home Assistant's view dispatcher calls the handler as
`handler(request, **request.match_info)`, so the `{filename}` path
parameter arrives as a keyword argument — and `AssetView.get` only
took `request`, so every load raised `TypeError: AssetView.get() got
an unexpected keyword argument 'filename'`. The offline test missed
it because it called the view directly instead of the way HA does.
The terminal's asset view already had the right signature; this one
did not. Live verification on Ngot's HA still pending after HACS
update + restart.

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
