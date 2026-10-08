"""Drive POST /api/chat/start up to its compression-lineage gate.

Shared by the lineage gate tests: the request runs in process against a given
session, and the first workspace mutation after the gate is a tripwire, so a
test sees either the gate's response or that the send got through.
"""
from __future__ import annotations

import io
import json
from typing import NamedTuple


class Handler:
    headers = {}

    def __init__(self):
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *args):
        pass

    def end_headers(self):
        pass


class Sent(NamedTuple):
    status: int | None
    payload: dict | None
    mutated: bool  # the gate let the send through to workspace mutation


class _Mutated(Exception):
    pass


def post_chat_start(session, monkeypatch, message="conclusion?") -> Sent:
    from api import routes

    def first_mutation(*_args, **_kwargs):
        raise _Mutated()

    monkeypatch.setattr(routes, "_agent_runtime_barrier_response", lambda **kw: None)
    monkeypatch.setattr(routes, "_get_or_materialize_session", lambda *a, **kw: session)
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "default")
    monkeypatch.setattr(routes, "_resolve_chat_workspace_with_recovery", first_mutation)
    h = Handler()
    try:
        routes._handle_chat_start(h, {"session_id": session.session_id, "message": message})
    except _Mutated:
        return Sent(None, None, True)
    return Sent(h.status, json.loads(h.wfile.getvalue()), False)
