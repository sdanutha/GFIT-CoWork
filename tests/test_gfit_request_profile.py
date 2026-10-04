"""GFIT-CoWork: one answer to "which Profile does this request run in?" -- the Admission.

- With a Directory session the request runs in its Admission's Profile: a
  User's own, ``default`` for the Admin. Whatever cookie the browser sends.
- With login turned off (no Directory session) the authenticated
  ``hermes_profile`` cookie picks it, as Upstream did.
- A Directory session with no recorded Admission, and a request with neither
  cookie nor Admission, have none (the process's Profile).

Table tests on :func:`api.access.profile_for_request` (no server), and HTTP
tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

from api.access import ROLE_ADMIN, ROLE_MEMBER, Admitted, profile_for_request
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"


@pytest.mark.parametrize("admission,directory_session,cookie,expected", [
    (Admitted(ROLE_MEMBER, ALICE), True, BOB, ALICE),
    (Admitted(ROLE_MEMBER, ALICE), True, None, ALICE),
    (Admitted(ROLE_ADMIN, "default"), True, ALICE, "default"),
    (Admitted(ROLE_ADMIN, "default"), True, None, "default"),
    (None, True, ALICE, None),  # a Directory session with no Admission: none
    (None, False, ALICE, ALICE),  # login off: the cookie
    (None, False, None, None),  # login off, no cookie; or a worker thread
])
def test_the_requests_profile(admission, directory_session, cookie, expected):
    assert profile_for_request(admission, directory_session=directory_session, cookie_profile=cookie) == expected


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


def _active(client) -> str:
    status, body, _ = client.get("/api/profile/active")
    assert status == 200, body
    return body["name"]


def test_an_admin_with_a_stale_profile_cookie_runs_in_default(srv):
    admin = srv.logged_in(ADMIN)
    admin.cookies["hermes_profile"] = ALICE

    assert _active(admin) == "default"
    assert _active(admin) == "default"


def test_a_user_runs_in_their_own_profile_whatever_cookie_is_sent(srv):
    alice = srv.logged_in(ALICE)
    alice.cookies["hermes_profile"] = BOB

    assert _active(alice) == ALICE


def test_with_login_off_the_cookie_picks_the_profile(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={}, profile_names=[ALICE], directory="") as srv:
        client = srv.client()
        assert _active(client) == "default"
        client.cookies["hermes_profile"] = ALICE
        assert _active(client) == ALICE
