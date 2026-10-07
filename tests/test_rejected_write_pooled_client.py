"""Pooled-client regression for reject-before-read on HTTP/1.1 keep-alive.

The unit tests beside this one assert `close_connection` is set on each
rejection path, and the raw-socket test drives the failure over one socket.
Neither proves the client side: a pooled client (`http.client`) only avoids
the broken connection when the rejection ADVERTISES `Connection: close`.
Without the advertisement the server drops the socket silently and the next
pooled request dies with BrokenPipeError/RemoteDisconnected — or, without the
close at all, the unread body is parsed as the next request line and the
client gets a 400 about a request it never sent.

This file drives the production handler through the real test server and
asserts both halves: the rejection carries `Connection: close`, and a
follow-up request on the same pooled connection succeeds.

The deprecated `/api/process-complete-ack` path is the probe: it answers 410
before reading its JSON body, by design (it runs ahead of the CSRF gate so a
stale tab gets 410, not 403).
"""

import contextlib
import http.client
import os
import socket
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

from tests._pytest_port import BASE

_PORT = urllib.parse.urlparse(BASE).port
_BODY = b'{"stale": true}'


def _test_user_cookie() -> str:
    """The shared test server's login for the test User (conftest.py).

    Login is always on (ADR 0006): a request with no session is refused 401
    before the rejection under test, so these raw requests carry it. The
    auth-path cases below run their own server and send their own cookies.
    """
    return os.environ["HERMES_WEBUI_TEST_COOKIE"]


def _as_test_user(request: bytes) -> bytes:
    """*request* with the test User's session cookie after its request line."""
    if b"\r\nCookie:" in request.split(b"\r\n\r\n", 1)[0]:
        return request
    line, rest = request.split(b"\r\n", 1)
    return line + b"\r\nCookie: " + _test_user_cookie().encode() + b"\r\n" + rest


def _reject_with_body_then_pooled_followup() -> tuple[int, str | None, int]:
    """POST a deprecated endpoint with a body, then GET on the same pool."""
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/process-complete-ack",
            body=_BODY,
            headers={"Content-Type": "application/json", "Cookie": _test_user_cookie()},
        )
        resp = conn.getresponse()
        reject_status = resp.status
        close_header = resp.getheader("Connection")
        resp.read()

        conn.request("GET", "/api/health/agent", headers={"Cookie": _test_user_cookie()})
        follow = conn.getresponse()
        follow_status = follow.status
        follow.read()
    finally:
        conn.close()
    return reject_status, close_header, follow_status


def test_rejected_write_advertises_close_and_pooled_followup_succeeds():
    status, close_header, follow_status = _reject_with_body_then_pooled_followup()
    assert status == 410, f"expected the deprecated-path 410, got {status}"
    assert close_header == "close", (
        f"rejection must advertise Connection: close so pooled clients don't "
        f"reuse the dead socket; got {close_header!r}"
    )
    assert follow_status == 200, (
        f"pooled follow-up after a reject-before-read must succeed; got {follow_status}"
    )


# ── Framing the Content-Length gate could not see ─────────────────────────────
#
# Both cases below are pipelined down ONE socket against the real server: the
# reject-before-read request first, an ordinary GET immediately after it in the
# same write. If the rejection closes, the server answers once and EOFs and the
# GET is never seen. If it does not, the leftover body/chunk bytes are parsed as
# the next request line and a second response comes back — a 400 bad-syntax or
# `501 Unsupported method` naming bytes the client never sent as a request.

_FOLLOWING_GET = b"GET /api/health/agent HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: */*\r\n\r\n"
_MULTIPART_UPLOAD_PATHS = (
    "/api/upload",
    "/api/upload/extract",
    "/api/workspace/upload",
    "/api/transcribe",
)
_MULTIPART_PAYLOAD = (
    b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
    b"\r\nhello\r\n--x--\r\n"
)
_CHUNKED_MULTIPART_BODY = (
    f"{len(_MULTIPART_PAYLOAD):X}\r\n".encode() + _MULTIPART_PAYLOAD + b"\r\n0\r\n\r\n"
)


