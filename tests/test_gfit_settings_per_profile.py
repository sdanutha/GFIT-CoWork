"""GFIT-CoWork: web settings belong to each Profile (ADR 0006, remove-admin ticket 02).

A User changes their own settings (theme, voice, composer buttons, ...); they
are kept in their Profile and never show up for another User. Settings that
belong to the whole Deployment (the assistant's name, API redaction, usage
sync, ...) stay in the Deployment's settings.json, which only the Operator
edits; a User who tries to change one is refused. A Profile with no settings of
its own starts from the Deployment's.
"""
from __future__ import annotations

import json

import pytest

import api.config as config
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={ALICE: "Alice", BOB: "Bob"}, profile_names=[ALICE, BOB]) as s:
        monkeypatch.setattr(config, "SETTINGS_FILE", s.state / "settings.json")
        yield s


def _settings(client) -> dict:
    status, body, _ = client.get("/api/settings")
    assert status == 200, body
    return body


def _own_file(srv, uid) -> dict:
    return json.loads((srv.profile_home(uid) / "webui_state" / "settings.json").read_text())


def test_a_users_settings_change_is_theirs_alone(srv):
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)

    status, body, _ = alice.post("/api/settings", {"theme": "light", "font_size": "large", "send_key": "ctrl+enter"})

    assert status == 200, body
    assert (body["theme"], body["font_size"], body["send_key"]) == ("light", "large", "ctrl+enter")
    mine = _settings(alice)
    assert (mine["theme"], mine["font_size"], mine["send_key"]) == ("light", "large", "ctrl+enter")
    theirs = _settings(bob)
    assert (theirs["theme"], theirs["font_size"], theirs["send_key"]) == ("dark", "default", "enter")


def test_a_users_settings_are_kept_in_their_profile_not_the_deployments_file(srv):
    alice = srv.logged_in(ALICE)

    alice.post("/api/settings", {"tts_enabled": True, "sidebar_density": "detailed"})

    assert _own_file(srv, ALICE) == {"tts_enabled": True, "sidebar_density": "detailed"}
    assert not config.SETTINGS_FILE.exists()
    assert not (srv.profile_home(BOB) / "webui_state" / "settings.json").exists()


def test_speech_settings_a_user_saved_are_reported_as_theirs(srv):
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)
    alice.post("/api/settings", {"tts_engine": "browser", "tts_rate": 1.5})

    assert set(_settings(alice)["persisted_speech_keys"]) == {"tts_engine", "tts_rate"}
    assert _settings(bob)["persisted_speech_keys"] == []


@pytest.mark.parametrize("change", [
    {"api_redact_enabled": False},
    {"bot_name": "Not Hermes"},
    {"sync_to_insights": True},
    {"auto_title_refresh_every": "5"},
    {"default_workspace": "/tmp"},
    {"theme": "light", "api_redact_enabled": False},
])
def test_a_user_cannot_change_a_deployment_setting(srv, change):
    config.SETTINGS_FILE.write_text(json.dumps({"api_redact_enabled": True}))
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/settings", change)

    assert status == 403, body
    assert "Operator" in body["error"]
    assert json.loads(config.SETTINGS_FILE.read_text()) == {"api_redact_enabled": True}
    assert not (srv.profile_home(ALICE) / "webui_state" / "settings.json").exists()


def test_a_profile_with_no_settings_of_its_own_starts_from_the_deployments(srv):
    config.SETTINGS_FILE.write_text(json.dumps({"theme": "light", "skin": "slate", "bot_name": "Athena"}))
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)

    alice.post("/api/settings", {"theme": "dark"})

    assert (_settings(bob)["theme"], _settings(bob)["skin"], _settings(bob)["bot_name"]) == ("light", "slate", "Athena")
    assert (_settings(alice)["theme"], _settings(alice)["bot_name"]) == ("dark", "Athena")
    assert json.loads(config.SETTINGS_FILE.read_text())["theme"] == "light"


def test_an_invalid_value_is_ignored_as_before(srv):
    alice = srv.logged_in(ALICE)
    alice.post("/api/settings", {"font_size": "large"})

    status, body, _ = alice.post("/api/settings", {"font_size": "enormous", "pinned_sessions_limit": 500})

    assert status == 200, body
    assert _settings(alice)["font_size"] == "large"
    assert _settings(alice)["pinned_sessions_limit"] == 3


def test_a_request_with_no_user_sees_the_deployments_settings_alone(srv):
    # Worker threads and the login page have no request Profile.
    config.SETTINGS_FILE.write_text(json.dumps({"theme": "light"}))
    srv.logged_in(ALICE).post("/api/settings", {"theme": "dark"})

    assert config.load_settings()["theme"] == "light"


def test_the_deployment_settings_are_the_ones_named():
    # Adding a setting means deciding whose it is: change this list on purpose.
    assert config._SETTINGS_DEPLOYMENT_KEYS == {
        "default_workspace", "onboarding_completed", "sync_to_insights", "api_redact_enabled",
        "dashboard_plugins", "auth_disabled_acknowledged", "bot_name",
        "auto_title_refresh_every", "inflight_state_max_sessions", "inflight_state_max_messages",
        "inflight_state_max_tool_calls", "inflight_state_max_string_chars", "inflight_state_max_json_chars",
    }
    assert config.PERSONAL_SETTINGS_KEYS == config._SETTINGS_ALLOWED_KEYS - config._SETTINGS_DEPLOYMENT_KEYS
    assert {"theme", "skin", "language", "tts_voice", "send_key"} <= config.PERSONAL_SETTINGS_KEYS
