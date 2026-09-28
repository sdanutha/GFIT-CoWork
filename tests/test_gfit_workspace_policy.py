"""GFIT-CoWork: one Workspace policy answers every Workspace question for a request.

A User's policy confines everything to that User's Workspace folder
(``<Profile>/workspace``); the unconfined policy is today's behaviour for the
Admin and for requests with no Admission (login turned off). A Directory
session with no recorded Admission gets the refusing answer (unknown is not
allowed).

The User's policy is tested from a temporary Profile folder alone: no server
and no request's Admission. The choice of policy is a table over Admissions.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import api.access as access
import api.profiles as profiles
from api.access import ROLE_ADMIN, ROLE_MEMBER, Admitted
from api.workspace import OUTSIDE_WORKSPACE_MESSAGE
from api.workspace_policy import (
    REFUSING,
    UNCONFINED,
    UserWorkspacePolicy,
    policy_for,
    request_workspace_policy,
)

ALICE = "521740"
BOB = "671278"


@pytest.fixture
def homes(tmp_path):
    """Two Profile folders side by side, and a folder outside every Profile."""
    root = tmp_path / "hermes" / "profiles"
    alice, bob = root / ALICE, root / BOB
    for home in (alice, bob):
        (home / "workspace" / "project").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (alice / "workspace" / "link-out").symlink_to(outside, target_is_directory=True)
    (alice / "workspace" / "notes.txt").write_text("notes")

    alice = alice.resolve()
    return SimpleNamespace(alice=alice, bob=bob.resolve(), outside=outside.resolve(), ws=alice / "workspace")


@pytest.fixture
def policy(homes):
    return UserWorkspacePolicy(homes.alice)


# name -> path, built from the fixture
def _path(homes, name: str) -> str:
    return {
        "the Workspace": str(homes.ws),
        "inside": str(homes.ws / "project"),
        "outside": str(homes.outside),
        "dot-dot out": str(homes.ws / "project" / ".." / ".." / ".." / BOB / "workspace"),
        "dot-dot in": str(homes.ws / "project" / ".." / "project"),
        "symlink out": str(homes.ws / "link-out"),
        "another Profile": str(homes.bob / "workspace"),
        "the Profile itself": str(homes.alice),
        "a file inside": str(homes.ws / "notes.txt"),
        "missing inside": str(homes.ws / "not-yet"),
        "missing outside": str(homes.outside / "not-yet"),
    }[name]


USE = [
    ("the Workspace", True),
    ("inside", True),
    ("dot-dot in", True),
    ("outside", False),
    ("dot-dot out", False),
    ("symlink out", False),
    ("another Profile", False),
    ("the Profile itself", False),
    ("a file inside", False),
    ("missing inside", False),
]


@pytest.mark.parametrize("name,allowed", USE)
def test_a_user_may_use_only_an_existing_folder_inside_their_workspace(homes, policy, name, allowed):
    path = _path(homes, name)
    assert policy.may_use(path) is allowed
    if allowed:
        assert policy.resolve_to_use(path) == Path(path).resolve()
        assert policy.resolve_to_register(path) == Path(path).resolve()
    else:
        with pytest.raises(ValueError):
            policy.resolve_to_use(path)
        with pytest.raises(ValueError):
            policy.resolve_to_register(path)


def test_outside_the_workspace_is_refused_with_the_workspace_message(homes, policy):
    with pytest.raises(ValueError, match=OUTSIDE_WORKSPACE_MESSAGE):
        policy.resolve_to_use(str(homes.outside))


@pytest.mark.parametrize("empty", [None, ""])
def test_an_empty_path_is_the_default_workspace_created_if_missing(tmp_path, empty):
    home = tmp_path / ALICE
    home.mkdir()
    policy = UserWorkspacePolicy(home)
    assert not (home / "workspace").exists()
    assert policy.resolve_to_use(empty) == (home / "workspace").resolve()
    assert (home / "workspace").is_dir()
    assert policy.may_use(empty) is False  # a stored empty value is not a Workspace


def test_the_default_workspace_is_the_users_workspace_created_if_missing(tmp_path):
    home = tmp_path / ALICE
    home.mkdir()
    policy = UserWorkspacePolicy(home)
    assert policy.default_workspace() == str((home / "workspace").resolve())
    assert (home / "workspace").is_dir()


def test_a_symlinked_workspace_folder_is_confined_by_its_resolved_path(tmp_path):
    home, real = tmp_path / ALICE, tmp_path / "real-workspace"
    (real / "project").mkdir(parents=True)
    home.mkdir()
    (home / "workspace").symlink_to(real, target_is_directory=True)
    policy = UserWorkspacePolicy(home)
    assert policy.root == real.resolve()
    assert policy.resolve_to_use(str(home / "workspace" / "project")) == (real / "project").resolve()


CONFINE = [
    ("the Workspace", True),
    ("inside", True),
    ("a file inside", True),
    ("missing inside", True),
    ("dot-dot in", True),
    ("outside", False),
    ("missing outside", False),
    ("dot-dot out", False),
    ("symlink out", False),
    ("another Profile", False),
    ("the Profile itself", False),
]


@pytest.mark.parametrize("name,allowed", CONFINE)
def test_confine_refuses_a_resolved_path_outside_the_workspace(homes, policy, name, allowed):
    path = Path(_path(homes, name))
    if allowed:
        assert policy.confine(path) == path.resolve()
    else:
        with pytest.raises(ValueError, match=OUTSIDE_WORKSPACE_MESSAGE):
            policy.confine(path)


@pytest.mark.parametrize("name,allowed", [
    ("inside", True),
    ("missing inside", True),
    ("outside", False),
    ("missing outside", False),
    ("dot-dot out", False),
    ("another Profile", False),
])
def test_register_target_is_checked_before_any_folder_is_created(homes, policy, name, allowed):
    path = _path(homes, name)
    existed = Path(path).exists()
    if allowed:
        assert policy.register_target(path) == Path(path).resolve()
    else:
        with pytest.raises(ValueError, match=OUTSIDE_WORKSPACE_MESSAGE):
            policy.register_target(path)
    assert Path(path).exists() is existed


def test_a_system_folder_is_refused_with_its_own_message_first(policy):
    """As before the policy: registering a blocked system folder names it as one."""
    with pytest.raises(ValueError, match="Path points to a system directory"):
        policy.register_target("/etc")


def test_the_saved_list_is_the_default_first_then_saved_folders_inside(homes, policy):
    saved = [
        {"path": str(homes.ws), "name": "duplicate of Home"},
        {"path": str(homes.ws / "project"), "name": "Project"},
        {"path": str(homes.ws / "project" / ".." / "project"), "name": ""},
        {"path": str(homes.outside), "name": "Outside"},
        {"path": str(homes.ws / "link-out"), "name": "Symlink out"},
        {"path": str(homes.bob / "workspace"), "name": "Bob"},
        {"path": "", "name": "Empty"},
        "not a dict",
    ]
    assert policy.saved_list(saved) == [
        {"path": str(homes.ws), "name": "Home"},
        {"path": str(homes.ws / "project"), "name": "Project"},
        {"path": str(homes.ws / "project"), "name": "project"},
    ]


def test_file_operations_reach_only_the_workspace(homes, policy, tmp_path):
    assert policy.file_roots() == [homes.ws]
    assert UserWorkspacePolicy(tmp_path / "no-such-profile").file_roots() == []


@pytest.mark.parametrize("name,allowed", [
    ("the Profile itself", True),
    ("a file inside", True),
    ("outside", False),
    ("another Profile", False),
    ("symlink out", False),
])
def test_the_media_viewer_serves_only_from_the_users_profile(homes, policy, name, allowed):
    # The route's deny-list (credentials, config, sessions, memories) still applies.
    assert policy.may_serve_media(Path(_path(homes, name)).resolve()) is allowed


@pytest.mark.parametrize("name,allowed", [
    ("inside", True),
    ("missing inside", True),
    ("outside", False),
    ("another Profile", False),
    ("symlink out", False),
])
def test_a_worktree_may_become_the_workspace_only_inside_it(homes, policy, name, allowed):
    assert policy.may_become_worktree(Path(_path(homes, name))) is allowed


# ── The refusing answer ──────────────────────────────────────────────────────

def test_the_refusing_policy_allows_nothing(homes):
    inside = _path(homes, "inside")
    assert REFUSING.may_use(inside) is False
    assert REFUSING.saved_list([{"path": inside, "name": "x"}]) == []
    assert REFUSING.file_roots() == []
    assert REFUSING.may_serve_media(Path(inside)) is False
    assert REFUSING.may_become_worktree(Path(inside)) is False
    for ask in (
        REFUSING.default_workspace,
        lambda: REFUSING.resolve_to_use(inside),
        lambda: REFUSING.resolve_to_register(inside),
        lambda: REFUSING.register_target(inside),
        lambda: REFUSING.confine(Path(inside)),
    ):
        with pytest.raises(ValueError, match=OUTSIDE_WORKSPACE_MESSAGE):
            ask()


# ── The unconfined policy is today's behaviour ───────────────────────────────

def test_the_unconfined_policy_does_not_confine(homes):
    for name in ("outside", "another Profile", "symlink out"):
        path = Path(_path(homes, name))
        assert UNCONFINED.confine(path) == path
        assert UNCONFINED.may_serve_media(path) is True
        assert UNCONFINED.may_become_worktree(path) is True


# ── Choosing the request's policy ────────────────────────────────────────────

@pytest.fixture
def profiles_root(monkeypatch, homes):
    hermes_home = homes.alice.parent.parent
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    return hermes_home / "profiles"


@pytest.mark.parametrize("admission,directory_session,chosen", [
    (Admitted(ROLE_MEMBER, ALICE), True, "user"),
    (Admitted(ROLE_ADMIN, "default"), True, "unconfined"),
    (None, False, "unconfined"),                          # login turned off
    (None, True, "refusing"),                             # unknown is not allowed
    (Admitted("owner", ALICE), True, "refusing"),         # an unknown role
    (Admitted(ROLE_MEMBER, ""), True, "refusing"),        # a User with no Profile
    (Admitted(ROLE_MEMBER, "../escape"), True, "refusing"),  # not a Profile name
])
def test_the_request_policy_is_chosen_from_the_admission(homes, profiles_root, admission, directory_session, chosen):
    policy = policy_for(admission, directory_session=directory_session)
    if chosen == "user":
        assert isinstance(policy, UserWorkspacePolicy)
        assert policy.root == homes.ws
    else:
        assert policy is {"unconfined": UNCONFINED, "refusing": REFUSING}[chosen]


@pytest.fixture
def no_request_admission():
    access.clear_request_admission()
    yield
    access.clear_request_admission()


@pytest.mark.parametrize("session,admission,chosen", [
    ({"username": ALICE, "role": ROLE_MEMBER, "bound_profile": ALICE}, Admitted(ROLE_MEMBER, ALICE), "user"),
    ({"username": "600001", "role": ROLE_ADMIN, "bound_profile": "default"}, Admitted(ROLE_ADMIN, "default"), "unconfined"),
    # Admission no longer gives the session's role: the Directory session has no Admission.
    ({"username": ALICE, "role": ROLE_ADMIN, "bound_profile": "default"}, Admitted(ROLE_MEMBER, ALICE), "refusing"),
])
def test_the_request_policy_follows_the_requests_admission(
    monkeypatch, homes, profiles_root, no_request_admission, session, admission, chosen,
):
    assert request_workspace_policy() is UNCONFINED  # no Directory session yet
    monkeypatch.setattr(access, "admit", lambda employee_id: admission)
    access.admit_request(session)
    policy = request_workspace_policy()
    if chosen == "user":
        assert isinstance(policy, UserWorkspacePolicy) and policy.root == homes.ws
    else:
        assert policy is {"unconfined": UNCONFINED, "refusing": REFUSING}[chosen]
    access.clear_request_admission()
    assert request_workspace_policy() is UNCONFINED


def test_worker_threads_carry_no_admission_and_are_unconfined(no_request_admission, monkeypatch, homes, profiles_root):
    import threading

    monkeypatch.setattr(access, "admit", lambda employee_id: Admitted(ROLE_MEMBER, ALICE))
    access.admit_request({"username": ALICE, "role": ROLE_MEMBER, "bound_profile": ALICE})
    seen = []
    worker = threading.Thread(target=lambda: seen.append(request_workspace_policy()))
    worker.start()
    worker.join()
    assert seen == [UNCONFINED]
