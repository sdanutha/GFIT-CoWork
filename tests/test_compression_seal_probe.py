"""A send after the Agent's lineage read fails reads the seal bit itself (ticket 15).

The Agent's read-only SessionDB open does no schema migration, so a state.db
written by an older Agent fails its newer reads (``get_session`` joins
``system_prompts``) until some writer migrates it. Refusing every send then
locks the Profile out: in the WebUI the writable open that would migrate it
happens during a send. Before a send, ``compression_state_for_send`` therefore
reads ``sessions.end_reason`` for the one id, read-only, when the rich read
fails: 'compression' (Hermes's seal) refuses with no continuation; NULL or
other text, or no row, lets the send through as the rich read would; a
database that cannot tell (no table, no column, corrupt, unreadable, a value
that is not text, a failing query or close) refuses. The probe never writes,
migrates or creates the database, and never opens the Agent's writable store.

The stand-in SessionDB runs Hermes's own ``get_session`` query against a real
SQLite file, so the old-schema failure is the real one.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.compression_continuation as continuation
from api import profiles, routes
from tests._lineage_gate import post_chat_start

SID = "upgradedparent"
# The test's own SQLite access, unaffected by the spies below (patching
# continuation.sqlite3.connect patches the sqlite3 module itself).
_connect = sqlite3.connect

# hermes_state_sessions.SessionDB.get_session as installed (it joins a table
# that older stores do not have).
_HERMES_GET_SESSION_SQL = (
    "SELECT s.*, COALESCE(sp.prompt, s.system_prompt) AS _system_prompt_resolved, "
    "COALESCE(tp.prompt, s.tool_names) AS _tool_names_resolved "
    "FROM sessions s LEFT JOIN system_prompts sp ON sp.hash = s.system_prompt_hash "
    "LEFT JOIN system_prompts tp ON tp.hash = s.tool_names WHERE s.id = ?"
)
_OLD_SCHEMA = "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, end_reason TEXT)"


class Store:
    """A profile's state.db written by an older Agent, read by a newer one."""

    def __init__(self, tmp_path, monkeypatch, schema=_OLD_SCHEMA):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.path = self.home / "state.db"
        self.opened = []
        conn = _connect(self.path)
        conn.executescript(schema)
        conn.commit()
        conn.close()
        store = self

        class NewerSessionDB:
            """The installed Agent's read-only SessionDB: its get_session expects the newer schema."""

            def __init__(self, db_path=None, read_only=False):
                store.opened.append(read_only)
                self._conn = _connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)

            def get_session(self, session_id):
                row = self._conn.execute(_HERMES_GET_SESSION_SQL, (session_id,)).fetchone()
                return dict(row) if row else None

            def get_compression_tip(self, session_id):
                return session_id

            def close(self):
                self._conn.close()

        monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=NewerSessionDB))
        monkeypatch.setattr(profiles, "_resolve_profile_home_for_name", lambda name: str(self.home))
        self.session = SimpleNamespace(session_id=SID, profile="default", pre_compression_snapshot=False)

    def add(self, end_reason, sid=SID):
        conn = _connect(self.path)
        conn.execute("INSERT INTO sessions (id, source, started_at, end_reason) VALUES (?, 'webui', 1.0, ?)",
                     (sid, end_reason))
        conn.commit()
        conn.close()

    def snapshot(self):
        conn = _connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
            rows = conn.execute("SELECT * FROM sessions ORDER BY id").fetchall() if any(
                name == "sessions" for _t, name, _s in schema) else []
        finally:
            conn.close()
        return {
            "bytes": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "mtime": os.stat(self.path).st_mtime_ns,
            "files": sorted(p.name for p in self.home.iterdir()),
            "schema": schema,
            "rows": rows,
        }


@pytest.fixture
def store(tmp_path, monkeypatch):
    return Store(tmp_path, monkeypatch)


def _refused(sent):
    return sent.status == 409 and sent.payload["code"] == "session_rotated" and sent.payload[
        "continuation_session_id"] is None and not sent.mutated


# ── The upgrade: an older store, a newer Agent ──


