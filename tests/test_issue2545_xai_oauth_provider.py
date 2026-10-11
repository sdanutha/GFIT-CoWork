import copy

import api.config as config
import api.profiles as profiles


def _with_config(monkeypatch, tmp_path, cfg):
    old_cfg = copy.deepcopy(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg.update(copy.deepcopy(cfg))
    config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    def restore():
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()

    return restore


def test_xai_oauth_model_picker_group_uses_live_catalog(monkeypatch, tmp_path):
    restore = _with_config(
        monkeypatch,
        tmp_path,
        {
            "model": {"provider": "xai-oauth", "default": "grok-4.20"},
            "providers": {"xai-oauth": {}},
        },
    )
    monkeypatch.setattr(config, "_read_live_provider_model_ids", lambda pid: ["grok-4.20"] if pid == "xai-oauth" else [])
    try:
        result = config.get_available_models()
        group = next(g for g in result["groups"] if g["provider_id"] == "xai-oauth")
        assert group["provider"] == "xAI Grok OAuth"
        assert group["models"] == [{"id": "grok-4.20", "label": "Grok 4.20"}]
        assert result["active_provider"] == "xai-oauth"
    finally:
        restore()
