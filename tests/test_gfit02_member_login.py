"""GFIT-CoWork ticket 02: Member login with an employee ID (in-memory Directory).

Every test talks HTTP to a real ``server.Handler`` started in-process on a free
port, with its auth state, Hermes home and Directory isolated to ``tmp_path``.
The shared live test server is left alone: turning Directory login on there
would put every other test behind a login.
"""
from __future__ import annotations

import http.client
import http.cookies
import json
import logging
import threading

import pytest

import api.auth as auth
import api.profiles as profiles

PASSWORD = "Tr0ub4dor&3-correct-horse"
WRONG_PASSWORD = "not-the-password"
MEMBER = "521740"
NO_PROFILE_USER = "671278"

INCORRECT = "incorrect username or password"


class Client:
    """A tiny browser: keeps cookies across requests to one server."""

    def __init__(self, port: int):
        self.port = port
        self.cookies: dict[str, str] = {}

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        set_cookies = resp.headers.get_all("Set-Cookie") or []
        for header in set_cookies:
            jar = http.cookies.SimpleCookie()
            jar.load(header)
            for name, morsel in jar.items():
                if morsel.value and morsel["max-age"] != "0":
                    self.cookies[name] = morsel.value
                else:
                    self.cookies.pop(name, None)
        conn.close()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return resp.status, payload, set_cookies

    def login(self, username, password):
        return self.request("POST", "/api/auth/login", {"username": username, "password": password})


@pytest.fixture
def gfit_server(monkeypatch, tmp_path):
    import server

    state = tmp_path / "state"
    state.mkdir()
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / MEMBER).mkdir(parents=True)

    users = tmp_path / "directory-users.json"
    users.write_text(json.dumps({
        MEMBER: {"password": PASSWORD, "display_name": "Somchai Jaidee"},
        NO_PROFILE_USER: {"password": PASSWORD, "display_name": "No Profile"},
    }))
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY_USERS", str(users))

    # Isolate auth state and switch off every other login method.
    monkeypatch.setattr(auth, "STATE_DIR", state)
    monkeypatch.setattr(auth, "_SESSIONS_FILE", state / ".sessions.json")
    monkeypatch.setattr(auth, "_LOGIN_ATTEMPTS_FILE", state / ".login_attempts.json")
    monkeypatch.setattr(auth, "is_password_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "are_passkeys_enabled", lambda: False)
    monkeypatch.setattr(auth, "is_oidc_auth_enabled", lambda: False)
    monkeypatch.delenv("HERMES_WEBUI_TRUSTED_AUTH_HEADER", raising=False)
    auth._sessions.clear()
    auth._login_attempts.clear()

    # Profiles live under an isolated Hermes home.
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    profiles._invalidate_root_profile_cache()

    httpd = server.QuietHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"port": httpd.server_address[1], "state": state, "users": users}
    finally:
        httpd.shutdown()
        httpd.server_close()
        auth._sessions.clear()
        auth._login_attempts.clear()
        profiles._invalidate_root_profile_cache()


@pytest.fixture
def client(gfit_server):
    return Client(gfit_server["port"])


def test_directory_login_turns_the_auth_gate_on(client):
    status, body, _ = client.request("GET", "/api/sessions")
    assert status == 401
    status, body, _ = client.request("GET", "/api/auth/status")
    assert status == 200
    assert body["auth_enabled"] is True
    assert body["logged_in"] is False


def test_login_page_has_username_and_password_fields(client):
    status, html, _ = client.request("GET", "/login")
    assert status == 200
    assert 'id="username"' in html
    assert 'autocomplete="username"' in html
    assert '<input type="password" id="pw"' in html
    assert "employee ID" in html
    assert 'placeholder="Employee ID"' in html


