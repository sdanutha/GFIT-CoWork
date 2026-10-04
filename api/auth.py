"""
GFIT-CoWork -- authentication: the session store, the session cookie, CSRF,
the signed Profile cookie and the per-request gate.
Login is the Directory (api.login, ADR 0004); off when no Directory is configured.
"""
import hashlib
import hmac
import http.cookies
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from api import directory
from api.config import STATE_DIR, load_settings
from api.helpers import request_declares_body

logger = logging.getLogger(__name__)


# Default session TTL — 30 days. Kept as a module-level constant for backwards
# compatibility with downstream code and regression tests that import it.
# At runtime, prefer ``_resolve_session_ttl()`` which honours the env var and
# settings.json overrides; this constant is the floor / fallback.
SESSION_TTL = 86400 * 30  # 30 days


def _resolve_session_ttl() -> int:
    """Resolve session TTL from env > settings > default.

    HERMES_WEBUI_SESSION_TTL env var first, then settings.json, falling back to ``SESSION_TTL`` (30 days).
    Clamped to [60s, 1 year] to prevent runaway cookies or self-lockout.
    """
    env_v = os.getenv('HERMES_WEBUI_SESSION_TTL', '').strip()
    if env_v.isdigit():
        val = int(env_v)
        if 60 <= val <= 86400 * 365:
            return val
    s = load_settings()
    v = s.get('session_ttl_seconds')
    if isinstance(v, int) and 60 <= v <= 86400 * 365:
        return v
    return SESSION_TTL


# ── Public paths (no auth required) ─────────────────────────────────────────
PUBLIC_PATHS = frozenset({
    '/login', '/health', '/favicon.ico', '/sw.js',
    '/api/auth/login', '/api/auth/status',
    '/share',
    '/manifest.json', '/manifest.webmanifest',
    '/session/manifest.json', '/session/manifest.webmanifest',
})

COOKIE_NAME = 'hermes_session'
CSRF_HEADER_NAME = 'X-Hermes-CSRF-Token'


# RFC 6265 cookie-name token: a non-empty run of token chars
# (no controls, whitespace, or separators such as ';', '=', ',').
_COOKIE_NAME_RE = re.compile(r"^[-!#$%&'*+.^_`|~0-9A-Za-z]+$")


def _resolve_cookie_name() -> str:
    """Resolve the auth session cookie name from env > default.

    Honours ``HERMES_WEBUI_COOKIE_NAME`` so multiple WebUI instances sharing a
    hostname (different ports) can use distinct cookie names instead of
    trampling each other's session — browsers scope cookies by host, not
    host+port (RFC 6265). Falls back to ``COOKIE_NAME`` when the env var is
    unset, empty, or not a valid RFC 6265 token.
    """
    name = os.getenv('HERMES_WEBUI_COOKIE_NAME', '').strip()
    if not name:
        return COOKIE_NAME
    if _COOKIE_NAME_RE.match(name):
        return name
    logger.warning(
        'Ignoring invalid HERMES_WEBUI_COOKIE_NAME=%r; falling back to %r '
        '(name must be a valid RFC 6265 token)', name, COOKIE_NAME,
    )
    return COOKIE_NAME


def _warn_auth_persistence_failure(prefix: str, artifact: Path, exc: Exception, consequence: str) -> None:
    logger.warning(
        '%s at %s (STATE_DIR=%s): %s: %s; %s',
        prefix,
        artifact,
        STATE_DIR,
        exc.__class__.__name__,
        exc,
        consequence,
    )


_SESSIONS_FILE = STATE_DIR / '.sessions.json'
def _session_expiry(record) -> float | None:
    if isinstance(record, dict):
        expiry = record.get('expiry', record.get('expires_at'))
    else:
        expiry = record
    try:
        expiry_f = float(expiry)
    except (TypeError, ValueError):
        return None
    return expiry_f


