"""GFIT-CoWork -- the server notices a disabled Profile and stops its live work.

The Operator changes the Profile roster from the command line
(``api.operator_cli``), in another process. A disabled Profile's logins and
running turns live only in this server process, so the server watches the
roster file: on start and whenever the file changes, it ends the logins and
stops the running turns of every disabled Profile (``roster.stop_live_work``).
The command line pauses the Profile's scheduled jobs itself, since those are
files the Hermes gateway reads.

Admission already refuses a disabled Profile's requests on every request; the
watcher adds what Admission cannot: turns that run with no request, and login
sessions that would come back to life if the Profile were enabled again.

An unreadable roster settles nothing here: Admission already refuses every
request while it is unreadable, and the watcher cannot tell whose work to stop.
"""
from __future__ import annotations

import logging
import threading

from api import roster

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0

_LOCK = threading.Lock()
_last_version: object = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def check() -> list[str]:
    """Settle the disabled Profiles if the roster changed since the last check.

    Returns the Profiles whose live work was stopped (empty when nothing changed).
    """
    global _last_version
    with _LOCK:
        version = roster.version()
        if version == _last_version:
            return []
        names = roster.disabled_names()
        if names is None:
            logger.warning("Profile roster is unreadable; no live work settled")
            # Try again on the next poll: the Operator may fix it in place.
            return []
        _last_version = version
    for name in names:
        roster.stop_live_work(name)
    return names


def _loop() -> None:
    while not _stop.wait(POLL_SECONDS):
        try:
            check()
        except Exception:
            logger.warning("Profile roster check failed", exc_info=True)


def start() -> None:
    """Settle the roster once now, then keep watching it in a daemon thread."""
    global _thread
    try:
        check()
    except Exception:
        logger.warning("Profile roster check failed", exc_info=True)
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="roster-watch")
    _thread.start()


def stop() -> None:
    _stop.set()


def reset() -> None:
    """Forget the last roster seen, so the next check settles it again (tests)."""
    global _last_version
    with _LOCK:
        _last_version = None
