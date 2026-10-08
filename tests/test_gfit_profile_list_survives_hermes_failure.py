"""The Profile list comes from the Profile directories; Hermes only enriches it (ticket 15).

``profiles.list_profiles_api`` builds the rows with Hermes Agent's cheap
``hermes_cli.profiles`` helpers, or its slower ``list_profiles()`` when those
helpers are missing, or, when ``hermes_cli.profiles`` is not installed, from
GFIT-CoWork's own Profile paths and name rule. The Profile directories are
the inventory. When Hermes is installed but its listing fails (an
ImportError raised inside it, any other error), the rows still come from the
directories, the server logs a warning, and ``operator_cli list`` prints it
on stderr; so does a Profile directory Hermes's listing leaves out, which is
listed too. Fields only Hermes knows are not invented: the model comes from
the Profile's own config.yaml or is None, and with no gateway probe
``gateway_running`` is None (unknown). When the directories themselves cannot
be read, the list fails (``ProfileInventoryUnreadable``) instead of looking
empty.
"""
from __future__ import annotations

import importlib.abc
import os
import shutil
import sys
import types

import pytest

import api.profiles as profiles
from api import operator_cli
from tests._gfit_server import gfit_server as _gfit_server

USERS = {"700001": "Somsri Jaidee", "700002": "Somchai Jaidee"}
NAMES = sorted(USERS)


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users=USERS) as s:
        # Created through the WebUI's own create (hermes_cli.profiles is pinned to None).
        for name in USERS:
            assert operator_cli.run(["create", name, "--display-name", USERS[name]]) == 0
        (s.profile_home("700001") / "config.yaml").write_text(
            "model:\n  default: disk-model\n  provider: disk-provider\n", encoding="utf-8")
        profiles._invalidate_list_profiles_cache()
        yield s


def _named(rows):
    return sorted(r["name"] for r in rows if not r.get("is_default"))


def _cli_list(capsys):
    profiles._invalidate_list_profiles_cache()
    capsys.readouterr()
    code = operator_cli.run(["list"])
    out = capsys.readouterr()
    return code, [line.split("\t")[0] for line in out.out.splitlines() if line.strip()], out.err


def _hermes_package(monkeypatch):
    """A hermes_cli package, whether or not the real one is importable here (CI has no Agent)."""
    package = types.ModuleType("hermes_cli")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "hermes_cli", package)


def _hermes(monkeypatch, **attrs):
    _hermes_package(monkeypatch)
    fake = types.ModuleType("hermes_cli.profiles")
    for key, value in attrs.items():
        setattr(fake, key, value)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)
    profiles._invalidate_list_profiles_cache()
    return fake


def _hermes_helpers(**overrides):
    helpers = dict(
        _get_default_hermes_home=lambda: profiles._DEFAULT_HERMES_HOME,
        _get_profiles_root=lambda: profiles._DEFAULT_HERMES_HOME / "profiles",
        _read_config_model=lambda home: ("hermes-model", "hermes-provider"),
        _check_gateway_running=lambda home: home.name == "700002",
        _PROFILE_ID_RE=profiles._PROFILE_ID_RE,
    )
    helpers.update(overrides)
    return helpers


class _BrokenImport(importlib.abc.MetaPathFinder):
    def __init__(self, error):
        self.error = error

    def find_spec(self, fullname, path, target=None):
        if fullname == "hermes_cli.profiles":
            raise self.error
        return None


def _broken_import(monkeypatch, error):
    _hermes_package(monkeypatch)
    monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BrokenImport(error), *sys.meta_path])
    profiles._invalidate_list_profiles_cache()


def _listed(name, home, model):
    """A row as Hermes's list_profiles() returns it."""
    return types.SimpleNamespace(name=name, path=home, is_default=name == "default", gateway_running=False,
                                 model=model, provider=None, has_env=False)


def _raise(error):
    def fail(*_args, **_kwargs):
        raise error
    return fail


# ── Hermes absent, or working: as before ──


def test_hermes_absent_lists_the_profile_directories_without_a_warning(srv, capsys):
    rows = profiles.list_profiles_api()
    assert _named(rows) == NAMES
    by_name = {r["name"]: r for r in rows}
    assert (by_name["700001"]["model"], by_name["700001"]["provider"]) == ("disk-model", "disk-provider")
    assert by_name["700002"]["model"] is None and by_name["700002"]["gateway_running"] is None
    assert _cli_list(capsys) == (0, NAMES, "")


