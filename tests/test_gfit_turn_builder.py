"""GFIT-CoWork: one turn builder says what every WebUI Hermes Agent turn is told.

Architecture review round 6, candidate 5. The streaming turn and the
non-streaming chat route used to write their own Workspace system prompt, and
the copies disagreed (the route named the live Workspace and dropped the
session's personality). Both ask ``api.turn_builder`` now.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from api.turn_builder import (
    TurnPrompts,
    personality_prompt,
    turn_prompts,
    webui_agent,
    workspace_system_message,
)

CONFIG = {"agent": {"personalities": {
    "pirate": {"system_prompt": "Talk like a pirate.", "tone": "salty", "style": "brief"},
    "plain": "Be plain.",
}}}


def _session(**fields):
    defaults = {"workspace": "/live/ws", "created_workspace": "/created/ws", "profile": "521740", "personality": None}
    return SimpleNamespace(**{**defaults, **fields})


def test_the_system_message_names_the_creation_workspace_and_the_prefix_the_live_one():
    prompts = turn_prompts(_session(), session_id="s1", config_data={})
    assert isinstance(prompts, TurnPrompts)
    assert prompts.system_message.startswith("Active workspace at session start: /created/ws\n")
    assert prompts.user_prefix == "[Workspace::v1: /live/ws]\n"


def test_a_session_with_no_creation_workspace_names_the_live_one():
    prompts = turn_prompts(_session(created_workspace=None), session_id="s1", config_data={})
    assert prompts.system_message == workspace_system_message("/live/ws")


def test_the_personality_and_surface_context_go_in_the_ephemeral_prompt():
    prompts = turn_prompts(_session(personality="pirate"), session_id="s1", config_data=CONFIG)
    ephemeral = prompts.ephemeral_system_prompt
    assert ephemeral.startswith("Talk like a pirate.\nTone: salty\nStyle: brief")
    assert "- Session ID: s1" in ephemeral
    assert "- Profile: 521740" in ephemeral
    assert "- Workspace: /created/ws" in ephemeral
    assert "WebUI progress guidance:" in ephemeral
    assert "WebUI progress guidance:" not in prompts.system_message


def test_a_session_without_a_personality_has_none():
    prompts = turn_prompts(_session(), session_id="s1", config_data=CONFIG)
    assert prompts.ephemeral_system_prompt.startswith("WebUI session context:")


def test_personality_prompt_reads_dict_and_text_personalities():
    assert personality_prompt("plain", CONFIG) == "Be plain."
    assert personality_prompt("missing", CONFIG) is None
    assert personality_prompt(None, CONFIG) is None


def test_webui_agent_is_made_with_the_webui_platform_quiet_and_the_bundle():
    class Agent:
        def __init__(self, model, provider, base_url, api_key, platform, quiet_mode, enabled_toolsets, session_id,
                     api_mode=None, callback=None):
            self.kwargs = dict(locals())
            self.kwargs.pop("self")

    bundle = {"api_mode": "chat", "acp_command": "x", "acp_args": [], "credential_pool": None}
    bundle.update(provider="p", base_url="u", api_key="k")
    agent = webui_agent(Agent, bundle, model="m", session_id="s1", toolsets=["files"])
    assert agent.kwargs == {
        "model": "m", "provider": "p", "base_url": "u", "api_key": "k", "platform": "webui",
        "quiet_mode": True, "enabled_toolsets": ["files"], "session_id": "s1",
        "api_mode": "chat", "callback": None,
    }


def test_the_non_streaming_chat_route_gets_the_builders_prompts(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.routes as routes

    session_dir = tmp_path / "state" / "sessions"
    session_dir.mkdir(parents=True)
    cfg = {"model": "test-model", "provider": "test-provider", **CONFIG}
    monkeypatch.setattr("api.config.SESSION_DIR", session_dir)
    monkeypatch.setattr("api.config.SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(routes, "get_session", models.get_session)
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    monkeypatch.setattr(config, "get_config_for_profile_home", lambda _home: cfg)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value, **_kw: tmp_path)
    monkeypatch.setattr(routes, "load_settings", lambda: {})
    monkeypatch.setattr(routes, "_resolve_cli_toolsets", lambda: [])
    session = models.Session(
        session_id="gfit_turn_builder_sync", workspace=str(tmp_path), model="test-model",
        model_provider="test-provider", personality="pirate",
    )
    session.created_workspace = "/created/ws"
    session.save(touch_updated_at=False)

    captured = {}

    class FakeAgent:
        def __init__(self, **_kwargs):
            self.ephemeral_system_prompt = None

        def run_conversation(self, **kwargs):
            captured["system_message"] = kwargs["system_message"]
            captured["user_message"] = kwargs["user_message"]
            captured["ephemeral"] = self.ephemeral_system_prompt
            return {"messages": [{"role": "assistant", "content": "ok"}], "final_response": "ok", "completed": True}

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=FakeAgent))

    class Handler:
        headers = {}
        status = None
        wfile = SimpleNamespace(write=lambda _body: None)

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

    handler = Handler()
    routes._handle_chat_sync(handler, {"session_id": session.session_id, "message": "hello", "workspace": str(tmp_path)})

    assert handler.status == 200
    assert captured["system_message"].startswith("Active workspace at session start: /created/ws\n")
    assert captured["user_message"] == f"[Workspace::v1: {tmp_path}]\nhello"
    assert (captured["ephemeral"] or "").startswith("Talk like a pirate.")
