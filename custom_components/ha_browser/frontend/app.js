/* HA Browser — address bar + iframe navigation.
 *
 * Plain, dependency-free JavaScript (no framework, no custom
 * elements, no bleeding-edge APIs) so the page works the same in
 * Chrome, Firefox, Safari, Edge and the Home Assistant app.
 * Every page the iframe shows is loaded through the server-side
 * proxy (/api/ha_browser/proxy?url=...), which is what makes
 * framing and ad-blocking work at all.
 */
(function () {
  var PROXY = "/api/ha_browser/proxy?url=";
  var SEARCH = "https://duckduckgo.com/html/?q=";
  var HOMEPAGE = window.HA_BROWSER_HOMEPAGE || "https://duckduckgo.com/html/";

  var frame = document.getElementById("frame");
  var address = document.getElementById("address");
  var blockedPill = document.getElementById("blocked");
  var overlay = document.getElementById("overlay");
  var overlayText = document.getElementById("overlay-text");

  function normalize(input) {
    input = (input || "").trim();
    if (!input) return "";
    // A search query (spaces, or no dot) goes to search.
    if (/\s/.test(input) || input.indexOf(".") === -1) {
      return SEARCH + encodeURIComponent(input);
    }
    // Looks like an address: add https:// when the scheme is missing.
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) {
      input = "https://" + input;
    }
    return input;
  }

  function showOverlay(text) {
    overlayText.textContent = text;
    overlay.classList.remove("hidden");
  }

  function hideOverlay() {
    overlay.classList.add("hidden");
  }

  function go(url) {
    if (!url) return;
    address.value = url;
    blockedPill.textContent = "\uD83D\uDEAB 0 blocked";
    frame.src = PROXY + encodeURIComponent(url);
  }

  function goFromBar() {
    go(normalize(address.value));
  }

  document.getElementById("btn-go").addEventListener("click", goFromBar);
  address.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") goFromBar();
  });
  document.getElementById("btn-back").addEventListener("click", function () {
    try { frame.contentWindow.history.back(); } catch (e) { /* cross-origin */ }
  });
  document.getElementById("btn-forward").addEventListener("click", function () {
    try { frame.contentWindow.history.forward(); } catch (e) { /* cross-origin */ }
  });
  document.getElementById("btn-reload").addEventListener("click", function () {
    frame.contentWindow.location.reload();
  });
  document.getElementById("btn-home").addEventListener("click", function () {
    go(HOMEPAGE);
  });

  // The proxied page is same-origin, so it can report its blocked
  // count straight to this bar (the injected adblock shim posts it).
  window.addEventListener("message", function (ev) {
    if (ev.origin !== window.location.origin) return;
    var data = ev.data || {};
    if (data.type === "ha-browser-blocked") {
      var n = data.count || 0;
      blockedPill.textContent = "\uD83D\uDEAB " + n + " blocked";
    }
  });

  // Keep the address bar in sync with what the iframe shows (same
  // origin, so reading its location is allowed).
  frame.addEventListener("load", function () {
    hideOverlay();
    try {
      var href = frame.contentWindow.location.href;
      var m = href.match(/[?&]url=([^&]*)/);
      if (m) address.value = decodeURIComponent(m[1]);
    } catch (e) { /* not readable yet */ }
  });

  showOverlay("Loading\u2026");
  go(HOMEPAGE);
})();
