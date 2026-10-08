"""GFIT-CoWork: the Operator manages the Profile lifecycle from the command line (ADR 0006).

The Operator creates a Profile named after an employee ID with a display name,
lists every Profile with its status and last login, and can disable,
re-enable and delete it. Display name and status live in the GFIT-CoWork
Profile roster, not in the Hermes Profile config. Every failure leaves the
Profile shut, never half-open.

Ported from ``test_gfit06_profile_management.py``, which drove the same
roster rules through the Admin's web routes. The command line is driven
in-process (``operator_cli.run``) next to an in-process server
(``tests/_gfit_server.py``), so logins show the effect.
"""
from __future__ import annotations

import pytest

from api import operator_cli, profiles, roster, roster_watch
from tests._gfit_server import gfit_server as _gfit_server

USER = "600001"
NEWCOMER = "700001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {USER: "User One", NEWCOMER: "Somsri Jaidee"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[USER]) as s:
        yield s


def cli(*argv) -> int:
    return operator_cli.run(list(argv))


def _records(srv) -> dict:
    import json

    path = srv.state / "gfit_roster.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _exists(name) -> bool:
    return profiles.named_profile_exists(name)


def _cannot_log_in(srv, name) -> bool:
    return srv.client().login(name)[0] == 403


# ── create ──────────────────────────────────────────────────────────────────

def test_create_with_a_display_name(srv, capsys):
    assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 0
    assert srv.profile_home(NEWCOMER).is_dir()

    view = roster.view(NEWCOMER)
    assert (view["display_name"], view["label"]) == ("Somsri J.", f"Somsri J. ({NEWCOMER})")
    assert (view["status"], view["last_login"]) == ("active", None)
    srv.logged_in(NEWCOMER)


def test_a_profile_without_a_display_name_is_shown_by_its_id(srv):
    assert cli("create", NEWCOMER) == 0
    view = roster.view(NEWCOMER)
    assert (view["display_name"], view["label"]) == ("", NEWCOMER)


def test_display_name_is_kept_out_of_the_hermes_profile(srv):
    cli("create", NEWCOMER, "--display-name", "Somsri Unique-Marker")
    for path in srv.profile_home(NEWCOMER).rglob("*"):
        if path.is_file():
            assert "Unique-Marker" not in path.read_text(encoding="utf-8", errors="replace"), path


@pytest.mark.parametrize("name", ["Bad Name!", "../etc", "-leading-dash", "x" * 65])
def test_a_name_that_breaks_the_profile_name_rules_is_refused(srv, capsys, name):
    assert cli("create", "--display-name", "Someone", "--", name) == 1
    assert "employee ID" in capsys.readouterr().err
    assert not _exists(name)


def test_the_built_in_default_profile_cannot_be_created(srv, capsys):
    assert cli("create", "default", "--display-name", "Someone") == 1
    assert "default" in capsys.readouterr().err
    assert "display_name" not in _records(srv).get("default", {})


def test_a_clone_from_default_copies_only_the_provider_credentials_of_its_env(srv):
    # Ticket 12: the Deployment's .env is shared configuration every User's agent
    # can read through the process env; a clone must not also hand each User a
    # private copy of its other secrets. It keeps the credential names a User's
    # scope masks (provider keys, the Agent's registry, the clone's custom
    # providers), which the User's agent can reach only through their own .env.
    (srv.hermes_home / "config.yaml").write_text(
        "custom_providers:\n- name: team\n  base_url: https://llm.example\n  key_env: TEAM_LLM_KEY\n",
        encoding="utf-8",
    )
    (srv.hermes_home / ".env").write_text(
        "# Deployment settings\n"
        "OPENROUTER_API_KEY=sk-or-deployment\n"
        "export ANTHROPIC_API_KEY='sk-ant-deployment'\n"
        "TEAM_LLM_KEY=team-key\n"
        "TAVILY_API_KEY=tvly-deployment-secret\n"
        "DATABASE_URL=postgres://ops:hunter2@db/app\n",
        encoding="utf-8",
    )

    assert cli("create", NEWCOMER, "--clone-from", "default") == 0

    cloned = (srv.profile_home(NEWCOMER) / ".env").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or-deployment" in cloned
    assert "export ANTHROPIC_API_KEY='sk-ant-deployment'" in cloned
    assert "TEAM_LLM_KEY=team-key" in cloned
    assert "tvly-deployment-secret" not in cloned
    assert "hunter2" not in cloned
    assert roster.view(NEWCOMER)["status"] == "active"


