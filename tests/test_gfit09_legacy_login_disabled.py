"""GFIT-CoWork ticket 09: the Upstream login methods are off (ADR 0004).

The Directory login is the only way in. A single shared password and passkeys
refuse, even when configured. Their
configuration still turns the auth gate on, so a Deployment that set a password
but no Directory is locked rather than open (fail closed).
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

import api.auth as auth
from tests._gfit_server import PASSWORD, gfit_server as _gfit_server

MEMBER = "521740"
LEGACY_PASSWORD = "the-old-shared-password"

LEGACY_ENV = {
    "HERMES_WEBUI_PASSWORD": LEGACY_PASSWORD,
    "HERMES_WEBUI_PASSKEY": "1",
    "HERMES_WEBUI_TRUSTED_AUTH_HEADER": "X-Remote-User",
}


@pytest.fixture(params=["memory", ""], ids=["directory-on", "directory-off"])
def srv(request, monkeypatch, tmp_path):
    with _gfit_server(
        monkeypatch, tmp_path, users={MEMBER: "Member"}, profile_names=[MEMBER],
        directory=request.param, legacy_env=LEGACY_ENV,
    ) as s:
        yield s


def _locked(client):
    status, _, _ = client.get("/api/sessions")
    return status == 401


def test_the_auth_gate_stays_on(srv):
    client = srv.client()
    assert _locked(client)
    status, body, _ = client.get("/api/auth/status")
    assert body["auth_enabled"] is True
    assert body["logged_in"] is False


@pytest.mark.parametrize("body", [
    {"password": LEGACY_PASSWORD},
    {"username": "admin", "password": LEGACY_PASSWORD},
    {"username": MEMBER, "password": LEGACY_PASSWORD},
])
def test_the_shared_password_does_not_open_a_way_in(srv, body):
    client = srv.client()
    status, payload, _ = client.post("/api/auth/login", body)
    assert status == 401, payload
    assert _locked(client)


@pytest.mark.parametrize("path", [
    "/api/auth/passkey/options",
    "/api/auth/passkey/login",
    "/api/auth/passkey/register/options",
    "/api/auth/passkey/register",
])
def test_passkey_endpoints_refuse(srv, path):
    client = srv.client()
    status, payload, _ = client.post(path, {})
    assert status in (401, 403, 404), payload
    assert _locked(client)


def test_a_trusted_header_creates_no_session(srv):
    client = srv.client()
    status, _, set_cookies = client.get("/api/sessions", headers={"X-Remote-User": MEMBER})
    assert status == 401
    assert not any(h.startswith(auth._resolve_cookie_name() + "=") for h in set_cookies)


def test_a_legacy_session_is_not_honoured(srv):
    client = srv.client()
    client.cookies[auth._resolve_cookie_name()] = auth.create_session()
    assert _locked(client)


def test_login_page_offers_only_the_directory_form(srv):
    status, html, _ = srv.client().get("/login")
    assert status == 200
    assert 'id="username"' in html
    assert 'id="passkey-login"' not in html


def test_auth_status_reports_the_legacy_methods_off(srv):
    status, body, _ = srv.client().get("/api/auth/status")
    assert body.get("password_auth_enabled") is False
    assert body.get("passkeys_enabled", False) is False
    assert not body.get("trusted_auth_enabled")


def test_the_directory_still_lets_a_member_in(srv):
    if not srv.directory:
        pytest.skip("Directory login is off: nobody can log in")
    client = srv.client()
    status, payload, _ = client.login(MEMBER, PASSWORD)
    assert status == 200, payload
    assert not _locked(client)
