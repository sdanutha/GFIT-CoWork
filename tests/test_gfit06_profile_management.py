"""GFIT-CoWork ticket 06: the Admin manages Profiles.

The Admin creates a Profile named after an employee ID with a display name,
sees every Profile as "name (ID)" with its status and last login, and can
disable, re-enable and delete it. Display name and status live in the
GFIT-CoWork Profile roster, not in the Hermes Profile config.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ADMIN = "521740"
MEMBER = "600001"
NEWCOMER = "700001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ADMIN: "Admin One", MEMBER: "Member One", NEWCOMER: "Somsri Jaidee"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[MEMBER], admins=ADMIN) as s:
        yield s


@pytest.fixture
def admin(srv):
    return srv.logged_in(ADMIN)


def _row(client, name):
    status, body, _ = client.get("/api/profiles")
    assert status == 200, body
    rows = {p["name"]: p for p in body["profiles"]}
    return rows.get(name)


# ── create ──────────────────────────────────────────────────────────────────

def test_admin_creates_a_profile_with_a_display_name(srv, admin):
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    assert status == 200, body
    assert srv.profile_home(NEWCOMER).is_dir()

    row = _row(admin, NEWCOMER)
    assert row["display_name"] == "Somsri J."
    assert row["label"] == f"Somsri J. ({NEWCOMER})"
    assert row["status"] == "active"
    assert row["last_login"] is None

    # The newcomer can log in straight away.
    srv.logged_in(NEWCOMER)


def test_a_profile_without_a_display_name_is_shown_by_its_id(admin):
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER})
    assert status == 200, body
    row = _row(admin, NEWCOMER)
    assert row["display_name"] == ""
    assert row["label"] == NEWCOMER


def test_display_name_is_kept_out_of_the_hermes_profile(srv, admin):
    admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri Unique-Marker"})
    for path in srv.profile_home(NEWCOMER).rglob("*"):
        if path.is_file():
            assert "Unique-Marker" not in path.read_text(encoding="utf-8", errors="replace"), path


@pytest.mark.parametrize("name", ["Bad Name!", "../etc", "-leading-dash", "x" * 65])
def test_a_name_that_breaks_the_profile_name_rules_is_refused(srv, admin, name):
    status, body, _ = admin.post("/api/profile/create", {"name": name, "display_name": "Someone"})
    assert status == 400, body
    assert "name" in body["error"].lower()
    assert _row(admin, name) is None


def test_the_built_in_default_profile_cannot_be_created(admin):
    status, body, _ = admin.post("/api/profile/create", {"name": "default", "display_name": "Someone"})
    assert status == 400, body
    assert "default" in body["error"]
    assert "display_name" not in _row(admin, "default")


def test_creating_an_existing_profile_is_refused(admin):
    status, body, _ = admin.post("/api/profile/create", {"name": MEMBER})
    assert status == 400, body


def _fail(*_args, **_kwargs):
    raise OSError("disk full")


def _cannot_log_in(srv, name):
    status, _, _ = srv.client().login(name)
    return status == 403


def test_a_failed_roster_write_on_create_leaves_no_profile(srv, admin, monkeypatch):
    import api.roster as roster

    with monkeypatch.context() as patch:
        patch.setattr(roster, "_save", _fail)
        status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    assert status == 500, body
    assert "not created" in body["error"]
    assert not srv.profile_home(NEWCOMER).exists()
    assert _cannot_log_in(srv, NEWCOMER)


def test_an_unreadable_roster_refuses_create(srv, admin):
    (srv.state / "gfit_roster.json").write_text("{not json")
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER})
    assert status == 500, body
    assert not srv.profile_home(NEWCOMER).exists()
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


def test_a_failed_hermes_create_leaves_no_roster_record(srv, admin, monkeypatch):
    import api.profiles as profiles

    def refuse(*_args, **_kwargs):
        raise RuntimeError("hermes said no")

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "create_profile_api", refuse)
        status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    assert status == 400, body
    assert "hermes said no" in body["error"]
    assert _row(admin, NEWCOMER) is None
    assert _cannot_log_in(srv, NEWCOMER)
    # No record is left behind: creating it again works and starts fresh.
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER})
    assert status == 200, body
    assert _row(admin, NEWCOMER)["label"] == NEWCOMER


def test_a_create_that_fails_part_way_leaves_the_profile_disabled(srv, admin, monkeypatch):
    # The Hermes Profile directory is made, then a later step fails: shut, not open.
    import api.profiles as profiles

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_write_endpoint_to_config", _fail)
        status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    assert status == 500, body
    assert "disabled" in body["error"]
    assert _row(admin, NEWCOMER)["status"] == "disabled"
    assert _cannot_log_in(srv, NEWCOMER)


def test_creating_an_existing_disabled_profile_leaves_it_disabled(admin):
    admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    admin.post("/api/profile/disable", {"name": NEWCOMER})
    status, body, _ = admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Someone Else"})
    assert status == 400, body
    row = _row(admin, NEWCOMER)
    assert row["status"] == "disabled"
    assert row["label"] == f"Somsri J. ({NEWCOMER})"


def test_member_list_is_labelled_with_the_roster(srv, admin):
    member = srv.logged_in(MEMBER)
    row = _row(admin, MEMBER)
    # Created outside the roster: active, with the login recorded and the
    # display name taken from the Directory on login (ticket 07).
    assert row["label"] == f"Member One ({MEMBER})"
    assert row["status"] == "active"
    assert isinstance(row["last_login"], (int, float))
    assert member.get("/api/profile/active")[0] == 200


# ── disable / enable ────────────────────────────────────────────────────────

def test_disabling_ends_sessions_and_refuses_login_but_keeps_data(srv, admin):
    member = srv.logged_in(MEMBER)
    assert member.get("/api/sessions")[0] == 200

    status, body, _ = admin.post("/api/profile/disable", {"name": MEMBER})
    assert status == 200, body
    assert body["profile"]["status"] == "disabled"

    status, _, _ = member.get("/api/sessions")
    assert status == 401

    status, body, _ = srv.client().login(MEMBER)
    assert status == 403
    assert "suspended" in body["error"].lower()

    assert srv.profile_home(MEMBER).is_dir()
    assert _row(admin, MEMBER)["status"] == "disabled"


def test_re_enabling_lets_the_person_log_in_again(srv, admin):
    admin.post("/api/profile/disable", {"name": MEMBER})
    status, body, _ = admin.post("/api/profile/enable", {"name": MEMBER})
    assert status == 200, body
    assert body["profile"]["status"] == "active"
    srv.logged_in(MEMBER)
    assert _row(admin, MEMBER)["status"] == "active"


def test_disabling_keeps_the_display_name(admin):
    admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    admin.post("/api/profile/disable", {"name": NEWCOMER})
    row = _row(admin, NEWCOMER)
    assert row["label"] == f"Somsri J. ({NEWCOMER})"
    assert row["status"] == "disabled"


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_disable_or_enable_an_unknown_profile_is_404(admin, action):
    status, _, _ = admin.post(f"/api/profile/{action}", {"name": "999999"})
    assert status == 404


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_the_default_profile_cannot_be_disabled(admin, action):
    status, _, _ = admin.post(f"/api/profile/{action}", {"name": "default"})
    assert status == 400


def test_an_admin_id_cannot_be_disabled(srv, admin):
    # A Profile named after an Admin would not shut the Admin out (they log in to default).
    srv.profile_home(ADMIN).mkdir()
    status, body, _ = admin.post("/api/profile/disable", {"name": ADMIN})
    assert status == 400
    assert "Admin" in body["error"]


def test_an_unreadable_roster_fails_closed(srv, admin):
    member = srv.logged_in(MEMBER)
    (srv.state / "gfit_roster.json").write_text("{not json")

    assert member.get("/api/sessions")[0] == 401
    status, _, _ = srv.client().login(MEMBER)
    assert status == 403
    assert _row(admin, MEMBER)["status"] == "disabled"
    # A write never overwrites the unreadable file.
    status, _, _ = admin.post("/api/profile/enable", {"name": MEMBER})
    assert status >= 500
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_an_unreadable_roster_leaves_the_profile_unchanged(srv, admin, action):
    (srv.state / "gfit_roster.json").write_text("{not json")
    status, body, _ = admin.post(f"/api/profile/{action}", {"name": MEMBER})
    assert status == 500, body
    assert f"'{MEMBER}' was not changed" in body["error"]
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


def test_a_failed_roster_write_leaves_the_profile_unchanged(srv, admin, monkeypatch):
    member = srv.logged_in(MEMBER)
    import api.roster as roster

    with monkeypatch.context() as patch:
        patch.setattr(roster, "_save", _fail)
        status, body, _ = admin.post("/api/profile/disable", {"name": MEMBER})
    assert status == 500, body
    assert f"'{MEMBER}' was not changed" in body["error"]
    assert member.get("/api/sessions")[0] == 200
    assert _row(admin, MEMBER)["status"] == "active"


# ── delete ──────────────────────────────────────────────────────────────────

def test_delete_without_confirmation_is_refused(srv, admin):
    status, body, _ = admin.post("/api/profile/delete", {"name": MEMBER})
    assert status == 400
    assert "confirm" in body["error"].lower()
    status, _, _ = admin.post("/api/profile/delete", {"name": MEMBER, "confirm": "someone-else"})
    assert status == 400
    assert srv.profile_home(MEMBER).is_dir()


def test_delete_removes_the_profile_and_its_roster_record(srv, admin):
    admin.post("/api/profile/create", {"name": NEWCOMER, "display_name": "Somsri J."})
    admin.post("/api/profile/disable", {"name": NEWCOMER})

    status, body, _ = admin.post("/api/profile/delete", {"name": NEWCOMER, "confirm": NEWCOMER})
    assert status == 200, body
    assert not srv.profile_home(NEWCOMER).exists()
    assert _row(admin, NEWCOMER) is None

    # A Profile created again under that ID starts fresh: no old name, active.
    admin.post("/api/profile/create", {"name": NEWCOMER})
    row = _row(admin, NEWCOMER)
    assert row["label"] == NEWCOMER
    assert row["status"] == "active"


def test_deleting_a_profile_ends_its_sessions(srv, admin):
    member = srv.logged_in(MEMBER)
    status, body, _ = admin.post("/api/profile/delete", {"name": MEMBER, "confirm": MEMBER})
    assert status == 200, body
    assert member.get("/api/sessions")[0] == 401
    status, _, _ = srv.client().login(MEMBER)
    assert status == 403


# ── Members are refused ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path,body", [
    ("/api/profile/create", {"name": NEWCOMER, "display_name": "x"}),
    ("/api/profile/disable", {"name": MEMBER}),
    ("/api/profile/enable", {"name": MEMBER}),
    ("/api/profile/delete", {"name": MEMBER, "confirm": MEMBER}),
])
def test_a_member_gets_403_from_profile_management(srv, path, body):
    member = srv.logged_in(MEMBER)
    status, _, _ = member.post(path, body)
    assert status == 403
    assert srv.profile_home(MEMBER).is_dir()
    assert not srv.profile_home(NEWCOMER).exists()
