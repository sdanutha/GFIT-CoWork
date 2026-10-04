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


# ── Ticket 02: switching Profile is a login-off feature only ────────────────

@pytest.fixture
def switch_effects(monkeypatch):
    """Records the switch route's side effects (gateway watcher restart, models cache clear)."""
    import api.config as config
    import api.gateway_watcher as gateway_watcher

    calls = []
    monkeypatch.setattr(gateway_watcher, "restart_watcher_for_profile", lambda name: calls.append(("watcher", name)))
    monkeypatch.setattr(config, "invalidate_models_cache", lambda **kw: calls.append(("models", None)))
    return calls


@pytest.mark.parametrize("who", [ADMIN, ALICE])
def test_a_directory_session_cannot_switch_profile(srv, switch_effects, who):
    client = srv.logged_in(who)
    before = _active(client)

    status, body, _ = client.post("/api/profile/switch", {"name": BOB})

    assert status == 403, body
    assert _active(client) == before
    assert switch_effects == []


@pytest.mark.parametrize("who", [ADMIN, ALICE])
def test_the_profile_list_says_a_directory_session_may_not_switch(srv, who):
    status, body, _ = srv.logged_in(who).get("/api/profiles")

    assert status == 200, body
    assert body["may_switch_profile"] is False


def test_the_admin_keeps_the_all_profiles_views(srv):
    status, body, _ = srv.logged_in(ADMIN).get("/api/profiles")

    assert body["single_profile_mode"] is False
    assert {ALICE, BOB} <= {p["name"] for p in body["profiles"]}


def test_with_login_off_the_switch_still_switches(monkeypatch, tmp_path, switch_effects):
    with _gfit_server(monkeypatch, tmp_path, users={}, profile_names=[ALICE], directory="") as srv:
        client = srv.client()
        status, body, _ = client.get("/api/profiles")
        assert body["may_switch_profile"] is True

        status, body, _ = client.post("/api/profile/switch", {"name": ALICE})

        assert status == 200, body
        assert _active(client) == ALICE
        assert ("watcher", ALICE) in switch_effects and ("models", None) in switch_effects


def test_the_profiles_panel_offers_no_switch_when_the_caller_may_not():
    from pathlib import Path

    panels = (Path(__file__).resolve().parent.parent / "static" / "panels.js").read_text(encoding="utf-8")
    start = panels.index("function _setProfileHeaderButtons(")
    block = panels[start:panels.index("\n}\n", start)]
    assert "may_switch_profile" in block