def test_hermes_working_enriches_the_rows(srv, monkeypatch, capsys):
    _hermes(monkeypatch, **_hermes_helpers())
    rows = {r["name"]: r for r in profiles.list_profiles_api()}
    assert sorted(n for n in rows if n != "default") == NAMES
    assert (rows["700001"]["model"], rows["700001"]["provider"]) == ("hermes-model", "hermes-provider")
    assert (rows["700001"]["gateway_running"], rows["700002"]["gateway_running"]) == (False, True)
    assert _cli_list(capsys) == (0, NAMES, "")


def test_hermes_without_the_cheap_helpers_uses_its_list(srv, monkeypatch, capsys):
    def list_profiles():
        return [_listed("default", profiles._DEFAULT_HERMES_HOME, "listed-model")] + [
            _listed(n, srv.profile_home(n), "listed-model") for n in NAMES]

    _hermes(monkeypatch, list_profiles=list_profiles)
    rows = profiles.list_profiles_api()
    assert _named(rows) == NAMES and {r["model"] for r in rows} == {"listed-model"}
    assert _cli_list(capsys) == (0, NAMES, "")


# ── Hermes installed but its listing fails: the directories, and a warning ──


FAILURES = {
    "import-dependency-missing": lambda mp: _broken_import(mp, ModuleNotFoundError("No module named 'ruamel'", name="ruamel")),
    "import-raises": lambda mp: _broken_import(mp, RuntimeError("hermes_cli.profiles exploded at import")),
    "list-import-error": lambda mp: _hermes(mp, list_profiles=_raise(ModuleNotFoundError("No module named 'ruamel'", name="ruamel"))),
    "list-os-error": lambda mp: _hermes(mp, list_profiles=_raise(OSError(5, "Input/output error"))),
    "list-runtime-error": lambda mp: _hermes(mp, list_profiles=_raise(RuntimeError("profiles dir unreadable"))),
    "helper-import-error": lambda mp: _hermes(mp, **_hermes_helpers(_get_profiles_root=_raise(ImportError("cannot import name 'x'")))),
    "helper-runtime-error": lambda mp: _hermes(mp, **_hermes_helpers(_get_default_hermes_home=_raise(RuntimeError("bad home")))),
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_a_failing_hermes_listing_still_lists_every_profile(srv, monkeypatch, caplog, failure):
    FAILURES[failure](monkeypatch)
    caplog.set_level("WARNING", logger="api.profiles")
    rows = profiles.list_profiles_api()
    assert _named(rows) == NAMES
    by_name = {r["name"]: r for r in rows}
    # Read from the Profile's own config.yaml, or unknown: nothing from Hermes is invented.
    assert (by_name["700001"]["model"], by_name["700001"]["provider"]) == ("disk-model", "disk-provider")
    assert (by_name["700002"]["model"], by_name["700002"]["provider"]) == (None, None)
    assert {r["gateway_running"] for r in rows} == {None}  # no probe ran: unknown
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("Hermes Agent" in m and "Profile directories" in m for m in warnings), warnings


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_operator_list_shows_every_profile_and_the_warning(srv, monkeypatch, capsys, failure):
    FAILURES[failure](monkeypatch)
    code, names, err = _cli_list(capsys)
    assert (code, names) == (0, NAMES)
    assert err.startswith("warning: ") and "Hermes Agent" in err and "Profile directories" in err
    assert "fix the hermes agent installation" in err.lower()


def test_the_warning_recurs_until_hermes_is_fixed_and_then_stops(srv, monkeypatch, capsys):
    FAILURES["list-import-error"](monkeypatch)
    assert _cli_list(capsys)[2]
    assert _cli_list(capsys)[2]
    _hermes(monkeypatch, **_hermes_helpers())
    assert _cli_list(capsys) == (0, NAMES, "")


# ── The directories cannot be read: the list fails, never looks empty ──


needs_posix = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                                 reason="needs POSIX permissions as non-root")


@needs_posix
@pytest.mark.parametrize("hermes", ["absent", "working", "failing"])
@pytest.mark.parametrize("where", ["profiles-dir", "hermes-home"])
def test_unreadable_profile_directories_fail_the_list(srv, monkeypatch, capsys, hermes, where):
    if hermes == "working":
        _hermes(monkeypatch, **_hermes_helpers())
    elif hermes == "failing":
        FAILURES["list-runtime-error"](monkeypatch)
    target = profiles._DEFAULT_HERMES_HOME / "profiles" if where == "profiles-dir" else profiles._DEFAULT_HERMES_HOME
    mode = target.stat().st_mode
    target.chmod(0)
    try:
        profiles._invalidate_list_profiles_cache()
        with pytest.raises(profiles.ProfileInventoryUnreadable):
            profiles.list_profiles_api()
        code, names, err = _cli_list(capsys)
    finally:
        target.chmod(mode)
    assert (code, names) == (1, [])
    assert "The Profile directories could not be read" in err and "the Profile list is not shown" in err
    assert "warning:" not in err  # the list failed; it is not a degraded success


