"""GFIT-CoWork: a User chooses their own Profile's models (ADR 0006, remove-admin ticket 03).

The default model, the auxiliary models and the reasoning settings live in the
Profile's ``config.yaml``. A User may change them for their own Profile; the
change never reaches another Profile or the Hermes root config. A model's
endpoint (``base_url``) and API key are provider setup, which is the
Operator's: a User who sends them is refused and nothing changes.
"""
from __future__ import annotations

import pytest
import yaml

import api.config as config
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
START = "model:\n  default: gpt-4o\n  provider: openai\n  base_url: http://127.0.0.1:9/v1\n"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={ALICE: "Alice", BOB: "Bob"}, profile_names=[ALICE, BOB]) as s:
        monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
        monkeypatch.setattr(config, "_DEFAULT_HERMES_HOME", s.hermes_home, raising=False)
        (s.hermes_home / "config.yaml").write_text(START, encoding="utf-8")
        for uid in (ALICE, BOB):
            (s.profile_home(uid) / "config.yaml").write_text(START, encoding="utf-8")
        with config._yaml_file_cache_lock:
            config._yaml_file_cache.clear()
        yield s
        with config._yaml_file_cache_lock:
            config._yaml_file_cache.clear()


def _config(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _profile_config(srv, uid) -> dict:
    return _config(srv.profile_home(uid) / "config.yaml")


def _untouched(srv, *uids):
    for path in [srv.hermes_home / "config.yaml"] + [srv.profile_home(u) / "config.yaml" for u in uids]:
        assert path.read_text(encoding="utf-8") == START, path


def test_a_user_sets_their_own_default_model(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/default-model", {"model": "gpt-4o-mini", "provider": "openai"})

    assert status == 200, body
    assert _profile_config(srv, ALICE)["model"]["default"] == "gpt-4o-mini"
    _untouched(srv, BOB)


def test_a_user_sets_their_own_auxiliary_model(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post(
        "/api/model/set", {"scope": "auxiliary", "task": "vision", "provider": "openai", "model": "gpt-4o-mini"})

    assert status == 200, body
    assert _profile_config(srv, ALICE)["auxiliary"]["vision"]["model"] == "gpt-4o-mini"
    _untouched(srv, BOB)


def test_a_user_sets_their_own_reasoning_effort(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/reasoning", {"effort": "high"})

    assert status == 200, body
    assert _profile_config(srv, ALICE)["agent"]["reasoning_effort"] == "high"
    _untouched(srv, BOB)


def test_one_users_model_change_does_not_show_for_another(srv):
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)
    alice.post("/api/default-model", {"model": "gpt-4o-mini", "provider": "openai"})

    assert bob.get("/api/settings")[1]["default_model"] == "gpt-4o"
    assert alice.get("/api/settings")[1]["default_model"] == "gpt-4o-mini"


@pytest.mark.parametrize("route,body", [
    ("/api/default-model", {"model": "gpt-4o", "advanced": {"base_url": "https://elsewhere.example/v1"}}),
    ("/api/default-model", {"model": "gpt-4o", "advanced": {"api_key": "sk-mine"}}),
    ("/api/model/set", {"scope": "main", "model": "gpt-4o", "advanced": {"base_url": ""}}),
    ("/api/model/set", {"scope": "auxiliary", "task": "vision", "model": "gpt-4o", "advanced": {"api_key_clear": True}}),
])
def test_a_user_cannot_set_a_models_endpoint_or_key(srv, route, body):
    alice = srv.logged_in(ALICE)

    status, answer, _ = alice.post(route, body)

    assert status == 403, answer
    assert "Operator" in answer["error"]
    _untouched(srv, ALICE, BOB)


def test_a_user_may_set_the_other_advanced_options(srv):
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/model/set", {
        "scope": "auxiliary", "task": "vision", "provider": "openai", "model": "gpt-4o-mini",
        "advanced": {"timeout": "30", "extra_body": {"seed": 1}},
    })

    assert status == 200, body
    vision = _profile_config(srv, ALICE)["auxiliary"]["vision"]
    assert (vision["timeout"], vision["extra_body"]) == (30, {"seed": 1})


@pytest.mark.parametrize("change", [
    ("/api/default-model", {"model": "gpt-4o-mini", "provider": "openai"}),
    ("/api/model/set", {"scope": "auxiliary", "task": "vision", "provider": "custom", "model": "local-vision"}),
])
def test_saving_a_model_in_a_users_profile_does_not_lock_up_the_server(srv, change):
    # Resolving a model reads the Profile's config view, which takes the config
    # lock; done under that lock it self-deadlocked every User's save and then
    # every request after it.
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)

    status, body, _ = alice.post(*change)

    assert status == 200, body
    assert bob.get("/api/settings")[0] == 200
