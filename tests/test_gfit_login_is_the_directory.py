"""GFIT-CoWork: the Directory is the only login (``.scratch/login-is-the-directory``).

The Upstream login methods are deleted one kind per change. Once a kind is
gone, its leftover settings neither turn the login gate on nor open a way in,
and the login status and sign-out carry no fields for it.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

import api.auth as auth
from tests._gfit_server import gfit_server as _gfit_server

MEMBER = "521740"
ADMIN = "100001"
MISSING_ROUTE = "/api/auth/no-such-route"

TRUSTED_HEADER_ENV = {
    "HERMES_WEBUI_TRUSTED_AUTH_HEADER": "X-Remote-User",
    "HERMES_WEBUI_TRUSTED_GROUPS_HEADER": "X-Remote-Groups",
    "HERMES_WEBUI_GROUP_PROFILE_MAP": '{"staff": "%s"}' % MEMBER,
    "HERMES_WEBUI_TRUSTED_AUTH_LOGOUT_URL": "https://sso.example.com/logout",
}
TRUSTED_HEADERS = {"X-Remote-User": MEMBER, "X-Remote-Groups": "staff"}

OIDC_ENV = {
    "HERMES_WEBUI_OIDC_ISSUER": "https://idp.example.com",
    "HERMES_WEBUI_OIDC_CLIENT_ID": "gfit-cowork",
    "HERMES_WEBUI_OIDC_CLIENT_SECRET": "s3cret",
    "HERMES_WEBUI_OIDC_ALLOW_CLAIM": "groups",
    "HERMES_WEBUI_OIDC_ALLOW_VALUES": "staff",
}


@pytest.fixture
def server(monkeypatch, tmp_path):
    def start(*, directory="memory", legacy_env=None):
        return _gfit_server(
            monkeypatch, tmp_path, users={MEMBER: "Member", ADMIN: "Admin"},
            profile_names=[MEMBER], admins=ADMIN, directory=directory, legacy_env=legacy_env,
        )
    return start


def _session_cookies(set_cookies) -> list[str]:
    return [h for h in set_cookies if h.startswith(auth._resolve_cookie_name() + "=")]


def _clients(srv):
    """A signed-out client, and, with a Directory, a signed-in Admin and User."""
    yield "signed-out", srv.client()
    if srv.directory:
        yield "admin", srv.logged_in(ADMIN)
        yield "user", srv.logged_in(MEMBER)


def assert_answers_like_a_missing_route(srv, method, path, body=None):
    """*path* gets the same answer as a route the server does not have, and logs nobody in."""
    for who, client in _clients(srv):
        cookies_before = dict(client.cookies)
        status, payload, set_cookies = client.request(method, path, body)
        expected_status, expected_payload, _ = client.request(method, MISSING_ROUTE, body)
        assert (status, payload) == (expected_status, expected_payload), (who, method, path)
        assert not _session_cookies(set_cookies), (who, path)
        assert client.cookies == cookies_before, (who, path)
        if who == "signed-out":
            assert client.get("/api/auth/status")[1]["logged_in"] is False


# ── trusted-header login (ticket 02) ────────────────────────────────────────

def test_a_leftover_trusted_header_setting_does_not_turn_login_on(server):
    with server(directory="", legacy_env=TRUSTED_HEADER_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["auth_enabled"] is False, body
        status, _, set_cookies = client.get("/api/sessions", headers=TRUSTED_HEADERS)
        assert status == 200
        assert not _session_cookies(set_cookies)


def test_with_a_directory_a_trusted_header_opens_no_way_in(server):
    with server(legacy_env=TRUSTED_HEADER_ENV) as srv:
        client = srv.client()
        status, _, set_cookies = client.get("/api/sessions", headers=TRUSTED_HEADERS)
        assert status == 401
        assert not _session_cookies(set_cookies)
        status, body, _ = client.get("/api/auth/status", headers=TRUSTED_HEADERS)
        assert body["logged_in"] is False
        assert "trusted_auth_enabled" not in body


def test_a_user_still_logs_in_and_signs_out_with_no_trusted_header_fields(server):
    with server(legacy_env=TRUSTED_HEADER_ENV) as srv:
        client = srv.logged_in(MEMBER)
        status, body, _ = client.get("/api/auth/status", headers=TRUSTED_HEADERS)
        assert body["logged_in"] is True
        assert body["user"] == MEMBER
        assert "trusted_auth_enabled" not in body
        status, body, _ = client.post("/api/auth/logout", headers=TRUSTED_HEADERS)
        assert status == 200
        assert "trusted_logout_url" not in body
        assert client.get("/api/sessions")[0] == 401


# ── OIDC login (ticket 03) ──────────────────────────────────────────────────

OIDC_ROUTES = ["/api/auth/oidc/start", "/api/auth/oidc/callback?state=abc&code=def"]


@pytest.mark.parametrize("directory", ["memory", ""], ids=["directory", "no-directory"])
@pytest.mark.parametrize("path", OIDC_ROUTES)
def test_the_oidc_routes_are_gone(server, directory, path):
    with server(directory=directory, legacy_env=OIDC_ENV) as srv:
        assert_answers_like_a_missing_route(srv, "GET", path)


def test_a_leftover_oidc_setting_does_not_turn_login_on(server):
    with server(directory="", legacy_env=OIDC_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["auth_enabled"] is False, body
        assert client.get("/api/sessions")[0] == 200


def test_the_login_status_and_page_name_no_oidc(server):
    with server(legacy_env=OIDC_ENV) as srv:
        status, body, _ = srv.client().get("/api/auth/status")
        assert "oidc_enabled" not in body
        status, html, _ = srv.client().get("/login")
        assert status == 200
        assert 'id="username"' in html
        assert "oidc" not in html.lower()
        assert "Continue with SSO" not in html
        status, body, _ = srv.logged_in(MEMBER).get("/api/auth/status")
        assert body["logged_in"] is True
        assert "oidc_enabled" not in body


# ── passkey login (ticket 04) ───────────────────────────────────────────────

PASSKEY_ENV = {"HERMES_WEBUI_PASSKEY": "1"}
PASSKEY_ROUTES = [
    "/api/auth/passkey/options",
    "/api/auth/passkey/login",
    "/api/auth/passkey/register/options",
    "/api/auth/passkey/register",
    "/api/auth/passkey/delete",
    "/api/auth/passkeys",
]
PASSKEY_FIELDS = ("passkeys_enabled", "passkeys_count", "passkey_feature_flag", "passwordless_enabled")


@pytest.mark.parametrize("directory", ["memory", ""], ids=["directory", "no-directory"])
@pytest.mark.parametrize("path", PASSKEY_ROUTES)
def test_the_passkey_routes_are_gone(server, directory, path):
    with server(directory=directory, legacy_env=PASSKEY_ENV) as srv:
        assert_answers_like_a_missing_route(srv, "POST", path, {})


def test_a_leftover_passkey_setting_does_not_turn_login_on(server):
    with server(directory="", legacy_env=PASSKEY_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["auth_enabled"] is False, body
        assert client.get("/api/sessions")[0] == 200


def test_the_login_status_page_and_settings_name_no_passkey(server):
    with server(legacy_env=PASSKEY_ENV) as srv:
        status, body, _ = srv.client().get("/api/auth/status")
        assert not set(PASSKEY_FIELDS) & set(body), body
        status, html, _ = srv.client().get("/login")
        assert status == 200
        assert "passkey" not in html.lower()
        admin = srv.logged_in(ADMIN)
        status, body, _ = admin.get("/api/auth/status")
        assert body["logged_in"] is True
        assert not set(PASSKEY_FIELDS) & set(body), body
        status, settings, _ = admin.get("/api/settings")
        assert status == 200
        assert not set(PASSKEY_FIELDS) & set(settings), settings
        status, shell, _ = admin.get("/")
        assert status == 200
        assert "passkey" not in shell.lower()


def test_the_terminal_refusal_names_the_directory(server, monkeypatch):
    # With login off, a request from a non-local client (through the trusted
    # loopback proxy) may not open the embedded terminal.
    monkeypatch.setenv("HERMES_WEBUI_TRUST_FORWARDED_FOR", "1")
    monkeypatch.delenv("HERMES_WEBUI_ONBOARDING_OPEN", raising=False)
    with server(directory="") as srv:
        status, body, _ = srv.client().post(
            "/api/terminal/start", {}, headers={"X-Forwarded-For": "8.8.8.8"},
        )
        assert status == 403, body
        message = body["error"]
        assert "Directory" in message
        assert "HERMES_WEBUI_DIRECTORY" in message
        assert "passkey" not in message.lower()
        assert "password" not in message.lower()
