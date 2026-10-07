"""GFIT-CoWork: Admission, the one decision about who may use the Deployment.

From an employee ID the Directory has confirmed, Admission says whether that
person is admitted, as a User to their own Profile, or why they are refused
(ADR 0004, ADR 0006: there is no Admin). Tested at its interface, with no
server: a temporary Hermes home, a temporary Profile roster and the leftover
Admin list env var, which grants nothing.
"""
from __future__ import annotations

import pytest

import api.auth as auth
import api.profiles as profiles
import api.roster as roster
from api.access import (
    LEFTOVER_ADMIN_USERS_ENV,
    REFUSED_NO_PROFILE,
    REFUSED_PROFILE_NOT_ACTIVE,
    ROLE_USER,
    Admitted,
    Refused,
    admit,
)

FORMER_ADMIN = "521740"
MEMBER = "600001"


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    """An isolated Deployment: Hermes home, Profile roster and the leftover Admin list."""
    state = tmp_path / "state"
    state.mkdir()
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    monkeypatch.setattr("api.config.STATE_DIR", state)
    monkeypatch.setattr(auth, "_SESSIONS_FILE", state / ".sessions.json")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    monkeypatch.delenv(LEFTOVER_ADMIN_USERS_ENV, raising=False)

    class Deployment:
        roster_file = state / roster.ROSTER_FILENAME

        def leftover_admins(self, value):
            monkeypatch.setenv(LEFTOVER_ADMIN_USERS_ENV, value)

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


def test_an_unreadable_roster_refuses_everyone(deployment):
    deployment.profile(MEMBER)
    deployment.roster_file.write_text("{ not json", encoding="utf-8")
    assert admit(MEMBER) == Refused(REFUSED_PROFILE_NOT_ACTIVE)


@pytest.mark.parametrize("profile", ["none", "active", "disabled"])
def test_a_leftover_admin_list_grants_nothing(deployment, profile):
    # A former Admin is a User like anyone else: their own Profile, or nothing.
    deployment.leftover_admins(f"{FORMER_ADMIN}, {MEMBER}")
    if profile != "none":
        deployment.profile(FORMER_ADMIN, active=profile == "active")
    expected = {
        "none": Refused(REFUSED_NO_PROFILE),
        "active": Admitted(ROLE_USER, FORMER_ADMIN),
        "disabled": Refused(REFUSED_PROFILE_NOT_ACTIVE),
    }[profile]
    assert admit(FORMER_ADMIN) == expected


def test_nobody_is_admitted_to_the_default_profile(deployment):
    deployment.leftover_admins("default")
    assert admit("default") == Refused(REFUSED_NO_PROFILE)


def test_admission_changes_nothing_on_disk(deployment, tmp_path):
    deployment.leftover_admins(FORMER_ADMIN)
    deployment.profile(MEMBER)
    deployment.profile("600002", active=False)

    def snapshot():
        return {
            str(p.relative_to(tmp_path)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in sorted(tmp_path.rglob("*"))
        }

    before = snapshot()
    for employee_id in (FORMER_ADMIN, MEMBER, "600002", "700001"):
        admit(employee_id)
    assert snapshot() == before

    deployment.roster_file.write_text("{ not json", encoding="utf-8")
    before = snapshot()
    admit(MEMBER)
    assert snapshot() == before
