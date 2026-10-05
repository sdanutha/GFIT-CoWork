"""Hermes Agent names that moved modules keep working at every WebUI call site.

The Agent's September 2026 decomposition moved names such as
``tools.approval.set_current_session_key`` into sibling modules. The old paths
resolved only through warning ``__getattr__`` pointers that are removed on
schedule; with the pointers gone, the WebUI's broad ``except Exception`` call
sites silently dropped approval session binding, MCP discovery/status, Claude
Code credential linking, and every kanban connection.

The call-site tests run each affected WebUI entry point against two fake Agent
shapes: ``pre_split`` (the name still lives on its original module) and
``pointer_removed`` (the name lives only in its new home module, and the
original module no longer resolves it).
"""

import sys
import types
import warnings

import pytest

from api.agent_compat import agent_attr

SHAPES = ("pre_split", "pointer_removed")


class _CompatWarning(FutureWarning):
    pass


def _real(value):
    return value


def _install(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _install_agent(monkeypatch, shape, facade, moved, native=None):
    """Install a fake Agent module ``facade``.

    ``moved`` maps each new home module to the ``{name: object}`` it now owns.
    ``pre_split`` puts those names on ``facade``; ``pointer_removed`` installs
    them only in their home modules. ``native`` names always stay on ``facade``.
    """
    names = dict(native or {})
    for home, attrs in moved.items():
        if shape == "pre_split":
            names.update(attrs)
        else:
            _install(monkeypatch, home, **attrs)
    return _install(monkeypatch, facade, **names)


# ── resolver ─────────────────────────────────────────────────────────────────


def _split_facade(monkeypatch, *, with_pointer):
    """An Agent after the split: the name lives in the home module; the facade
    optionally keeps a warning PEP 562 pointer (the temporary compat layer)."""
    _install(monkeypatch, "fakeagent_home", moved=_real)
    facade = _install(monkeypatch, "fakeagent_facade", native=_real)
    if with_pointer:
        def __getattr__(name):
            if name == "moved":
                warnings.warn("`fakeagent_facade.moved` moved", _CompatWarning, stacklevel=2)
                return _real
            raise AttributeError(name)
        facade.__getattr__ = __getattr__
    return facade


def test_pre_split_agent_uses_original_module(monkeypatch):
    orig = _install(monkeypatch, "fakeagent_facade", moved=lambda: "pre-split")
    assert agent_attr("fakeagent_facade", "moved", "fakeagent_missing_home")() == "pre-split"
    assert agent_attr(orig, "moved", "fakeagent_missing_home")() == "pre-split"


def test_split_agent_with_pointer_uses_home_without_compat_warning(monkeypatch):
    _split_facade(monkeypatch, with_pointer=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error", _CompatWarning)
        assert agent_attr("fakeagent_facade", "moved", "fakeagent_home") is _real


def test_split_agent_after_pointer_removal_uses_home(monkeypatch):
    facade = _split_facade(monkeypatch, with_pointer=False)
    with pytest.raises(AttributeError):
        _ = facade.moved  # what `from facade import moved` hits after the removal
    assert agent_attr(facade, "moved", "fakeagent_home") is _real


def test_stub_on_original_module_still_wins(monkeypatch):
    _split_facade(monkeypatch, with_pointer=False)
    stub = lambda: "stub"  # noqa: E731
    monkeypatch.setattr(sys.modules["fakeagent_facade"], "moved", stub, raising=False)
    assert agent_attr("fakeagent_facade", "moved", "fakeagent_home") is stub


def test_non_module_double_uses_plain_attribute(monkeypatch):
    class FakeKanbanDB:
        def connect(self, *, board=None):
            return board

    double = FakeKanbanDB()
    assert agent_attr(double, "connect", "fakeagent_home")(board="b") == "b"
    assert agent_attr(double, "connect_closing", "fakeagent_home", None) is None


def test_default_when_name_is_absent_everywhere(monkeypatch):
    _split_facade(monkeypatch, with_pointer=False)
    assert agent_attr("fakeagent_facade", "nope", "fakeagent_home", None) is None
    with pytest.raises(AttributeError):
        agent_attr("fakeagent_facade", "nope", "fakeagent_home")


def test_missing_agent_raises_import_error_or_returns_default():
    with pytest.raises(ImportError):
        agent_attr("fakeagent_not_installed", "moved", "fakeagent_not_installed_home")
    assert agent_attr("fakeagent_not_installed", "moved", "fakeagent_not_installed_home", None) is None


# ── WebUI call sites ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", SHAPES)
def test_turn_session_identity_binds_and_resets_agent_approval_key(monkeypatch, shape):
    import api.streaming as streaming

    calls = []

    def set_current_session_key(sid):
        calls.append(("set", sid))
        return f"token-{sid}"

    def reset_current_session_key(token):
        calls.append(("reset", token))

    _install_agent(monkeypatch, shape, "tools.approval", {"tools.approval_context": {
        "set_current_session_key": set_current_session_key,
        "reset_current_session_key": reset_current_session_key,
    }})

    tokens = streaming._set_turn_session_identity("sess-1")
    assert tokens.get("approval") == "token-sess-1"
    streaming._reset_turn_session_identity(tokens)
    assert calls == [("set", "sess-1"), ("reset", "token-sess-1")]


@pytest.mark.parametrize("shape", SHAPES)
def test_mcp_runtime_status_reads_agent_registry(monkeypatch, shape):
    import api.routes as routes

    statuses = [{"name": "alpha", "connected": True}, {"name": "beta", "connected": False}]
    _install_agent(monkeypatch, shape, "tools.mcp_tool", {"tools.mcp_tool_discovery": {
        "get_mcp_status": lambda: statuses,
    }})

    assert routes._mcp_runtime_status_by_name() == {"alpha": statuses[0], "beta": statuses[1]}


@pytest.mark.parametrize("shape", SHAPES)
def test_claude_code_credentials_read_through_agent(monkeypatch, shape):
    import api.oauth as oauth

    creds = {"accessToken": "cc-access", "refreshToken": "cc-refresh"}
    _install_agent(monkeypatch, shape, "agent.anthropic_adapter", {"agent.anthropic_credentials": {
        "read_claude_code_credentials": lambda: creds,
        "is_claude_code_token_valid": lambda value: value is creds,
    }})

    assert oauth._read_claude_code_credentials() is creds


@pytest.mark.parametrize("shape", SHAPES)
def test_lmstudio_reasoning_options_use_agent_probe(monkeypatch, shape):
    import api.config as config

    seen = []

    def lmstudio_model_reasoning_options(model, base_url, api_key=None, timeout=5.0):
        seen.append((model, base_url, timeout))
        return ["low", "high"]

    monkeypatch.setattr(config, "_lmstudio_reasoning_probe_options_fallback", lambda *a, **k: ["fallback"])
    _install_agent(monkeypatch, shape, "hermes_cli.models", {"hermes_cli.models_local": {
        "lmstudio_model_reasoning_options": lmstudio_model_reasoning_options,
    }})

    assert config._lmstudio_model_reasoning_options("qwen", "http://127.0.0.1:1234", timeout=2.0) == ["low", "high"]
    assert seen == [("qwen", "http://127.0.0.1:1234", 2.0)]

