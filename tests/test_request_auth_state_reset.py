"""The per-request auth state does not cross a keep-alive request boundary.

``server.Handler`` is reused across HTTP/1.1 keep-alive requests. A Set-Cookie
queued by one request but never flushed (for example the Profile cookie a
Directory session's request queues) must not be emitted by the next response,
where it could overwrite a later valid cookie.
"""
from __future__ import annotations

import api.auth as auth
from api.helpers import flush_pending_auth_cookies


class _Handler:
    def __init__(self):
        self.sent_headers = []

    def send_header(self, name, value):
        self.sent_headers.append((name, value))


def test_reset_clears_pending_cookies_across_keepalive_requests():
    handler = _Handler()

    # Request N queues a cookie but the response is never flushed.
    auth._queue_pending_cookie(handler, "hermes_profile=stale-value; Path=/")
    handler._request_session = {"auth_type": auth.DIRECTORY_AUTH_TYPE}

    # Request N+1 begins on the same reused handler.
    auth.reset_request_auth_state(handler)

    assert not hasattr(handler, "_request_session")
    flush_pending_auth_cookies(handler)
    assert handler.sent_headers == []
