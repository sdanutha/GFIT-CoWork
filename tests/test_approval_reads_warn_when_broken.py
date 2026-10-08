"""Ticket 15, Class B group 2: an approval read that fails inside the Agent is warned, not taken for "absent".

The approval notify registration and the blocking-approval check degrade to
polling when the Agent's ``tools.approval`` is not installed. Before, an
ImportError raised inside it (or any failure of the call) degraded silently
the same way; the agent then blocks on an approval the UI may never surface.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from api import streaming


@pytest.fixture
def approval(monkeypatch):
    pkg = types.ModuleType("tools")
    pkg.__path__ = []
    module = types.ModuleType("tools.approval")
    registered = {}
    module.register_gateway_notify = lambda sid, cb: registered.__setitem__(sid, cb)
    module.unregister_gateway_notify = lambda sid: registered.pop(sid, None)
    module.has_blocking_approval = lambda sid: sid in registered
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.approval", module)
    return types.SimpleNamespace(module=module, registered=registered)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_registration_and_the_blocking_check_use_the_agents_approval_module(approval, caplog):
    assert streaming._register_approval_notify("s1", lambda data: None) is True
    assert streaming._session_has_blocking_approval("s1") is True
    assert streaming._session_has_blocking_approval("s2") is False
    assert _warnings(caplog) == []


def test_an_absent_approval_module_falls_back_to_polling_quietly(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "tools", None)
    monkeypatch.setitem(sys.modules, "tools.approval", None)
    caplog.set_level(logging.DEBUG)
    assert streaming._register_approval_notify("s1", lambda data: None) is False
    assert streaming._session_has_blocking_approval("s1") is False
    assert _warnings(caplog) == []
    assert "falling back to polling" in caplog.text


def test_an_import_error_inside_the_agent_is_warned(monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "tools.approval", raising=False)
    pkg = types.ModuleType("tools")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "tools", pkg)

    class _BrokenFinder:
        def find_spec(self, name, path=None, target=None):
            if name == "tools.approval":
                raise ModuleNotFoundError("No module named 'ruamel'", name="ruamel")
            return None

    monkeypatch.setattr(sys, "meta_path", [_BrokenFinder(), *sys.meta_path])
    assert streaming._register_approval_notify("s1", lambda data: None) is False
    assert streaming._session_has_blocking_approval("s1") is False
    warnings = _warnings(caplog)
    assert warnings and all("ruamel" in m for m in warnings)


@pytest.mark.parametrize("failure", [ImportError("inner", name="ruamel"), RuntimeError("secret-text")])
def test_a_failing_call_is_warned_without_its_text(approval, caplog, failure):
    def _raise(*_args):
        raise failure

    approval.module.register_gateway_notify = _raise
    approval.module.has_blocking_approval = _raise
    assert streaming._register_approval_notify("s1", lambda data: None) is False
    assert streaming._session_has_blocking_approval("s1") is False
    warnings = _warnings(caplog)
    assert len(warnings) == 2 and type(failure).__name__ in warnings[0]
    assert "secret-text" not in caplog.text
