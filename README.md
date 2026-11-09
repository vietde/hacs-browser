# HA Browser

A tabbed **browser panel for Home Assistant**. Unlike `hass_ingress` (fixed, YAML-defined panels), HA Browser lets you type *any* URL at runtime. Pages are fetched by your HA server, so you can reach LAN devices (routers, NAS, cameras, add-on UIs) from anywhere you can reach HA, including via Nabu Casa.

- Address bar with search fallback, back/forward/reload, multiple tabs, bookmarks
- HTTP + WebSocket + streaming + Range support, redirects, per-session cookie jar
- HTML/CSS URL rewriting plus a client shim for `fetch`/XHR/WebSocket/`history`/popups
- UI configuration (Settings → Devices & services → HA Browser → Configure)

## Install
1. HACS → ⋮ → Custom repositories → add this repo as *Integration*.
2. Download, restart HA.
3. Settings → Devices & services → Add integration → **HA Browser**.

## Security model (read this)
Proxying arbitrary sites means untrusted code runs behind your HA URL. Mitigations:

| Risk | Mitigation |
|---|---|
| Site steals HA login tokens | Every response carries `Content-Security-Policy: sandbox`, and the panel iframe is sandboxed **without** `allow-same-origin`, so pages get an opaque origin and cannot touch HA's localStorage. This also holds if a proxy URL is opened directly in a browser tab. |
| Server-side request forgery | Admin-only by default. Loopback is blocked by default. Link-local/cloud-metadata ranges (169.254/16, fd00:ec2::/32), multicast and reserved ranges are **always** blocked. IPs are validated at connect time inside the connector (defeats DNS rebinding). Redirects are re-checked on every hop. |
| Token leakage | Session token lives in the URL path, is random (192-bit), expires after idle, is bound to the HA user, and is re-validated (active/admin) on every request. `Referrer-Policy: no-referrer`. |
| Over-broad access | Optional allow list / block list (hostnames with `*`, IPs, CIDRs). |

**Leave "Admin only" on** unless every HA user is trusted to make your server fetch internal URLs.

## Options
`allow_private`, `allow_public`, `allow_loopback`, allow/block lists (one per line; an allow-list entry overrides the category toggles but never the always-blocked ranges), `verify_ssl` (turn off for self-signed LAN devices), `home_url`, `search_url` (`%s` = query), `session_ttl`.

## Known limitations (v0.1)
- It's a rewriting proxy, not a real browser engine. Sites whose JS reads `location`/`document.domain` for routing, or assigns `location.href = "/x"`, can misbehave (the sandbox makes the real `location` unfixable from a shim).
- `localStorage`/`sessionStorage`/`document.cookie` in pages are in-memory per page load (server-side cookies from `Set-Cookie` *do* persist per session). No IndexedDB, service workers, or web workers.
- No HTTP basic-auth prompt (`WWW-Authenticate` isn't forwarded), no `user:pass@host` URLs.
- Back/forward buttons are always enabled; bookmarks/tabs are stored in the browser's localStorage.
- Upstream CSP / X-Frame-Options are intentionally stripped (needed for framing and the shim).

## Ideas for next versions
Persisted per-site storage via postMessage, basic-auth credential store, per-host header injection (like hass_ingress `headers`), download manager, history page, find-in-page.
