"""GFIT-CoWork: the run registry is the one way to open and close a turn's stream.

Architecture review round 6, candidate 4. Session ownership decides who may
watch or stop a stream by its owning session, so the owner must be recorded
before the stream is visible and forgotten when it closes. A guard keeps the
stream map and the owner record written only here.
"""
from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.config as config
from api import run_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stream_id():
    sid = "gfit-run-registry-stream"
    yield sid
    run_registry.close_stream(sid)


def _owner_seen_when_published(monkeypatch):
    """Record, each time an owner is registered, whether its stream was already visible."""
    seen = []
    original = config.register_stream_owner

    def recording(stream_id, session_id):
        seen.append(stream_id in config.STREAMS)
        original(stream_id, session_id)

    monkeypatch.setattr(config, "register_stream_owner", recording)
    return seen


def test_opening_records_the_owner_before_the_stream_is_visible(monkeypatch, stream_id):
    seen = _owner_seen_when_published(monkeypatch)
    channel = run_registry.open_stream("session-a", stream_id, goal_related=True)
    assert channel is config.STREAMS[stream_id]
    assert seen == [False]
    assert config.stream_owner_session_id(stream_id) == "session-a"
    assert config.STREAM_GOAL_RELATED.get(stream_id) is True


def test_opening_only_if_absent_leaves_a_live_stream_and_its_owner_alone(stream_id):
    first = run_registry.open_stream("session-a", stream_id)
    assert run_registry.open_stream("session-b", stream_id, only_if_absent=True) is None
    assert config.STREAMS[stream_id] is first
    assert config.stream_owner_session_id(stream_id) == "session-a"


def test_closing_removes_the_stream_its_owner_and_its_goal_mark(stream_id):
    run_registry.open_stream("session-a", stream_id, goal_related=True)
    assert run_registry.close_stream(stream_id) is True
    assert stream_id not in config.STREAMS
    assert config.stream_owner_session_id(stream_id) is None
    assert stream_id not in config.STREAM_GOAL_RELATED
    assert run_registry.close_stream(stream_id) is False


def test_closing_a_replaced_channel_leaves_the_new_one(stream_id):
    old = run_registry.open_stream("session-a", stream_id)
    run_registry.close_stream(stream_id)
    new = run_registry.open_stream("session-a", stream_id)
    assert run_registry.close_stream(stream_id, channel=old) is False
    assert config.STREAMS[stream_id] is new


def test_readers_see_live_streams_by_owning_session(stream_id):
    run_registry.open_stream("session-a", stream_id)
    assert run_registry.stream_is_live(stream_id)
    assert stream_id in run_registry.live_stream_ids()
    assert run_registry.live_streams_of_sessions({"session-a", "other"}) == {stream_id: "session-a"}


def test_a_resumed_gateway_turn_is_owned_before_it_is_visible(monkeypatch, stream_id):
    from api import gateway_chat

    seen = _owner_seen_when_published(monkeypatch)
    monkeypatch.setattr(gateway_chat, "_gateway_endpoint_for_profile", lambda _profile: ("http://gw", ""))
    monkeypatch.setattr(gateway_chat, "_mark_gateway_run_starting", lambda _sid: None)
    monkeypatch.setattr(gateway_chat.threading, "Thread", lambda **_kw: SimpleNamespace(start=lambda: None))
    session = SimpleNamespace(
        session_id="session-gw", profile="default", gateway_run={"run_id": "run-1", "stream_id": stream_id},
        active_stream_id=stream_id, pending_user_message="hi", pending_attachments=[], model="m",
        model_provider="p", workspace="/ws",
    )
    assert gateway_chat._resume_gateway_run_for_session(session) is True
    assert seen == [False]
    assert config.stream_owner_session_id(stream_id) == "session-gw"


def test_a_reader_never_sees_a_stream_without_its_owner(stream_id):
    """Readers take the stream lock; while they hold it a stream is either absent or owned."""
    stop = threading.Event()
    orphans = []

    def reader():
        while not stop.is_set():
            with config.STREAMS_LOCK:
                if stream_id in config.STREAMS and config.stream_owner_session_id(stream_id) is None:
                    orphans.append(True)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for _ in range(200):
            run_registry.open_stream("session-a", stream_id)
            run_registry.close_stream(stream_id)
    finally:
        stop.set()
        thread.join(5)
    assert orphans == []


# ── Guard: only the registry writes the stream map and the owner record ─────

def stream_writes(source: str) -> list[int]:
    """Lines that write the stream map or the owner record outside the registry."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and ast.unparse(target.value).split(".")[-1] == "STREAMS":
                    found.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
            name = ast.unparse(node.func)
            if name.split(".")[-1] in ("register_stream_owner", "unregister_stream_owner"):
                found.append(node.lineno)
            if name.endswith(("STREAMS.pop", "STREAMS.clear", "STREAMS.update", "STREAMS.setdefault")):
                found.append(node.lineno)
    return sorted(found)


def test_only_the_registry_writes_streams_and_owners():
    writes = {
        str(path.relative_to(ROOT)): stream_writes(path.read_text(encoding="utf-8"))
        for path in (ROOT / "api").glob("*.py")
        if path.name not in ("run_registry.py", "config.py")
    }
    assert {module: lines for module, lines in writes.items() if lines} == {}


def test_the_stream_guard_finds_each_write():
    source = (
        "STREAMS[s] = c\n"
        "_cfg.STREAMS.pop(s, None)\n"
        "register_stream_owner(s, sid)\n"
        "config.unregister_stream_owner(s)\n"
        "del STREAMS[s]\n"
        "x = STREAMS.get(s)\n"
    )
    assert stream_writes(source) == [1, 2, 3, 4, 5]
