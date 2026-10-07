"""Issue #7710, reversed by GFIT-CoWork ticket 07: a session guard never names another Profile.

Upstream answered a session owned by a known other profile with 409
``session_profile_mismatch`` naming that profile, so the client could offer to
switch to it. GFIT-CoWork has no Profile switch (ADR 0006), and naming the
owner tells a caller whose session an id is. The guard
``_session_id_visible_to_request_profile`` answers 404 "Session not found"
for another Profile's session exactly as for a legacy one.

These tests drive the real helper through the unconfined adapter (code with
no caller), with the session lookup and the active Profile stubbed.
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


def test_another_profiles_session_is_404_and_names_no_owner(guard) -> None:
    """A session owned by a known other profile is refused with 404, never naming the owner."""
    handler = _FakeHandler()
    fn = guard(lambda sid: _FakeSession(profile="alpha"))
    assert fn(handler, "sess-1") is False
    assert handler.status == 404
    assert handler.body() == {"error": "Session not found"}


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
# Source-shape: the refusal has one answer, 404, and no 409 naming an owner.
# ---------------------------------------------------------------------------


def test_the_refusal_has_one_answer_and_no_409() -> None:
    src = (REPO_ROOT / "api" / "session_ownership.py").read_text(encoding="utf-8")
    body = src[src.index("class Refusal"):src.index("NOT_FOUND = Refusal()")]
    assert "status=409" not in body
    assert "session_profile_mismatch" not in body
    assert "self.owner" not in body and "owner:" not in body
    assert 'NOT_FOUND_MESSAGE = "Session not found"' in src
