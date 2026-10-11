"""GFIT-CoWork: deleting a Profile leaves Hermes's sticky active profile sane (ticket 09).

The Operator deletes a Profile with ``python3 -m api.operator_cli delete``
(``roster.delete_profile`` → ``profiles.delete_profile_api``). The process runs
as the Deployment's default Profile (ticket 11), so there is no Profile to
switch away from first; Upstream's process-wide ``switch_profile`` is gone.

Hermes's sticky ``~/.hermes/active_profile`` is the Operator's shell
convenience. When it names the deleted Profile it must go back to default
(no file), so ``hermes`` is not left on a Profile that does not exist:
Hermes Agent's own ``delete_profile`` does that itself (step 5,
``_retarget_active_profile``); the manual fallback, used when ``hermes_cli``
cannot be imported, does it in the WebUI. Any other sticky value is left alone.
"""
from __future__ import annotations

import sys
import types

import pytest

import api.profiles as profiles

USER = "600001"
OTHER = "600002"


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    for name in (USER, OTHER):
        (home / "profiles" / name).mkdir(parents=True)
        (home / "profiles" / name / "config.yaml").write_text("model: {}\n")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", home)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    return home


@pytest.fixture
def fallback(monkeypatch):
    """hermes_cli cannot be imported: delete_profile_api removes the directory itself."""
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)


@pytest.fixture
def hermes(monkeypatch, root):
    """A stand-in for Hermes Agent's delete_profile, recording what it was asked."""
    calls = []
    fake = types.ModuleType("hermes_cli.profiles")

    def delete_profile(name, yes=False):
        calls.append((name, yes))
        import shutil

        shutil.rmtree(root / "profiles" / name)

    fake.delete_profile = delete_profile
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)
    return calls


def _sticky(root):
    path = root / "active_profile"
    return path.read_text().strip() if path.exists() else None


def test_switch_profile_is_gone():
    assert not hasattr(profiles, "switch_profile")


def test_fallback_delete_of_the_sticky_profile_resets_it_to_default(root, fallback):
    (root / "active_profile").write_text(USER + "\n")
    profiles.delete_profile_api(USER)
    assert not (root / "profiles" / USER).exists()
    assert _sticky(root) is None  # default is no file, as Hermes writes it


def test_fallback_delete_of_another_profile_leaves_the_sticky_profile_alone(root, fallback):
    (root / "active_profile").write_text(OTHER + "\n")
    profiles.delete_profile_api(USER)
    assert not (root / "profiles" / USER).exists()
    assert _sticky(root) == OTHER


def test_fallback_delete_with_no_sticky_profile(root, fallback):
    profiles.delete_profile_api(USER)
    assert not (root / "profiles" / USER).exists()
    assert _sticky(root) is None


def test_hermes_path_is_used_when_available_and_owns_the_sticky_reset(root, hermes):
    (root / "active_profile").write_text(USER + "\n")
    profiles.delete_profile_api(USER)
    assert hermes == [(USER, True)]
    # Hermes's delete_profile resets the sticky file itself (its step 5); the
    # WebUI does not second-guess it on this path.
    assert _sticky(root) == USER


@pytest.mark.parametrize("name", ["default", ""])
def test_the_default_profile_cannot_be_deleted_and_its_state_is_kept(root, fallback, name):
    (root / "active_profile").write_text(USER + "\n")
    with pytest.raises(ValueError):
        profiles.delete_profile_api(name)
    assert _sticky(root) == USER
    assert (root / "profiles" / USER).is_dir()


def test_a_missing_profile_is_refused_and_the_sticky_profile_kept(root, fallback):
    (root / "active_profile").write_text(USER + "\n")
    with pytest.raises(ValueError):
        profiles.delete_profile_api("700009")
    assert _sticky(root) == USER