@pytest.mark.parametrize("clone_from", ["Bad Name!", "../etc", "-leading-dash", "x" * 65])
def test_a_clone_from_name_that_breaks_the_profile_name_rule_is_refused(srv, capsys, clone_from):
    assert cli("create", NEWCOMER, "--display-name", "Somsri J.", f"--clone-from={clone_from}") == 1
    assert "employee ID" in capsys.readouterr().err
    assert not srv.profile_home(NEWCOMER).exists()
    # No record is left behind: creating it again starts fresh.
    assert cli("create", NEWCOMER) == 0
    assert roster.view(NEWCOMER)["label"] == NEWCOMER


def _fail(*_args, **_kwargs):
    raise OSError("disk full")


def test_a_failed_roster_write_on_create_leaves_no_profile(srv, monkeypatch, capsys):
    with monkeypatch.context() as patch:
        patch.setattr(roster, "_save", _fail)
        assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 1
    assert "not created" in capsys.readouterr().err
    assert not srv.profile_home(NEWCOMER).exists()
    assert _cannot_log_in(srv, NEWCOMER)


def test_an_unreadable_roster_refuses_create(srv):
    (srv.state / "gfit_roster.json").write_text("{not json")
    assert cli("create", NEWCOMER) == 1
    assert not srv.profile_home(NEWCOMER).exists()
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


def test_a_failed_hermes_create_leaves_no_roster_record(srv, monkeypatch, capsys):
    def refuse(*_args, **_kwargs):
        raise RuntimeError("hermes said no")

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "create_profile_api", refuse)
        assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 1
    assert "hermes said no" in capsys.readouterr().err
    assert NEWCOMER not in _records(srv)
    assert _cannot_log_in(srv, NEWCOMER)
    assert cli("create", NEWCOMER) == 0
    assert roster.view(NEWCOMER)["label"] == NEWCOMER


def test_a_create_that_fails_part_way_leaves_the_profile_disabled(srv, monkeypatch, capsys):
    # The Hermes Profile directory is made, then a later step fails: shut, not open.
    real_create = profiles.create_profile_api

    def half_create(name, **options):
        real_create(name, **options)
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(profiles, "create_profile_api", half_create)
        assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 1
    assert "disabled" in capsys.readouterr().err
    assert roster.view(NEWCOMER)["status"] == "disabled"
    assert _cannot_log_in(srv, NEWCOMER)


def test_a_failed_activation_leaves_the_new_profile_disabled(srv, monkeypatch, capsys):
    # The record is written disabled, then made active once the Hermes Profile exists.
    real_save = roster._save
    saves = []

    def save_once(records):
        saves.append(records)
        if len(saves) > 1:
            raise OSError("disk full")
        real_save(records)

    with monkeypatch.context() as patch:
        patch.setattr(roster, "_save", save_once)
        assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 1
    assert "disabled" in capsys.readouterr().err
    assert srv.profile_home(NEWCOMER).is_dir()
    assert roster.view(NEWCOMER)["status"] == "disabled"
    assert _cannot_log_in(srv, NEWCOMER)


def test_creating_an_existing_disabled_profile_leaves_it_disabled(srv):
    cli("create", NEWCOMER, "--display-name", "Somsri J.")
    cli("disable", NEWCOMER)
    assert cli("create", NEWCOMER, "--display-name", "Someone Else") == 1
    view = roster.view(NEWCOMER)
    assert (view["status"], view["label"]) == ("disabled", f"Somsri J. ({NEWCOMER})")


def test_a_profile_made_outside_the_roster_is_labelled_on_login(srv):
    user = srv.logged_in(USER)
    view = roster.view(USER)
    # Active, with the login recorded and the display name taken from the Directory.
    assert (view["label"], view["status"]) == (f"User One ({USER})", "active")
    assert isinstance(view["last_login"], (int, float))
    assert user.get("/api/profile/active")[0] == 200


# ── disable / enable ────────────────────────────────────────────────────────

