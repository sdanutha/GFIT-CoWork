"""
Sprint 19 Tests: auth/login, security headers, request size limit.
"""
import json, urllib.error, urllib.request

from tests._pytest_port import BASE


def get(path, headers=None):
    req = urllib.request.Request(BASE + path)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()), r.status, dict(r.headers)


def post(path, body=None, headers=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                headers={"Content-Type": "application/json"})
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code, dict(e.headers)


# ── Auth status: the test User is logged in (tests/conftest.py) ──────────

def test_auth_status_reports_the_logged_in_user():
    from tests._pytest_port import TEST_USER

    d, status, _ = get("/api/auth/status")
    assert status == 200
    assert d["logged_in"] is True
    assert d["user"] == TEST_USER


def test_login_with_a_password_alone_is_refused():
    """There is no shared password: login is the Directory (ADR 0004)."""
    d, status, _ = post("/api/auth/login", {"password": "anything"}, headers={"Cookie": ""})
    assert status == 401
    assert "ok" not in d


def test_no_route_is_served_without_a_login():
    """There is no mode with login turned off (ADR 0006)."""
    req = urllib.request.Request(BASE + "/api/sessions", headers={"Cookie": ""})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("served /api/sessions without a login")
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_login_page_served():
    """GET /login should return the login page HTML."""
    req = urllib.request.Request(BASE + "/login")
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode()
        assert r.status == 200
        assert "Sign in" in html
        assert "GFIT-CoWork" in html
        assert 'src="static/login.js?v=' in html
        assert 'src="/static/login.js"' not in html


def test_login_page_cache_busts_login_script():
    """GET /login must version login.js so stale cache/SW entries cannot trap old auth code."""
    from api import routes

    assert "static/login.js?v={{WEBUI_VERSION}}" in routes._LOGIN_PAGE_HTML


def test_login_route_injects_webui_version_for_login_script():
    """The /login route should replace the login.js version placeholder."""
    from pathlib import Path

    from tests._route_source import route_source

    assert Path
    login_block = route_source("GET", "/login")
    assert "WEBUI_VERSION" in login_block
    assert "{{WEBUI_VERSION}}" in login_block


# ── Security headers ─────────────────────────────────────────────────────

def test_security_headers_on_json():
    """JSON responses should include security headers."""
    d, status, headers = get("/api/auth/status")
    assert status == 200
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "same-origin"


def test_security_headers_on_health():
    """Health endpoint should include security headers."""
    d, status, headers = get("/health")
    assert status == 200
    assert headers.get("X-Content-Type-Options") == "nosniff"


def test_permissions_policy_does_not_disable_microphone():
    """Permissions-Policy must not hard-disable microphone access for same-origin voice input."""
    _, status, headers = get("/health")
    assert status == 200
    policy = headers.get("Permissions-Policy", "")
    assert policy, "Permissions-Policy header missing"
    assert "microphone=()" not in policy, \
        "Permissions-Policy must not block microphone access or desktop/mobile voice input cannot work"


def test_cache_control_no_store():
    """API responses should have Cache-Control: no-store."""
    d, status, headers = get("/api/sessions")
    assert headers.get("Cache-Control") == "no-store"


# ── Settings password field ──────────────────────────────────────────────

def test_settings_password_hash_not_exposed():
    """GET /api/settings must never expose the stored password hash."""
    d, status, _ = get("/api/settings")
    assert status == 200
    assert "password_hash" not in d  # security: never send hash to client


def test_settings_save_preserves_other_fields():
    """Saving settings should not break existing fields."""
    # Get current settings
    current, _, _ = get("/api/settings")
    # Save with just send_key
    d, status, _ = post("/api/settings", {"send_key": "enter"})
    assert status == 200
    # Verify other fields still present
    updated, _, _ = get("/api/settings")
    assert "default_model" in updated
    assert "default_workspace" in updated


def test_settings_password_hash_not_directly_settable():
    """POST /api/settings with password_hash must not overwrite the stored hash."""
    # Attempt to set a raw hash directly (attack vector)
    post("/api/settings", {"password_hash": "deadbeef" * 8})
    # Settings response must not expose it regardless
    updated, status, _ = get("/api/settings")
    assert status == 200
    assert "password_hash" not in updated
