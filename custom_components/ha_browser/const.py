"""Constants for HA Browser."""
from __future__ import annotations

DOMAIN = "ha_browser"
VERSION = "0.1.0"

PANEL_URL_PATH = "ha-browser"
PANEL_ELEMENT = "ha-browser-panel"
STATIC_URL = "/ha_browser_static"
PROXY_BASE = "/api/ha_browser/s"

CONF_TITLE = "title"
CONF_ICON = "icon"
CONF_REQUIRE_ADMIN = "require_admin"
CONF_HOME_URL = "home_url"
CONF_SEARCH_URL = "search_url"
CONF_ALLOW_LOOPBACK = "allow_loopback"
CONF_ALLOW_PRIVATE = "allow_private"
CONF_ALLOW_PUBLIC = "allow_public"
CONF_ALLOWED_HOSTS = "allowed_hosts"
CONF_BLOCKED_HOSTS = "blocked_hosts"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SESSION_TTL = "session_ttl"  # minutes of inactivity

DEFAULTS: dict = {
    CONF_TITLE: "Browser",
    CONF_ICON: "mdi:web",
    CONF_REQUIRE_ADMIN: True,
    CONF_HOME_URL: "",
    CONF_SEARCH_URL: "https://html.duckduckgo.com/html/?q=%s",
    CONF_ALLOW_LOOPBACK: False,
    CONF_ALLOW_PRIVATE: True,
    CONF_ALLOW_PUBLIC: True,
    CONF_ALLOWED_HOSTS: "",
    CONF_BLOCKED_HOSTS: "",
    CONF_VERIFY_SSL: True,
    CONF_SESSION_TTL: 480,
}

# Largest HTML/CSS document we will buffer in memory for rewriting.
MAX_REWRITE_BYTES = 25 * 1024 * 1024
MAX_SESSIONS_PER_USER = 10