def _load_sessions() -> dict[str, float | dict]:
    """Load persisted sessions from STATE_DIR, pruning expired entries.

    Returns an empty dict on any read or parse error so startup is never
    blocked by a corrupt or missing sessions file.
    """
    try:
        if not _SESSIONS_FILE.exists():
            return {}
        raw = _SESSIONS_FILE.read_text(encoding='utf-8')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('malformed sessions file: expected dict')
    except OSError as e:
        _warn_auth_persistence_failure(
            'Auth session store read failed',
            _SESSIONS_FILE,
            e,
            'starting fresh with an empty session table',
        )
        return {}
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _warn_auth_persistence_failure(
            'Ignoring malformed auth session store',
            _SESSIONS_FILE,
            e,
            'starting fresh with an empty session table',
        )
        return {}
    except Exception as e:
        _warn_auth_persistence_failure(
            'Ignoring malformed auth session store',
            _SESSIONS_FILE,
            e,
            'starting fresh with an empty session table',
        )
        return {}
    now = time.time()
    sessions: dict[str, float | dict] = {}
    for token, record in data.items():
        if not isinstance(token, str) or not token:
            continue
        expiry = _session_expiry(record)
        if expiry is None or expiry <= now:
            continue
        if isinstance(record, dict):
            normalized = dict(record)
            normalized['expiry'] = expiry
            sessions[token] = normalized
        else:
            sessions[token] = expiry
    return sessions


