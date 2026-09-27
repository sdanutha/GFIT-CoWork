"""GFIT-CoWork ticket 07: the real name next to the employee ID, and Sign Out.

Every successful login writes the display name from the Directory into the
Profile roster. Auth status sends the display name, the employee ID and the
"name (ID)" label to the frontend. Sign Out ends this session only.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ADMIN = "521740"
MEMBER = "600001"
NEWCOMER = "700001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ADMIN: "Admin One", MEMBER: "สมชาย ใจดี", NEWCOMER: ""}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[MEMBER], admins=ADMIN) as s:
        yield s


def _set_directory_name(srv, employee_id, display_name):
    users = json.loads(Path(srv.users).read_text())
    users[employee_id]["display_name"] = display_name
    Path(srv.users).write_text(json.dumps(users))


def _status(client):
    status, body, _ = client.get("/api/auth/status")
    assert status == 200, body
    return body


def _row(client, name):
    status, body, _ = client.get("/api/profiles")
    assert status == 200, body
    return {p["name"]: p for p in body["profiles"]}.get(name)


# ── display name ────────────────────────────────────────────────────────────

def test_auth_status_sends_the_members_name_and_employee_id(srv):
    body = _status(srv.logged_in(MEMBER))
    assert body["logged_in"] is True
    assert body["user"] == MEMBER
    assert body["display_name"] == "สมชาย ใจดี"
    assert body["label"] == f"สมชาย ใจดี ({MEMBER})"


def test_login_writes_the_directory_name_into_the_roster(srv):
    srv.logged_in(MEMBER)
    row = _row(srv.logged_in(ADMIN), MEMBER)
    assert row["display_name"] == "สมชาย ใจดี"
    assert row["label"] == f"สมชาย ใจดี ({MEMBER})"


def test_the_display_name_updates_from_the_directory_on_every_login(srv):
    srv.logged_in(MEMBER)
    _set_directory_name(srv, MEMBER, "สมชาย ใจดีมาก")

    body = _status(srv.logged_in(MEMBER))
    assert body["display_name"] == "สมชาย ใจดีมาก"
    assert _row(srv.logged_in(ADMIN), MEMBER)["display_name"] == "สมชาย ใจดีมาก"


def test_without_a_directory_name_the_admins_typed_name_is_kept(srv):
    admin = srv.logged_in(ADMIN)
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    assert status == 200, body

    body = _status(srv.logged_in(NEWCOMER))
    assert body["display_name"] == "Somsri J."
    assert body["label"] == f"Somsri J. ({NEWCOMER})"


def test_without_any_name_the_label_is_the_employee_id(srv):
    admin = srv.logged_in(ADMIN)
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER})
    assert status == 200, body

    body = _status(srv.logged_in(NEWCOMER))
    assert body["display_name"] == ""
    assert body["label"] == NEWCOMER


def test_auth_status_sends_the_admins_name(srv):
    body = _status(srv.logged_in(ADMIN))
    assert body["user"] == ADMIN
    assert body["role"] == "admin"
    assert body["display_name"] == "Admin One"
    assert body["label"] == f"Admin One ({ADMIN})"


def test_logged_out_auth_status_carries_no_identity(srv):
    body = _status(srv.client())
    assert body["logged_in"] is False
    assert "display_name" not in body
    assert "label" not in body


# ── Sign Out ────────────────────────────────────────────────────────────────

def test_sign_out_ends_the_session_and_clears_the_cookie(srv):
    from api.auth import _resolve_cookie_name

    member = srv.logged_in(MEMBER)
    session = member.cookies[_resolve_cookie_name()]

    status, body, set_cookies = member.post("/api/auth/logout")
    assert status == 200, body
    assert any(c.startswith(f"{_resolve_cookie_name()}=") and "Max-Age=0" in c for c in set_cookies)
    assert _resolve_cookie_name() not in member.cookies
    assert _status(member)["logged_in"] is False

    # The old cookie no longer works, even if the browser kept it.
    replay = srv.client()
    replay.cookies[_resolve_cookie_name()] = session
    assert _status(replay)["logged_in"] is False
    status, _, _ = replay.get("/api/sessions")
    assert status == 401


def test_sign_out_ends_this_session_only(srv):
    laptop = srv.logged_in(MEMBER)
    phone = srv.logged_in(MEMBER)

    status, body, _ = laptop.post("/api/auth/logout")
    assert status == 200, body
    assert _status(laptop)["logged_in"] is False
    assert _status(phone)["logged_in"] is True


def test_a_member_can_log_in_again_after_signing_out(srv):
    member = srv.logged_in(MEMBER)
    member.post("/api/auth/logout")
    status, body, _ = member.login(MEMBER)
    assert status == 200, body
    assert _status(member)["logged_in"] is True
