"""GFIT-CoWork: a Profile create fails when Hermes's own create fails (ticket 14).

``profiles.create_profile_api`` creates a Profile with Hermes Agent's
``hermes_cli.profiles.create_profile`` when that capability can be imported,
and with the WebUI's own fallback (``_create_profile_fallback``) when it cannot.
As for delete (ticket 13), the fallback is for a missing capability only:
once Hermes's create is called, any error from it, an ImportError raised
inside it included, fails the create. The fallback never builds the Profile
instead, and the create is never reported as done.

Hermes builds a Profile in a hidden staging directory and publishes it with
one rename; an error before that leaves nothing (Hermes removes the staging
tree). After the rename it registers the gateway service and notifies the
multiplexer, which can still raise with the Profile in place. The roster's
existing contract covers both: nothing in place -> no record, "was not
created"; the Profile in place -> its record stays disabled, "created only in
part and is disabled; delete it and create it again".
"""
from __future__ import annotations

import sys
import types

import pytest

import api.profiles as profiles
from api import operator_cli, roster
from tests._gfit_server import gfit_server as _gfit_server

NEWCOMER = "700001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={NEWCOMER: "Somsri Jaidee"}) as s:
        yield s


def _hermes(monkeypatch, create_profile):
    fake = types.ModuleType("hermes_cli.profiles")
    fake.create_profile = create_profile
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)


def _publish(srv, name):
    home = srv.profile_home(name)
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("model: {}\n")
    return home


def _no_fallback(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the WebUI fallback built the Profile after Hermes's create had been called")

    monkeypatch.setattr(profiles, "_create_profile_fallback", refuse)


def _records(srv) -> dict:
    import json

    path = srv.state / "gfit_roster.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _create(capsys, *extra) -> tuple[int, str]:
    capsys.readouterr()
    code = operator_cli.run(["create", NEWCOMER, "--display-name", "Somsri J.", *extra])
    return code, capsys.readouterr().err


def test_hermes_unavailable_uses_the_fallback(srv, capsys):
    # _gfit_server pins hermes_cli.profiles to None: the capability is missing.
    code, _err = _create(capsys)
    assert code == 0
    assert srv.profile_home(NEWCOMER).is_dir()
    assert roster.view(NEWCOMER)["status"] == "active"


def test_hermes_available_and_succeeding_is_the_hermes_path(srv, monkeypatch, capsys):
    calls = []

    def create_profile(name, **options):
        calls.append(name)
        return _publish(srv, name)

    _hermes(monkeypatch, create_profile)
    _no_fallback(monkeypatch)
    code, _err = _create(capsys)
    assert code == 0
    assert calls == [NEWCOMER]
    assert roster.view(NEWCOMER)["status"] == "active"
    srv.logged_in(NEWCOMER)


def test_an_import_error_inside_hermes_create_fails_and_does_not_fall_back(srv, monkeypatch, capsys):
    def create_profile(name, **options):
        raise ModuleNotFoundError("No module named 'ruamel'")  # before Hermes publishes anything

    _hermes(monkeypatch, create_profile)
    _no_fallback(monkeypatch)
    code, err = _create(capsys)
    assert code == 1
    assert "was not created" in err and "Hermes Agent could not create" in err and "ruamel" in err
    assert not srv.profile_home(NEWCOMER).exists()
    assert NEWCOMER not in _records(srv)
    assert srv.client().login(NEWCOMER)[0] == 403


@pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("gateway refused")])
def test_any_other_error_inside_hermes_create_fails_too(srv, monkeypatch, capsys, error):
    def create_profile(name, **options):
        raise error

    _hermes(monkeypatch, create_profile)
    _no_fallback(monkeypatch)
    code, err = _create(capsys)
    assert code == 1
    assert "was not created" in err and str(error) in err
    assert not srv.profile_home(NEWCOMER).exists()
    assert NEWCOMER not in _records(srv)


def test_a_failure_after_hermes_published_the_profile_leaves_it_disabled(srv, monkeypatch, capsys):
    def create_profile(name, **options):
        _publish(srv, name)  # the rename happened; registering the gateway service failed
        raise ModuleNotFoundError("No module named 's6_supervise'")

    _hermes(monkeypatch, create_profile)
    _no_fallback(monkeypatch)
    code, err = _create(capsys)
    assert code == 1
    assert "created only in part and is disabled" in err and "s6_supervise" in err
    assert roster.view(NEWCOMER)["status"] == "disabled"
    assert srv.client().login(NEWCOMER)[0] == 403
    # The fallback did not write over the partial Profile.
    assert (srv.profile_home(NEWCOMER) / "config.yaml").read_text() == "model: {}\n"


def test_a_create_that_failed_before_publishing_can_simply_be_run_again(srv, monkeypatch, capsys):
    def broken(name, **options):
        raise ModuleNotFoundError("No module named 'ruamel'")

    _hermes(monkeypatch, broken)
    assert _create(capsys)[0] == 1

    _hermes(monkeypatch, lambda name, **options: _publish(srv, name))
    assert _create(capsys)[0] == 0
    assert roster.view(NEWCOMER)["status"] == "active"


def test_a_partial_create_is_recovered_by_delete_then_create(srv, monkeypatch, capsys):
    def half(name, **options):
        _publish(srv, name)
        raise RuntimeError("multiplexer unreachable")

    _hermes(monkeypatch, half)
    assert _create(capsys)[0] == 1

    import shutil

    fake = sys.modules["hermes_cli.profiles"]
    fake.delete_profile = lambda name, yes=False: shutil.rmtree(srv.profile_home(name))
    assert operator_cli.run(["delete", NEWCOMER, "--confirm", NEWCOMER]) == 0
    fake.create_profile = lambda name, **options: _publish(srv, name)
    assert _create(capsys)[0] == 0
    assert roster.view(NEWCOMER)["status"] == "active"
