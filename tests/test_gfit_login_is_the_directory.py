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

TRUSTED_HEADER_ENV = {
    "HERMES_WEBUI_TRUSTED_AUTH_HEADER": "X-Remote-User",
    "HERMES_WEBUI_TRUSTED_GROUPS_HEADER": "X-Remote-Groups",
    "HERMES_WEBUI_GROUP_PROFILE_MAP": '{"staff": "%s"}' % MEMBER,
    "HERMES_WEBUI_TRUSTED_AUTH_LOGOUT_URL": "https://sso.example.com/logout",
}
TRUSTED_HEADERS = {"X-Remote-User": MEMBER, "X-Remote-Groups": "staff"}


@pytest.fixture
def server(monkeypatch, tmp_path):
    def start(*, directory="memory", legacy_env=None):
        return _gfit_server(
            monkeypatch, tmp_path, users={MEMBER: "Member"}, profile_names=[MEMBER],
            directory=directory, legacy_env=legacy_env,
        )
    return start


def _session_cookies(set_cookies) -> list[str]:
    return [h for h in set_cookies if h.startswith(auth._resolve_cookie_name() + "=")]


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
