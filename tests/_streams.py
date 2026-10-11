"""Close the streams a test opens, for tests whose stubbed worker never runs.

Chat start opens a stream (``api.config.STREAMS``, its owner record, the
session's write-back owner); the worker closes it when the turn ends. A test
that replaces the worker thread with a stub must close what it opened itself,
or the stream stays live for the rest of the process (the session-list cache
then freezes its state.db stamps; ticket 10). Only the streams opened inside
the block are closed; anything else is left alone.
"""
from __future__ import annotations

import contextlib


@contextlib.contextmanager
def closing_streams_opened(session_id: str | None = None):
    from api import config, run_registry

    before = set(config.STREAMS)
    try:
        yield
    finally:
        for stream_id in set(config.STREAMS) - before:
            run_registry.close_stream(stream_id)
            if session_id:
                config.clear_session_writeback_owner_if_owned(session_id, stream_id)