def _save_sessions(sessions: dict[str, float | dict]) -> None:
    """Atomically persist sessions to STATE_DIR/.sessions.json (0600).

    Uses a temp file + os.replace() so a crash mid-write never leaves a
    truncated file.  Mirrors the same pattern as .signing_key persistence.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix='.sessions.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(sessions, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _SESSIONS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        _warn_auth_persistence_failure(
            'Auth session persistence failed',
            _SESSIONS_FILE,
            e,
            'keeping the in-process session table available',
        )


# Active sessions: token -> expiry timestamp (persisted across restarts via STATE_DIR)
_sessions = _load_sessions()
_SESSIONS_LOCK = threading.Lock()

def _load_key(filename: str) -> bytes:
    """Load a 32-byte key from STATE_DIR, generating and persisting one if missing."""
    key_file = STATE_DIR / filename
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except OSError as e:
        _warn_auth_persistence_failure(
            'Auth key read failed',
            key_file,
            e,
            'generating a new key and continuing',
        )
    except Exception as e:
        _warn_auth_persistence_failure(
            'Auth key read failed',
            key_file,
            e,
            'generating a new key and continuing',
        )
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except OSError as e:
        _warn_auth_persistence_failure(
            'Auth key persistence failed',
            key_file,
            e,
            'returning the generated key so startup can continue',
        )
    except Exception as e:
        _warn_auth_persistence_failure(
            'Auth key persistence failed',
            key_file,
            e,
            'returning the generated key so startup can continue',
        )
    return key


_SIGNING_KEY_CACHE: bytes | None = None


def _signing_key() -> bytes:
    global _SIGNING_KEY_CACHE
    if _SIGNING_KEY_CACHE is None:
        _SIGNING_KEY_CACHE = _load_key('.signing_key')
    return _SIGNING_KEY_CACHE


# Session ``auth_type`` for a GFIT-CoWork Directory login.
DIRECTORY_AUTH_TYPE = 'directory'


def create_session(
    *,
    auth_type: str | None = None,
    username: str | None = None,
    bound_profile: str | None = None,
    role: str | None = None,
    display_name: str | None = None,
) -> str:
    """Create a new auth session. Returns signed cookie value."""
    token = secrets.token_hex(32)
    expiry = time.time() + _resolve_session_ttl()
    record: float | dict
    if any(value is not None for value in (auth_type, username, bound_profile, role)):
        record = {
            'expiry': expiry,
            'auth_type': auth_type,
            'username': username,
            'bound_profile': bound_profile,
        }
        if role is not None:
            record['role'] = role
        if display_name is not None:
            record['display_name'] = display_name
    else:
        record = expiry
    with _SESSIONS_LOCK:
        _sessions[token] = record
        _save_sessions(_sessions)
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{sig}"


def _prune_expired_sessions():
    """Remove all expired session entries to prevent unbounded memory growth."""
    now = time.time()
    with _SESSIONS_LOCK:
        expired = [t for t, record in _sessions.items() if (expiry := _session_expiry(record)) is None or now > expiry]
        if expired:
            for token in expired:
                _sessions.pop(token, None)
            _save_sessions(_sessions)


def verify_session(cookie_value: str) -> bool:
    """Verify a signed session cookie. Returns True if valid and not expired."""
    if not cookie_value or '.' not in cookie_value:
        return False
    _prune_expired_sessions()  # lazy cleanup on every verification attempt
    token, sig = cookie_value.rsplit('.', 1)
    full_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    # Accept both new (64-char) and legacy (32-char truncated) signatures so
    # existing sessions survive the upgrade without a forced global logout.
    # The legacy branch can be removed once session TTLs have expired (~30 days).
    valid = hmac.compare_digest(sig, full_sig) or (
        len(sig) == 32 and hmac.compare_digest(sig, full_sig[:32])
    )
    if not valid:
        return False
    with _SESSIONS_LOCK:
        expiry = _session_expiry(_sessions.get(token))
        if expiry is None or time.time() > expiry:
            _sessions.pop(token, None)
            _save_sessions(_sessions)
            return False
    return True


def _queue_pending_cookie(handler, cookie_header: str) -> None:
    if not cookie_header:
        return
    pending = getattr(handler, '_pending_set_cookies', None)
    if pending is None:
        pending = []
        handler._pending_set_cookies = pending
    pending.append(cookie_header)


def _auth_cookie_header(cookie_value, handler=None) -> str:
    cookie = http.cookies.SimpleCookie()
    name = _resolve_cookie_name()
    cookie[name] = cookie_value
    cookie[name]['httponly'] = True
    cookie[name]['samesite'] = 'Lax'
    cookie[name]['path'] = '/'
    cookie[name]['max-age'] = str(_resolve_session_ttl())
    if _is_secure_context(handler):
        cookie[name]['secure'] = True
    return cookie[name].OutputString()


def _clear_auth_cookie_header() -> str:
    cookie = http.cookies.SimpleCookie()
    name = _resolve_cookie_name()
    cookie[name] = ''
    cookie[name]['httponly'] = True
    cookie[name]['path'] = '/'
    cookie[name]['samesite'] = 'Lax'
    cookie[name]['max-age'] = '0'
    return cookie[name].OutputString()


def _build_profile_cookie_header(name: str, session_cookie_value: str | None) -> str:
    from api.helpers import build_profile_cookie

    return build_profile_cookie(name, session_cookie_value=session_cookie_value)


def get_session_info(cookie_value: str) -> dict | None:
    if not verify_session(cookie_value):
        return None
    token = _session_token_from_cookie_value(cookie_value)
    if not token:
        return None
    with _SESSIONS_LOCK:
        record = _sessions.get(token)
    expiry = _session_expiry(record)
    if expiry is None:
        return None
    info: dict[str, object] = {'token': token, 'expiry': expiry}
    if isinstance(record, dict):
        info.update({k: v for k, v in record.items() if k != 'expiry'})
    if 'bound_profile' not in info and isinstance(info.get('profile'), str):
        info['bound_profile'] = info.get('profile')
    info.setdefault('auth_type', None)
    info.setdefault('username', None)
    info.setdefault('bound_profile', None)
    return info


def session_bound_profile(cookie_value: str) -> str | None:
    info = get_session_info(cookie_value)
    if not info:
        return None
    bound_profile = info.get('bound_profile')
    bound_profile = str(bound_profile or '').strip()
    return bound_profile or None


def _remember_request_session(handler, info: dict | None) -> dict | None:
    handler._request_session = info
    return info


def reset_request_auth_state(handler) -> None:
    for name in (
        '_request_session',
        '_request_session_rejected',
        # Clear any auth cookie queued by a prior request but not yet flushed.
        # The handler is reused across HTTP/1.1 keep-alive requests, so a stale
        # queued Set-Cookie would otherwise cross the request boundary and be
        # emitted by a later response. Reset it at the per-request boundary
        # (server.py do_GET/do_POST).
        '_pending_set_cookies',
    ):
        try:
            delattr(handler, name)
        except AttributeError:
            pass


def _sync_profile_cookie(handler, bound_profile: str | None, cookie_value: str) -> None:
    """Keep the browser's profile cookie on the Admission's Profile (the request's
    Profile itself is set by :func:`api.access.settle_request_profile`)."""
    if bound_profile is None:
        return
    from api.helpers import get_profile_cookie

    if get_profile_cookie(handler) != bound_profile:
        _queue_pending_cookie(handler, _build_profile_cookie_header(bound_profile, cookie_value))


def ensure_request_session(handler) -> dict | None:
    """This request's session, decided once per request: a Directory session still admitted, else None.

    Only a Directory session is honoured (ADR 0004); any other session is ended.
    """
    if hasattr(handler, '_request_session'):
        return handler._request_session
    cookie_value = parse_cookie(handler)
    info = get_session_info(cookie_value) if cookie_value and verify_session(cookie_value) else None
    if info and info.get('auth_type') == DIRECTORY_AUTH_TYPE:
        return _reconcile_directory_session(handler, info, cookie_value)
    if info:
        invalidate_session(cookie_value)
        handler._request_session_rejected = True
    return _remember_request_session(handler, None)


def _reconcile_directory_session(handler, info: dict, cookie_value: str) -> dict | None:
    """Run a Directory session's request in its bound Profile, whatever the client sent.

    A User's request runs in, and is bound to, the Profile its Admission names
    (:func:`api.access.caller_bound_profile`), so no Profile the client names
    (cookie, query or body) can reach another Profile's data. An Admin's request
    runs in ``default``.

    Fails closed: the session is ended when Directory login is no longer
    configured, or when Admission (:func:`api.access.admit`) for the session's
    employee ID no longer gives the session's role and Profile -- the Profile
    was deleted or disabled, the Admin list changed, or the role is unknown.
    Otherwise the confirmed Admission is recorded as the request's Admission.
    """
    from api.access import admit_request

    admission = admit_request(info) if directory.is_directory_enabled() else None
    if admission is None:
        invalidate_session(cookie_value)
        handler._request_session_rejected = True
        return _remember_request_session(handler, None)
    _sync_profile_cookie(handler, admission.profile, cookie_value)
    return _remember_request_session(handler, info)


def _refuse_admin_only_for_user(handler, parsed, session_info: dict) -> bool:
    """The Admin-only gate: True (after sending 403) when a User calls a non-User endpoint.

    The role is the request's Admission. A Directory session with none was not
    admitted for this request, so it may call nothing.
    """
    if session_info.get('auth_type') != DIRECTORY_AUTH_TYPE:
        return False
    from api.access import ADMIN_ONLY_MESSAGE, ROLE_ADMIN, request_admission, user_may_call

    admission = request_admission()
    if admission is not None and admission.role == ROLE_ADMIN:
        return False
    if admission is not None and user_may_call(getattr(handler, 'command', 'GET'), parsed.path):
        return False
    _send_forbidden(handler, parsed, ADMIN_ONLY_MESSAGE)
    return True


def _send_forbidden(handler, parsed, message: str) -> None:
    if parsed.path.startswith('/api/'):
        body = json.dumps({'error': message}).encode()
        content_type = 'application/json'
    else:
        body = message.encode()
        content_type = 'text/plain; charset=utf-8'
    handler.send_response(403)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _session_token_from_cookie_value(cookie_value: str) -> str | None:
    """Return the raw server-side session token from a signed cookie value."""
    if not cookie_value or '.' not in cookie_value:
        return None
    token, _sig = cookie_value.rsplit('.', 1)
    return token or None


def sign_profile_cookie_value(profile_name: str, session_cookie_value: str | None) -> str:
    """Return a profile cookie value authenticated for one WebUI session.

    The active-profile cookie is client-controlled, so when auth is enabled it
    must not be trusted as a bare profile name. Binding the selected profile to
    the HttpOnly session token prevents a client from forging
    ``hermes_profile=<other-profile>`` and bypassing profile visibility guards.
    """
    if not session_cookie_value or not verify_session(session_cookie_value):
        raise ValueError("active auth session is required to sign profile cookie")
    token = _session_token_from_cookie_value(session_cookie_value)
    if not token:
        raise ValueError("active auth session is required to sign profile cookie")
    sig = hmac.new(
        _signing_key(),
        f"profile:{token}:{profile_name}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{profile_name}.{sig}"


def verify_profile_cookie_value(cookie_value: str, session_cookie_value: str | None) -> str | None:
    """Verify a session-bound profile cookie and return its profile name."""
    if not cookie_value or '.' not in cookie_value:
        return None
    if not session_cookie_value or not verify_session(session_cookie_value):
        return None
    profile_name, sig = cookie_value.rsplit('.', 1)
    token = _session_token_from_cookie_value(session_cookie_value)
    if not profile_name or not token or not sig:
        return None
    # Defense-in-depth: validate the profile-name pattern here too, not only in
    # get_profile_cookie(), so any future caller of this verifier can't return an
    # unvalidated name. (#4023 Opus hardening.)
    from api.profiles import _PROFILE_ID_RE
    if profile_name != 'default' and not _PROFILE_ID_RE.fullmatch(profile_name):
        return None
    expected = hmac.new(
        _signing_key(),
        f"profile:{token}:{profile_name}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if hmac.compare_digest(str(sig), expected):
        return profile_name
    return None


def csrf_token_for_session(cookie_value: str) -> str | None:
    """Return the CSRF token bound to an authenticated WebUI session.

    The browser can read this token from the authenticated shell and echoes it
    in ``X-Hermes-CSRF-Token`` on unsafe API requests. The token is derived
    from the HttpOnly session cookie's server-side token, so it automatically
    rotates on login and is invalidated when the auth session expires or logs
    out. Callers must still verify the auth session before trusting it.
    """
    token = _session_token_from_cookie_value(cookie_value)
    if not token:
        return None
    return hmac.new(_signing_key(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()


def verify_csrf_token(cookie_value: str, csrf_token: str) -> bool:
    """Verify a submitted CSRF token against the authenticated session."""
    if not cookie_value or not csrf_token or not verify_session(cookie_value):
        return False
    expected = csrf_token_for_session(cookie_value)
    return bool(expected and hmac.compare_digest(str(csrf_token), expected))


def invalidate_session(cookie_value) -> None:
    """Remove a session token."""
    if cookie_value and '.' in cookie_value:
        token = cookie_value.rsplit('.', 1)[0]
        with _SESSIONS_LOCK:
            if token in _sessions:
                _sessions.pop(token, None)
                _save_sessions(_sessions)


def invalidate_sessions_for_profile(profile: str) -> None:
    """End every session bound to *profile* (it was disabled or deleted)."""
    if not profile:
        return
    with _SESSIONS_LOCK:
        doomed = [
            token for token, record in _sessions.items()
            if isinstance(record, dict) and record.get('bound_profile') == profile
        ]
        for token in doomed:
            _sessions.pop(token, None)
        if doomed:
            _save_sessions(_sessions)


def parse_cookie(handler) -> str | None:
    """Extract the auth cookie from the request headers."""
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get(_resolve_cookie_name())
    return morsel.value if morsel else None


def _safe_login_inner_next(query: str | None) -> str:
    """#5578: extract a SAFE, non-login inner redirect from a login page's query.

    When an expired-auth bounce lands back on the login page (which already
    carries its own `next` in the query), we want to preserve a legitimate inner
    destination X across the redirect to the real login route — but only if X is
    itself safe (path-absolute, not protocol-relative/backslash, no control
    chars) AND not login-shaped / not itself carrying a nested next param.
    Anything else collapses to '' (no inner redirect), which kills the
    self-referential chain. Mirrors login.js `_safeNextPath()`.
    """
    import urllib.parse as _u
    raw = _u.parse_qs(query or "").get("next", [""])[0]
    path = str(raw or "").strip()
    if not path or path[0] != "/" or path[1:2] in {"/", "\\"}:
        return ""
    if re.search(r"[\x00-\x1f\x7f\s]", path) or len(path) > 2048:
        return ""
    # Collapse only login-route chains — decode a few levels so a nested
    # `/session/login%3Fnext%3D...` (encoded `?`) is still recognized by its
    # leading PATH — but preserve a legitimate non-login inner path that merely
    # carries its own `next=` query key (e.g. `/admin?next=/real/path`).
    _probe = path
    for _ in range(8):
        _p = _probe.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].rstrip("/")
        if _p == "/login" or _p.endswith("/login"):
            return ""
        _decoded = _u.unquote(_probe)
        if _decoded == _probe:
            break
        _probe = _decoded
    else:
        # Still decoding at the cap (pathologically deep encoding) → fail closed.
        _p = _probe.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].rstrip("/")
        if _p == "/login" or _p.endswith("/login"):
            return ""
        return ""
    return path


def check_auth(handler, parsed) -> bool:
    """Check if request is authorized. Returns True if OK.
    If not authorized, sends 401 (API) or 302 redirect (page) and returns False."""
    if not directory.is_directory_enabled():
        return True
    # Public paths don't require auth
    if (
        parsed.path in PUBLIC_PATHS
        or parsed.path.startswith('/share/')
        or (
            parsed.path.startswith('/api/share/')
            and parsed.path not in {'/api/share/create', '/api/share/revoke'}
        )
        or parsed.path.startswith('/static/')
        or parsed.path.startswith('/session/static/')
    ):
        return True
    cookie_val = parse_cookie(handler)
    has_session = bool(cookie_val and verify_session(cookie_val))
    if parsed.path == '/api/auth/logout':
        if has_session:
            return True
        body = b'{"error":"Authentication required"}'
        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return False
    session_info = ensure_request_session(handler)
    if session_info:
        if _refuse_admin_only_for_user(handler, parsed, session_info):
            return False
        return True
    # Not authorized
    if parsed.path.startswith('/api/'):
        body = b'{"error":"Authentication required"}'
        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    else:
        handler.send_response(302)
        # Pass the original path as ?next= so login.js redirects back after auth.
        # SECURITY/CORRECTNESS: the inner `?` and `&` MUST be percent-encoded
        # when stuffed into the outer `?next=` parameter, otherwise:
        #   (a) multi-param query strings get truncated at the first inner `&`
        #       (e.g. `/api/sessions?limit=50&offset=0` would round-trip as
        #       just `/api/sessions?limit=50` after the browser parses the
        #       outer URL — `offset=0` becomes a separate top-level query
        #       parameter that the login page ignores).
        #   (b) attacker-controlled paths could inject a second `next=`
        #       parameter; per RFC 3986 the duplicate behaviour is undefined
        #       and parsers diverge (Python's parse_qs returns last-match,
        #       URLSearchParams returns first-match), opening a query-pollution
        #       footgun even though _safeNextPath() rejects most malicious
        #       shapes downstream.
        # Encoding the entire `path?query` blob with quote(safe='/') turns
        # `?` → `%3F` and `&` → `%26`, so the outer parameter holds exactly
        # one path-with-query string and `searchParams.get('next')` returns
        # the full original URL (the browser auto-decodes once).
        # (Opus pre-release advisor finding for v0.50.258.)
        import urllib.parse as _urlparse
        # #5578: if the page being redirected is ALREADY login-shaped, do NOT
        # wrap its full `path?query` into a fresh `next=` — that query already
        # carries a `next=`, so quoting the whole thing nests the login URL into
        # itself and re-encodes it on every expired-auth bounce, exploding the
        # URL until the tab breaks. This guard runs in check_auth() (BEFORE
        # route handling), the actual source of the server-side loop.
        #
        # The login page is served ONLY at the public `/login` route (see
        # PUBLIC_PATHS + the routes.py `/login` handler); the app's client route
        # `/session/login` is NOT public, so a bare relative `login` from
        # `/session/login` resolves to `/session/login` again and re-triggers
        # check_auth() — an infinite redirect. Resolve to the real login route
        # with `../login`, which lands on `/login` from a `/session/*` scope and
        # on `<mount>/login` under a subpath mount (verified via urljoin). Carry
        # through only a validated, non-login inner `next` so a legitimate
        # post-login destination still survives a bounce that happened to land
        # on the login page.
        _login_path = (parsed.path or '/').rstrip('/')
        if _login_path == '/login' or _login_path.endswith('/login'):
            # /login itself is public → check_auth never redirects it; this only
            # fires for the non-public client login route (e.g. /session/login).
            _target = '../login' if '/' in _login_path.lstrip('/') else 'login'
            _inner = _safe_login_inner_next(parsed.query)
            if _inner:
                _target += '?next=' + _urlparse.quote(_inner, safe='/')
            handler.send_header('Location', _target)
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return False
        _path_with_query = parsed.path or '/'
        if parsed.query:
            _path_with_query += '?' + parsed.query
        # safe='/' keeps path separators readable; everything else (including
        # `?`, `&`, `=`) gets percent-encoded.
        _next = _urlparse.quote(_path_with_query, safe='/')
        handler.send_header('Location', 'login?next=' + _next)
        handler.send_header('Content-Length', '0')
        handler.end_headers()
    return False


def check_auth_or_close(handler, parsed) -> bool:
    """Check auth; when rejected, close so an unread body can't poison HTTP/1.1 reuse.

    The flag is armed BEFORE check_auth() writes its 401/302, so end_headers()
    can advertise ``Connection: close``; success restores the prior flag so an
    authenticated request keeps its keep-alive.

    Armed ONLY when the request's framing declares a body still queued in
    ``rfile`` (see ``request_declares_body()``), which is the same rule the other
    reject-before-read sites apply through ``arm_connection_close_if_body_pending()``.
    Arming unconditionally was the mirror image of the over-close this PR already
    fixed on the sidecar path: a body-less POST that failed auth answered
    ``401`` WITH ``Connection: close`` and dropped the client's pipelined
    follow-up, killing a healthy keep-alive connection for no framing reason.
    Verified on the wire, pipelined down one socket against the production
    handler: ``POST /api/session/new`` with no ``Content-Length`` (and with
    ``Content-Length: 0``) answered ``401`` + ``Connection: close`` and the
    following ``GET /api/auth/status`` was never served. The helper cannot be
    swapped in directly here because the arming has to happen before
    ``check_auth()`` writes its response and be undone if it succeeds.

    ``close_connection`` only exists once BaseHTTPRequestHandler has parsed a
    request line, so partially-built handler stubs may not have it at all. Read
    it defensively and only arm/restore when it was really there: inventing the
    attribute on a stub would leave a bogus keep-alive verdict behind.
    """
    if not hasattr(handler, 'close_connection') or not request_declares_body(handler):
        return check_auth(handler, parsed)
    prior_close = handler.close_connection
    handler.close_connection = True
    if check_auth(handler, parsed):
        handler.close_connection = prior_close
        return True
    return False


def _is_loopback(addr: str) -> bool:
    """Return True if *addr* is a loopback address (127.x.x.x, ::1, or ::ffff:127.x.x.x)."""
    import ipaddress as _ipaddress
    try:
        ip = _ipaddress.ip_address(addr)
        if ip.is_loopback:
            return True
        # Python < 3.12: is_loopback is False for ::ffff:127.x.x.x (gh-117566)
        if hasattr(ip, 'ipv4_mapped') and ip.ipv4_mapped is not None:
            return ip.ipv4_mapped.is_loopback
        return False
    except ValueError:
        return False


def _is_secure_context(handler=None) -> bool:
    """Return True if cookies should carry the Secure flag.

    Priority order:
    1. ``HERMES_WEBUI_SECURE`` env var: 1/true/yes -> True; 0/false/no -> False.
    2. Direct TLS socket (handler.request.getpeercert present) -> True.
    3. ``HERMES_WEBUI_TRUST_FORWARDED_PROTO=1`` opt-in: trust
       ``X-Forwarded-Proto: https`` header from a known reverse proxy.
    4. Otherwise -> False (loopback or non-loopback, plain HTTP is not secure).

    .. warning::
       ``X-Forwarded-Proto`` is only trustworthy behind a reverse proxy.
       It is ignored unless ``HERMES_WEBUI_TRUST_FORWARDED_PROTO=1`` is
       set explicitly, preventing header-injection attacks on plain-HTTP
       deployments.
    """
    env = os.getenv('HERMES_WEBUI_SECURE', '').strip().lower()
    if env in ('1', 'true', 'yes'):
        return True
    if env in ('0', 'false', 'no'):
        return False
    if handler is not None:
        if getattr(handler.request, 'getpeercert', None) is not None:
            return True
        trust_fwd = os.getenv('HERMES_WEBUI_TRUST_FORWARDED_PROTO', '').strip().lower()
        if trust_fwd in ('1', 'true', 'yes'):
            if handler.headers.get('X-Forwarded-Proto', '') == 'https':
                return True
    return False


def set_auth_cookie(handler, cookie_value) -> None:
    """Set the auth cookie on the response."""
    handler.send_header('Set-Cookie', _auth_cookie_header(cookie_value, handler))


def clear_auth_cookie(handler) -> None:
    """Clear the auth cookie on the response."""
    handler.send_header('Set-Cookie', _clear_auth_cookie_header())