def _pipelined_after(
    request: bytes,
    *,
    stop_after: int | None = None,
    port: int | None = None,
    follow: bytes = _FOLLOWING_GET,
) -> bytes:
    """Send *request* and a plain GET in one write; return every byte answered.

    Reads to EOF by default, which is the whole point of the closing cases: the
    proof is that NOTHING follows the single response. `stop_after` bounds the
    read for the keep-alive cases, where the socket stays open by design and
    reading to EOF would only burn the timeout.

    `port`/`follow` exist for the auth-enabled cases below, which need their own
    server instance and a PUBLIC follow-up path.
    """
    if port is None:
        request, follow = _as_test_user(request), _as_test_user(follow)
    sock = socket.create_connection(("127.0.0.1", port or _PORT), timeout=10)
    try:
        sock.sendall(request + follow)
        received = b""
        while stop_after is None or received.count(b"HTTP/1.1 ") < stop_after:
            try:
                chunk = sock.recv(65536)
            except (TimeoutError, socket.timeout, ConnectionError):
                break
            if not chunk:
                break
            received += chunk
        return received
    finally:
        sock.close()


def _pipelined_after_login_rejection(request: bytes, *, stop_after: int | None = None) -> bytes:
    """Like :func:`_pipelined_after`, on a server with login on, where *request*
    (an unauthenticated GET) is refused 401 before any body is read; the
    follow-up is a public GET."""
    with _own_server() as port:
        return _pipelined_after(request, stop_after=stop_after, port=port, follow=_PUBLIC_FOLLOWING_GET)


def _assert_single_closed_response(answered: bytes, status: bytes, leftover: bytes) -> None:
    text = answered.decode("latin-1", errors="replace")
    assert answered.startswith(b"HTTP/1.1 " + status), text
    assert b"Connection: close" in answered, text
    assert answered.count(b"HTTP/1.1 ") == 1, (
        f"the pipelined GET was answered, so the socket stayed open: {text}"
    )
    assert leftover not in answered, f"the unread body reached the request parser: {text}"
    assert b"Bad request syntax" not in answered, text
    assert b"Unsupported method" not in answered, text


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
def test_chunked_upload_rejection_closes_and_cannot_poison_the_socket(path):
    """A chunked upload has no Content-Length — the old gate read it as empty.

    All four multipart handlers then answered 400 "No file field in request"
    with keep-alive intact, leaving the chunk bytes on the socket.
    """
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n"
        "Transfer-Encoding: chunked\r\n"
        "\r\n".encode() + _CHUNKED_MULTIPART_BODY
    )

    _assert_single_closed_response(answered, b"411", _MULTIPART_PAYLOAD)


def test_unauthenticated_get_with_a_body_closes_and_cannot_poison_the_socket(auth_on):
    """An unauthenticated GET that carries a declared body.

    `read_request_body=False` said "no body" and the 403 went out with
    keep-alive; the gate's repro then got `501 Unsupported method ('{}GET')` on
    the same socket. No login cookie here, so auth fails
    before the body would ever be read.
    """
    answered = _pipelined_after_login_rejection(
        "GET /api/sessions HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(_BODY)}\r\n"
        "\r\n".encode() + _BODY
    )

    _assert_single_closed_response(answered, b"401", _BODY)


# ── Framing hidden behind a DUPLICATED header ─────────────────────────────────
#
# `Content-Length: 0` sent ahead of the real length: `Message.get()` returns only
# the first value, so every reader saw an empty body and drained nothing. Both
# cases below were reproduced live on the otherwise-fixed head, poisoning the
# socket exactly as the single-header cases used to:
#   sidecar GET -> 403, then 400 Bad request syntax ('{"stale": true}GET /...')
#   /api/upload -> 400, then 400 Bad request syntax ('--x')


