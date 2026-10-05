"""GFIT-CoWork -- the run registry: the one way to open and close a turn's stream.

Every turn the agent runs (chat, regeneration, ``/btw``, background, a gateway
turn and a gateway turn resumed after a restart) gets a stream: the event
channel the browser watches, published in the stream map (``STREAMS``) so that
Stop, Steer, reconnect and session ownership can find it. Session ownership
decides who may watch or stop a stream by the session that owns it, so the
owner is recorded *before* the stream is visible.

- :func:`open_stream` records the owner and publishes the channel in one
  critical section under the stream lock (optionally only if no stream with
  that id is live), and marks goal-related turns.
- :func:`close_stream` removes a stream that never got a worker (a failed
  launch) under the stream lock, forgets its owner and drops its goal mark.
- :func:`detach_stream_locked` removes a stream while its worker may still be
  unwinding: Stop's eager detach and the workers' teardowns, which already
  hold the stream lock and clear the rest of the turn's state in the same
  critical section. The owner stays recorded while the active-run row exists,
  so Stop and session ownership keep finding the run; unregistering the
  active run (``api.config.unregister_active_run``) forgets it.
- :func:`forget_owner` drops only the owner record: a stream cancelled before
  its worker started, or an active-run row pruned as a zombie.
- :func:`live_stream_ids` answers readers outside the turn machinery.

Lock order: ``STREAMS_LOCK``, then ``ACTIVE_RUNS_LOCK`` (the Stop/Steer edge,
taken by cancel and Steer, never here), and the owner record's own lock
innermost. Nothing here takes ``ACTIVE_RUNS_LOCK``.

The maps themselves (``STREAMS``, the owner record, the goal marks) stay
defined in ``api.config``; only this module writes the stream map and the
owner record (``api.config.unregister_active_run`` also forgets an owner).
"""
from __future__ import annotations

from api import config as _config


def open_stream(session_id: str, stream_id: str, *, goal_related: bool = False, only_if_absent: bool = False):
    """Open *stream_id* for *session_id* and return its channel.

    With *only_if_absent*, a stream already live under that id is left alone
    (its owner too) and None is returned.
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


def close_stream(stream_id: str) -> bool:
    """Remove a stream no worker took up, forget its owner and drop its goal mark.
    Returns whether a live stream was removed."""
    with _config.STREAMS_LOCK:
        removed = _config.STREAMS.pop(stream_id, None) is not None
        _config.unregister_stream_owner(stream_id)
    _config.STREAM_GOAL_RELATED.pop(stream_id, None)
    return removed


def detach_stream_locked(stream_id: str) -> bool:
    """Remove *stream_id* from the stream map, keeping its owner recorded (see
    the module docstring). The caller holds ``STREAMS_LOCK``."""
    return _config.STREAMS.pop(stream_id, None) is not None


def forget_owner(stream_id: str) -> None:
    """Drop the owner record of a stream whose worker never ran or is gone."""
    _config.unregister_stream_owner(stream_id)


def live_stream_ids() -> frozenset:
    with _config.STREAMS_LOCK:
        return frozenset(_config.STREAMS)
