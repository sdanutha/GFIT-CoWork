"""GFIT-CoWork: one answer to "which Profile does this request run in?" -- the Admission.

- With a Directory session the request runs in its Admission's Profile, the
  User's own, whatever cookie the browser sends. Nobody switches Profile:
  there is no switch route (ADR 0006).
- With login turned off (no Directory session) the authenticated
  ``hermes_profile`` cookie picks it, as Upstream did.
- A Directory session with no recorded Admission, and a request with neither
  cookie nor Admission, have none (the process's Profile).

Table tests on :func:`api.access.profile_for_request` (no server), and HTTP
tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

from api.access import ROLE_USER, Admitted, profile_for_request
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"


@pytest.mark.parametrize("admission,directory_session,cookie,expected", [
    (Admitted(ROLE_USER, ALICE), True, BOB, ALICE),
    (Admitted(ROLE_USER, ALICE), True, None, ALICE),
    (None, True, ALICE, None),  # a Directory session with no Admission: none
    (None, False, ALICE, ALICE),  # login off: the cookie
    (None, False, None, None),  # login off, no cookie; or a worker thread
])
def test_the_requests_profile(admission, directory_session, cookie, expected):
    assert profile_for_request(admission, directory_session=directory_session, cookie_profile=cookie) == expected


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB]) as s:
        yield s


def _active(client) -> str:
    status, body, _ = client.get("/api/profile/active")
    assert status == 200, body
    return body["name"]


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


# ── Nobody switches Profile ─────────────────────────────────────────────────

def test_there_is_no_profile_switch_route(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/profile/switch", {"name": BOB})

    assert status == 403, body
    assert _active(alice) == ALICE


def test_a_users_profile_list_is_their_own_profile_alone(srv):
    status, body, _ = srv.logged_in(ALICE).get("/api/profiles")

    assert status == 200, body
    assert body["single_profile_mode"] is True
    assert [p["name"] for p in body["profiles"]] == [ALICE]
    assert "may_switch_profile" not in body
