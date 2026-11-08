/* HA Browser sidebar panel (custom element).
 *
 * Home Assistant sets the `hass` object on this element. The panel
 * renders the whole browser chrome itself — toolbar with back /
 * forward / reload / home, a large omnibox-style address bar, a
 * blocked-ads pill, a loading bar, and the page in an iframe below —
 * and signs every proxy URL with the `auth/sign_path` websocket
 * command before loading it.
 *
 * Why a custom element, and why signing (v0.1.1): v0.1.0 was a plain
 * iframe panel pointing at an authenticated page, and the sidebar
 * showed 401 instead of a browser. Home Assistant only accepts a
 * Bearer `Authorization` header or a signed `?authSig=` query
 * parameter — there is no cookie auth — and a plain iframe
 * navigation sends neither, nor does the frontend add one to the
 * panel URL. The `hass` object is the only token source that works
 * on the web, on mobile browsers and in the HA app (the app keeps
 * tokens in native storage, so localStorage is empty there — the
 * same lesson HA Terminal learned in v0.1.3). The signature is
 * per-URL and lasts an hour; sub-resources inside a proxied page
 * are signed server-side when the page is served.
 *
 * NOTE: a custom element constructor must not set attributes or add
 * children — setting this.style.* there throws NotSupportedError and
 * the element can never be created (that was the terminal's
 * v0.1.4/v0.1.5 black screen on every platform). Styles and DOM go
 * in connectedCallback.
 */

class HABrowserPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._homepage = "https://duckduckgo.com/html/";
    this._adblock = true;
    this._configLoaded = false;
    this._configLoading = false;
    this._navigated = false;
    this._currentTarget = null;
    this._frame = null;
    this._address = null;
    this._blockedPill = null;
    this._overlay = null;
    this._overlayText = null;
    this._overlayRetry = null;
    this._progress = null;
    this._onMessage = this._onMessage.bind(this);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._configLoaded) {
      this._loadConfig();
    }
  }

  connectedCallback() {
    this.style.display = "block";
    this.style.height = "100%";
    this.style.position = "relative";

    const style = document.createElement("style");
    style.textContent = [
      ".hab-wrap{display:flex;flex-direction:column;height:100%;",
      "background:var(--primary-background-color,#fafafa);",
      "color:var(--primary-text-color,#212121);}",
      ".hab-bar{display:flex;align-items:center;gap:6px;padding:8px 10px;",
      "background:var(--secondary-background-color,#fff);",
      "border-bottom:1px solid var(--divider-color,#e0e0e0);flex:none;}",
      ".hab-btn{flex:none;width:34px;height:34px;border:0;border-radius:50%;",
      "background:transparent;color:var(--primary-text-color,#212121);",
      "font-size:18px;line-height:1;cursor:pointer;}",
      ".hab-btn:hover{background:var(--divider-color,#e0e0e0);}",
      ".hab-btn:disabled{opacity:.35;cursor:default;background:transparent;}",
      ".hab-omni{flex:1;display:flex;align-items:center;gap:8px;height:38px;",
      "padding:0 6px 0 12px;border-radius:19px;",
      "border:1px solid var(--divider-color,#e0e0e0);",
      "background:var(--card-background-color,var(--primary-background-color,#fff));}",
      ".hab-omni:focus-within{border-color:var(--primary-color,#03a9f4);",
      "box-shadow:0 0 0 1px var(--primary-color,#03a9f4);}",
      ".hab-globe{flex:none;font-size:15px;opacity:.7;}",
      ".hab-address{flex:1;min-width:0;height:100%;border:0;outline:0;",
      "background:transparent;color:var(--primary-text-color,#212121);",
      "font-size:15px;font-family:inherit;}",
      ".hab-go{flex:none;width:28px;height:28px;border:0;border-radius:50%;",
      "background:var(--primary-color,#03a9f4);color:#fff;font-size:14px;",
      "line-height:1;cursor:pointer;}",
      ".hab-pill{flex:none;font-size:13px;white-space:nowrap;padding:5px 10px;",
      "border-radius:12px;background:var(--divider-color,#e0e0e0);}",
      ".hab-progress{flex:none;height:2px;background:transparent;overflow:hidden;}",
      ".hab-progress.on{background:var(--primary-color,#03a9f4);",
      "animation:hab-slide 1.2s ease-in-out infinite;}",
      "@keyframes hab-slide{0%{transform:translateX(-100%);}100%{transform:translateX(400%);}}",
      ".hab-view{flex:1;position:relative;min-height:0;}",
      ".hab-frame{border:0;position:absolute;inset:0;width:100%;height:100%;",
      "display:block;background:var(--primary-background-color,#fff);}",
      ".hab-overlay{position:absolute;inset:0;display:flex;align-items:center;",
      "justify-content:center;background:var(--primary-background-color,#fafafa);}",
      ".hab-overlay.hidden{display:none;}",
      ".hab-card{text-align:center;padding:24px;max-width:420px;}",
      ".hab-card h2{font-size:18px;margin:0 0 8px;font-weight:600;}",
      ".hab-card p{margin:0;color:var(--secondary-text-color,#727272);",
      "font-size:14px;line-height:1.5;}",
      ".hab-retry{margin-top:16px;padding:8px 18px;border:0;border-radius:18px;",
      "background:var(--primary-color,#03a9f4);color:#fff;font-size:14px;",
      "cursor:pointer;}",
      ".hab-retry.hidden{display:none;}",
    ].join("\n");
    this.appendChild(style);

    const wrap = document.createElement("div");
    wrap.className = "hab-wrap";

    const bar = document.createElement("div");
    bar.className = "hab-bar";

    const btnBack = document.createElement("button");
    btnBack.className = "hab-btn";
    btnBack.title = "Back";
    btnBack.textContent = "\u2190";
    const btnForward = document.createElement("button");
    btnForward.className = "hab-btn";
    btnForward.title = "Forward";
    btnForward.textContent = "\u2192";
    const btnReload = document.createElement("button");
    btnReload.className = "hab-btn";
    btnReload.title = "Reload";
    btnReload.textContent = "\u21bb";
    const btnHome = document.createElement("button");
    btnHome.className = "hab-btn";
    btnHome.title = "Home";
    btnHome.textContent = "\u2302";

    const omni = document.createElement("div");
    omni.className = "hab-omni";
    const globe = document.createElement("span");
    globe.className = "hab-globe";
    globe.textContent = "\uD83C\uDF10";
    const address = document.createElement("input");
    address.className = "hab-address";
    address.type = "text";
    address.placeholder = "Search or type a web address";
    address.autocomplete = "off";
    address.autocapitalize = "off";
    address.spellcheck = false;
    const btnGo = document.createElement("button");
    btnGo.className = "hab-go";
    btnGo.title = "Go";
    btnGo.textContent = "\u27a4";
    omni.appendChild(globe);
    omni.appendChild(address);
    omni.appendChild(btnGo);

    const pill = document.createElement("span");
    pill.className = "hab-pill";
    pill.title = "Ads and trackers blocked on this page";
    pill.textContent = "\uD83D\uDEAB 0 blocked";

    bar.appendChild(btnBack);
    bar.appendChild(btnForward);
    bar.appendChild(btnReload);
    bar.appendChild(btnHome);
    bar.appendChild(omni);
    bar.appendChild(pill);

    const progress = document.createElement("div");
    progress.className = "hab-progress";

    const view = document.createElement("div");
    view.className = "hab-view";
    const frame = document.createElement("iframe");
    frame.className = "hab-frame";
    frame.title = "Browser";
    frame.setAttribute(
      "sandbox",
      "allow-scripts allow-same-origin allow-forms allow-popups"
    );
    const overlay = document.createElement("div");
    overlay.className = "hab-overlay";
    const card = document.createElement("div");
    card.className = "hab-card";
    const overlayText = document.createElement("p");
    overlayText.textContent = "Loading\u2026";
    const retry = document.createElement("button");
    retry.className = "hab-retry hidden";
    retry.textContent = "Try again";
    card.appendChild(overlayText);
    card.appendChild(retry);
    overlay.appendChild(card);
    view.appendChild(frame);
    view.appendChild(overlay);

    wrap.appendChild(bar);
    wrap.appendChild(progress);
    wrap.appendChild(view);
    this.appendChild(wrap);

    this._frame = frame;
    this._address = address;
    this._blockedPill = pill;
    this._overlay = overlay;
    this._overlayText = overlayText;
    this._overlayRetry = retry;
    this._progress = progress;

    btnGo.addEventListener("click", () => this._goFromBar());
    address.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") this._goFromBar();
    });
    btnBack.addEventListener("click", () => {
      try { frame.contentWindow.history.back(); } catch (e) { /* not ready */ }
    });
    btnForward.addEventListener("click", () => {
      try { frame.contentWindow.history.forward(); } catch (e) { /* not ready */ }
    });
    btnReload.addEventListener("click", () => {
      if (this._currentTarget) this.go(this._currentTarget);
    });
    btnHome.addEventListener("click", () => this.go(this._homepage));
    retry.addEventListener("click", () => {
      this.go(this._currentTarget || this._homepage);
    });
    // Register the load listener before the first navigation, or the
    // loading overlay never hides.
    frame.addEventListener("load", () => this._onFrameLoad());
    window.addEventListener("message", this._onMessage);

    // The hass object may already have arrived (and finished loading
    // the config) before this element was connected — then go straight
    // to the homepage instead of waiting for a config load that
    // already happened.
    if (this._configLoaded) {
      if (!this._navigated) this.go(this._homepage);
    } else {
      this._loadConfig();
    }
    requestAnimationFrame(() => this._fixHeight());
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
    this._frame = null;
  }

  // If the percentage heights did not resolve (the element collapsed
  // to near zero — seen on the mobile app, where the panel showed
  // only a black screen), fall back to a viewport-based height so the
  // browser is actually visible. Only kicks in when the layout gave
  // us nothing; a calc() with dvh stays live across rotation.
  _fixHeight() {
    try {
      if (this.getBoundingClientRect().height < 50) {
        this.style.height = "calc(100dvh - 110px)";
      }
    } catch (e) { /* ignore */ }
  }

  async _loadConfig() {
    if (this._configLoaded || this._configLoading) return;
    // No hass object yet (it can arrive after connectedCallback):
    // do not mark the config loaded and do not navigate — the hass
    // setter retries this once hass arrives. Navigating now would
    // fail signing and flash an error for no reason.
    const hass = this._hass;
    const token =
      hass && hass.auth && hass.auth.data && hass.auth.data.access_token;
    if (!token) return;
    this._configLoading = true;
    try {
      const resp = await fetch("/api/ha_browser/config", {
        headers: { Authorization: "Bearer " + token },
      });
      if (resp.ok) {
        const cfg = await resp.json();
        if (cfg.homepage) this._homepage = cfg.homepage;
        if (typeof cfg.adblock === "boolean") this._adblock = cfg.adblock;
      }
    } catch (e) { /* fall back to defaults */ }
    this._configLoaded = true;
    this._configLoading = false;
    if (!this._navigated && this._frame) {
      this.go(this._homepage);
    }
  }

  _sign(path) {
    // Home Assistant's auth/sign_path websocket command signs a path
    // for the current user (default 30 s; we ask for an hour so a
    // page open a while still loads its late sub-resources).
    if (!this._hass || typeof this._hass.callWS !== "function") {
      return Promise.reject(new Error("no Home Assistant connection"));
    }
    return this._hass
      .callWS({ type: "auth/sign_path", path: path, expires: 3600 })
      .then((res) => res.path);
  }

  _normalize(input) {
    input = (input || "").trim();
    if (!input) return "";
    // A search query (spaces, or no dot) goes to search.
    if (/\s/.test(input) || input.indexOf(".") === -1) {
      return "https://duckduckgo.com/html/?q=" + encodeURIComponent(input);
    }
    // Looks like an address: add https:// when the scheme is missing.
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) {
      input = "https://" + input;
    }
    return input;
  }

  _showOverlay(text, showRetry) {
    if (this._overlayText) this._overlayText.textContent = text;
    if (this._overlayRetry) {
      this._overlayRetry.classList.toggle("hidden", !showRetry);
    }
    if (this._overlay) this._overlay.classList.remove("hidden");
    if (this._progress) this._progress.classList.add("on");
  }

  _hideOverlay() {
    if (this._overlay) this._overlay.classList.add("hidden");
    if (this._progress) this._progress.classList.remove("on");
  }

  async go(url) {
    if (!url || !this._frame) return;
    this._currentTarget = url;
    this._navigated = true;
    if (this._address) this._address.value = url;
    if (this._blockedPill) this._blockedPill.textContent = "\uD83D\uDEAB 0 blocked";
    this._showOverlay("Loading\u2026", false);
    try {
      const signed = await this._sign(
        "/api/ha_browser/proxy?url=" + encodeURIComponent(url)
      );
      this._frame.src = signed;
    } catch (e) {
      this._showOverlay(
        "Could not open this page — Home Assistant did not sign the " +
        "address. Close the Browser panel and open it again; if it " +
        "keeps happening, restart Home Assistant.",
        true
      );
    }
  }

  _goFromBar() {
    if (this._address) this.go(this._normalize(this._address.value));
  }

  _onFrameLoad() {
    this._hideOverlay();
    // Keep the address bar in sync with what the iframe shows (same
    // origin, so reading its location is allowed).
    try {
      const href = this._frame.contentWindow.location.href;
      const m = href.match(/[?&]url=([^&]*)/);
      if (m && this._address) {
        this._address.value = decodeURIComponent(m[1]);
      }
    } catch (e) { /* not readable yet */ }
  }

  _onMessage(ev) {
    if (ev.origin !== window.location.origin) return;
    const data = ev.data || {};
    // The proxied page is same-origin, so it can report its blocked
    // count straight to this bar (the injected adblock shim posts it).
    if (data.type === "ha-browser-blocked" && this._blockedPill) {
      const n = data.count || 0;
      this._blockedPill.textContent = "\uD83D\uDEAB " + n + " blocked";
    }
  }
}

// Guarded: HA loads this module with a plain <script type="module">
// (no cache), so a panel re-created after a disconnect would inject
// it a second time — defining the same name twice throws.
if (!customElements.get("ha-browser-panel")) {
  customElements.define("ha-browser-panel", HABrowserPanel);
}
