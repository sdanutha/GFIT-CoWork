from pathlib import Path

import api.config as config
import api.paths as paths
import api.profiles as profiles


def test_profiles_unwrap_profile_home_to_base():
    base = Path('/tmp/hermes-base')
    profile_home = base / 'profiles' / 'webui'
    assert profiles._unwrap_profile_home_to_base(profile_home) == base


def test_default_hermes_home_returns_path_object():
    home = config._platform_default_hermes_home()
    assert isinstance(home, Path)


def test_default_hermes_home_is_dot_hermes_in_home():
    assert config._platform_default_hermes_home() == paths.HOME / ".hermes"


def test_profiles_base_home_uses_shared_path_helper(monkeypatch, tmp_path):
    """profiles._resolve_base_hermes_home() must share config's path helper so
    the active-profile pointer never diverges from config.STATE_DIR (#2905)."""
    sentinel = tmp_path / "sentinel-home"
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_BASE_HOME", raising=False)
    monkeypatch.setattr(paths, "_platform_default_hermes_home", lambda: sentinel)

    assert profiles._resolve_base_hermes_home() == sentinel