def test_the_rich_read_really_fails_on_the_older_store(store):
    store.add(None)
    with pytest.raises(continuation.LineageUnreadable) as caught:
        continuation.durable_compression_continuation(store.session)
    assert "system_prompts" in str(caught.value.__cause__)


def test_an_unsealed_session_in_an_older_store_is_sent(store, monkeypatch):
    store.add(None)
    assert post_chat_start(store.session, monkeypatch).mutated


def test_a_sealed_session_in_an_older_store_stays_refused(store, monkeypatch):
    store.add("compression")
    assert _refused(post_chat_start(store.session, monkeypatch))


def test_a_session_absent_from_an_older_store_is_sent_as_before(store, monkeypatch):
    store.add("compression", sid="someoneelse")
    assert post_chat_start(store.session, monkeypatch).mutated


@pytest.mark.parametrize("reason", ["idle_timeout", "cli_close", "new_session", "", "Compression", " compression"])
def test_only_hermes_seal_value_seals(store, monkeypatch, reason):
    # Hermes seals with end_reason = 'compression' exactly (its _ended_by_compression
    # and SQL); the rich read in the WebUI uses the same rule.
    store.add(reason)
    assert post_chat_start(store.session, monkeypatch).mutated


# ── The probe cannot tell: refused ──


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE other (id TEXT PRIMARY KEY)",
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL)",
    ],
    ids=["no-sessions-table", "no-end_reason-column"],
)
def test_a_store_without_the_seal_bit_refuses(tmp_path, monkeypatch, schema):
    store = Store(tmp_path, monkeypatch, schema=schema)
    assert _refused(post_chat_start(store.session, monkeypatch))


def test_a_corrupt_store_refuses(store, monkeypatch):
    store.path.write_bytes(b"not a sqlite database" * 64)
    assert _refused(post_chat_start(store.session, monkeypatch))


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as non-root")
def test_a_store_that_cannot_be_opened_refuses(store, monkeypatch):
    store.add(None)
    store.path.chmod(0)
    try:
        assert _refused(post_chat_start(store.session, monkeypatch))
    finally:
        store.path.chmod(0o644)


@pytest.mark.parametrize("value", [b"compression", 7, 1.5], ids=["blob", "integer", "real"])
def test_an_end_reason_that_is_not_text_refuses(tmp_path, monkeypatch, value):
    # An untyped column keeps the stored type (a TEXT column would coerce numbers).
    store = Store(tmp_path, monkeypatch, schema="CREATE TABLE sessions (id TEXT PRIMARY KEY, source, started_at, end_reason)")
    store.add(value)
    assert _refused(post_chat_start(store.session, monkeypatch))


