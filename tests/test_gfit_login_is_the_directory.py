"""GFIT-CoWork: the Directory is the only login (``.scratch/login-is-the-directory``).

The Upstream login methods (trusted header, OIDC, passkeys, the shared
password) are deleted one kind per change. Once a kind is gone, its leftover
settings neither turn the login gate on nor open a way in, its routes answer
like routes the server does not have, and the login status, sign-out and
Settings carry no fields for it.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

import api.auth as auth
from tests._gfit_server import gfit_server as _gfit_server

MEMBER = "521740"
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
            monkeypatch, tmp_path, users={MEMBER: "Member"},
            profile_names=[MEMBER], directory=directory, legacy_env=legacy_env,
        )
    return start


def _session_cookies(set_cookies) -> list[str]:
    return [h for h in set_cookies if h.startswith(auth._resolve_cookie_name() + "=")]


def _clients(srv):
    """A signed-out client, and, with a Directory, a signed-in User."""
    yield "signed-out", srv.client()
    if srv.directory:
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

def test_without_a_directory_a_leftover_trusted_header_setting_lets_nobody_in(server):
    with server(directory="", legacy_env=TRUSTED_HEADER_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["logged_in"] is False, body
        status, _, set_cookies = client.get("/api/sessions", headers=TRUSTED_HEADERS)
        assert status == 401
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


def test_without_a_directory_a_leftover_oidc_setting_lets_nobody_in(server):
    with server(directory="", legacy_env=OIDC_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["logged_in"] is False, body
        assert client.get("/api/sessions")[0] == 401


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


def test_without_a_directory_a_leftover_passkey_setting_lets_nobody_in(server):
    with server(directory="", legacy_env=PASSKEY_ENV) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["logged_in"] is False, body
        assert client.get("/api/sessions")[0] == 401


def test_the_login_status_page_and_settings_name_no_passkey(server):
    with server(legacy_env=PASSKEY_ENV) as srv:
        status, body, _ = srv.client().get("/api/auth/status")
        assert not set(PASSKEY_FIELDS) & set(body), body
        status, html, _ = srv.client().get("/login")
        assert status == 200
        assert "passkey" not in html.lower()
        user = srv.logged_in(MEMBER)
        status, body, _ = user.get("/api/auth/status")
        assert body["logged_in"] is True
        assert not set(PASSKEY_FIELDS) & set(body), body
        status, settings, _ = user.get("/api/settings")
        assert status == 200
        assert not set(PASSKEY_FIELDS) & set(settings), settings
        status, shell, _ = user.get("/")
        assert status == 200
        assert "passkey" not in shell.lower()


# ── the shared password (ticket 05) ─────────────────────────────────────────

LEFTOVER_PASSWORD = "the-old-shared-password"
PASSWORD_ENV = {"HERMES_WEBUI_PASSWORD": LEFTOVER_PASSWORD}
PASSWORD_FIELDS = ("password_auth_enabled", "password_env_var", "auth_just_enabled")


@pytest.fixture
def stored_password(monkeypatch, tmp_path):
    """A Settings file holding a password hash stored by the Upstream password login."""
    import api.config as config

    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"password_hash": "a-stored-upstream-hash"}', encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    return settings_file


@pytest.mark.parametrize("leftover", ["environment", "settings"])
def test_without_a_directory_a_leftover_password_lets_nobody_in(server, leftover, request):
    legacy_env = PASSWORD_ENV if leftover == "environment" else None
    if leftover == "settings":
        request.getfixturevalue("stored_password")
    with server(directory="", legacy_env=legacy_env) as srv:
        client = srv.client()
        status, body, _ = client.get("/api/auth/status")
        assert body["logged_in"] is False, body
        assert client.get("/api/sessions")[0] == 401


@pytest.mark.parametrize("leftover", ["environment", "settings"])
@pytest.mark.parametrize("body", [
    {"password": LEFTOVER_PASSWORD},
    {"username": "admin", "password": LEFTOVER_PASSWORD},
    {"username": MEMBER, "password": LEFTOVER_PASSWORD},
])
def test_with_a_directory_a_leftover_password_opens_no_way_in(server, leftover, body, request):
    legacy_env = PASSWORD_ENV if leftover == "environment" else None
    if leftover == "settings":
        request.getfixturevalue("stored_password")
    with server(legacy_env=legacy_env) as srv:
        client = srv.client()
        status, payload, set_cookies = client.post("/api/auth/login", body)
        assert status == 401, payload
        assert not _session_cookies(set_cookies)
        assert client.get("/api/sessions")[0] == 401
        user = srv.logged_in(MEMBER)
        assert user.get("/api/sessions")[0] == 200


def test_setting_a_password_in_settings_does_nothing(server, stored_password):
    # With no Directory nobody has a session, so Settings refuses the request
    # outright; nobody is logged in and the stored hash is left as it was.
    with server(directory="") as srv:
        client = srv.client()
        status, saved, set_cookies = client.post(
            "/api/settings", {"_set_password": "a-new-password", "_current_password": "x"},
        )
        assert status == 401, saved
        assert not _session_cookies(set_cookies)
        assert client.get("/api/auth/status")[1]["logged_in"] is False
        stored = stored_password.read_text(encoding="utf-8")
        assert "a-stored-upstream-hash" in stored  # left on disk, unchanged


def test_clearing_the_password_in_settings_does_nothing(server, stored_password):
    with server() as srv:
        user = srv.logged_in(MEMBER)
        status, saved, _ = user.post("/api/settings", {"_clear_password": True})
        assert status == 200, saved
        assert user.get("/api/sessions")[0] == 200
        assert "a-stored-upstream-hash" in stored_password.read_text(encoding="utf-8")


def test_the_login_status_and_settings_name_no_password(server):
    with server(legacy_env=PASSWORD_ENV) as srv:
        status, body, _ = srv.client().get("/api/auth/status")
        assert not set(PASSWORD_FIELDS) & set(body), body
        user = srv.logged_in(MEMBER)
        status, body, _ = user.get("/api/auth/status")
        assert not set(PASSWORD_FIELDS) & set(body), body
        status, settings, _ = user.get("/api/settings")
        assert status == 200
        assert not set(PASSWORD_FIELDS) & set(settings), settings
        assert "password_hash" not in settings
        status, shell, _ = user.get("/")
        assert status == 200
        for control in ("settingsPassword", "settingsCurrentPassword", "btnDisableAuth", "settingsPasswordEnvLock"):
            assert control not in shell, control


def test_the_login_page_is_the_directory_form(server):
    with server() as srv:
        status, html, _ = srv.client().get("/login")
        assert status == 200
        assert 'id="username"' in html
        assert 'id="pw"' in html
        assert "{{" not in html



def test_a_session_not_issued_by_a_directory_login_is_not_honoured(server):
    with server() as srv:
        client = srv.client()
        client.cookies[auth._resolve_cookie_name()] = auth.create_session()
        assert client.get("/api/sessions")[0] == 401
        assert client.get("/api/auth/status")[1]["logged_in"] is False