@pytest.mark.parametrize("username", [MEMBER, f"GFIT\\{MEMBER}", f"{MEMBER}@gfit.co.th", f"  {MEMBER.upper()} "])
def test_correct_password_and_existing_profile_lands_in_own_profile(client, username):
    status, body, _ = client.login(username, PASSWORD)
    assert status == 200, body
    assert body["ok"] is True

    status, body, _ = client.request("GET", "/api/profile/active")
    assert status == 200
    assert body["name"] == MEMBER

    status, body, _ = client.request("GET", "/api/auth/status")
    assert body["logged_in"] is True
    assert body["auth_type"] == "directory"
    assert body["user"] == MEMBER
    assert body["bound_profile"] == MEMBER


def test_login_sets_session_and_profile_cookies(client):
    status, _, set_cookies = client.login(MEMBER, PASSWORD)
    assert status == 200
    names = {http.cookies.SimpleCookie(h).keys().__iter__().__next__() for h in set_cookies}
    assert auth._resolve_cookie_name() in names
    assert any(n != auth._resolve_cookie_name() for n in names), "profile cookie must be set"
    assert all("HttpOnly" in h for h in set_cookies)


def test_bound_profile_wins_over_a_session_without_profile_cookie(client):
    client.login(MEMBER, PASSWORD)
    # Drop the profile cookie: the request must still run in the bound Profile.
    session_cookie = auth._resolve_cookie_name()
    client.cookies = {session_cookie: client.cookies[session_cookie]}
    status, body, _ = client.request("GET", "/api/profile/active")
    assert status == 200
    assert body["name"] == MEMBER


@pytest.mark.parametrize(
    "username,password",
    [
        (MEMBER, WRONG_PASSWORD),
        ("999999", PASSWORD),          # unknown user
        ("default", PASSWORD),         # not a valid User name
        ("../etc", PASSWORD),
        ("", PASSWORD),
        (MEMBER, ""),
    ],
)
def test_wrong_password_or_unknown_user_is_401_with_one_message(client, username, password):
    status, body, set_cookies = client.login(username, password)
    assert status == 401
    assert body["error"] == INCORRECT
    assert not any(h.startswith(auth._resolve_cookie_name() + "=") and "Max-Age=0" not in h for h in set_cookies)
    status, _, _ = client.request("GET", "/api/sessions")
    assert status == 401


def test_correct_password_but_no_profile_is_refused_with_contact_admin(client):
    status, body, _ = client.login(NO_PROFILE_USER, PASSWORD)
    assert status == 403
    assert "contact your team's Admin" in body["error"]
    assert body["error"] != INCORRECT
    status, _, _ = client.request("GET", "/api/sessions")
    assert status == 401


def test_wrong_password_attempts_are_rate_limited_per_ip(client):
    for _ in range(auth._LOGIN_MAX_ATTEMPTS):
        status, _, _ = client.login(MEMBER, WRONG_PASSWORD)
        assert status == 401
    status, body, _ = client.login(MEMBER, PASSWORD)
    assert status == 429
    assert "Too many attempts" in body["error"]


def test_successful_login_clears_failed_attempts(client):
    for _ in range(auth._LOGIN_MAX_ATTEMPTS - 1):
        client.login(MEMBER, WRONG_PASSWORD)
    status, _, _ = client.login(MEMBER, PASSWORD)
    assert status == 200
    for _ in range(auth._LOGIN_MAX_ATTEMPTS - 1):
        status, _, _ = client.login(MEMBER, WRONG_PASSWORD)
        assert status == 401


def test_password_is_never_logged_or_stored(client, gfit_server, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    client.login(MEMBER, WRONG_PASSWORD)
    client.login(NO_PROFILE_USER, PASSWORD)
    client.login(MEMBER, PASSWORD)
    client.request("GET", "/api/profile/active")

    out = capsys.readouterr()
    for secret in (PASSWORD, WRONG_PASSWORD):
        assert secret not in caplog.text
        assert secret not in out.out
        assert secret not in out.err
        for path in gfit_server["state"].rglob("*"):
            if path.is_file():
                assert secret not in path.read_text(encoding="utf-8", errors="replace"), path

