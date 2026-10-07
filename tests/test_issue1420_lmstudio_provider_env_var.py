"""Regression: LM Studio missing from Settings → Providers after onboarding (#1420).

The bug: users who completed the onboarding wizard with the LM Studio provider —
either via the "Open / self-hosted" category in the wizard, or by hand-editing
config.yaml + .env to point at an LM Studio instance — would see LM Studio
listed in the model picker and could chat just fine, but Settings → Providers
showed *no* LM Studio entry, or showed it with `has_key=False, configurable=False`
even when LMSTUDIO_API_KEY was already in `~/.hermes/.env`.

Root cause (verified by reproduction in the original investigation, then by
the regression tests below):

`api/providers.py:_PROVIDER_ENV_VAR` is the dict that maps each provider id
to the .env / os.environ key the WebUI should look for. It's used by:

  1. `_provider_has_key(pid)` — does an env-var-based detection lookup, returns
     False (and `key_source='none'`) if the provider id isn't in the dict.
  2. `get_providers()` line 364 — sets `configurable = pid in _PROVIDER_ENV_VAR`
     so the UI knows whether to render the "Add API key" form for that provider.

Without an `lmstudio: "LMSTUDIO_API_KEY"` entry, both checks miss: the env var
is invisible to the Settings panel, AND the UI hides the surface that would
let the user fix the situation by typing a new key.

This is the same bug-shape as #1410 (Ollama Cloud / local Ollama env var
collision) and is fixed by the same kind of edit: add the missing mapping in
`_PROVIDER_ENV_VAR`. Unlike #1410, there's no collision concern for LM Studio
because LMSTUDIO_API_KEY isn't shared with any other provider's runtime.

Reporters: @chwps, @AdoneyGalvan (#1420 thread).
"""

import sys
import types

import api.config as config


def _install_fake_hermes_cli(monkeypatch):
    """Stub hermes_cli modules so tests are deterministic and offline.

    Mirrors the helper in test_provider_management.py — kept inline here so
    this regression test stays self-contained and survives refactors there.
    """
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []

    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: []
    fake_models.provider_model_ids = lambda pid: []

    fake_auth = types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _pid: {}

    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    monkeypatch.delitem(sys.modules, "agent.credential_pool", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)

    try:
        from api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass


def _swap_in_test_config(extra_cfg):
    """Snapshot config.cfg, replace with a minimal test config; return restore-fn."""
    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    config.cfg.update(extra_cfg)
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    def _restore():
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime

    return _restore


class TestIssue1420LMStudioProviderEnvVar:
    """LM Studio's env var must be in `_PROVIDER_ENV_VAR` so Settings detects it.

    Three angles of the same fix:
      1. The dict literally contains the mapping (catches accidental removal).
      2. With `LMSTUDIO_API_KEY` in env, get_providers() reports has_key=True.
      3. Without env but with `providers.lmstudio.api_key` in config.yaml,
         get_providers() also reports has_key=True (defense for users who
         configured via config.yaml directly).
      4. LM Studio is rendered as `configurable=True` so the UI shows the
         "Add API key" form when no key is configured.
    """

    def test_lmstudio_in_provider_env_var_dict(self):
        """`_PROVIDER_ENV_VAR['lmstudio']` must equal `'LM_API_KEY'` (canonical, agent-aligned).

        The original #1420 fix used `'LMSTUDIO_API_KEY'`. After #1500 (cross-tool
        env-var alignment with the agent CLI) the canonical name is `LM_API_KEY`,
        and `LMSTUDIO_API_KEY` is preserved as a read-only legacy alias in
        `_PROVIDER_ENV_VAR_ALIASES` so existing users don't lose detection.
        """
        from api.providers import _PROVIDER_ENV_VAR, _PROVIDER_ENV_VAR_ALIASES
        assert "lmstudio" in _PROVIDER_ENV_VAR, (
            "_PROVIDER_ENV_VAR is missing the 'lmstudio' entry — Settings → "
            "Providers will render LM Studio as has_key=False / "
            "configurable=False. See #1420."
        )
        assert _PROVIDER_ENV_VAR["lmstudio"] == "LM_API_KEY", (
            f"_PROVIDER_ENV_VAR['lmstudio'] = {_PROVIDER_ENV_VAR['lmstudio']!r}, "
            f"expected 'LM_API_KEY' to match the agent CLI's "
            f"hermes_cli/auth.py:lmstudio.api_key_env_vars. See #1500."
        )
        # The legacy alias must still be registered so users with the pre-#1500
        # env var don't lose detection on upgrade.
        assert "lmstudio" in _PROVIDER_ENV_VAR_ALIASES, (
            "_PROVIDER_ENV_VAR_ALIASES['lmstudio'] missing — pre-#1500 users "
            "with LMSTUDIO_API_KEY in their .env will see Settings flip to "
            "'no key' on upgrade.  Keep the alias for at least a few releases."
        )
        assert "LMSTUDIO_API_KEY" in _PROVIDER_ENV_VAR_ALIASES["lmstudio"], (
            f"Expected 'LMSTUDIO_API_KEY' as a legacy alias for lmstudio, got "
            f"{_PROVIDER_ENV_VAR_ALIASES['lmstudio']!r}."
        )
