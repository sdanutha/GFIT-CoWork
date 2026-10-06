"""
Shared test server constants for use in individual test files.

Instead of hardcoding ``BASE = "http://127.0.0.1:8788"`` in every test file,
import from here so the port and state dir are always consistent with
what conftest.py computed for this worktree.

Usage::

    from tests._pytest_port import BASE

conftest.py publishes ``HERMES_WEBUI_TEST_PORT`` and
``HERMES_WEBUI_TEST_STATE_DIR`` to ``os.environ`` at module level
(before any test file is imported), so this module always reads the
correct values.  The auto-derivation fallback matches conftest's logic
exactly, so standalone imports also work correctly.
"""
import hashlib
import os
import pathlib

def _auto_test_port(repo_root: pathlib.Path) -> int:
    h = int(hashlib.md5(str(repo_root).encode()).hexdigest(), 16)
    return 20000 + (h % 10000)

def _auto_state_dir_name(repo_root: pathlib.Path) -> str:
    h = hashlib.md5(str(repo_root).encode()).hexdigest()[:8]
    return f"webui-test-{h}"

_TESTS_DIR   = pathlib.Path(__file__).parent.resolve()
_REPO_ROOT   = _TESTS_DIR.parent.resolve()

TEST_PORT = int(os.environ.get('HERMES_WEBUI_TEST_PORT',
                               str(_auto_test_port(_REPO_ROOT))))
BASE = f"http://127.0.0.1:{TEST_PORT}"

# Test state dir: prefer the value conftest.py published to the environment.
# The standalone fallback anchors under the OS temp dir (NOT ~/.hermes) so test
# state is never created inside the production Hermes home — matching conftest's
# hard-isolation default. (See conftest.py TEST_STATE_DIR.)
import tempfile as _tempfile
_TEST_STATE_ROOT = pathlib.Path(
    os.environ.get('HERMES_WEBUI_TEST_STATE_ROOT', _tempfile.gettempdir())
) / 'hermes-webui-tests'
TEST_STATE_DIR = pathlib.Path(os.environ.get(
    'HERMES_WEBUI_TEST_STATE_DIR',
    str(_TEST_STATE_ROOT / _auto_state_dir_name(_REPO_ROOT))
)).resolve()

# Defense-in-depth: mirror conftest.py's production-proximity guard so a
# standalone import (without conftest) can't resolve a state dir inside the real
# ~/.hermes either. Same logic: trip only if under literal ~/.hermes and the temp
# root isn't itself under production.
_PROD_HERMES_HOME = (pathlib.Path.home() / '.hermes').resolve()
_TEMP_ROOT = pathlib.Path(_tempfile.gettempdir()).resolve()
_temp_root_is_safe = not (
    _TEMP_ROOT == _PROD_HERMES_HOME or _PROD_HERMES_HOME in _TEMP_ROOT.parents
)
_under_temp = _temp_root_is_safe and (
    TEST_STATE_DIR == _TEMP_ROOT or _TEMP_ROOT in TEST_STATE_DIR.parents
)
_under_prod = TEST_STATE_DIR == _PROD_HERMES_HOME or _PROD_HERMES_HOME in TEST_STATE_DIR.parents
if _under_prod and not _under_temp:
    raise RuntimeError(
        f"REFUSING TO RUN: test state dir {TEST_STATE_DIR} is inside the production "
        f"Hermes home {_PROD_HERMES_HOME}. Tests must never touch production files."
    )

# Default model injected by conftest — tests that mutate the default model
# must restore to this value so later tests see a consistent baseline.
TEST_DEFAULT_MODEL = os.environ.get('HERMES_WEBUI_DEFAULT_MODEL', 'openai/gpt-5.4-mini')


# The shared test server's test User and their Profile (conftest.py): every
# request the test process sends to BASE goes as this User, bound to this
# Profile, so state a test seeds for the server's requests belongs here.
TEST_USER = os.environ.get('HERMES_WEBUI_TEST_USER', '100001')
TEST_PROFILE_HOME = pathlib.Path(os.environ.get(
    'HERMES_WEBUI_TEST_PROFILE_HOME', str(TEST_STATE_DIR / 'profiles' / TEST_USER)
))
# The test User's own web settings (settings belong to each Profile, ADR 0006).
TEST_USER_SETTINGS_FILE = TEST_PROFILE_HOME / 'webui_state' / 'settings.json'
# The test User's Workspace folder: every session of theirs works inside it.
TEST_USER_WORKSPACE = TEST_PROFILE_HOME / 'workspace'


def directory_env(state_dir) -> dict:
    """Environment that gives a server subprocess a login (the in-memory Directory).

    There is no mode with login turned off (ADR 0006): a server with no
    Directory refuses to start. A test that starts its own server.py and only
    needs public paths (/health, /login) passes this, with no users.
    """
    import json

    users = pathlib.Path(state_dir) / 'directory-users.json'
    users.parent.mkdir(parents=True, exist_ok=True)
    if not users.exists():
        users.write_text(json.dumps({}), encoding='utf-8')
    return {'HERMES_WEBUI_DIRECTORY': 'memory', 'HERMES_WEBUI_DIRECTORY_USERS': str(users)}


import contextlib as _contextlib


@_contextlib.contextmanager
def deployment_settings(**overrides):
    """Set Deployment settings (TEST_STATE_DIR/settings.json) for the block, as the Operator would.

    Settings that belong to the whole Deployment (the assistant's name, ...)
    are not a User's to change over HTTP (ADR 0006), and the login page, served
    before login, reads only these.
    """
    import json

    path = TEST_STATE_DIR / 'settings.json'
    original = path.read_text(encoding='utf-8') if path.exists() else None
    current = json.loads(original) if original else {}
    path.write_text(json.dumps({**current, **overrides}), encoding='utf-8')
    try:
        yield path
    finally:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original, encoding='utf-8')


def new_session_id() -> str:
    """A new session of the test User's on the shared server.

    A session id the User does not own, made-up ids included, is "not found"
    before any route runs (ADR 0002), so a test of a route's own answers names
    a session the test User owns.
    """
    import json
    import urllib.request

    req = urllib.request.Request(
        BASE + '/api/session/new', data=b'{}', headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())['session']['session_id']