def test_duplicate_content_length_unauthenticated_get_closes_and_cannot_poison_the_socket(auth_on):
    answered = _pipelined_after_login_rejection(
        "GET /api/sessions HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 0\r\n"
        f"Content-Length: {len(_BODY)}\r\n"
        "\r\n".encode() + _BODY
    )

    _assert_single_closed_response(answered, b"401", _BODY)


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
def test_duplicate_content_length_upload_closes_and_cannot_poison_the_socket(path):
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n"
        "Content-Length: 0\r\n"
        f"Content-Length: {len(_MULTIPART_PAYLOAD)}\r\n"
        "\r\n".encode() + _MULTIPART_PAYLOAD
    )

    _assert_single_closed_response(answered, b"400", _MULTIPART_PAYLOAD)


# ── Framing hidden behind a BLANK header value ────────────────────────────────
#
# `Content-Length:` with nothing after the colon reads back as '' and was skipped
# as "no length declared", so the rejection kept the connection alive. Both cases
# below were reproduced live on the otherwise-fixed head:
#   sidecar GET   -> 403, then 400 Bad request syntax ('{"stale": true}GET /...')
#   /api/upload   -> 400, then 400 Bad request syntax ('--x')
# `Message` collapses `Content-Length:` and `Content-Length:   ` to the same '',
# and a blank `Transfer-Encoding:` reached the allowlist of codings that "framed
# nothing" (since deleted — see the identity section below), so each spelling
# below poisoned the socket the same way.

_BLANK_FRAMING_HEADER_LINES = (
    b"Content-Length:\r\n",
    b"Content-Length:    \r\n",
    b"Content-Length:\r\nContent-Length:\r\n",
    b"Content-Length: 0\r\nContent-Length:\r\n",
    b"Transfer-Encoding:\r\n",
)


@pytest.mark.parametrize("framing", _BLANK_FRAMING_HEADER_LINES)
def test_blank_framing_unauthenticated_get_closes_and_cannot_poison_the_socket(framing, auth_on):
    answered = _pipelined_after_login_rejection(
        b"GET /api/sessions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n" + framing + b"\r\n" + _BODY
    )

    _assert_single_closed_response(answered, b"401", _BODY)


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
@pytest.mark.parametrize(
    ("framing", "status"),
    [
        (b"Content-Length:\r\n", b"400"),
        (b"Content-Length:    \r\n", b"400"),
        (b"Transfer-Encoding:\r\n", b"411"),
    ],
    ids=["blank-length", "whitespace-only-length", "blank-transfer-encoding"],
)
def test_blank_framing_upload_closes_and_cannot_poison_the_socket(path, framing, status):
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n".encode()
        + framing
        + b"\r\n"
        + _MULTIPART_PAYLOAD
    )

    _assert_single_closed_response(answered, status, _MULTIPART_PAYLOAD)


# ── Framing only `int()` reads as zero ────────────────────────────────────────
#
# RFC 9110 is `Content-Length = 1*DIGIT`; `int()` also takes a sign, PEP 515
# underscores and non-ASCII whitespace padding, so each value below parsed to an
# honest `0`, took the "every declared length agrees on zero" keep-alive branch
# and left the payload queued. Reproduced live on the otherwise-fixed head:
#   sidecar GET -> 403 (no Connection: close), then
#                  400 Bad request syntax ('{"stale": true}GET /api/health/agent HTTP/1.1')
#   /api/upload -> 400, then 400 Bad request syntax ('--x')
# `Transfer-Encoding: \xa0identity` is the same mistake one header over: strip()
# erased the U+00A0 and it read back as the `identity` token. Everything here is
# wire-reachable -- a request line decodes as latin-1, so U+00A0 and U+0085 pass
# through untouched (a non-ASCII DIGIT such as U+0660 cannot, which is why that
# family is pinned at the helper level in test_rejected_write_connection_close.py).