def test_no_profile_directory_at_all_is_an_empty_list(srv):
    shutil.rmtree(profiles._DEFAULT_HERMES_HOME / "profiles")
    profiles._invalidate_list_profiles_cache()
    assert _named(profiles.list_profiles_api()) == []


# ── Callers: the web Profile list and the cron Profile picker keep their reach ──


@pytest.mark.parametrize("failure", ["list-import-error", "import-dependency-missing"])
def test_a_user_still_sees_only_their_own_profile(srv, monkeypatch, failure):
    FAILURES[failure](monkeypatch)
    status, data, _ = srv.logged_in("700001").get("/api/profiles")
    assert status == 200
    assert [p["name"] for p in data["profiles"]] == ["700001"]


@pytest.mark.parametrize("failure", ["list-import-error", "import-dependency-missing"])
def test_the_cron_picker_still_offers_a_users_own_profile(srv, monkeypatch, failure):
    # The caller's reach decides what is offered (stubbed: one User's Profile);
    # the list it filters still holds that Profile while Hermes's listing fails.
    FAILURES[failure](monkeypatch)
    from api import routes

    monkeypatch.setattr(routes, "request_session_ownership",
                        lambda: types.SimpleNamespace(may_name_profile=lambda name: True))
    for name, other in (("700001", "700002"), ("700002", "700001")):
        monkeypatch.setattr(routes, "request_caller_reach",
                            lambda n=name: types.SimpleNamespace(includes=lambda candidate: candidate == n))
        offered = routes._available_cron_profile_names()
        assert offered == {name} and other not in offered


def test_create_still_finds_the_new_profile_while_hermes_listing_fails(srv, monkeypatch, capsys):
    published = []

    def create_profile(name, **options):
        home = srv.profile_home(name)
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("model: {}\n")
        published.append(home)

    assert operator_cli.run(["disable", "700002"]) == 0
    assert operator_cli.run(["delete", "700002", "--confirm", "700002"]) == 0
    _hermes(monkeypatch, create_profile=create_profile,
            list_profiles=_raise(ModuleNotFoundError("No module named 'ruamel'", name="ruamel")))
    capsys.readouterr()
    assert operator_cli.run(["create", "700002", "--display-name", "Somchai"]) == 0
    assert published == [srv.profile_home("700002")]
    assert "700002" in _named(profiles.list_profiles_api())


# ── Hermes "succeeds" but leaves a Profile directory out: listed anyway ──


def test_a_profile_outside_hermess_root_is_still_listed(srv, monkeypatch, capsys, tmp_path):
    # Hermes's helpers scan another root: one Profile of the same name, elsewhere.
    other_root = tmp_path / "hermes-root"
    (other_root / "700001").mkdir(parents=True)
    _hermes(monkeypatch, **_hermes_helpers(_get_profiles_root=lambda: other_root))
    rows = profiles.list_profiles_api()
    by_name = {r["name"]: r for r in rows}
    assert sorted(n for n in by_name if n != "default") == NAMES and len(rows) == len(by_name)
    for name in NAMES:  # each at its own Profile directory, not at Hermes's
        assert by_name[name]["path"] == str(srv.profile_home(name))
        assert by_name[name]["gateway_running"] is None
    code, names, err = _cli_list(capsys)
    assert (code, names) == (0, NAMES)
    assert "Hermes Agent did not list Profile 700001, 700002 at its Profile directory" in err


@needs_posix
def test_an_unreadable_hermes_root_falls_back_to_the_profile_directories(srv, monkeypatch, capsys, tmp_path):
    other_root = tmp_path / "hermes-root"
    other_root.mkdir()
    _hermes(monkeypatch, **_hermes_helpers(_get_profiles_root=lambda: other_root))
    other_root.chmod(0)
    try:
        code, names, err = _cli_list(capsys)
    finally:
        other_root.chmod(0o755)
    assert (code, names) == (0, NAMES)
    assert "Hermes Agent's Profile listing failed" in err and "could not be read" in err


