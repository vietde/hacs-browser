(function (cfg) {
  "use strict";
  // Runs inside every proxied HTML page (sandboxed, opaque origin).
  // Keeps dynamically-built URLs inside the proxy, emulates storage that the
  // sandbox blocks, and reports navigation to the parent panel.
  var PREFIX = cfg.prefix;
  var BASE = cfg.base;
  var REAL = cfg.url;
  var ORIGIN = location.origin;
  var SKIP = /^(?:data|blob|javascript|about|mailto|tel|sms|file):/i;

  function inProxy(s) {
    return s.indexOf(PREFIX + "/") === 0 || s.indexOf(ORIGIN + PREFIX + "/") === 0;
  }

  function toProxy(input) {
    var s;
    try { s = String(input); } catch (e) { return input; }
    if (!s || s.charAt(0) === "#" || SKIP.test(s) || inProxy(s)) return input;
    var u;
    try { u = new URL(s, BASE); } catch (e) { return input; }
    var p = u.protocol;
    if (p === "ws:") p = "http:"; else if (p === "wss:") p = "https:";
    if (p !== "http:" && p !== "https:") return input;
    return PREFIX + "/" + p.slice(0, -1) + "/" + u.host + u.pathname + u.search + u.hash;
  }

  function fromProxy(href) {
    var m;
    try { m = new URL(href, location.href); } catch (e) { return String(href); }
    if (m.pathname.indexOf(PREFIX + "/") !== 0) return m.href;
    var rest = m.pathname.slice(PREFIX.length + 1);
    var i = rest.indexOf("/");
    if (i < 0) return m.href;
    var scheme = rest.slice(0, i);
    rest = rest.slice(i + 1);
    var j = rest.indexOf("/");
    var host = j < 0 ? rest : rest.slice(0, j);
    var path = j < 0 ? "/" : rest.slice(j);
    return scheme + "://" + host + path + m.search + m.hash;
  }

  function post(msg) {
    msg.__hb = 1;
    try { parent.postMessage(msg, "*"); } catch (e) { /* not framed */ }
  }

  function guard(fn) { try { fn(); } catch (e) { /* never break the page */ } }

  // ---- network ------------------------------------------------------
  guard(function () {
    var _fetch = window.fetch;
    window.fetch = function (input, init) {
      try {
        if (typeof Request !== "undefined" && input instanceof Request) {
          var req = input;
          var url = toProxy(req.url);
          var opts = {
            method: req.method, headers: req.headers, mode: "cors",
            credentials: "omit", cache: req.cache, redirect: req.redirect,
            signal: req.signal
          };
          if (req.method === "GET" || req.method === "HEAD") {
            return _fetch.call(this, new Request(url, opts));
          }
          var self = this;
          return req.clone().arrayBuffer().then(function (buf) {
            opts.body = buf;
            return _fetch.call(self, new Request(url, opts));
          });
        }
        input = toProxy(input);
        init = Object.assign({}, init || {}, { credentials: "omit" });
      } catch (e) { /* fall through */ }
      return _fetch.call(this, input, init);
    };
  });

  guard(function () {
    var _open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      var args = Array.prototype.slice.call(arguments);
      args[1] = toProxy(url);
      return _open.apply(this, args);
    };
    Object.defineProperty(XMLHttpRequest.prototype, "withCredentials", {
      configurable: true, get: function () { return false; }, set: function () {}
    });
  });

  guard(function () {
    var _WS = window.WebSocket;
    function WS(url, protocols) {
      var u = new URL(toProxy(url), location.href);
      u.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      return protocols === undefined ? new _WS(u.href) : new _WS(u.href, protocols);
    }
    WS.prototype = _WS.prototype;
    ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) { WS[k] = _WS[k]; });
    window.WebSocket = WS;
  });

  guard(function () {
    var _ES = window.EventSource;
    if (!_ES) return;
    function ES(url, init) { return new _ES(toProxy(url), { withCredentials: false }); }
    ES.prototype = _ES.prototype;
    window.EventSource = ES;
  });

  guard(function () {
    var _beacon = navigator.sendBeacon;
    if (_beacon) navigator.sendBeacon = function (u, d) { return _beacon.call(navigator, toProxy(u), d); };
  });

  // ---- DOM URL properties / attributes -------------------------------
  var ATTRS = { src: 1, href: 1, action: 1, poster: 1, formaction: 1, data: 1 };
  guard(function () {
    var _sa = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (n, v) {
      if (typeof n === "string" && ATTRS[n.toLowerCase()]) {
        var ln = n.toLowerCase();
        if (ln !== "data" || this.localName === "object") v = toProxy(v);
      }
      return _sa.call(this, n, v);
    };
  });

  function patchProp(ctorName, prop) {
    guard(function () {
      var proto = window[ctorName] && window[ctorName].prototype;
      var d = proto && Object.getOwnPropertyDescriptor(proto, prop);
      if (!d || !d.set) return;
      Object.defineProperty(proto, prop, {
        configurable: true, enumerable: d.enumerable, get: d.get,
        set: function (v) { d.set.call(this, toProxy(v)); }
      });
    });
  }
  [
    ["HTMLScriptElement", "src"], ["HTMLImageElement", "src"],
    ["HTMLIFrameElement", "src"], ["HTMLEmbedElement", "src"],
    ["HTMLSourceElement", "src"], ["HTMLMediaElement", "src"],
    ["HTMLTrackElement", "src"], ["HTMLInputElement", "src"],
    ["HTMLLinkElement", "href"], ["HTMLAnchorElement", "href"],
    ["HTMLAreaElement", "href"], ["HTMLFormElement", "action"],
    ["HTMLObjectElement", "data"], ["HTMLVideoElement", "poster"]
  ].forEach(function (p) { patchProp(p[0], p[1]); });

  // ---- popups / link targets ------------------------------------------
  guard(function () {
    window.open = function (url, target) {
      var t = (target || "_blank").toLowerCase();
      if (url == null || url === "") return null;
      if (t === "_self" || t === "_top" || t === "_parent") {
        location.assign(toProxy(url));
        return null;
      }
      var real;
      try { real = new URL(String(url), BASE).href; } catch (e) { return null; }
      post({ type: "open", url: real });
      return null;
    };
  });

  guard(function () {
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button) return;
      var a = e.target && e.target.closest && e.target.closest("a[href], area[href]");
      if (!a) return;
      var raw = a.getAttribute("href");
      if (!raw || raw.charAt(0) === "#" || SKIP.test(raw)) return;
      var t = (a.getAttribute("target") || "").toLowerCase();
      var proxied = toProxy(a.href);
      if (t && t !== "_self") {
        e.preventDefault();
        if (t === "_top" || t === "_parent") location.assign(proxied);
        else post({ type: "open", url: fromProxy(proxied) });
        return;
      }
      if (proxied !== a.href) { e.preventDefault(); location.assign(proxied); }
    }, true);
  });

  // ---- history / address bar -------------------------------------------
  var titleTimer;
  function notify() {
    clearTimeout(titleTimer);
    titleTimer = setTimeout(function () {
      post({ type: "nav", url: REAL, title: document.title || "" });
    }, 30);
  }

  guard(function () {
    ["pushState", "replaceState"].forEach(function (fn) {
      var orig = History.prototype[fn];
      History.prototype[fn] = function (state, title, url) {
        if (url != null && url !== "") {
          var real;
          try { real = new URL(String(url), BASE).href; } catch (e) { real = null; }
          if (real) {
            try {
              orig.call(this, state, title, toProxy(real));
              BASE = REAL = real;
            } catch (e) {
              try { orig.call(this, state, title); } catch (e2) { /* ignore */ }
            }
            notify();
            return;
          }
        }
        orig.apply(this, arguments);
        notify();
      };
    });
    function resync() { BASE = REAL = fromProxy(location.href); notify(); }
    window.addEventListener("popstate", resync);
    window.addEventListener("hashchange", resync);
  });

  guard(function () {
    document.addEventListener("DOMContentLoaded", function () {
      notify();
      var t = document.querySelector("title");
      if (t) new MutationObserver(notify).observe(t, { childList: true, characterData: true, subtree: true });
    });
    window.addEventListener("load", notify);
  });
  notify();

  // ---- storage the sandbox would otherwise block -------------------------
  function memStore() {
    var d = Object.create(null);
    var api = {
      getItem: function (k) { k = String(k); return k in d ? d[k] : null; },
      setItem: function (k, v) { d[String(k)] = String(v); },
      removeItem: function (k) { delete d[String(k)]; },
      clear: function () { d = Object.create(null); },
      key: function (i) { var ks = Object.keys(d); return i < ks.length ? ks[i] : null; }
    };
    return new Proxy(api, {
      get: function (t, k) {
        if (k === "length") return Object.keys(d).length;
        if (k in t) return t[k];
        return typeof k === "string" && k in d ? d[k] : undefined;
      },
      set: function (t, k, v) { d[String(k)] = String(v); return true; },
      deleteProperty: function (t, k) { delete d[String(k)]; return true; },
      has: function (t, k) { return k in t || k in d; },
      ownKeys: function () { return Object.keys(d); },
      getOwnPropertyDescriptor: function (t, k) {
        return k in d ? { value: d[k], writable: true, enumerable: true, configurable: true } : undefined;
      }
    });
  }
  ["localStorage", "sessionStorage"].forEach(function (name) {
    guard(function () {
      var s = memStore();
      Object.defineProperty(window, name, { configurable: true, get: function () { return s; } });
    });
  });

  guard(function () {
    var jar = {};
    Object.defineProperty(Document.prototype, "cookie", {
      configurable: true,
      get: function () {
        return Object.keys(jar).map(function (k) { return k + "=" + jar[k]; }).join("; ");
      },
      set: function (v) {
        var parts = String(v).split(";");
        var kv = parts[0], i = kv.indexOf("=");
        var k = (i < 0 ? "" : kv.slice(0, i)).trim();
        var val = i < 0 ? kv.trim() : kv.slice(i + 1).trim();
        var attrs = parts.slice(1).join(";");
        var del = false, m;
        if ((m = /max-age=\s*(-?\d+)/i.exec(attrs)) && parseInt(m[1], 10) <= 0) del = true;
        if ((m = /expires=([^;]+)/i.exec(attrs)) && Date.parse(m[1]) < Date.now()) del = true;
        if (del) delete jar[k]; else jar[k] = val;
      }
    });
  });

  guard(function () {
    if (navigator.serviceWorker) {
      navigator.serviceWorker.register = function () {
        return Promise.reject(new Error("Service workers are disabled in HA Browser"));
      };
    }
  });

  // ---- commands from the panel ------------------------------------------
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (e.source !== parent || !d || d.__hb_cmd !== 1) return;
    if (d.cmd === "back") history.back();
    else if (d.cmd === "forward") history.forward();
    else if (d.cmd === "reload") location.reload();
    else if (d.cmd === "stop") window.stop();
  });
})
