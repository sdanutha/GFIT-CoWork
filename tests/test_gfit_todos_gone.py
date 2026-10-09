"""GFIT-CoWork: the web app has no Todos (remove-todos tickets 01-03).

The sidebar Todos panel, the Workspace Todos tab and its setting are gone, and
so is the ``todo_state`` pipeline that fed them. The agent keeps its ``todo``
tool; its calls still show as ordinary tool cards.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import json

import pytest

from tests._gfit_server import gfit_server as _gfit_server

USER = "600001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(
        monkeypatch, tmp_path, users={USER: "User"}, profile_names=[USER],
    ) as s:
        yield s


def _page(srv) -> str:
    status, body, _ = srv.logged_in(USER).get("/")
    assert status == 200
    assert isinstance(body, str)
    return body


def test_the_workspace_panel_has_no_todos_tab_and_settings_no_checkbox(srv):
    page = _page(srv)
    assert 'id="workspaceFilesTab"' in page or "switchWorkspacePanelTab('files')" in page
    for gone in ("workspaceTodosTab", "workspaceTodosPanel", "settingsWorkspaceTodosTab",
                 "switchWorkspacePanelTab('todos')"):
        assert gone not in page, gone


def test_the_workspace_todos_setting_is_not_returned_or_stored(srv):
    client = srv.logged_in(USER)
    status, body, _ = client.post("/api/settings", {"workspace_todos_tab": True, "send_key": "ctrl+enter"})
    assert status == 200, body
    status, body, _ = client.get("/api/settings")
    assert status == 200
    assert body["send_key"] == "ctrl+enter"
    assert "workspace_todos_tab" not in body


def test_the_sidebar_has_no_todos_panel_or_rail_button(srv):
    page = _page(srv)
    assert 'data-panel="chat"' in page
    for gone in ('data-panel="todos"', "switchPanel('todos'", 'id="panelTodos"', 'id="todoPanel"'):
        assert gone not in page, gone


def _served_scripts(srv) -> dict[str, str]:
    import re

    client = srv.logged_in(USER)
    scripts = {}
    for src in re.findall(r'<script src="(static/[^"?]+)', _page(srv)):
        status, body, _ = client.get("/" + src)
        assert status == 200, src
        scripts[src] = body
    assert "static/messages.js" in scripts
    return scripts


def test_the_browser_neither_listens_for_nor_replays_todo_state(srv):
    """Old Run Journals still hold ``todo_state`` events; the browser ignores them."""
    for src, js in _served_scripts(srv).items():
        # No listener, no entry in the replay list, no read of session.todo_state.
        assert "todo_state" not in js, src


TODO_RESULT = json.dumps({
    "todos": [{"id": "1", "content": "ship it", "status": "in_progress"}],
    "summary": {"total": 1, "pending": 0, "in_progress": 1, "completed": 0, "cancelled": 0},
})
TODO_TOOL_MESSAGES = [
    {"role": "user", "content": "plan it"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "todo", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "call_1", "name": "todo", "content": TODO_RESULT},
    {"role": "assistant", "content": "planned"},
]


def test_the_session_get_carries_no_todo_state(srv):
    from api.models import get_session

    client = srv.logged_in(USER)
    status, body, _ = client.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    session = get_session(sid)
    session.messages = list(TODO_TOOL_MESSAGES)
    session.save()

    status, body, _ = client.get(f"/api/session?session_id={sid}&messages=1")
    assert status == 200, body
    assert [m["role"] for m in body["session"]["messages"]][:3] == ["user", "assistant", "tool"]
    assert "todo_state" not in body["session"]


# ── A todo tool call during a stream (seam 2: the streaming worker) ──────────

class _FakeSession:
    def __init__(self, sid):
        self.session_id = sid
        self.title = "Todo tool"
        self.workspace = "/tmp"
        self.model = "gpt-test"
        self.model_provider = None
        self.profile = None
        self.personality = None
        self.messages = []
        self.context_messages = []
        self.input_tokens = self.output_tokens = 0
        self.estimated_cost = 0
        self.cache_read_tokens = self.cache_write_tokens = 0
        self.tool_calls = []
        self.gateway_routing = None
        self.gateway_routing_history = []
        self.active_stream_id = ""
        self.pending_user_message = None
        self.pending_attachments = []
        self.pending_started_at = None
        self.context_length = self.threshold_tokens = self.last_prompt_tokens = 0
        self.llm_title_generated = True

    def save(self, *args, **kwargs):
        pass

    def compact(self):
        return {"session_id": self.session_id, "title": self.title, "workspace": self.workspace,
                "model": self.model, "created_at": 0, "updated_at": 0, "pinned": False,
                "archived": False, "project_id": None, "profile": None, "input_tokens": 0,
                "output_tokens": 0, "estimated_cost": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "personality": None}


class _AgentBase:
    def _init(self, kwargs):
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.context_compressor = None
        self.session_prompt_tokens = self.session_completion_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cache_read_tokens = self.session_cache_write_tokens = 0
        self.reasoning_config = None
        self.ephemeral_system_prompt = None
        self._last_error = None

    def _finish(self, kwargs):
        history = kwargs.get("conversation_history", [])
        return {"messages": history + [
            {"role": "user", "content": kwargs["persist_user_message"]},
            *TODO_TOOL_MESSAGES[1:],
        ]}

    def interrupt(self, _message):
        pass


class _ProgressCallbackAgent(_AgentBase):
    """An older Agent build: tool events only through tool_progress_callback."""

    def __init__(self, model=None, provider=None, base_url=None, platform=None, quiet_mode=False,
                 enabled_toolsets=None, fallback_model=None, session_id=None, session_db=None,
                 prefill_messages=None, stream_delta_callback=None, reasoning_callback=None,
                 tool_progress_callback=None, clarify_callback=None, **kwargs):
        self._init({"tool_progress_callback": tool_progress_callback})

    def run_conversation(self, **kwargs):
        self.tool_progress_callback("tool.started", "todo", "todo", {})
        self.tool_progress_callback("tool.completed", "todo", TODO_RESULT[:40], {}, result=TODO_RESULT)
        return self._finish(kwargs)


class _StructuredCallbackAgent(_AgentBase):
    """A current Agent build: tool_start_callback / tool_complete_callback."""

    def __init__(self, model=None, provider=None, base_url=None, platform=None, quiet_mode=False,
                 enabled_toolsets=None, fallback_model=None, session_id=None, session_db=None,
                 prefill_messages=None, stream_delta_callback=None, reasoning_callback=None,
                 tool_progress_callback=None, clarify_callback=None, tool_start_callback=None,
                 tool_complete_callback=None, **kwargs):
        self._init({"tool_progress_callback": tool_progress_callback})
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback

    def run_conversation(self, **kwargs):
        self.tool_start_callback("call_1", "todo", {})
        self.tool_complete_callback("call_1", "todo", {}, TODO_RESULT)
        return self._finish(kwargs)


@pytest.mark.parametrize("agent_cls", [_ProgressCallbackAgent, _StructuredCallbackAgent])
def test_a_todo_tool_call_streams_as_a_tool_card_and_no_todo_state(agent_cls, cleanup_test_sessions):
    import queue
    import sys
    import types
    from unittest import mock

    import api.streaming as streaming

    session = _FakeSession(f"todos_gone_{agent_cls.__name__}")
    stream_id = f"stream_{session.session_id}"
    session.active_stream_id = stream_id
    events_q = queue.Queue()

    runtime = types.ModuleType("hermes_cli.runtime_provider")
    payload = {"provider": "openai", "base_url": None, "api_mode": "chat_completions",
               "command": None, "args": [], "credential_pool": None}
    payload["api_" + "key"] = "***"
    runtime.__dict__["resolve_runtime_provider"] = mock.Mock(return_value=payload)
    hermes_cli = types.ModuleType("hermes_cli")
    hermes_cli.__dict__["runtime_provider"] = runtime
    hermes_state = types.ModuleType("hermes_state")
    hermes_state.__dict__["SessionDB"] = mock.Mock(return_value=None)

    with mock.patch.dict(sys.modules, {"hermes_cli": hermes_cli,
                                       "hermes_cli.runtime_provider": runtime,
                                       "hermes_state": hermes_state}), \
         mock.patch.object(streaming, "get_session", return_value=session), \
         mock.patch.object(streaming, "_get_ai_agent", return_value=agent_cls), \
         mock.patch.object(streaming, "resolve_model_provider", return_value=("gpt-test", "openai", None)), \
         mock.patch("api.config.get_config", return_value={}), \
         mock.patch("api.config._resolve_cli_toolsets", return_value=[]):
        streaming.STREAMS[stream_id] = events_q
        try:
            streaming._run_agent_streaming(
                session_id=session.session_id, msg_text="plan it", model="gpt-test",
                workspace="/tmp", stream_id=stream_id,
            )
        finally:
            streaming.STREAMS.pop(stream_id, None)

    events = list(events_q.queue)
    names = [event for event, _ in events]
    assert ("tool", "todo") in [(e, p.get("name")) for e, p in events if e == "tool"]
    assert ("tool_complete", "todo") in [(e, p.get("name")) for e, p in events if e == "tool_complete"]
    assert "todo_state" not in names, names
    for event, data in events:
        if isinstance(data, dict) and isinstance(data.get("session"), dict):
            assert "todo_state" not in data["session"], event
