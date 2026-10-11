"""GFIT-CoWork: a Profile delete fails when Hermes's own delete fails (ticket 13).

``profiles.delete_profile_api`` has two ways to delete a Profile: Hermes
Agent's ``hermes_cli.profiles.delete_profile`` when that capability can be
imported, and a manual fallback (remove the directory) when it cannot. The
fallback is for a missing capability only. Once Hermes's delete has been
imported and called, any exception from it, an ImportError raised inside it
included, fails the delete: no fallback ``rmtree``, no success, the sticky
active profile untouched by GFIT-CoWork, and the Profile left disabled in the
roster so the Operator can fix the install and run the delete again.

Hermes's delete is not atomic: it may have stopped the Profile's gateway or
written its tombstone before it raised. GFIT-CoWork does not undo or hide
that; the Operator's error says the delete did not finish.
"""
from __future__ import annotations

import sys
import types

import pytest

import api.profiles as profiles
from api import operator_cli, roster
from tests._gfit_server import gfit_server as _gfit_server

USER = "600001"


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    (home / "profiles" / USER).mkdir(parents=True)
    (home / "profiles" / USER / "config.yaml").write_text("model: {}\n")
    (home / "profiles" / USER / "memory.md").write_text("the User's data\n")
    (home / "active_profile").write_text(USER + "\n")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", home)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    return home


def _hermes(monkeypatch, delete_profile):
    fake = types.ModuleType("hermes_cli.profiles")
    fake.delete_profile = delete_profile
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)


def _no_rmtree(monkeypatch):
    import shutil

    def refuse(*_args, **_kwargs):
        raise AssertionError("the fallback removed the Profile after Hermes's delete failed")

    monkeypatch.setattr(shutil, "rmtree", refuse)


def _intact(root):
    assert (root / "profiles" / USER / "memory.md").read_text() == "the User's data\n"
    assert (root / "active_profile").read_text().strip() == USER


def test_hermes_unavailable_uses_the_fallback(root, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)
    profiles.delete_profile_api(USER)
    assert not (root / "profiles" / USER).exists()
    assert not (root / "active_profile").exists()  # the fallback resets it (ticket 09)


def test_hermes_available_and_succeeding_is_the_normal_path(root, monkeypatch):
    calls = []

    def delete_profile(name, yes=False):
        calls.append((name, yes))
        import shutil

        shutil.rmtree(root / "profiles" / name)

    _hermes(monkeypatch, delete_profile)
    assert profiles.delete_profile_api(USER) == {"ok": True, "name": USER}
    assert calls == [(USER, True)]
    assert not (root / "profiles" / USER).exists()


def test_an_import_error_inside_hermes_delete_fails_and_does_not_fall_back(root, monkeypatch):
    def delete_profile(name, yes=False):
        raise ModuleNotFoundError("No module named 'ruamel'")

    _hermes(monkeypatch, delete_profile)
    _no_rmtree(monkeypatch)
    with pytest.raises(RuntimeError) as raised:
        profiles.delete_profile_api(USER)
    assert "ruamel" in str(raised.value) and "Hermes" in str(raised.value)
    assert isinstance(raised.value.__cause__, ImportError)
    _intact(root)


@pytest.mark.parametrize("error", [
    OSError("disk full"),
    RuntimeError("an agent is still running"),
    PermissionError("not allowed"),
])
def test_any_other_error_inside_hermes_delete_fails_too(root, monkeypatch, error):
    def delete_profile(name, yes=False):
        raise error

    _hermes(monkeypatch, delete_profile)
    _no_rmtree(monkeypatch)
    with pytest.raises(type(error)):
        profiles.delete_profile_api(USER)
    _intact(root)


# ── what the Operator sees ──────────────────────────────────────────────────

@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={USER: "User One"}, profile_names=[USER]) as s:
        yield s


def test_the_operator_is_told_the_delete_did_not_finish_and_the_profile_stays_disabled(srv, monkeypatch, capsys):
    def delete_profile(name, yes=False):
        raise ModuleNotFoundError("No module named 'ruamel'")

    assert operator_cli.run(["disable", USER]) == 0
    capsys.readouterr()
    _hermes(monkeypatch, delete_profile)
    _no_rmtree(monkeypatch)

    assert operator_cli.run(["delete", USER, "--confirm", USER]) == 1
    err = capsys.readouterr().err
    assert "was not deleted" in err and "ruamel" in err and "deleted\n" not in err
    assert srv.profile_home(USER).is_dir()
    assert roster.view(USER)["status"] == "disabled"
    assert srv.client().login(USER)[0] == 403