class _Connection:
    def __init__(self, real, fail):
        self._real, self._fail = real, fail

    def execute(self, sql, *args):
        if self._fail == "query" and sql.startswith("SELECT"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._real.execute(sql, *args)

    def close(self):
        self._real.close()
        if self._fail == "close":
            raise sqlite3.OperationalError("close failed")


@pytest.mark.parametrize("fail", ["query", "close"])
def test_a_failing_probe_query_or_close_refuses(store, monkeypatch, fail):
    store.add(None)
    monkeypatch.setattr(continuation.sqlite3, "connect", lambda *a, **kw: _Connection(_connect(*a, **kw), fail))
    assert _refused(post_chat_start(store.session, monkeypatch))


# ── The probe reads only ──


@pytest.mark.parametrize("reason", [None, "compression"])
def test_the_probe_changes_nothing(store, monkeypatch, reason):
    store.add(reason)
    before = store.snapshot()
    post_chat_start(store.session, monkeypatch)
    assert store.snapshot() == before


def test_the_probe_opens_read_only_and_never_the_writable_store(store, monkeypatch):
    store.add(None)
    calls = []

    def spy(database, *args, **kwargs):
        calls.append((database, kwargs))
        return _connect(database, *args, **kwargs)

    monkeypatch.setattr(continuation.sqlite3, "connect", spy)
    assert post_chat_start(store.session, monkeypatch).mutated
    # One Agent open, read-only (no migrating open); one probe connection, mode=ro.
    assert store.opened == [True]
    assert len(calls) == 1
    database, kwargs = calls[0]
    assert database == f"{store.path.resolve().as_uri()}?mode=ro" and kwargs["uri"] is True


def test_the_probe_does_not_create_a_missing_store(tmp_path):
    missing = tmp_path / "state.db"
    with pytest.raises(sqlite3.OperationalError):
        continuation._sealed_by_compression(missing, SID)
    assert not missing.exists()


def test_the_probe_connection_refuses_writes(store, monkeypatch):
    store.add(None)
    opened = []

    class Held:
        """The probe's own connection, kept open past its close() to try a write on it."""

        def __init__(self, conn):
            self.conn = conn

        def execute(self, *args):
            return self.conn.execute(*args)

        def close(self):
            pass

    monkeypatch.setattr(continuation.sqlite3, "connect", lambda *a, **kw: opened.append(Held(_connect(*a, **kw))) or opened[-1])
    assert continuation._sealed_by_compression(store.path, SID) is False
    assert len(opened) == 1
    conn = opened[0].conn
    try:
        for write in ("CREATE TABLE x (a)", "UPDATE sessions SET end_reason = 'compression'"):
            with pytest.raises(sqlite3.OperationalError, match="readonly|query_only"):
                conn.execute(write)
    finally:
        conn.close()


# ── Boundaries that stay as they were ──


def test_another_profiles_session_never_reaches_the_lineage_reads(store, monkeypatch):
    store.add(None)

    class Refusal:
        def answer(self, handler, sid):
            return routes.bad(handler, "Session not found", 404)

    monkeypatch.setattr(routes, "request_session_ownership",
                        lambda: SimpleNamespace(refuse_found_session=lambda sid, s: Refusal()))
    monkeypatch.setattr(continuation, "compression_state_for_send",
                        lambda s: pytest.fail("the lineage gate ran before the ownership check"))
    sent = post_chat_start(store.session, monkeypatch)
    assert (sent.status, sent.mutated) == (404, False)


def test_the_get_hint_does_not_use_the_probe(store, monkeypatch):
    store.add("compression")
    monkeypatch.setattr(continuation, "_sealed_by_compression",
                        lambda *a: pytest.fail("the read-only GET hint used the send-path probe"))
    assert routes._pre_compression_continuation_session_id(store.session) is None


# ── With the real Agent (skipped where hermes_state cannot be imported) ──


@pytest.fixture
def real_agent_db(tmp_path, monkeypatch):
    state = pytest.importorskip("hermes_state")
    monkeypatch.syspath_prepend(str(Path(state.__file__).parent))
    monkeypatch.setattr(profiles, "_resolve_profile_home_for_name", lambda name: str(tmp_path))
    return state.SessionDB, tmp_path / "state.db"


@pytest.mark.parametrize("reason", [None, "compression", "idle_timeout", "cli_close", "new_session", "agent_close"])
def test_the_probe_agrees_with_the_real_agent(real_agent_db, reason):
    session_db, path = real_agent_db
    db = session_db(path)
    db.create_session("realsession", source="webui")
    if reason:
        db.end_session("realsession", reason)
    db.close()
    session = SimpleNamespace(session_id="realsession", profile="default")
    rich_sealed = continuation.durable_compression_continuation(session)[0]
    assert continuation._sealed_by_compression(path, "realsession") is rich_sealed is (reason == "compression")
    assert continuation.compression_state_for_send(session)[0] is rich_sealed


@pytest.mark.parametrize("reason,sealed", [(None, False), ("compression", True)])
def test_the_real_agent_on_an_older_store(real_agent_db, reason, sealed):
    session_db, path = real_agent_db
    conn = _connect(path)
    conn.executescript(_OLD_SCHEMA)
    conn.execute("INSERT INTO sessions VALUES ('oldsession', 'webui', 1.0, ?)", (reason,))
    conn.commit()
    conn.close()
    session = SimpleNamespace(session_id="oldsession", profile="default")
    with pytest.raises(continuation.LineageUnreadable):
        continuation.durable_compression_continuation(session)
    assert continuation.compression_state_for_send(session) == (sealed, None)
