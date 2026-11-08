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
- The **built-in adblocker** works in two layers: the proxy removes
  resources from known ad/tracker domains and whole ad slots from the
  page, and an injected script blocks ad/tracker requests the page
  makes on its own (fetch, XHR, dynamically added tags).

## What it is not (honest limits)

- **Light browser, not a desktop browser.** JavaScript-heavy apps,
  sites that need a login, video streaming and websockets mostly do
  not work well through a simple proxy. It is for reading pages,
  news and docs.
- **No logins through the proxy.** Do not sign in to anything
  important in it — the browser has its own cookie jar, but login
  flows through a proxy are unreliable and the session is shared
  with every admin who opens the panel.
- **Not uBlock-level.** The blocklist is a curated list of ad and
  tracker domains plus ad-slot removal — good coverage, but not the
  full EasyList cosmetic filtering of a real adblocker.
- The proxy never fetches `localhost`, private LAN addresses or
  `.local` hosts, and never fetches anything but `http(s)`.
- Some sites detect proxies or bots and refuse to load; pages over
  10 MB are refused.

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

v0.1.0 — first version. Proxy + adblock core tested offline;
live verification on a real Home Assistant install is still pending.