_MALFORMED_ZERO_FRAMING_LINES = [
    b"Content-Length: +0\r\n",
    b"Content-Length: -0\r\n",
    b"Content-Length: 0_0\r\n",
    b"Content-Length: +00\r\n",
    b"Content-Length: 0_0_0\r\n",
    b"Content-Length: \xa00\r\n",
    b"Content-Length: 0\x85\r\n",
    b"Content-Length: 0\r\nContent-Length: +0\r\n",
    b"Transfer-Encoding: \xa0identity\r\n",
]


@pytest.mark.parametrize("framing", _MALFORMED_ZERO_FRAMING_LINES)
def test_malformed_zero_framing_unauthenticated_get_closes_and_cannot_poison_the_socket(framing, auth_on):
    answered = _pipelined_after_login_rejection(
        b"GET /api/sessions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n" + framing + b"\r\n" + _BODY
    )

    _assert_single_closed_response(answered, b"401", _BODY)


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
@pytest.mark.parametrize(
    ("framing", "status"),
    [
        (b"Content-Length: +0\r\n", b"400"),
        (b"Content-Length: 0_0\r\n", b"400"),
        (b"Content-Length: \xa00\r\n", b"400"),
        (b"Transfer-Encoding: \xa0identity\r\n", b"411"),
    ],
    ids=["sign", "underscore", "nbsp-padded", "nbsp-padded-identity"],
)
def test_malformed_zero_framing_upload_closes_and_cannot_poison_the_socket(path, framing, status):
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n".encode()
        + framing
        + b"\r\n"
        + _MULTIPART_PAYLOAD
    )

    _assert_single_closed_response(answered, status, _MULTIPART_PAYLOAD)


# ── `Transfer-Encoding: identity` frames nothing a reader can trust ───────────
#
# `identity` sat in an allowlist of codings that "frame nothing", so a request
# carrying it read back as body-less however many payload bytes followed. RFC
# 9112 §6.3 leaves no room for that: a request whose final transfer coding is not
# `chunked` cannot be framed by a recipient, so it must be refused and the
# connection closed — and `http.server` decodes no transfer coding at all, so
# `identity` is no more readable here than `chunked` is. Reproduced live on the
# otherwise-fixed head, pipelined down one socket:
#
#   GET /api/extensions/probe/sidecar/ping  `Transfer-Encoding: identity`
#                                           {"stale": true}
#     -> HTTP/1.1 403 Forbidden        (no Connection: close)
#     -> HTTP/1.1 400 Bad request syntax
#            ('{"stale": true}GET /api/health/agent HTTP/1.1')
#   POST /api/upload  `Transfer-Encoding: identity`  --x...
#     -> HTTP/1.1 400 Bad Request      (no Connection: close, "No file field")
#     -> HTTP/1.1 400 Bad request syntax ('--x')
#
# Only the spellings that normalize to the bare token were poisoned — every
# neighbouring value (`identity, chunked`, `identity,identity`, a lone comma, an
# unknown coding, a non-OWS-padded token, a blank value) already missed the
# allowlist and already closed. Those rows are in the sweep below anyway: this is
# the fourth round in which an adjacent spelling of one framing rule survived a
# fix, so the whole neighbourhood gets pinned rather than just the reported case.

_IDENTITY_FRAMING_LINES = [
    b"Transfer-Encoding: identity\r\n",
    b"Transfer-Encoding: IDENTITY\r\n",
    b"Transfer-Encoding: Identity\r\n",
    b"Transfer-Encoding:  identity \r\n",
    b"Transfer-Encoding: \tidentity\t\r\n",
    b"Transfer-Encoding: identity\r\nTransfer-Encoding: identity\r\n",
    b"Transfer-Encoding: identity\r\nContent-Length: 0\r\n",
    b"Transfer-Encoding: identity\r\nContent-Length: " + str(len(_BODY)).encode() + b"\r\n",
    b"Transfer-Encoding: identity, chunked\r\n",
    b"Transfer-Encoding: chunked, identity\r\n",
    b"Transfer-Encoding: identity,identity\r\n",
    b"Transfer-Encoding: ,\r\n",
    b"Transfer-Encoding: banana\r\n",
]


