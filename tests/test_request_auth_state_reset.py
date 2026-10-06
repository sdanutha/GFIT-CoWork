"""The per-request auth state does not cross a keep-alive request boundary.

``server.Handler`` is reused across HTTP/1.1 keep-alive requests, so the
session decided for one request must not be the next request's. (The queued
Profile cookie this file also guarded went with the Profile cookie, ADR 0006.)
"""
from __future__ import annotations

import api.auth as auth


class _Handler:
    pass


def test_reset_clears_the_request_session_across_keepalive_requests():
    handler = _Handler()
    handler._request_session = {"auth_type": auth.DIRECTORY_AUTH_TYPE}
    handler._request_session_rejected = True

    auth.reset_request_auth_state(handler)

    assert not hasattr(handler, "_request_session")
    assert not hasattr(handler, "_request_session_rejected")
