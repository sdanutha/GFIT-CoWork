"""GFIT-CoWork session ownership: every User route answers alike for another
User's session and a missing one (session-ownership ticket 04).

For every User route that names a session (a session id in the query, the body,
a form field or the path, or a stream id), Alice naming Bob's session gets
exactly the same status and body as naming a session that does not exist, once
the id is masked, and Bob's session is unchanged afterwards.

The table is checked against the Admin gate's User route list: every User
entry is either in :data:`SESSION_ROUTES` or in :data:`NAMES_NO_SESSION` with a
reason, so a new User route must be placed in one of them or this test fails.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass, field

import pytest

from api.access import USER_ENDPOINTS
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"


@dataclass(frozen=True)
class Query:
    """``?session_id=`` on a GET, with *params* besides."""
    params: dict = field(default_factory=dict)
    key: str = "session_id"


@dataclass(frozen=True)
class Body:
    """``session_id`` in a JSON body, with *fields* besides."""
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Form:
    """``session_id`` as a multipart form field, with one uploaded file."""
    fields: dict = field(default_factory=dict)
    filename: str = "note.txt"
    content: bytes = b"hello"


@dataclass(frozen=True)
class InPath:
    """The session id is a segment of the path."""
    template: str


@dataclass(frozen=True)
class Stream:
    """``?stream_id=`` naming a run of the session."""


# (method, User entry) -> how the route names a session.
SESSION_ROUTES: dict[tuple[str, str], object] = {
    ("GET", "/api/session"): Query(),
    ("GET", "/api/session/compress/status"): Query(),
    ("GET", "/api/session/export"): Query(),
    ("GET", "/api/session/lineage/report"): Query(),
    ("GET", "/api/session/status"): Query(),
    ("GET", "/api/session/stream"): Query(),
    ("GET", "/api/session/usage"): Query(),
    ("GET", "/api/session/worktree/status"): Query(),
    ("GET", "/api/session/yolo"): Query(),
    ("POST", "/api/session/anchor-scene"): Body(),
    ("POST", "/api/session/archive"): Body({"archived": True}),
    ("POST", "/api/session/branch"): Body(),
    ("POST", "/api/session/clear"): Body(),
    ("POST", "/api/session/compress"): Body(),
    ("POST", "/api/session/compress/start"): Body(),
    ("POST", "/api/session/compression-recovery/start"): Body(),
    ("POST", "/api/session/conversation-rounds"): Body(),
    ("POST", "/api/session/delete"): Body(),
    ("POST", "/api/session/draft"): Body({"text": "draft"}),
    ("POST", "/api/session/duplicate"): Body(),
    ("POST", "/api/session/handoff-summary"): Body(),
    ("POST", "/api/session/import_cli"): Body(),
    ("POST", "/api/session/move"): Body({"project_id": None}),
    ("POST", "/api/session/new"): Body(),  # as prev_session_id, below
    ("POST", "/api/session/pin"): Body({"pinned": True}),
    ("POST", "/api/session/rename"): Body({"title": "renamed"}),
    ("POST", "/api/session/retry"): Body(),
    ("POST", "/api/session/title/regenerate"): Body(),
    ("POST", "/api/session/toolsets"): Body({"toolsets": None}),
    ("POST", "/api/session/truncate"): Body({"keep_count": 0}),
    ("POST", "/api/session/undo"): Body(),
    ("POST", "/api/session/update"): Body({"title": "updated"}),
    ("GET", "/api/sessions/<id>/events"): InPath("/api/sessions/{sid}/events"),
    ("GET", "/session/*"): InPath("/session/{sid}"),
    ("POST", "/api/chat"): Body({"message": "hi"}),
    ("POST", "/api/chat/start"): Body({"message": "hi", "profile": ALICE}),
    ("POST", "/api/chat/steer"): Body({"text": "hi"}),
    ("GET", "/api/chat/stream"): Stream(),
    ("GET", "/api/chat/stream/status"): Stream(),
    ("GET", "/api/chat/cancel"): Stream(),
    ("POST", "/api/btw"): Body({"prompt": "hi"}),
    ("POST", "/api/background"): Body({"question": "hi"}),
    ("GET", "/api/background/status"): Query(),
    ("POST", "/api/goal"): Body({"text": "hi"}),
    ("POST", "/api/bg-task-complete-ack"): Body({"task_id": "t1"}),
    ("GET", "/api/approval/pending"): Query(),
    ("GET", "/api/approval/stream"): Query(),
    ("POST", "/api/approval/respond"): Body({"choice": "deny"}),
    ("GET", "/api/clarify/pending"): Query(),
    ("GET", "/api/clarify/stream"): Query(),
    ("POST", "/api/clarify/respond"): Body({"response": "a"}),
    ("POST", "/api/personality/set"): Body({"name": "default"}),
    ("POST", "/api/upload"): Form(),
    ("POST", "/api/upload/extract"): Form(filename="a.zip", content=b"PK\x05\x06" + b"\0" * 18),
    ("POST", "/api/workspace/upload"): Form({"path": ""}),
    ("GET", "/api/list"): Query({"path": "."}),
    ("GET", "/api/file"): Query({"path": "notes.txt"}),
    ("GET", "/api/file/raw"): Query({"path": "notes.txt"}),
    ("GET", "/api/folder/download"): Query({"path": "."}),
    ("POST", "/api/file/save"): Body({"path": "notes.txt", "content": "changed"}),
    ("POST", "/api/file/office-save"): Body({"path": "notes.docx", "content": ""}),
    ("POST", "/api/file/create"): Body({"path": "new.txt", "content": ""}),
    ("POST", "/api/file/create-dir"): Body({"path": "new-dir"}),
    ("POST", "/api/file/rename"): Body({"path": "notes.txt", "new_name": "moved.txt"}),
    ("POST", "/api/file/move"): Body({"path": "notes.txt", "dest_dir": "sub"}),
    ("POST", "/api/file/delete"): Body({"path": "notes.txt"}),
    ("POST", "/api/file/path"): Body({"path": "notes.txt"}),
    ("GET", "/api/git/status"): Query(),
    ("GET", "/api/git/branches"): Query(),
    ("GET", "/api/git/diff"): Query({"path": "notes.txt"}),
    ("GET", "/api/git-info"): Query(),
}

# (method, User entry) -> why it names no session.
_APP_SHELL = "the app shell and its assets"
_PROFILE = "acts on the User's own Profile, not on one session"
NAMES_NO_SESSION: dict[tuple[str, str], str] = {
    **{("GET", path): _APP_SHELL for path in (
        "/", "/index.html", "/sessions", "/session/static/*", "/static/*", "/manifest.json",
        "/manifest.webmanifest", "/session/manifest.json", "/session/manifest.webmanifest",
        "/sw.js", "/favicon.ico", "/health", "/plugins/*", "/dashboard-plugins/*",
    )},
    ("GET", "/api/auth/status"): "sign in and out",
    ("POST", "/api/auth/login"): "sign in and out",
    ("POST", "/api/auth/logout"): "sign in and out",
    ("POST", "/api/session/import"): "creates a new session with a new id; any id in the body is ignored",
    ("GET", "/api/sessions"): "lists the User's own Profile",
    ("GET", "/api/sessions/search"): "searches the User's own Profile",
    ("GET", "/api/sessions/events"): "the session-list events stream (session-ownership ticket 01)",
    ("GET", "/api/sessions/gateway/stream"): "the gateway session-list stream of the User's own Profile",
    ("POST", "/api/process-complete-ack"): "deprecated: answers 410 before reading the body",
    ("GET", "/api/media"): "a file by path, confined by the Workspace policy",
    ("POST", "/api/transcribe"): "audio in, text out",
    ("GET", "/api/transcribe/capability"): "audio in, text out",
    ("POST", "/api/tts"): "text in, audio out",
    ("POST", "/api/client-events/log"): "browser diagnostics",
    ("GET", "/api/rollback/list"): "a Workspace by path, confined by the Workspace policy",
    ("GET", "/api/rollback/diff"): "a Workspace by path, confined by the Workspace policy",
    ("POST", "/api/rollback/restore"): "a Workspace by path, confined by the Workspace policy",
    ("GET", "/api/crons/recent"): "cron jobs of the User's own Profile; session ids appear only in the answer",
    **{(method, path): _PROFILE for method, path in (
        ("GET", "/api/projects"), ("POST", "/api/projects/create"), ("POST", "/api/projects/rename"),
        ("POST", "/api/projects/delete"), ("GET", "/api/prompts"), ("POST", "/api/prompts"),
        ("DELETE", "/api/prompts"), ("GET", "/api/commands"), ("GET", "/api/commands/bundles"),
        ("GET", "/api/commands/moa/resolve"), ("POST", "/api/commands/bundles/resolve"),
        ("GET", "/api/personalities"), ("GET", "/api/reasoning"),
        ("GET", "/api/models"), ("GET", "/api/models/live"), ("GET", "/api/model/auxiliary"),
        ("GET", "/api/profiles"), ("GET", "/api/profile/active"),
        ("GET", "/api/memory"), ("POST", "/api/memory/write"),
        ("GET", "/api/skills"), ("GET", "/api/skills/content"), ("GET", "/api/skills/usage"),
        ("POST", "/api/skills/save"), ("POST", "/api/skills/delete"), ("POST", "/api/skills/toggle"),
        ("GET", "/api/crons"), ("GET", "/api/crons/status"), ("GET", "/api/crons/history"),
        ("GET", "/api/crons/output"), ("GET", "/api/crons/run"), ("GET", "/api/crons/delivery-options"),
        ("POST", "/api/crons/create"), ("POST", "/api/crons/update"), ("POST", "/api/crons/delete"),
        ("POST", "/api/crons/pause"), ("POST", "/api/crons/resume"), ("POST", "/api/crons/run"),
        ("GET", "/api/workspaces"), ("GET", "/api/workspaces/suggest"),
        ("POST", "/api/workspaces/add"), ("POST", "/api/workspaces/remove"),
        ("POST", "/api/workspaces/rename"), ("POST", "/api/workspaces/reorder"),
        ("GET", "/api/settings"), ("GET", "/api/insights"), ("GET", "/api/project-os/dashboard"),
        ("GET", "/api/wiki/status"), ("GET", "/api/wiki/browse"), ("GET", "/api/wiki/page"),
        ("GET", "/api/notes/sources"), ("GET", "/api/notes/search"), ("GET", "/api/notes/item"),
        ("GET", "/api/plugins"), ("GET", "/api/gateway/status"),
        ("GET", "/api/health/agent"), ("GET", "/api/system/health"),
    )},
}


def _user_entries() -> set[tuple[str, str]]:
    return {(method, path) for methods, path in USER_ENDPOINTS for method in methods}


def test_every_user_route_is_placed_as_naming_a_session_or_not():
    entries = _user_entries()
    assert not set(SESSION_ROUTES) & set(NAMES_NO_SESSION)
    unplaced = sorted(entries - set(SESSION_ROUTES) - set(NAMES_NO_SESSION))
    assert not unplaced, (
        "Add each User route to SESSION_ROUTES if it names a session (then its answer "
        f"for another User's session is tested), or to NAMES_NO_SESSION with a reason: {unplaced}"
    )
    stale = sorted((set(SESSION_ROUTES) | set(NAMES_NO_SESSION)) - entries)
    assert not stale, f"not a User route any more: {stale}"


# ── The answers ──────────────────────────────────────────────────────────────

@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


@pytest.fixture
def alice(srv):
    return srv.logged_in(ALICE)


@pytest.fixture
def bob(srv):
    return srv.logged_in(BOB)


def _bob_session(bob) -> str:
    status, body, _ = bob.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    status, body, _ = bob.post("/api/session/rename", {"session_id": sid, "title": "Bob's plan"})
    assert status == 200, body
    return sid


def _bob_stream(sid) -> str:
    """A run of Bob's session, as the active-run registry records it."""
    from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK

    stream_id = f"stream-of-{sid}"
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS[stream_id] = {"session_id": sid, "stream_id": stream_id}
    return stream_id


