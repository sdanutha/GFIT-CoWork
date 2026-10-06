"""GFIT-CoWork: one answer to "which Profile does this request run in?" -- the Admission.

- A request runs in its Admission's Profile, the User's own, whatever the
  browser sends (a ``hermes_profile`` cookie, from before ADR 0006, is
  ignored). Nobody switches Profile: there is no switch route.
- A request with no Admission (a public route, a worker thread) runs in no
  Profile of its own: the process's.
- There is no mode with login turned off, so nothing else picks the Profile.

HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

from api import access, profiles
from api.access import ROLE_USER, Admitted
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"


class _Handler:
    headers = {"Cookie": f"hermes_profile={BOB}"}


@pytest.mark.parametrize("admission,expected", [
    (Admitted(ROLE_USER, ALICE), ALICE),
    (None, None),
])
def test_the_requests_profile_is_its_admissions(monkeypatch, admission, expected):
    profiles.clear_request_profile()
    monkeypatch.setattr(access._request, "admission", admission, raising=False)
    try:
        access.settle_request(_Handler())
        assert getattr(profiles._tls, "profile", None) == expected
    finally:
        profiles.clear_request_profile()


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


# ── Nobody switches Profile ─────────────────────────────────────────────────

def test_there_is_no_profile_switch_route(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/profile/switch", {"name": BOB})

    assert status == 403, body
    assert _active(alice) == ALICE


def test_a_users_profile_list_is_their_own_profile_alone(srv):
    status, body, _ = srv.logged_in(ALICE).get("/api/profiles")

    assert status == 200, body
    assert [p["name"] for p in body["profiles"]] == [ALICE]
    assert "may_switch_profile" not in body
