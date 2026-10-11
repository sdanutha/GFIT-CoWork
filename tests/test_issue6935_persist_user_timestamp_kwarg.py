"""
Test for Issue #6935: Ensure agent.run_conversation() does not trip a TypeError
when persist_user_timestamp is passed to agent implementations that do not accept it.

The tests drive ``_build_run_conversation_kwargs``, which the streaming worker
uses to build every ``run_conversation()`` call.
"""
from api.streaming import _build_run_conversation_kwargs


class LegacyAgentStub:
    """Agent stub mimicking older hermes-agent whose run_conversation does NOT accept **kwargs or persist_user_timestamp."""
    def run_conversation(self, user_message, system_message=None, conversation_history=None, task_id=None, persist_user_message=None):
        return {"final_response": "ok", "messages": []}


class ModernAgentWithKwargsStub:
    """Agent stub whose run_conversation accepts **kwargs."""
    def run_conversation(self, **kwargs):
        return {"final_response": "ok", "messages": []}


class ModernAgentWithExplicitParamStub:
    """Agent stub whose run_conversation explicitly accepts persist_user_timestamp."""
    def run_conversation(self, user_message, persist_user_timestamp=None):
        return {"final_response": "ok", "messages": []}


def _kwargs_for(callable_obj):
    return _build_run_conversation_kwargs(
        callable_obj,
        user_message="Hello",
        system_message="System prompt",
        conversation_history=[],
        conversation_history_revision="rev-1",
        task_id="test-session-123",
        persist_user_message="Hello",
        persist_user_timestamp=1700000000.0,
    )


def test_persist_user_timestamp_is_signature_gated():
    legacy = _kwargs_for(LegacyAgentStub().run_conversation)
    modern_kwargs = _kwargs_for(ModernAgentWithKwargsStub().run_conversation)
    modern_explicit = _kwargs_for(ModernAgentWithExplicitParamStub().run_conversation)

    assert "persist_user_timestamp" not in legacy
    assert modern_kwargs["persist_user_timestamp"] == 1700000000.0
    assert modern_explicit["persist_user_timestamp"] == 1700000000.0


def test_legacy_agent_run_conversation_accepts_the_built_kwargs():
    legacy_agent = LegacyAgentStub()

    # Calling run_conversation with the built kwargs must not raise TypeError
    result = legacy_agent.run_conversation(**_kwargs_for(legacy_agent.run_conversation))
    assert result["final_response"] == "ok"


def test_unreadable_signature_drops_the_optional_kwargs():
    """Fail safe: when the signature cannot be read, send only the base contract."""

    class Unintrospectable:
        __signature__ = "not a signature"

        def __call__(self, **kwargs):
            return kwargs

    kwargs = _kwargs_for(Unintrospectable())
    assert "persist_user_timestamp" not in kwargs
    assert "conversation_history_revision" not in kwargs
    assert kwargs["user_message"] == "Hello"
