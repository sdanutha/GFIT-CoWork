"""A send to a session whose compression lineage cannot be read is refused (ticket 15).

These cover the Agent's rich lineage read. Its stand-in state.db here is not a
SQLite file, so the seal-state probe cannot tell either when the rich read
fails; ``test_compression_seal_probe`` covers a probe that can.

``durable_compression_continuation`` reads the Agent's SQLite lineage before
``POST /api/chat/start`` mutates anything. An older Agent without the read API
(no ``hermes_state`` module or ``SessionDB``, no read-only open, no lookup
methods) and an absent database keep legacy sidecar behavior. Any other failed
read (``hermes_state`` failing on a module it imports, a SQLite or OS error,
a malformed row),
nothing establishes that the parent may be written, so the send gets the
existing 409 ``session_rotated`` with no continuation, before workspace, model,
pending-turn or worker mutation, and the server logs the cause. The GET
navigation hint is read-only and keeps its legacy sidecar fallback.

The SessionDB here is a stand-in with the Agent's read API, so these run
without hermes-agent; ``test_compressed_idle_resume`` covers the real one.
"""
from __future__ import annotations

import copy
import importlib.abc
import json
import logging
import sqlite3
import sys
import types
from types import SimpleNamespace

import pytest

from api import profiles, routes
from api.compression_continuation import durable_compression_continuation
from tests._lineage_gate import post_chat_start

PARENT, CHILD = "sealedparent", "idlechild"
SECRET_DETAIL = "No module named 'zstd_internal' at /srv/hermes/profiles/state.db"


def _rows(end_reason="compression"):
    return {
        PARENT: {"id": PARENT, "end_reason": end_reason, "source": "webui", "profile_name": None},
        CHILD: {"id": CHILD, "end_reason": "idle_timeout", "ended_at": 2.0, "source": "tui", "profile_name": None},
    }


class Lineage:
    """The Agent's state.db as the read API sees it, with injectable failures."""

    def __init__(self, tmp_path, monkeypatch):
        self.rows = _rows()
        self.on_open = None  # exception raised by the read-only open
        self.on_get = None  # exception raised by get_session, or a callable returning the row
        self.on_tip = None  # exception raised by get_compression_tip
        self.on_close = None  # exception raised by close
        self.opened = []
        lineage = self

        class SessionDB:
            def __init__(self, db_path=None, read_only=False):
                lineage.opened.append(read_only)
                if lineage.on_open is not None:
                    raise lineage.on_open

            def get_session(self, session_id):
                if isinstance(lineage.on_get, BaseException):
                    raise lineage.on_get
                if callable(lineage.on_get):
                    return lineage.on_get(session_id)
                return copy.deepcopy(lineage.rows.get(session_id))

            def get_compression_tip(self, session_id):
                if lineage.on_tip is not None:
                    raise lineage.on_tip
                return CHILD if lineage.rows[PARENT]["end_reason"] == "compression" else session_id

            def close(self):
                if lineage.on_close is not None:
                    raise lineage.on_close

        self.SessionDB = SessionDB
        self.db_file = tmp_path / "state.db"
        self.db_file.write_bytes(b"lineage")  # not SQLite: the seal-state probe cannot read it
        monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=SessionDB))
        monkeypatch.setattr(profiles, "_resolve_profile_home_for_name", lambda name: str(tmp_path))
        self.session = SimpleNamespace(
            session_id=PARENT, profile="default", pre_compression_snapshot=False,
            workspace="/work/original", model="model-a", messages=[{"role": "user", "content": "before"}],
        )


@pytest.fixture
def lineage(tmp_path, monkeypatch):
    return Lineage(tmp_path, monkeypatch)


def _post(lineage, monkeypatch):
    return post_chat_start(lineage.session, monkeypatch)


def _assert_refused(lineage, monkeypatch, caplog):
    before = copy.deepcopy(vars(lineage.session))
    caplog.set_level(logging.WARNING, logger="api.compression_continuation")
    sent = _post(lineage, monkeypatch)
    assert not sent.mutated, "the send reached workspace mutation after a failed lineage read"
    assert sent.status == 409
    assert sent.payload["code"] == "session_rotated"
    assert sent.payload["continuation_session_id"] is None
    assert vars(lineage.session) == before
    # The server log names the cause; the response carries no detail of it.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and PARENT in warnings[0].getMessage()
    assert warnings[0].exc_info is not None
    cause = warnings[0].exc_info[1]
    body = json.dumps(sent.payload)
    for detail in ("zstd_internal", "/srv/hermes", "state.db", "database", "malformed", type(cause).__name__):
        assert detail.lower() not in body.lower()
    return warnings[0]