def _forget_stream(stream_id):
    from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK

    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS.pop(stream_id, None)


def _raw(client, method, path, *, body=None, headers=None) -> tuple[int, str]:
    """Status and body text; for an event stream, only up to its first event."""
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=15)
    sent = dict(headers or {})
    sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    conn.request(method, path, body=body, headers=sent)
    resp = conn.getresponse()
    if "text/event-stream" in (resp.headers.get("Content-Type") or ""):
        lines = []
        while True:
            line = resp.readline().decode("utf-8", "replace")
            if not line or (not line.strip() and lines):
                break
            lines.append(line)
        text = "".join(lines)
    else:
        text = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, text


def _ask(client, method, entry, how, sid) -> tuple[int, str]:
    """What *client* is answered when *method* *entry* names the session *sid*."""
    if isinstance(how, InPath):
        return _raw(client, method, how.template.format(sid=sid))
    if isinstance(how, Stream):
        return _raw(client, method, f"{entry}?stream_id={sid}")
    if isinstance(how, Query):
        query = "&".join(f"{k}={v}" for k, v in {how.key: sid, **how.params}.items())
        return _raw(client, method, f"{entry}?{query}")
    if isinstance(how, Form):
        boundary = "gfit-test-boundary"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in {"session_id": sid, **how.fields}.items()
        ]
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{how.filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode() + how.content + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        return _raw(client, method, entry, body=b"".join(parts),
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    key = "prev_session_id" if entry == "/api/session/new" else "session_id"
    payload = json.dumps({key: sid, **how.fields}).encode()
    return _raw(client, method, entry, body=payload, headers={"Content-Type": "application/json"})


def _masked(answer, sid, entry) -> tuple[int, str]:
    """The answer with *sid* masked, and for a new session its fresh id and times."""
    status, text = answer
    text = text.replace(sid, "<sid>")
    if entry == "/api/session/new":
        text = re.sub(r'"(session_id|created_at|updated_at|last_message_at)": ("[^"]*"|[0-9.]+)',
                      r'"\1": <x>', text)
    return status, text


def _bob_view(srv, bob, sid):
    """Bob's session as he loads it, and every file in his Workspace."""
    status, body, _ = bob.get(f"/api/session?session_id={sid}")
    session = (body or {}).get("session") if isinstance(body, dict) else None
    if isinstance(session, dict):
        session = {k: v for k, v in session.items() if k not in ("last_viewed_at", "server_time")}
    workspace = srv.profile_home(BOB) / "workspace"
    files = {
        str(path.relative_to(workspace)): path.read_bytes() if path.is_file() else None
        for path in sorted(workspace.rglob("*"))
    }
    return status, session, files


@pytest.mark.parametrize("method,entry", sorted(SESSION_ROUTES))
def test_another_users_session_answers_like_a_missing_one(srv, alice, bob, method, entry):
    how = SESSION_ROUTES[(method, entry)]
    bob_sid = _bob_session(bob)
    # The files the write routes name, in Bob's Workspace.
    workspace = srv.profile_home(BOB) / "workspace"
    (workspace / "sub").mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("Bob's notes")
    missing = f"missing-{bob_sid}"
    if isinstance(how, Stream):
        theirs, nobodys = _bob_stream(bob_sid), f"stream-of-{missing}"
    else:
        theirs, nobodys = bob_sid, missing
    before = _bob_view(srv, bob, bob_sid)
    try:
        theirs_answer = _ask(alice, method, entry, how, theirs)
        missing_answer = _ask(alice, method, entry, how, nobodys)
    finally:
        if isinstance(how, Stream):
            _forget_stream(theirs)
    assert _masked(theirs_answer, theirs, entry) == _masked(missing_answer, nobodys, entry)
    assert BOB not in theirs_answer[1]
    assert _bob_view(srv, bob, bob_sid) == before
