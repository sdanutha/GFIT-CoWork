"""GFIT-CoWork: Admission, the one decision about who may use the Deployment.

From an employee ID the Directory has confirmed, Admission says whether that
person is admitted, with which role and to which Profile, or why they are
refused (ADR 0004). Tested at its interface, with no server: a temporary
Hermes home, a temporary Profile roster and the Admin list env var.
"""
from __future__ import annotations

import pytest

import api.auth as auth
import api.profiles as profiles
import api.roster as roster
from api.access import (
    ADMIN_USERS_ENV,
    REFUSED_NO_PROFILE,
    REFUSED_PROFILE_NOT_ACTIVE,
    ROLE_ADMIN,
    ROLE_USER,
    Admitted,
    Refused,
    admit,
)

ADMIN = "521740"
MEMBER = "600001"


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    """An isolated Deployment: Hermes home, Profile roster and Admin list."""
    state = tmp_path / "state"
    state.mkdir()
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    monkeypatch.setattr(roster, "STATE_DIR", state)
    monkeypatch.setattr(auth, "STATE_DIR", state)
    monkeypatch.setattr(auth, "_SESSIONS_FILE", state / ".sessions.json")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    monkeypatch.setenv(ADMIN_USERS_ENV, "")

    class Deployment:
        roster_file = state / roster.ROSTER_FILENAME

        def admins(self, value):
            if value is None:
                monkeypatch.delenv(ADMIN_USERS_ENV)
            else:
                monkeypatch.setenv(ADMIN_USERS_ENV, value)

        def profile(self, name, *, active=True, in_roster=True):
            (hermes_home / "profiles" / name).mkdir()
            if not in_roster:
                return
            roster.add(name)
            if not active:
                roster.disable(name)

    return Deployment()


def test_an_active_profile_is_admitted_as_a_member_to_that_profile(deployment):
    deployment.profile(MEMBER)
    assert admit(MEMBER) == Admitted(ROLE_USER, MEMBER)


def test_someone_without_a_profile_is_refused_for_having_no_profile(deployment):
    assert admit(MEMBER) == Refused(REFUSED_NO_PROFILE)


def test_a_disabled_profile_is_refused_as_not_active(deployment):
    deployment.profile(MEMBER, active=False)
    assert admit(MEMBER) == Refused(REFUSED_PROFILE_NOT_ACTIVE)


def test_a_profile_without_a_roster_record_is_admitted_as_active(deployment):
    # A Profile made before the Profile roster existed has no record, and stays usable.
    deployment.profile(MEMBER, in_roster=False)
    assert admit(MEMBER) == Admitted(ROLE_USER, MEMBER)


def test_an_unreadable_roster_refuses_everyone_but_the_admin(deployment):
    deployment.admins(ADMIN)
    deployment.profile(MEMBER)
    deployment.roster_file.write_text("{ not json", encoding="utf-8")
    assert admit(MEMBER) == Refused(REFUSED_PROFILE_NOT_ACTIVE)
    assert admit(ADMIN) == Admitted(ROLE_ADMIN, "default")


@pytest.mark.parametrize("profile", ["none", "active", "disabled"])
def test_the_admin_list_always_wins_and_binds_to_default(deployment, profile):
    deployment.admins(f"{MEMBER}, 999999")
    if profile != "none":
        deployment.profile(MEMBER, active=profile == "active")
    assert admit(MEMBER) == Admitted(ROLE_ADMIN, "default")


@pytest.mark.parametrize("admins", [None, "", " , "])
def test_with_no_admin_list_nobody_is_admin(deployment, admins):
    deployment.admins(admins)
    deployment.profile(MEMBER)
    assert admit(MEMBER) == Admitted(ROLE_USER, MEMBER)


def test_admission_changes_nothing_on_disk(deployment, tmp_path):
    deployment.admins(ADMIN)
    deployment.profile(MEMBER)
    deployment.profile("600002", active=False)

    def snapshot():
        return {
            str(p.relative_to(tmp_path)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in sorted(tmp_path.rglob("*"))
        }

    before = snapshot()
    for employee_id in (ADMIN, MEMBER, "600002", "700001"):
        admit(employee_id)
    assert snapshot() == before

    deployment.roster_file.write_text("{ not json", encoding="utf-8")
    before = snapshot()
    admit(MEMBER)
    assert snapshot() == before