# ── Behavior that stays as it was ──


def test_sealed_parent_is_refused_with_its_continuation(lineage, monkeypatch):
    sent = _post(lineage, monkeypatch)
    assert (sent.status, sent.payload["code"], sent.payload["continuation_session_id"], sent.mutated) == (
        409, "session_rotated", CHILD, False)


def test_unsealed_parent_is_sent(lineage, monkeypatch):
    lineage.rows = _rows(end_reason=None)
    assert _post(lineage, monkeypatch).mutated


def test_a_session_not_in_the_database_is_sent(lineage, monkeypatch):
    lineage.rows = {}
    assert _post(lineage, monkeypatch).mutated


def test_no_hermes_state_keeps_legacy_behavior(lineage, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_state", None)  # import raises ImportError
    assert durable_compression_continuation(lineage.session) == (False, None)
    assert _post(lineage, monkeypatch).mutated


def test_an_absent_database_keeps_legacy_behavior(lineage, monkeypatch):
    lineage.db_file.unlink()
    assert _post(lineage, monkeypatch).mutated
    assert lineage.opened == []


def test_an_agent_without_a_read_only_open_keeps_legacy_behavior(lineage, monkeypatch):
    class OlderSessionDB(lineage.SessionDB):
        def __init__(self, db_path=None):
            raise AssertionError("an Agent without read-only opens must not be opened for writing")

    monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=OlderSessionDB))
    assert durable_compression_continuation(lineage.session) == (False, None)
    assert _post(lineage, monkeypatch).mutated


@pytest.mark.parametrize("method", ["get_session", "get_compression_tip"])
def test_an_agent_without_the_lookup_methods_keeps_legacy_behavior(lineage, monkeypatch, method):
    class OlderSessionDB(lineage.SessionDB):
        pass

    setattr(OlderSessionDB, method, None)
    monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=OlderSessionDB))
    assert durable_compression_continuation(lineage.session) == (False, None)
    assert _post(lineage, monkeypatch).mutated


def test_hermes_state_without_a_session_db_is_a_broken_read(lineage, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "hermes_state", types.ModuleType("hermes_state"))
    _assert_refused(lineage, monkeypatch, caplog)


def test_an_unreadable_signature_is_a_broken_read(lineage, monkeypatch, caplog):
    import inspect

    real_signature = inspect.signature

    def signature(obj, *args, **kwargs):
        if obj is lineage.SessionDB:
            raise ValueError("no signature found")
        return real_signature(obj, *args, **kwargs)

    monkeypatch.setattr(inspect, "signature", signature)
    _assert_refused(lineage, monkeypatch, caplog)


# ── The read API is present and the read fails: refused, nothing mutated ──


class _BrokenHermesState(importlib.abc.MetaPathFinder):
    """hermes_state is installed, but importing it fails."""

    def __init__(self, error):
        self.error = error

    def find_spec(self, fullname, path, target=None):
        if fullname == "hermes_state":
            raise self.error
        return None


@pytest.mark.parametrize(
    "error",
    [ModuleNotFoundError("No module named 'ruamel'", name="ruamel"), ImportError("cannot import name 'x' from 'hermes_state_fts'", name="hermes_state_fts"),
     RuntimeError(SECRET_DETAIL), SyntaxError("invalid syntax")],
    ids=["dependency-missing", "sibling-broken", "RuntimeError", "SyntaxError"],
)
def test_a_broken_hermes_state_import_refuses_the_send(lineage, monkeypatch, caplog, error):
    monkeypatch.delitem(sys.modules, "hermes_state")
    monkeypatch.setattr(sys, "meta_path", [_BrokenHermesState(error), *sys.meta_path])
    _assert_refused(lineage, monkeypatch, caplog)


def test_a_failing_close_does_not_change_the_outcome(lineage, monkeypatch, caplog):
    lineage.on_close = sqlite3.OperationalError("close failed")
    assert _post(lineage, monkeypatch).payload["continuation_session_id"] == CHILD
    lineage.on_get = sqlite3.OperationalError("database is locked")
    _assert_refused(lineage, monkeypatch, caplog)


