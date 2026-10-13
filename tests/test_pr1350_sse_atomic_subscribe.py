"""Test that the SSE subscribe + snapshot are taken atomically under _lock.

Regression test for the snapshot/subscribe race condition: if subscribe
happens AFTER the snapshot, a submit_pending() that fires in the gap is
both appended to _pending (after our snapshot) AND notified to subscribers
(before we joined) — the client never learns about it until the next event.

The fix in v0.50.248 takes the lock once, registers the subscriber queue,
THEN reads the snapshot — all under the same lock acquisition. That block now
lives in ``route_approvals._approval_sse_subscribe_with_snapshot``, which the
SSE handler calls, so the behavioural tests below run the production code.
"""

import inspect
import pathlib
import sys
import threading
import uuid

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

ROUTES_SRC = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")


def _handler_body() -> str:
    start = ROUTES_SRC.find("def _handle_approval_sse_stream(")
    assert start != -1, "_handle_approval_sse_stream must exist"
    end = ROUTES_SRC.find("\ndef ", start + 1)
    return ROUTES_SRC[start:end if end != -1 else len(ROUTES_SRC)]


def _approval(command):
    return {
        "command": command,
        "pattern_key": command,
        "pattern_keys": [command],
        "description": command,
    }


def test_handler_subscribes_through_the_atomic_helper():
    """The handler must take its queue and snapshot from the atomic helper."""
    body = _handler_body()
    assert "= _approval_sse_subscribe_with_snapshot(sid)" in body
    assert "_approval_sse_subscribers" not in body, (
        "the handler must not register a subscriber outside the atomic helper"
    )


def test_subscriber_registered_before_snapshot_under_one_lock():
    """Source order inside the helper: one `with _lock:`, append before the read."""
    from api import route_approvals as ra

    src = inspect.getsource(ra._approval_sse_subscribe_with_snapshot)
    assert src.count("with _lock:") == 1
    lock_body = src.split("with _lock:", 1)[1]
    sub_idx = lock_body.find("_approval_sse_subscribers.setdefault(session_id, []).append(q)")
    snap_idx = lock_body.find("_pending.get(session_id)")
    assert sub_idx != -1 and snap_idx != -1
    assert sub_idx < snap_idx


def test_snapshot_reflects_pending_head_and_count():
    from api import route_approvals as ra

    sid = f"sse-snap-{uuid.uuid4().hex[:8]}"
    try:
        ra.submit_pending(sid, _approval("first"))
        ra.submit_pending(sid, _approval("second"))
        q, head, total = ra._approval_sse_subscribe_with_snapshot(sid)
        try:
            assert head["command"] == "first"
            assert total == 2
            assert q.empty(), "existing approvals arrive in the snapshot, not the queue"
        finally:
            ra._approval_sse_unsubscribe(sid, q)
    finally:
        with ra._lock:
            ra._pending.pop(sid, None)


def test_submit_racing_the_subscribe_is_never_lost(monkeypatch):
    """A submit_pending() started while the helper runs is seen exactly once.

    The barrier fires from inside the helper (at the gateway reconcile step,
    between registration and snapshot), where ``_lock`` is held. The submitter
    thread is started there, so it can only append and notify after the helper
    releases the lock: the snapshot is empty and the approval must reach the
    queue. A helper that snapshotted without the subscription in place would
    report it nowhere.
    """
    from api import route_approvals as ra

    sid = f"sse-race-{uuid.uuid4().hex[:8]}"
    submitter_waiting = threading.Event()
    real_reconcile = ra.reconcile_gateway_pending_mirror_locked
    started = []

    def submitter():
        submitter_waiting.set()
        ra.submit_pending(sid, _approval("raced"))

    at_barrier = {}

    def reconcile_with_barrier(session_key):
        if not started:
            # Deterministic half: the snapshot step runs with the lock held
            # and the subscriber already registered.
            at_barrier["locked"] = ra._lock.locked()
            at_barrier["subscribed"] = bool(ra._approval_sse_subscribers.get(sid))
            t = threading.Thread(target=submitter)
            started.append(t)
            t.start()
            # We hold ``_lock`` here, so the submitter cannot append or
            # notify until the helper has both subscribed and snapshotted.
            assert submitter_waiting.wait(5)
        return real_reconcile(session_key)

    monkeypatch.setattr(ra, "reconcile_gateway_pending_mirror_locked", reconcile_with_barrier)
    try:
        q, head, total = ra._approval_sse_subscribe_with_snapshot(sid)
        try:
            assert at_barrier == {"locked": True, "subscribed": True}
            started[0].join(5)
            assert not started[0].is_alive()
            if total:
                assert head["command"] == "raced" and q.empty()
            else:
                payload = q.get(timeout=1)
                assert payload["pending"]["command"] == "raced"
                assert payload["pending_count"] == 1
        finally:
            ra._approval_sse_unsubscribe(sid, q)
    finally:
        with ra._lock:
            ra._pending.pop(sid, None)


