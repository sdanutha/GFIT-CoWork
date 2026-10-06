"""GFIT-CoWork ticket 02: Member login with an employee ID (in-memory Directory).

Every test talks HTTP to a real ``server.Handler`` started in-process (see
``tests/_gfit_server.py``).
"""
from __future__ import annotations

import http.cookies
import logging

import pytest

import api.auth as auth
import api.login as login
from tests._gfit_server import PASSWORD, WRONG_PASSWORD, Client, gfit_server as _gfit_server

MEMBER = "521740"
NO_PROFILE_USER = "671278"

INCORRECT = "incorrect username or password"


@pytest.fixture
def gfit_server(monkeypatch, tmp_path):
    users = {MEMBER: "Somchai Jaidee", NO_PROFILE_USER: "No Profile"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[MEMBER]) as srv:
        yield {"port": srv.port, "state": srv.state, "users": srv.users}


@pytest.fixture
def client(gfit_server):
    return Client(gfit_server["port"])


def test_a_request_without_a_login_is_refused(client):
    status, body, _ = client.request("GET", "/api/sessions")
    assert status == 401
    status, body, _ = client.request("GET", "/api/auth/status")
    assert status == 200
    assert body == {"logged_in": False}


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


def test_login_sets_the_session_cookie_only(client):
    # The Profile comes from the Admission; there is no Profile cookie (ADR 0006).
    status, _, set_cookies = client.login(MEMBER, PASSWORD)
    assert status == 200
    names = {next(iter(http.cookies.SimpleCookie(h).keys())) for h in set_cookies}
    assert names == {auth._resolve_cookie_name()}
    assert all("HttpOnly" in h for h in set_cookies)


def test_the_bound_profile_needs_only_the_session_cookie(client):
    client.login(MEMBER, PASSWORD)
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
    assert "contact your team's Operator" in body["error"]
    assert body["error"] != INCORRECT
    status, _, _ = client.request("GET", "/api/sessions")
    assert status == 401


def test_wrong_password_attempts_are_rate_limited_per_ip(client):
    for _ in range(login._LOGIN_MAX_ATTEMPTS):
        status, _, _ = client.login(MEMBER, WRONG_PASSWORD)
        assert status == 401
    status, body, _ = client.login(MEMBER, PASSWORD)
    assert status == 429
    assert "Too many attempts" in body["error"]


def test_successful_login_clears_failed_attempts(client):
    for _ in range(login._LOGIN_MAX_ATTEMPTS - 1):
        client.login(MEMBER, WRONG_PASSWORD)
    status, _, _ = client.login(MEMBER, PASSWORD)
    assert status == 200
    for _ in range(login._LOGIN_MAX_ATTEMPTS - 1):
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

