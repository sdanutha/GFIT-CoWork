"""
Sprint 27 Tests: configurable assistant display name (bot_name).
Tests cover settings API round-trip, empty/missing input defaults,
login page rendering, and server-side sanitization.
"""
import json
import urllib.error
import urllib.request

from tests._pytest_port import BASE, deployment_settings


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status


def get_raw(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read().decode(), r.status


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ── Default value ─────────────────────────────────────────────────────────

def test_settings_default_bot_name():
    """GET /api/settings should return bot_name defaulting to 'Hermes'."""
    d, status = get("/api/settings")
    assert status == 200
    assert "bot_name" in d
    assert d["bot_name"] == "Hermes"


# ── Round-trip ────────────────────────────────────────────────────────────

def test_settings_report_the_deployments_bot_name():
    """The assistant's name is the Deployment's: the Operator sets it, every User sees it."""
    with deployment_settings(bot_name="TestBot <&>"):
        d, status = get("/api/settings")
        assert status == 200
        assert d.get("bot_name") == "TestBot <&>"


def test_a_user_cannot_change_the_bot_name():
    d, status = post("/api/settings", {"bot_name": "TestBot"})
    assert status == 403
    assert "Operator" in d.get("error", "")
    assert get("/api/settings")[0].get("bot_name") != "TestBot"


def test_login_page_shows_app_name():
    """GET /login shows the web app's name (GFIT-CoWork) in title and h1."""
    html, status = get_raw("/login")
    assert status == 200
    assert "<title>GFIT-CoWork" in html
    assert "<h1>GFIT-CoWork</h1>" in html


def test_login_page_ignores_custom_bot_name():
    """The assistant name is not the web app's name: /login keeps GFIT-CoWork."""
    with deployment_settings(bot_name="Aria"):
        html, status = get_raw("/login")
        assert status == 200
        assert "<title>GFIT-CoWork" in html
        assert "<h1>GFIT-CoWork</h1>" in html
        assert "Aria" not in html


def test_login_page_empty_name_does_not_crash():
    """Login page must not 500 even if somehow bot_name is empty in settings."""
    with deployment_settings(bot_name=""):
        html, status = get_raw("/login")
    assert status == 200
    assert "Sign in" in html


def test_login_page_xss_escaped():
    """bot_name with HTML special chars should be escaped in the login page."""
    with deployment_settings(bot_name="<script>alert(1)</script>"):
        html, status = get_raw("/login")
        assert status == 200
        # Raw tag must not appear unescaped
        assert "<script>alert(1)</script>" not in html
