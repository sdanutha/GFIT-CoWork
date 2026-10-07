"""Tests for issue #1612: renamed root profile must resolve to ~/.hermes,
not ~/.hermes/profiles/<name>.

A renamed root/default Hermes profile (`is_default=True` on the agent side
but with a display name like `kinni`) was being treated as a named profile
directory under `~/.hermes/profiles/kinni`, which doesn't exist. Every
`if name == 'default':` site in api/profiles.py fell through to the wrong
filesystem path with `Profile 'kinni' does not exist.`

Fix: centralize the "is this the root?" check in `_is_root_profile(name)`
and replace each scattered `if name == 'default':` with it.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ── _is_root_profile core ───────────────────────────────────────────────────


def test_is_root_profile_default_alias():
    """Legacy 'default' literal always resolves as root, regardless of cache state."""
    import api.profiles as p
    p._invalidate_root_profile_cache()
    assert p._is_root_profile('default') is True


def test_is_root_profile_empty_or_none_is_false():
    """Empty/None name is NOT root — caller code decides what to do."""
    import api.profiles as p
    assert p._is_root_profile('') is False
    assert p._is_root_profile(None) is False


def test_is_root_profile_renamed_root_via_list_profiles_api(monkeypatch):
    """A profile name reported by list_profiles_api with is_default=True is treated as root."""
    import api.profiles as p

    monkeypatch.setattr(p, 'list_profiles_api', lambda: [
        {'name': 'kinni', 'is_default': True, 'path': str(p._DEFAULT_HERMES_HOME)},
        {'name': 'haku', 'is_default': False, 'path': '/tmp/profiles/haku'},
    ])
    p._invalidate_root_profile_cache()

    assert p._is_root_profile('kinni') is True
    assert p._is_root_profile('haku') is False
    assert p._is_root_profile('default') is True


def test_is_root_profile_caches_results(monkeypatch):
    """Repeated calls don't re-invoke list_profiles_api — once-per-mutation memoization."""
    import api.profiles as p

    calls = {'n': 0}
    def fake_list():
        calls['n'] += 1
        return [{'name': 'kinni', 'is_default': True, 'path': '/tmp/.hermes'}]
    monkeypatch.setattr(p, 'list_profiles_api', fake_list)
    p._invalidate_root_profile_cache()

    p._is_root_profile('kinni')
    p._is_root_profile('kinni')
    p._is_root_profile('haku')
    assert calls['n'] == 1, "Cache should be hit after first lookup"


def test_is_root_profile_invalidation_drops_stale(monkeypatch):
    """Explicit invalidation forces re-query on next call."""
    import api.profiles as p

    seq = [
        [{'name': 'kinni', 'is_default': True, 'path': '/tmp/.hermes'}],
        [{'name': 'noblepro', 'is_default': True, 'path': '/tmp/.hermes'}],
    ]
    monkeypatch.setattr(p, 'list_profiles_api', lambda: seq[0] if seq else [])

    p._invalidate_root_profile_cache()
    assert p._is_root_profile('kinni') is True
    assert p._is_root_profile('noblepro') is False

    # Simulate rename — drop first state, second is now the truth
    seq.pop(0)
    p._invalidate_root_profile_cache()
    assert p._is_root_profile('kinni') is False
    assert p._is_root_profile('noblepro') is True


def test_is_root_profile_handles_list_profiles_failure(monkeypatch):
    """If list_profiles_api raises, fall back to literal-default-only — never raise."""
    import api.profiles as p

    def boom():
        raise RuntimeError("hermes_cli explosion")
    monkeypatch.setattr(p, 'list_profiles_api', boom)
    p._invalidate_root_profile_cache()

    # 'default' still works (handled before list_profiles_api call).
    assert p._is_root_profile('default') is True
    # Other names return False on failure.
    assert p._is_root_profile('kinni') is False


# ── get_active_hermes_home: returns _DEFAULT_HERMES_HOME for renamed root ──


def test_get_active_hermes_home_returns_default_for_renamed_root(tmp_path, monkeypatch):
    """The core bug: a renamed root profile must resolve to _DEFAULT_HERMES_HOME,
    not _DEFAULT_HERMES_HOME / 'profiles' / <name>."""
    import api.profiles as p

    monkeypatch.setattr(p, '_DEFAULT_HERMES_HOME', tmp_path)
    monkeypatch.setattr(p, 'list_profiles_api', lambda: [
        {'name': 'kinni', 'is_default': True, 'path': str(tmp_path)},
    ])
    p._invalidate_root_profile_cache()
    monkeypatch.setattr(p, '_active_profile', 'kinni')

    result = p.get_active_hermes_home()
    assert result == tmp_path, f"Expected {tmp_path}, got {result}"


def test_get_active_hermes_home_returns_named_for_real_named_profile(tmp_path, monkeypatch):
    """Backward compat: a real named (non-default) profile still resolves to profiles/<name>."""
    import api.profiles as p

    profile_dir = tmp_path / 'profiles' / 'haku'
    profile_dir.mkdir(parents=True)
    monkeypatch.setattr(p, '_DEFAULT_HERMES_HOME', tmp_path)
    monkeypatch.setattr(p, 'list_profiles_api', lambda: [
        {'name': 'kinni', 'is_default': True, 'path': str(tmp_path)},
        {'name': 'haku', 'is_default': False, 'path': str(profile_dir)},
    ])
    p._invalidate_root_profile_cache()
    monkeypatch.setattr(p, '_active_profile', 'haku')

    result = p.get_active_hermes_home()
    assert result == profile_dir


def test_delete_profile_blocks_renamed_root(tmp_path, monkeypatch):
    """delete_profile_api on a renamed root must refuse, same as 'default'."""
    import api.profiles as p

    monkeypatch.setattr(p, '_DEFAULT_HERMES_HOME', tmp_path)
    monkeypatch.setattr(p, 'list_profiles_api', lambda: [
        {'name': 'kinni', 'is_default': True, 'path': str(tmp_path)},
    ])
    p._invalidate_root_profile_cache()

    with pytest.raises(ValueError, match="Cannot delete the default profile"):
        p.delete_profile_api('kinni')


# ── Cleanup: invalidate cache between tests so they don't leak ─────────────


@pytest.fixture(autouse=True)
def _invalidate_cache_around_test():
    import api.profiles as p
    p._invalidate_root_profile_cache()
    yield
    p._invalidate_root_profile_cache()