def test_disabling_ends_sessions_and_refuses_login_but_keeps_data(srv):
    user = srv.logged_in(USER)
    assert user.get("/api/sessions")[0] == 200

    assert cli("disable", USER) == 0
    roster_watch.check()

    assert user.get("/api/sessions")[0] == 401
    status, body, _ = srv.client().login(USER)
    assert status == 403 and "suspended" in body["error"].lower()
    assert srv.profile_home(USER).is_dir()
    assert roster.view(USER)["status"] == "disabled"


def test_re_enabling_lets_the_person_log_in_again(srv):
    cli("disable", USER)
    assert cli("enable", USER) == 0
    srv.logged_in(USER)
    assert roster.view(USER)["status"] == "active"


def test_disabling_keeps_the_display_name(srv):
    cli("create", NEWCOMER, "--display-name", "Somsri J.")
    cli("disable", NEWCOMER)
    view = roster.view(NEWCOMER)
    assert (view["label"], view["status"]) == (f"Somsri J. ({NEWCOMER})", "disabled")


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_the_default_profile_cannot_be_disabled_or_enabled(srv, action):
    assert cli(action, "default") == 1


def test_an_unreadable_roster_fails_closed(srv):
    user = srv.logged_in(USER)
    (srv.state / "gfit_roster.json").write_text("{not json")

    assert user.get("/api/sessions")[0] == 401
    assert _cannot_log_in(srv, USER)
    assert roster.view(USER)["status"] == "disabled"
    # A write never overwrites the unreadable file.
    assert cli("enable", USER) == 1
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_an_unreadable_roster_leaves_the_profile_unchanged(srv, capsys, action):
    (srv.state / "gfit_roster.json").write_text("{not json")
    assert cli(action, USER) == 1
    assert f"'{USER}' was not changed" in capsys.readouterr().err
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


def test_a_failed_roster_write_leaves_the_profile_unchanged(srv, monkeypatch, capsys):
    user = srv.logged_in(USER)
    with monkeypatch.context() as patch:
        patch.setattr(roster, "_save", _fail)
        assert cli("disable", USER) == 1
    assert f"'{USER}' was not changed" in capsys.readouterr().err
    roster_watch.check()
    assert user.get("/api/sessions")[0] == 200
    assert roster.view(USER)["status"] == "active"


# ── delete ──────────────────────────────────────────────────────────────────

def test_delete_removes_the_profile_and_its_roster_record(srv):
    cli("create", NEWCOMER, "--display-name", "Somsri J.")
    cli("disable", NEWCOMER)

    assert cli("delete", NEWCOMER, "--confirm", NEWCOMER) == 0
    assert not srv.profile_home(NEWCOMER).exists()
    assert NEWCOMER not in _records(srv)

    # A Profile created again under that ID starts fresh: no old name, active.
    cli("create", NEWCOMER)
    view = roster.view(NEWCOMER)
    assert (view["label"], view["status"]) == (NEWCOMER, "active")


def test_a_deleted_profiles_sessions_are_ended(srv):
    user = srv.logged_in(USER)
    cli("disable", USER)
    roster_watch.check()

    assert cli("delete", USER, "--confirm", USER) == 0
    assert user.get("/api/sessions")[0] == 401
    assert _cannot_log_in(srv, USER)


def test_a_deletion_that_cannot_finish_leaves_the_profile_disabled(srv, monkeypatch, capsys):
    def busy(*_args, **_kwargs):
        raise RuntimeError("an agent is still running")

    cli("disable", USER)
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "delete_profile_api", busy)
        assert cli("delete", USER, "--confirm", USER) == 1
    assert "an agent is still running" in capsys.readouterr().err
    assert srv.profile_home(USER).is_dir()
    assert roster.view(USER)["status"] == "disabled"
    assert _cannot_log_in(srv, USER)


def test_an_unreadable_roster_refuses_delete(srv):
    (srv.state / "gfit_roster.json").write_text("{not json")
    assert cli("delete", USER, "--confirm", USER) == 1
    assert srv.profile_home(USER).is_dir()
    assert (srv.state / "gfit_roster.json").read_text() == "{not json"


def test_the_default_profile_cannot_be_deleted(srv, capsys):
    assert cli("delete", "default", "--confirm", "default") == 1
    err = capsys.readouterr().err
    assert "default" in err and "create" not in err
