"""Both WebUI completion consumers restore the durable ledger before reading.

Hermes Agent stopped replaying pending async-delegation completions on
``import tools.process_registry`` (agent 34c2dc5, #123265): the first consumer
must call ``process_registry.restore_completions()``. Without that call a
completion that was pending when GFIT-CoWork restarted never reaches the
queue, so it is never delivered. Older Agent builds replay on import and have
no ``restore_completions``; the consumers must keep working with them.
"""

from __future__ import annotations

import queue
import threading

from tests._wakeup_helpers import install_fake_registry


class _Registry:
    def __init__(self, *, with_restore: bool = True, restore_raises: bool = False):
        self.completion_queue = queue.Queue()
        self.calls: list[str] = []
        self._restore_raises = restore_raises
        if not with_restore:
            self.restore_completions = None

    def restore_completions(self):  # noqa: D401 - mirrors the Agent's name
        self.calls.append("restore")
        if self._restore_raises:
            raise RuntimeError("ledger unreadable")
        self.completion_queue.put({"type": "completion", "session_id": "proc-1"})
        return 1

    def is_completion_consumed(self, _sid):
        self.calls.append("read")
        return True

    def get(self, _sid):
        return None


def test_next_turn_drain_restores_before_reading(monkeypatch):
    from api import streaming

    reg = _Registry()
    install_fake_registry(monkeypatch, reg)

    streaming._drain_webui_process_notifications("websess-1")

    assert reg.calls[:2] == ["restore", "read"]


def test_background_drain_thread_restores_before_reading(monkeypatch):
    from api import background_process as bp

    reg = _Registry()
    install_fake_registry(monkeypatch, reg)
    stop = threading.Event()
    seen: list[dict] = []

    def _process_one(evt):
        seen.append(evt)
        stop.set()

    monkeypatch.setattr(bp, "_DRAIN_STOP", stop)
    monkeypatch.setattr(bp, "_process_one", _process_one)
    t = threading.Thread(target=bp._drain_loop, daemon=True)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive()
    assert reg.calls == ["restore"]
    assert seen == [{"type": "completion", "session_id": "proc-1"}]


def test_older_agent_without_restore_still_drains(monkeypatch):
    from api import streaming

    reg = _Registry(with_restore=False)
    reg.completion_queue.put({"type": "completion", "session_id": "proc-1"})
    install_fake_registry(monkeypatch, reg)

    streaming._drain_webui_process_notifications("websess-1")

    assert reg.calls == ["read"]


def test_failed_restore_does_not_stop_the_drain(monkeypatch):
    from api import streaming

    reg = _Registry(restore_raises=True)
    reg.completion_queue.put({"type": "completion", "session_id": "proc-1"})
    install_fake_registry(monkeypatch, reg)

    streaming._drain_webui_process_notifications("websess-1")

    assert reg.calls == ["restore", "read"]