def test_an_unreadable_profile_in_hermess_list_fails_the_list(srv, monkeypatch, capsys):
    def list_profiles():
        return [_listed("default", profiles._DEFAULT_HERMES_HOME, None)] + [
            _listed(n, srv.profile_home(n), None) for n in NAMES]

    real_stats = profiles._get_profile_skills_stats

    def stats(path):
        if str(path) == str(srv.profile_home("700002")):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stats(path)

    _hermes(monkeypatch, list_profiles=list_profiles)
    monkeypatch.setattr(profiles, "_get_profile_skills_stats", stats)
    code, names, err = _cli_list(capsys)
    assert (code, names) == (1, []) and "could not be read" in err


def test_a_profile_hermess_list_leaves_out_is_still_listed(srv, monkeypatch, capsys):
    def list_profiles():  # an entry it could not read is silently skipped
        return [_listed("default", profiles._DEFAULT_HERMES_HOME, "listed-model"),
                _listed("700001", srv.profile_home("700001"), "listed-model")]

    _hermes(monkeypatch, list_profiles=list_profiles)
    rows = {r["name"]: r for r in profiles.list_profiles_api()}
    assert rows["700001"]["model"] == "listed-model" and rows["700002"]["model"] is None
    code, names, err = _cli_list(capsys)
    assert (code, names) == (0, NAMES) and "did not list Profile 700002 at its Profile directory" in err


# ── The inventory cannot be read: every caller fails, none sees "no Profiles" ──


def _inventory_unreadable(monkeypatch):
    def unreadable(*_args, **_kwargs):
        raise profiles.ProfileInventoryUnreadable(
            "The Profile directories could not be read (/srv/secret/profiles: denied); the Profile list is not shown.")

    monkeypatch.setattr(profiles, "_filesystem_profile_inventory", unreadable)
    profiles._invalidate_list_profiles_cache()


def test_an_unreadable_inventory_fails_the_web_list_without_detail(srv, monkeypatch):
    client = srv.logged_in("700001")
    _inventory_unreadable(monkeypatch)
    status, data, _ = client.get("/api/profiles")
    assert status == 500
    assert "/srv/secret" not in str(data) and "profiles" not in str(data.get("profiles", ""))


def test_an_unreadable_inventory_fails_the_cron_picker(srv, monkeypatch):
    from api import routes

    _inventory_unreadable(monkeypatch)
    monkeypatch.setattr(routes, "request_session_ownership",
                        lambda: types.SimpleNamespace(may_name_profile=lambda name: True))
    monkeypatch.setattr(routes, "request_caller_reach",
                        lambda: types.SimpleNamespace(includes=lambda candidate: candidate == "700001"))
    with pytest.raises(profiles.ProfileInventoryUnreadable):
        routes._available_cron_profile_names()


def test_an_unreadable_inventory_during_create_leaves_the_profile_disabled(srv, monkeypatch, capsys):
    from api import roster

    def create_profile(name, **options):
        home = srv.profile_home(name)
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("model: {}\n")
        _inventory_unreadable(monkeypatch)  # the directories become unreadable after Hermes created it

    assert operator_cli.run(["disable", "700002"]) == 0
    assert operator_cli.run(["delete", "700002", "--confirm", "700002"]) == 0
    _hermes(monkeypatch, create_profile=create_profile)
    capsys.readouterr()
    assert operator_cli.run(["create", "700002", "--display-name", "Somchai"]) == 1
    assert "created only in part and is disabled" in capsys.readouterr().err
    assert roster.view("700002")["status"] == "disabled"


def test_an_unreadable_inventory_is_not_taken_as_no_renamed_root(srv, monkeypatch):
    profiles._invalidate_root_profile_cache()
    _inventory_unreadable(monkeypatch)
    assert profiles._is_root_profile("default") is True
    assert profiles._is_root_profile("700001") is False
    assert profiles._root_profile_name_cache_loaded is False  # not cached: retried next time


@needs_posix
def test_an_unreadable_profile_directory_fails_the_list_not_drops_it(srv, monkeypatch, capsys):
    skills = srv.profile_home("700002") / "skills"
    skills.mkdir(exist_ok=True)
    (skills / "a").mkdir(exist_ok=True)
    home = srv.profile_home("700002")
    mode = home.stat().st_mode
    home.chmod(0)
    try:
        profiles._SKILLS_STATS_CACHE.clear()
        code, names, err = _cli_list(capsys)
    finally:
        home.chmod(mode)
    assert (code, names) == (1, []) and "could not be read" in err