@pytest.mark.parametrize("framing", _IDENTITY_FRAMING_LINES)
def test_identity_framing_unauthenticated_get_closes_and_cannot_poison_the_socket(framing, auth_on):
    answered = _pipelined_after_login_rejection(
        b"GET /api/sessions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n" + framing + b"\r\n" + _BODY
    )

    _assert_single_closed_response(answered, b"401", _BODY)


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
@pytest.mark.parametrize(
    "framing",
    [
        b"Transfer-Encoding: identity\r\n",
        b"Transfer-Encoding: IDENTITY \r\n",
        b"Transfer-Encoding: identity\r\nContent-Length: 0\r\n",
    ],
    ids=["identity", "identity-cased-and-padded", "identity-plus-zero-length"],
)
def test_identity_framing_upload_closes_and_cannot_poison_the_socket(path, framing):
    """All four multipart handlers, mirroring the chunked cases above.

    `identity` carries no Content-Length either, so the length-0 read found no
    parts and each handler answered 400 "No file field in request" with the whole
    multipart payload still queued on an open socket.
    """
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n".encode()
        + framing
        + b"\r\n"
        + _MULTIPART_PAYLOAD
    )

    _assert_single_closed_response(answered, b"411", _MULTIPART_PAYLOAD)


@pytest.mark.parametrize(
    "framing",
    [
        b"",
        b"Content-Length: 0\r\n",
        b"Content-Length: 00\r\n",
        b"Content-Length:  0 \r\n",
        b"Content-Length: 0\r\nContent-Length: 0\r\n",
    ],
    ids=[
        "no-length",
        "zero-length",
        "double-zero-length",
        "ows-padded-zero",
        "two-agreeing-zeroes",
    ],
)
def test_bodyless_framing_keeps_the_pooled_socket_alive(framing, auth_on):
    """The over-close half of the contract, on the wire.

    Framing that positively says "no body" must NOT be swept up by the blank
    rule: the rejection answers without `Connection: close` and the pipelined GET
    is served on the same socket. Two agreeing zeroes are still no body.
    """
    answered = _pipelined_after_login_rejection(
        b"GET /api/sessions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n" + framing + b"\r\n",
        stop_after=2,
    )

    text = answered.decode("latin-1", errors="replace")
    assert answered.startswith(b"HTTP/1.1 401"), text
    assert b"HTTP/1.1 200 OK" in answered, (
        f"the pipelined GET was not served, so a body-less rejection dropped a "
        f"healthy pooled connection: {text}"
    )
    assert b"Connection: close" not in answered, text


# ── Every reject-before-read site, in BOTH directions, on the wire ─────────────
#
# The sidecar path above was the first site routed through the framing-aware
# helper; the sibling sites kept the unconditional `arm_connection_close()` and
# so were the mirror image of the same bug -- a body-less rejection answered with
# `Connection: close` and dropped the client's pipelined follow-up, killing a
# healthy keep-alive connection for no framing reason. Each pair below is the
# reproduction (body-less -> must stay alive) beside its close half (a declared
# body -> must still close), pipelined down one socket against the production
# handler. Reproduced on the pre-fix head, all seven sites, `responses seen: 1`:
#
#   POST /api/session/new        (auth off-session)  -> 401 + Connection: close
#   POST /api/session/new        (bad Origin)        -> 403 + Connection: close
#   POST /api/session/new        (no CSRF token)     -> 403 + Connection: close
#   POST /api/csp-report         (limiter tripped)   -> 204 + Connection: close
#   POST /api/health/restart     (success)           -> 200 + Connection: close
#   POST /api/process-complete-ack                   -> 410 + Connection: close
#   POST /api/upload             (no boundary)       -> 400 + Connection: close

