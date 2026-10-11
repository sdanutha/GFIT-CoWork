"""Tests for the silent-failure detection fix in api/streaming.py.

The decision lives in ``_assistant_reply_added_after_current_turn``, which the
streaming worker calls after each turn: do the messages the Agent returned
contain an assistant message with visible content AFTER the pre-turn history
(or, when the result is not an append of that history, after the current user
turn)?

These tests cover the 8 scenarios specified in the task description to ensure
that historical assistant messages don't mask a silent provider failure.
"""

import pytest

from api.streaming import _assistant_reply_added_after_current_turn


# ── Helpers ──────────────────────────────────────────────────────────────────

def _msg(role: str, content: str) -> dict:
    """Shorthand for building a message dict."""
    return {"role": role, "content": content}


def _reply_added(all_msgs, prev, msg_text=None):
    """Run the production check; the current turn's text defaults to the last user row."""
    if msg_text is None:
        msg_text = next(
            (m.get("content") for m in reversed(all_msgs) if m.get("role") == "user"),
            "",
        )
    return _assistant_reply_added_after_current_turn(all_msgs, prev, msg_text)


# ── Test scenarios ───────────────────────────────────────────────────────────

class TestAssistantReplyAddedAfterCurrentTurn:
    """All 8 scenarios from the task specification."""

    # Scenario 1 ──────────────────────────────────────────────────────────
    def test_history_has_assistant_but_current_turn_failed(self):
        """History has assistant content, but no new assistant was added."""
        prev = [
            _msg("user", "hi"),
            _msg("assistant", "hello"),
            _msg("user", "what's up?"),
        ]
        all_msgs = list(prev)  # same length — nothing new
        assert _reply_added(all_msgs, prev) is False

    # Scenario 2 ──────────────────────────────────────────────────────────
    def test_history_has_assistant_and_new_reply_added(self):
        """New assistant reply was appended this turn → should detect it."""
        prev = [
            _msg("user", "hi"),
            _msg("assistant", "hello"),
            _msg("user", "what's up?"),
        ]
        all_msgs = prev + [_msg("assistant", "not much, you?")]
        assert _reply_added(all_msgs, prev) is True

    # Scenario 3 ──────────────────────────────────────────────────────────
    def test_empty_history_empty_result(self):
        """Completely empty conversation → no assistant reply."""
        assert _reply_added([], []) is False

    # Scenario 4 ──────────────────────────────────────────────────────────
    def test_new_assistant_with_empty_content(self):
        """New assistant message added but content is empty string."""
        prev = [
            _msg("user", "hello"),
            _msg("assistant", "hi there"),
        ]
        all_msgs = prev + [_msg("assistant", "")]
        assert _reply_added(all_msgs, prev) is False

    # Scenario 5 ──────────────────────────────────────────────────────────
    def test_new_assistant_with_whitespace_content(self):
        """New assistant message added but content is only whitespace."""
        prev = [
            _msg("user", "hello"),
            _msg("assistant", "hi there"),
        ]
        all_msgs = prev + [_msg("assistant", "  \n  ")]
        assert _reply_added(all_msgs, prev) is False

    # Scenario 6 ──────────────────────────────────────────────────────────
    def test_long_history_new_assistant_at_tail(self):
        """Many historical messages; two new ones at the end, last is assistant."""
        prev = [_msg("user", f"msg {i}") if i % 2 == 0 else _msg("assistant", f"reply {i}")
                for i in range(10)]
        # prev has 10 messages (indices 0..9)
        all_msgs = prev + [
            _msg("user", "new question"),
            _msg("assistant", "new answer with real content"),
        ]
        assert _reply_added(all_msgs, prev) is True

    # Scenario 7 ──────────────────────────────────────────────────────────
    def test_result_length_equals_prev_len(self):
        """No new messages at all — result length == prev length."""
        prev = [
            _msg("user", "hi"),
            _msg("assistant", "hey"),
        ]
        all_msgs = list(prev)
        assert _reply_added(all_msgs, prev) is False

    # Scenario 8 ──────────────────────────────────────────────────────────
    def test_result_shorter_than_prev_len_returns_false(self):
        """Edge-case: a result shorter than the pre-turn history.

        It is not an append of that history, so only rows after the current
        user turn count. An older assistant reply must not hide the
        silent-failure banner.
        """
        prev = [
            _msg("user", "a"),
            _msg("assistant", "b"),
            _msg("user", "c"),
            _msg("assistant", "d"),
            _msg("user", "e"),
        ]
        all_msgs = [
            _msg("user", "a"),
            _msg("assistant", "b"),
            _msg("user", "c"),
        ]
        assert _reply_added(all_msgs, prev, "e") is False

        all_msgs_no_asst = [
            _msg("user", "a"),
            _msg("user", "b"),
            _msg("user", "c"),
        ]
        assert _reply_added(all_msgs_no_asst, prev, "e") is False

    def test_compacted_result_with_reply_after_current_turn(self):
        """A compacted result counts only the reply after the current user turn."""
        prev = [_msg("user", f"m{i}") if i % 2 == 0 else _msg("assistant", f"r{i}") for i in range(6)]
        summary_only = [_msg("assistant", "[summary]"), _msg("user", "now")]
        assert _reply_added(summary_only, prev, "now") is False
        assert _reply_added(summary_only + [_msg("assistant", "answer")], prev, "now") is True


# ── Additional edge-case tests ───────────────────────────────────────────────

class TestAssistantReplyAddedEdgeCases:
    """Extra coverage for content field variants."""

    def test_content_is_none(self):
        """assistant message with content=None should not count."""
        prev = [_msg("user", "hi")]
        all_msgs = prev + [{"role": "assistant", "content": None}]
        assert _reply_added(all_msgs, prev) is False

    def test_content_is_missing_key(self):
        """assistant message without 'content' key should not count."""
        prev = [_msg("user", "hi")]
        all_msgs = prev + [{"role": "assistant"}]
        assert _reply_added(all_msgs, prev) is False

    def test_non_assistant_role_in_new_messages(self):
        """Only 'assistant' role counts; 'user' or 'system' in new msgs → False."""
        prev = [_msg("user", "hi")]
        all_msgs = prev + [_msg("user", "follow-up")]
        assert _reply_added(all_msgs, prev) is False

    def test_prev_count_zero_with_assistant(self):
        """prev_count=0 with a new assistant → scans from index 0, finds it."""
        all_msgs = [_msg("assistant", "hello")]
        assert _reply_added(all_msgs, []) is True

    def test_prev_count_zero_without_assistant(self):
        """prev_count=0 with only user messages → False."""
        all_msgs = [_msg("user", "hello")]
        assert _reply_added(all_msgs, []) is False

    def test_multiple_new_assistant_first_empty_second_has_content(self):
        """First new assistant is empty, second has content → True."""
        prev = [_msg("user", "q")]
        all_msgs = prev + [
            _msg("assistant", ""),
            _msg("assistant", "actual content"),
        ]
        assert _reply_added(all_msgs, prev) is True

    def test_error_assistant_row_is_not_a_reply(self):
        """An assistant row the WebUI marked as an error does not count."""
        prev = [_msg("user", "q")]
        all_msgs = prev + [{"role": "assistant", "content": "Provider failed", "_error": True}]
        assert _reply_added(all_msgs, prev) is False

    def test_multiple_new_assistant_all_empty(self):
        """Multiple new assistant messages, all empty → False."""
        prev = [_msg("user", "q")]
        all_msgs = prev + [
            _msg("assistant", ""),
            _msg("assistant", "   "),
        ]
        assert _reply_added(all_msgs, prev) is False
