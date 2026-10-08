"""Regression test: credential-pool detection must be profile-scoped.

Guards the cross-profile leak Codex caught on #4247 — _CREDENTIAL_POOL_CACHE
was keyed by provider id alone, so a custom provider configured under profile A
made profile B (no pool entry) report has_key=True from a stale cache hit.

Detection now reads the active Profile's own auth.json (ticket 16), so each
lookup answers from that Profile's file and never from another's.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.config as config  # noqa: E402


def test_has_explicit_pool_credentials_is_profile_scoped(monkeypatch, tmp_path):
    """A pool in profile A's auth.json must not satisfy a lookup under profile B."""
    config._CREDENTIAL_POOL_CACHE.clear()
    profile_a = tmp_path / "profileA" / "auth.json"
    profile_b = tmp_path / "profileB" / "auth.json"
    profile_a.parent.mkdir()
    profile_b.parent.mkdir()
    entry = {"source": "config_yaml", "label": "custom:bothub", "key_source": "config_yaml"}
    profile_a.write_text(json.dumps({"credential_pool": {"custom:bothub": [entry]}}), encoding="utf-8")
    profile_b.write_text(json.dumps({"credential_pool": {}}), encoding="utf-8")
    active = {"path": profile_a}

    monkeypatch.setattr(config, "_get_auth_store_path", lambda: active["path"])
    monkeypatch.setattr(config, "_resolve_provider_alias", lambda p: p)

    # Under profile A: custom:bothub IS configured (real entry, not ambient gh_cli).
    assert config._has_explicit_pool_credentials("custom:bothub") is True

    # Switch to profile B (no pool entry). It must NOT see A's pool.
    active["path"] = profile_b
    assert config._has_explicit_pool_credentials("custom:bothub") is False

    # Back to A — still configured.
    active["path"] = profile_a
    assert config._has_explicit_pool_credentials("custom:bothub") is True

    config._CREDENTIAL_POOL_CACHE.clear()
