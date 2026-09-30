"""GFIT-CoWork: the session ownership module answers "which Profiles may this request read?".

A User's request reads exactly the User's own Profile, whatever it asks,
never counts other Profiles, and is single-Profile. The unconfined adapter
(the Admin, and requests with no Admission) keeps today's rules: the active
Profile, or every Profile when the request asks and Upstream's isolated
profile mode is off. The refusing answer reads no Profile.

Tested from temporary Profile folders alone: no server and no request thread.
Each case is a row in a table.
"""
from __future__ import annotations

import pytest

import api.profiles as profiles
from api.access import ROLE_ADMIN, ROLE_MEMBER, Admitted
from api.session_ownership import ownership_for

ALICE = "521740"
BOB = "671278"
EVERY = "every Profile"


@pytest.fixture
def world(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    for uid in (ALICE, BOB):
        (hermes / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes)
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()
    monkeypatch.setattr(profiles._tls, "profile", None, raising=False)
    yield hermes
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()


def _isolated_mode(monkeypatch, hermes, profile):
    """Upstream's posture: this process serves *profile* only."""
    monkeypatch.setattr(profiles, "_INITIAL_ISOLATED_PROFILE_OPT_IN", "1")
    monkeypatch.setattr(profiles, "_INITIAL_HERMES_HOME", str(hermes / "profiles" / profile))


ADAPTERS = {
    "user": lambda: ownership_for(Admitted(ROLE_MEMBER, ALICE), directory_session=True),
    "admin": lambda: ownership_for(Admitted(ROLE_ADMIN, "default"), directory_session=True),
    "login off": lambda: ownership_for(None, directory_session=False),
    "refusing": lambda: ownership_for(None, directory_session=True),
}


def _readable(reach):
    return EVERY if reach.every_profile else set(reach.profiles)


# (adapter, all Profiles asked, isolated mode) -> (readable, counts others, single-Profile)
CASES = [
    ("user", False, False, ({ALICE}, False, True)),
    ("user", True, False, ({ALICE}, False, True)),
    ("admin", False, False, ({"default"}, True, False)),
    ("admin", True, False, (EVERY, False, False)),
    ("login off", False, False, ({"default"}, True, False)),
    ("login off", True, False, (EVERY, False, False)),
    ("login off", False, True, ({BOB}, False, True)),
    ("login off", True, True, ({BOB}, False, True)),
    ("refusing", False, False, (set(), False, True)),
    ("refusing", True, False, (set(), False, True)),
]


@pytest.mark.parametrize("adapter,all_profiles,isolated,expected", CASES)
def test_which_profiles_a_request_may_read(world, monkeypatch, adapter, all_profiles, isolated, expected):
    if isolated:
        _isolated_mode(monkeypatch, world, BOB)
    active = BOB if isolated else "default"
    if adapter == "user":
        active = ALICE  # the request's Admission sets a User's active Profile

    reach = ADAPTERS[adapter]().profile_reach(active, all_profiles=all_profiles)

    assert (_readable(reach), reach.counts_other_profiles, reach.single_profile) == expected


@pytest.mark.parametrize("adapter,row_profile,expected", [
    ("user", ALICE, True),
    ("user", BOB, False),
    ("user", "default", False),
    ("user", None, False),  # a row with no Profile is the root Profile's
    ("user", "", False),
    ("admin", BOB, True),
    ("admin", None, True),
    ("refusing", ALICE, False),
])
def test_an_all_profiles_reach_includes_only_readable_rows(world, adapter, row_profile, expected):
    reach = ADAPTERS[adapter]().profile_reach("default", all_profiles=True)

    assert reach.includes(row_profile) is expected


def test_the_admins_own_view_reads_only_the_active_profile(world):
    reach = ADAPTERS["admin"]().profile_reach(BOB, all_profiles=False)

    assert reach.includes(BOB)
    assert not reach.includes(ALICE)
    assert not reach.includes("default")


# ── A Profile-home lookup refuses what the request may not read ─────────────

import api.access as access  # noqa: E402

REFUSED = "refused"


def _in_request(monkeypatch, admission, *, directory_session=True):
    monkeypatch.setattr(access._request, "admission", admission, raising=False)
    monkeypatch.setattr(access._request, "directory_session", directory_session, raising=False)
    if admission is not None:
        monkeypatch.setattr(profiles._tls, "profile", admission.profile, raising=False)


def _lookup(name):
    try:
        return profiles.get_hermes_home_for_profile(name)
    except ValueError:
        return REFUSED


@pytest.mark.parametrize("name,expected", [
    (ALICE, "alice"),
    (None, "alice"),  # no name: the request's own Profile
    ("", "alice"),
    (BOB, REFUSED),
    ("default", REFUSED),
    ("no-such-profile", REFUSED),
    ("../../etc", REFUSED),
])
def test_a_users_profile_home_lookup_resolves_only_their_own(world, monkeypatch, name, expected):
    _in_request(monkeypatch, Admitted(ROLE_MEMBER, ALICE))
    homes = {"alice": world / "profiles" / ALICE}

    assert _lookup(name) == homes.get(expected, expected)


@pytest.mark.parametrize("name,expected", [
    (ALICE, "alice"),
    (BOB, "bob"),
    ("default", "root"),
    (None, "root"),
])
def test_the_admins_profile_home_lookup_resolves_any_profile(world, monkeypatch, name, expected):
    _in_request(monkeypatch, Admitted(ROLE_ADMIN, "default"))
    homes = {"alice": world / "profiles" / ALICE, "bob": world / "profiles" / BOB, "root": world}

    assert _lookup(name) == homes[expected]


def test_upstream_isolated_mode_keeps_its_quiet_clamp(world, monkeypatch):
    _isolated_mode(monkeypatch, world, BOB)
    _in_request(monkeypatch, None, directory_session=False)

    assert _lookup(ALICE) == world / "profiles" / BOB
    assert _lookup(BOB) == world / "profiles" / BOB


def test_isolated_mode_is_the_process_posture_not_the_caller(world, monkeypatch):
    _in_request(monkeypatch, Admitted(ROLE_MEMBER, ALICE))

    assert profiles._is_isolated_profile_mode() is False
    assert profiles.get_active_profile_name() == ALICE
    assert profiles.get_active_hermes_home() == world / "profiles" / ALICE
