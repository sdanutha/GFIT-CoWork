import logging
import types
import threading


def test_server_shutdown_audit_logs_active_stream_context(monkeypatch, caplog):
    import server
    from api import models

    monkeypatch.setattr(server, "_SHUTDOWN_AUDIT_LOGGED", False)
    monkeypatch.setitem(
        models.SESSIONS,
        "session-1\nforged",
        types.SimpleNamespace(active_stream_id="stream-1\rforged", pending_user_message="hello"),
    )
    monkeypatch.setitem(
        models.SESSIONS,
        "session-2",
        types.SimpleNamespace(active_stream_id=None, pending_user_message=None),
    )

    caplog.set_level(logging.INFO, logger="server")
    server._log_shutdown_audit(reason="test-exit")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "[shutdown-audit]" in logged
    assert "reason=test-exit" in logged
    assert "sid=session-1?forged stream=stream-1?forged pending=True" in logged
    assert "session-1\nforged" not in logged
    assert "stream-1\rforged" not in logged
    assert "session-2" not in logged

