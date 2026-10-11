"""A stale approval card cannot answer the session's current gateway run.

Kept from the YOLO compatibility tests when session YOLO went with the Admin
(ADR 0006): the run-owner check guards every approval relay, YOLO or not.
"""

from types import SimpleNamespace

import pytest

import api.config as config
import api.gateway_chat as gateway_chat

try:
    import tools.approval  # noqa: F401

    APPROVAL_AVAILABLE = True
except ImportError:
    APPROVAL_AVAILABLE = False


@pytest.mark.skipif(not APPROVAL_AVAILABLE, reason="tools.approval unavailable")
def test_stale_card_run_owner_cannot_rebind_to_current_run(monkeypatch):
    from api import route_approvals, routes
    from tools.approval import _ApprovalEntry

    sid = "webui-card-stale-run-owner"
    stream_id = "stream-current-owner"
    current_run_id = "run-current-owner"
    approval_id = "gateway-reused-approval-id"
    response = {}
    relays = []

    def fake_j(_handler, data, status=200, extra_headers=None):
        response.update(payload=data, status=status)
        return data

    def fake_respond(_self, run_id, got_approval_id, choice):
        relays.append((run_id, got_approval_id, choice))
        return {"resolved": 1}

    current = {
        "command": "current command",
        "approval_id": approval_id,
        "run_id": current_run_id,
        "_gateway_agent_identity_v1": True,
    }
    current_entry = _ApprovalEntry(current)
    with routes._lock:
        routes._gateway_queues[sid] = [current_entry]
    route_approvals.submit_gateway_pending_mirror(sid, current)

    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(active_stream_id=stream_id))
    monkeypatch.setattr("api.runner_client.HttpRunnerClient.respond_approval", fake_respond)
    monkeypatch.setattr(config, "gateway_supports_approval_identity_v1", lambda *_a, **_k: True)
    monkeypatch.setenv("HERMES_WEBUI_CHAT_BACKEND", "gateway")
    gateway_chat._STREAM_RUN_IDS[stream_id] = current_run_id

    try:
        body = {
            "session_id": sid,
            "choice": "once",
            "approval_id": approval_id,
            "run_id": "run-stale-owner",
            "mirror_token": "mirror-stale-owner",
        }
        routes._handle_approval_respond(object(), body)

        assert response["status"] == 409
        assert response["payload"]["ok"] is False
        assert response["payload"]["code"] == "gateway_run_unavailable"
        assert relays == []
        assert not current_entry.event.is_set()
        assert route_approvals.gateway_pending_mirror(
            sid, approval_id=approval_id, run_id=current_run_id
        ) is not None
    finally:
        gateway_chat._STREAM_RUN_IDS.pop(stream_id, None)
        route_approvals.retire_gateway_pending_mirror(sid, run_id=current_run_id)
        with routes._lock:
            routes._pending.pop(sid, None)
            routes._gateway_queues.pop(sid, None)
