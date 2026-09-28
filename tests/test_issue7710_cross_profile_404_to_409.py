"""Regression tests for issue #7710 — cross-profile session guards return 409 + ``session_profile_mismatch`` instead of masking a known-other-profile session as 404.

The detail-load endpoint (and the foreign-session synthesizer) already
distinguish "session owned by a KNOWN other profile" (return ``409
session_profile_mismatch``) from "session missing/legacy with no
profile stamped" (return ``404 Session not found`` so the frontend
self-heal clears the stale URL).

The generic request-guard
``_session_id_visible_to_request_profile`` (used by
``_guard_request_session_visibility``) flattened both cases to a
``404 Session not found``, so any cross-profile POST/PATCH/DELETE
(archive, rename, pin, delete, …) failed with a misleading "Session
not found" instead of the actionable 409 the detail endpoint emits.
The fix mirrors the detail endpoint's contract in the generic guard.

The guard now asks session ownership (``api.session_ownership``); these tests
drive the real helper through the unconfined adapter (no request's
Admission), with the session lookup and the active Profile stubbed.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeSession:
    def __init__(self, profile: str | None) -> None:
        self.profile = profile


class _FakeHandler:
    """Minimal stand-in for the routes handler the helper writes through."""

    def __init__(self) -> None:
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    def body(self):
        raw = self.wfile.getvalue()
        return json.loads(raw) if raw else None


@pytest.fixture
def guard(monkeypatch):
    """The real helper, with the session lookup driven per test and the root Profile active."""
    import api.models as models
    import api.profiles as profiles
    import api.routes as routes

    resolved = {}

    def get_session(sid, metadata_only=False):
        return resolved["resolve"](sid)

    monkeypatch.setattr(models, "get_session", get_session)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    def drive(resolve):
        resolved["resolve"] = resolve
        return routes._session_id_visible_to_request_profile

    return drive


def test_profile_mismatch_returns_409_with_code_and_profile(guard) -> None:
    """A session owned by a known other profile MUST yield 409 ``session_profile_mismatch``."""
    handler = _FakeHandler()
    fn = guard(lambda sid: _FakeSession(profile="alpha"))
    assert fn(handler, "sess-1") is False
    assert handler.status == 409
    body = handler.body()
    assert body["code"] == "session_profile_mismatch"
    assert body["session_id"] == "sess-1"
    assert body["profile"] == "alpha"
    assert "different profile" in body["error"].lower()


def test_unknown_profile_returns_404_for_frontend_self_heal(guard, monkeypatch) -> None:
    """A session with ``profile=None`` MUST keep 404 so the frontend's self-heal fires."""
    import api.profiles as profiles

    # A named Profile is active, so the legacy None-profile session is not visible.
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")
    handler = _FakeHandler()
    fn = guard(lambda sid: _FakeSession(profile=None))
    assert fn(handler, "sess-1") is False
    assert handler.status == 404
    assert handler.body() == {"error": "Session not found"}


def test_visible_session_returns_no_error(guard) -> None:
    """A session owned by the active profile MUST pass the guard silently."""
    handler = _FakeHandler()
    fn = guard(lambda sid: _FakeSession(profile="default"))
    assert fn(handler, "sess-1") is True
    assert handler.status is None


def test_missing_session_returns_no_error(guard) -> None:
    """A session id that get_session cannot resolve MUST pass the guard silently (the route will 404 later)."""
    handler = _FakeHandler()
    fn = guard(lambda sid: (_ for _ in ()).throw(KeyError(sid)))
    assert fn(handler, "sess-1") is True
    assert handler.status is None


def test_emit_error_false_suppresses_response(guard) -> None:
    """``emit_error=False`` MUST return False without writing any response (used by the guard's exemption probe)."""
    handler = _FakeHandler()
    fn = guard(lambda sid: _FakeSession(profile="alpha"))
    assert fn(handler, "sess-1", emit_error=False) is False
    assert handler.status is None


# ---------------------------------------------------------------------------
# Source-shape: the 409 payload MUST keep the four contract fields and the
# status code, in the session ownership refusal. A silent reversion to a
# single 404 fails the suite.
# ---------------------------------------------------------------------------


def test_source_emits_409_not_404_in_helper() -> None:
    """The refusal MUST contain a 409 ``session_profile_mismatch`` branch and keep the 404."""
    src = (REPO_ROOT / "api" / "session_ownership.py").read_text(encoding="utf-8")
    body = src[src.index("class Refusal"):src.index("NOT_FOUND = Refusal()")]
    assert "status=409" in body, (
        "the refusal no longer emits 409 for a known-other-profile session"
    )
    assert "session_profile_mismatch" in body, (
        "the refusal no longer carries the ``session_profile_mismatch`` code"
    )
    assert "NOT_FOUND_MESSAGE" in body and 'NOT_FOUND_MESSAGE = "Session not found"' in src, (
        "the None-profile 404 self-heal path was dropped — "
        "keep the 404 for the None-profile branch so the frontend "
        "self-heal still fires for actually-missing sids"
    )