_BODYLESS_FRAMING = [b"", b"Content-Length: 0\r\n"]
_BODYLESS_IDS = ["no-content-length", "zero-content-length"]
# A PUBLIC path, so the follow-up is a genuine 200 even with auth enabled.
_PUBLIC_FOLLOWING_GET = (
    b"GET /api/auth/status HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: */*\r\n\r\n"
)


@contextlib.contextmanager
def _own_server():
    """The production Handler on a private port.

    The shared test server runs out of process with one logged-in test User,
    so the auth/CSRF-token rejections and the deterministic restart/limiter outcomes need
    an instance this process can configure. It is the same `server.Handler` class
    the real server binds, driven over a real socket.
    """
    import server

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture
def auth_on(monkeypatch, tmp_path):
    """Turn GFIT-CoWork Directory login on for this test, with one User and their Profile."""
    import api.auth as auth
    import api.profiles as profiles

    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / _USER).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()
    monkeypatch.setattr("api.config.STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    from api.directory import is_directory_enabled

    assert is_directory_enabled(), "the auth-path regression needs auth enabled"
    return auth


_USER = "600001"


def _user_session(auth) -> str:
    """A valid Directory session (the only kind GFIT-CoWork honours)."""
    return auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE, username=_USER, bound_profile=_USER, role="user",
    )


def _assert_kept_alive(answered: bytes, status: bytes) -> None:
    text = answered.decode("latin-1", errors="replace")
    assert answered.startswith(b"HTTP/1.1 " + status), text
    assert b"Connection: close" not in answered, (
        f"a body-less rejection advertised close, so a pooled client's healthy "
        f"connection was dropped: {text}"
    )
    assert b"HTTP/1.1 200 OK" in answered, (
        f"the pipelined follow-up was not served on the same socket: {text}"
    )


def _assert_closed(answered: bytes, status: bytes) -> None:
    text = answered.decode("latin-1", errors="replace")
    assert answered.startswith(b"HTTP/1.1 " + status), text
    assert b"Connection: close" in answered, text
    assert answered.count(b"HTTP/1.1 ") == 1, (
        f"the pipelined GET was answered, so the socket stayed open: {text}"
    )


@pytest.mark.parametrize("framing", _BODYLESS_FRAMING, ids=_BODYLESS_IDS)
def test_bodyless_auth_rejection_keeps_the_pooled_socket_alive(framing, auth_on):
    """A body-less POST that fails auth must not cost the client its connection."""
    with _own_server() as port:
        answered = _pipelined_after(
            b"POST /api/session/new HTTP/1.1\r\nHost: 127.0.0.1\r\n" + framing + b"\r\n",
            stop_after=2,
            port=port,
            follow=_PUBLIC_FOLLOWING_GET,
        )

    _assert_kept_alive(answered, b"401")


def test_auth_rejection_with_a_body_still_closes_the_pooled_socket(auth_on):
    """The close half at the auth gate: those bytes are still queued in rfile."""
    with _own_server() as port:
        answered = _pipelined_after(
            b"POST /api/session/new HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(_BODY)).encode() + b"\r\n\r\n" + _BODY,
            port=port,
            follow=_PUBLIC_FOLLOWING_GET,
        )

    _assert_closed(answered, b"401")
    assert _BODY not in answered, answered.decode("latin-1", errors="replace")


@pytest.mark.parametrize("framing", _BODYLESS_FRAMING, ids=_BODYLESS_IDS)
def test_bodyless_csrf_origin_rejection_keeps_the_pooled_socket_alive(framing):
    """`_check_csrf()` origin mismatch, on the shared server."""
    answered = _pipelined_after(
        b"POST /api/session/new HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Origin: http://evil.invalid\r\n" + framing + b"\r\n",
        stop_after=2,
    )

    _assert_kept_alive(answered, b"403")