@pytest.mark.parametrize("where", ["open", "get_session"])
@pytest.mark.parametrize(
    "error",
    [
        ModuleNotFoundError(SECRET_DETAIL),
        ImportError(SECRET_DETAIL),
        sqlite3.OperationalError("database is locked: " + SECRET_DETAIL),
        sqlite3.DatabaseError("database disk image is malformed: " + SECRET_DETAIL),
        OSError(5, "Input/output error", SECRET_DETAIL),
        RuntimeError(SECRET_DETAIL),
    ],
    ids=lambda e: type(e).__name__ if isinstance(e, BaseException) else e,
)
def test_a_failed_lineage_read_refuses_the_send(lineage, monkeypatch, caplog, where, error):
    setattr(lineage, "on_open" if where == "open" else "on_get", error)
    warning = _assert_refused(lineage, monkeypatch, caplog)
    assert "zstd_internal" in warning.getMessage()


@pytest.mark.parametrize(
    "row",
    [["not", "a", "row"], "sealedparent", 42, object(), {"id": PARENT}, {"id": PARENT, "end_reason": 7}],
    ids=["list", "str", "int", "object", "no-end_reason", "end_reason-not-text"],
)
def test_a_malformed_lineage_row_refuses_the_send(lineage, monkeypatch, caplog, row):
    lineage.on_get = lambda session_id: row
    _assert_refused(lineage, monkeypatch, caplog)


def test_a_failure_after_the_parent_is_known_sealed_stays_sealed(lineage, monkeypatch):
    lineage.on_tip = sqlite3.OperationalError("database is locked")
    assert durable_compression_continuation(lineage.session) == (True, None)
    sent = _post(lineage, monkeypatch)
    assert (sent.status, sent.payload["continuation_session_id"], sent.mutated) == (409, None, False)


def test_the_send_is_accepted_once_the_read_succeeds_again(lineage, monkeypatch, caplog):
    lineage.rows = _rows(end_reason=None)
    lineage.on_get = sqlite3.OperationalError("database is locked")
    _assert_refused(lineage, monkeypatch, caplog)
    lineage.on_get = None
    assert _post(lineage, monkeypatch).mutated


def test_the_read_raises_lineage_unreadable_for_callers(lineage):
    from api.compression_continuation import LineageUnreadable

    lineage.on_get = ModuleNotFoundError(SECRET_DETAIL)
    with pytest.raises(LineageUnreadable):
        durable_compression_continuation(lineage.session)


# ── The GET navigation hint is read-only: unchanged ──


def test_get_hint_keeps_sidecar_recovery_when_the_read_fails(lineage, monkeypatch, tmp_path):
    lineage.on_get = sqlite3.OperationalError("database is locked")
    assert routes._pre_compression_continuation_session_id(lineage.session) is None
    lineage.on_get = None
    monkeypatch.delitem(sys.modules, "hermes_state")
    monkeypatch.setattr(sys, "meta_path", [_BrokenHermesState(RuntimeError("boom")), *sys.meta_path])
    assert routes._pre_compression_continuation_session_id(lineage.session) is None

    lineage.session.pre_compression_snapshot = True
    child = SimpleNamespace(session_id="sidecarchild", profile="default", parent_session_id=PARENT,
                            pre_compression_snapshot=False, updated_at=2, created_at=1)
    monkeypatch.setattr(routes, "SESSIONS", {"sidecarchild": child})
    monkeypatch.setattr("api.config.SESSION_DIR", tmp_path)
    assert routes._pre_compression_continuation_session_id(lineage.session) == "sidecarchild"


# ── The browser: no continuation means the ordinary failed-send path ──


def test_browser_takes_the_failed_send_path_for_a_refusal_without_continuation():
    import subprocess
    from pathlib import Path

    source = (Path(__file__).parents[1] / "static/messages.js").read_text()
    helper = source[source.index("async function _recoverCompressedSend("):source.index("function _restoreComposerDraftAfterFailedSend(")]
    script = r"""
const assert = require('node:assert/strict');
let S={session:{session_id:'old'}}, INFLIGHT={old:{}};
const calls=[];
const stopApprovalPolling=()=>{},stopClarifyPolling=()=>{},removeThinking=()=>{},setBusy=()=>{},setComposerStatus=()=>{},showToast=()=>{};
const loadSession=async sid=>{calls.push(['load',sid])};
const _restoreComposerDraftAfterFailedSend=(...args)=>calls.push(['restore',...args]);
(async()=>{
const err={status:409,body:JSON.stringify({code:'session_rotated',continuation_session_id:null,error:'x'})};
// Not handled here: send() falls through to its failed-send path, which shows
// the error and restores the draft (tests/test_issue5472_preserve_draft_on_failed_send.py).
assert.equal(await _recoverCompressedSend(err,'old','conclusion?',[],Promise.resolve()),false);
assert.deepEqual(calls,[]);
assert.deepEqual(INFLIGHT.old,{});
})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(["node", "-e", helper + script], check=True, timeout=15)
