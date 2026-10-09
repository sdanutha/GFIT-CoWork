"""What was left of the first-run onboarding is gone (remove-admin ticket 18).

Setup is the Operator's, on the server, with Hermes Agent's own `hermes setup`
(CONTEXT.md: Setup). The web app keeps no onboarding module, flag, strings or
docs; a settings file written while the wizard existed still loads.
"""

import json
from pathlib import Path

import api.config as config
import api.routes as routes

REPO = Path(__file__).resolve().parents[1]


def test_the_onboarding_module_is_gone():
    assert not (REPO / "api" / "onboarding.py").exists()
    for path in (REPO / "api").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "api.onboarding" not in src, path.name
    assert not hasattr(routes, "_onboarding_request_is_local")


def test_onboarding_completed_is_not_a_setting(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_path)
    settings_path.write_text(
        json.dumps({"send_key": "ctrl+enter", "onboarding_completed": True}), encoding="utf-8"
    )

    loaded = config.load_settings()
    assert loaded["send_key"] == "ctrl+enter"
    assert "onboarding_completed" not in loaded
    assert "onboarding_completed" not in config._SETTINGS_ALLOWED_KEYS

    config.save_settings({"send_key": "enter", "onboarding_completed": True})
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["send_key"] == "enter"
    assert "onboarding_completed" not in on_disk


def test_an_install_that_finished_the_old_wizard_keeps_cli_sessions_off(monkeypatch, tmp_path):
    """The #3988 grandfather rule: a settings file from a finished wizard and no
    show_cli_sessions choice is an established install, so its sidebar does not change."""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_path)
    settings_path.write_text(json.dumps({"onboarding_completed": True}), encoding="utf-8")

    assert config.load_settings()["show_cli_sessions"] is False


def test_no_onboarding_strings_are_left():
    src = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")
    assert "// onboarding" not in src
    assert "oauth_codex_" not in src
    assert "oauth_login_codex" not in src


def test_setup_docs_replace_the_onboarding_docs():
    assert (REPO / "docs" / "setup.md").is_file()
    assert (REPO / "docs" / "setup-agent-checklist.md").is_file()
    assert not (REPO / "docs" / "onboarding.md").exists()
    assert not (REPO / "docs" / "onboarding-agent-checklist.md").exists()
    for rel in ("AGENTS.md", "README.md", "docs/CONTRACTS.md", "docs/setup.md",
                "docs/setup-agent-checklist.md"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "onboarding.md" not in src, rel
        assert "onboarding-agent-checklist.md" not in src, rel