def test_csrf_origin_rejection_with_a_body_still_closes_the_pooled_socket():
    answered = _pipelined_after(
        b"POST /api/session/new HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Origin: http://evil.invalid\r\nContent-Type: application/json\r\n"
        b"Content-Length: " + str(len(_BODY)).encode() + b"\r\n\r\n" + _BODY
    )

    _assert_closed(answered, b"403")
    assert _BODY not in answered, answered.decode("latin-1", errors="replace")


def _authenticated_same_origin_post(port, cookie_name, cookie, framing, body=b""):
    return (
        f"POST /api/session/new HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        f"Cookie: {cookie_name}={cookie}\r\n".encode() + framing + b"\r\n" + body
    )


@pytest.mark.parametrize("framing", _BODYLESS_FRAMING, ids=_BODYLESS_IDS)
def test_bodyless_csrf_token_rejection_keeps_the_pooled_socket_alive(framing, auth_on):
    """A real session, a same-origin POST, and no CSRF token -> token_mismatch."""
    cookie = _user_session(auth_on)
    try:
        with _own_server() as port:
            answered = _pipelined_after(
                _authenticated_same_origin_post(
                    port, auth_on._resolve_cookie_name(), cookie, framing
                ),
                stop_after=2,
                port=port,
                follow=_PUBLIC_FOLLOWING_GET,
            )
    finally:
        auth_on.invalidate_session(cookie)

    _assert_kept_alive(answered, b"403")


def test_csrf_token_rejection_with_a_body_still_closes_the_pooled_socket(auth_on):
    cookie = _user_session(auth_on)
    try:
        with _own_server() as port:
            answered = _pipelined_after(
                _authenticated_same_origin_post(
                    port,
                    auth_on._resolve_cookie_name(),
                    cookie,
                    b"Content-Type: application/json\r\nContent-Length: "
                    + str(len(_BODY)).encode()
                    + b"\r\n",
                    body=_BODY,
                ),
                port=port,
                follow=_PUBLIC_FOLLOWING_GET,
            )
    finally:
        auth_on.invalidate_session(cookie)

    _assert_closed(answered, b"403")
    assert _BODY not in answered, answered.decode("latin-1", errors="replace")


@pytest.mark.parametrize("framing", _BODYLESS_FRAMING, ids=_BODYLESS_IDS)
def test_bodyless_deprecated_ack_keeps_the_pooled_socket_alive(framing):
    """The 410 alias, which the body-bearing test at the top of this file pins closed."""
    answered = _pipelined_after(
        b"POST /api/process-complete-ack HTTP/1.1\r\nHost: 127.0.0.1\r\n" + framing + b"\r\n",
        stop_after=2,
    )

    _assert_kept_alive(answered, b"410")


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
@pytest.mark.parametrize(
    "framing",
    [
        b"Content-Type: application/json\r\n",
        b"Content-Type: application/json\r\nContent-Length: 0\r\n",
        b"Content-Type: multipart/form-data\r\n",  # multipart, but no boundary=
    ],
    ids=["json-no-length", "json-zero-length", "multipart-without-boundary"],
)
def test_bodyless_upload_boundary_rejection_keeps_the_pooled_socket_alive(path, framing):
    """`_reject_before_read()`'s one body-less reachable rejection: the boundary check."""
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n".encode() + framing + b"\r\n",
        stop_after=2,
    )

    _assert_kept_alive(answered, b"400")


@pytest.mark.parametrize("path", _MULTIPART_UPLOAD_PATHS)
def test_upload_boundary_rejection_with_a_body_still_closes(path):
    answered = _pipelined_after(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(_BODY)}\r\n\r\n".encode() + _BODY
    )

    _assert_closed(answered, b"400")
    assert _BODY not in answered, answered.decode("latin-1", errors="replace")
