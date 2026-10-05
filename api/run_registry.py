"""GFIT-CoWork -- the run registry: the one way to open and close a turn's stream.

Every turn the agent runs (chat, regeneration, ``/btw``, background, a gateway
turn and a gateway turn resumed after a restart) gets a stream: the event
channel the browser watches, published in the stream map (``STREAMS``) so that
Stop, Steer, reconnect and session ownership can find it. Session ownership
decides who may watch or stop a stream by the session that owns it, so the
owner is recorded *before* the stream is visible, and forgotten when it closes.

- :func:`open_stream` records the owner, creates the channel, publishes it
  under the stream lock (optionally only if no stream with that id is live)
  and marks goal-related turns.
- :func:`close_stream` removes the stream under the stream lock and forgets
  its owner. A caller that already holds the stream lock (a worker's teardown,
  which pops the rest of the turn's state in the same critical section) calls
  :func:`close_stream_locked`. Lock order is ``STREAMS_LOCK``, then the
  owner record's own lock; nothing here takes ``ACTIVE_RUNS_LOCK`` (which
  comes after ``STREAMS_LOCK`` too).
- :func:`forget_owner` drops only the owner record, for an active-run row
  pruned as a zombie (its stream is already gone).
- Readers ask :func:`stream_is_live`, :func:`live_stream_ids` and
  :func:`live_streams_of_sessions` instead of reading the map.

The maps themselves (``STREAMS``, the owner record, the goal marks) stay
defined in ``api.config``; this module is the only one that writes them.
"""
from __future__ import annotations

from api import config as _config


def open_stream(session_id: str, stream_id: str, *, goal_related: bool = False, only_if_absent: bool = False):
    """Open *stream_id* for *session_id* and return its channel.

    The owner is recorded before the stream is published, both under the
    stream lock, so a reader that sees the stream also sees its owner. With
    *only_if_absent*, a stream already live under that id is left alone (its
    owner too) and None is returned.
    """
    channel = _config.create_stream_channel()
    with _config.STREAMS_LOCK:
        if only_if_absent and stream_id in _config.STREAMS:
            return None
        _config.register_stream_owner(stream_id, session_id)
        _config.STREAMS[stream_id] = channel
    if goal_related:
        _config.STREAM_GOAL_RELATED[stream_id] = True
    return channel


def close_stream_locked(stream_id: str, *, channel=None) -> bool:
    """Remove *stream_id* (only while it is still *channel*, when given) and
    forget its owner. The caller holds ``STREAMS_LOCK``."""
    current = _config.STREAMS.get(stream_id)
    if channel is not None and current is not channel:
        return False
    removed = _config.STREAMS.pop(stream_id, None) is not None
    _config.unregister_stream_owner(stream_id)
    return removed


def close_stream(stream_id: str, *, channel=None) -> bool:
    """Remove *stream_id* under the stream lock and forget its owner; also drops
    its goal-related mark. Returns whether a live stream was removed."""
    with _config.STREAMS_LOCK:
        removed = close_stream_locked(stream_id, channel=channel)
    _config.STREAM_GOAL_RELATED.pop(stream_id, None)
    return removed


def forget_owner(stream_id: str) -> None:
    """Drop the owner record of a stream that is no longer live (a pruned zombie run)."""
    _config.unregister_stream_owner(stream_id)


def stream_is_live(stream_id: str) -> bool:
    with _config.STREAMS_LOCK:
        return stream_id in _config.STREAMS


def live_stream_ids() -> frozenset:
    with _config.STREAMS_LOCK:
        return frozenset(_config.STREAMS)


def live_streams_of_sessions(session_ids) -> dict:
    """{stream_id: session_id} for the live streams owned by any of *session_ids*."""
    wanted = {str(sid) for sid in session_ids if sid}
    found = {}
    for stream_id in live_stream_ids():
        owner = _config.stream_owner_session_id(stream_id)
        if owner in wanted:
            found[stream_id] = owner
    return found
