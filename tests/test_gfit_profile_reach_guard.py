"""GFIT-CoWork: one place answers "which Profiles may this request read?" -- session ownership.

Each request's session ownership adapter (``api.session_ownership``) answers
which Profiles the request may read (``profile_reach``). This guard reads the
application source (``server.py`` and ``api/``) and fails when code outside the
policy modules decides that on its own:

- asks the Admission helpers whether the caller is a User
  (``caller_is_user()``, ``caller_bound_profile()``);
- reads Upstream's isolated profile mode to scope a request
  (``_is_isolated_profile_mode()`` outside ``api/profiles.py``, which owns
  that process posture);
- filters rows by comparing a row's Profile with the active Profile
  (``_profiles_match(<row>.get("profile"), <active>)`` or ``<row>["profile"]``).

The active Profile is a call to ``get_active_profile_name`` or a variable named
``active``, ``active_profile`` or ``current_profile``.

:data:`NOT_SCOPING` names the places that match a spelling but do not scope a
request, each with its reason. :data:`ALLOWLIST` names callers not yet moved to
the module; it is empty. Limits: only these spellings are recognised; the MCP
server (``mcp_server.py``) is a separate process with no request Admission and
is not read.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POLICY_MODULES = {"api/access.py", "api/session_ownership.py", "api/workspace_policy.py"}
POSTURE_MODULE = "api/profiles.py"

ASKS_USER = "asks the Admission whether the caller is a User"
READS_POSTURE = "reads isolated profile mode"
FILTERS_ROWS = "compares a row's Profile with the active Profile"

USER_QUESTIONS = frozenset({"caller_is_user", "caller_bound_profile"})
ACTIVE_CALLS = frozenset({"get_active_profile_name", "_get_active_profile_name"})
ACTIVE_NAMES = frozenset({"active", "active_profile", "current_profile"})

# (file, function, spelling): why it matches but does not scope a request.
NOT_SCOPING: dict[tuple[str, str, str], str] = {
    ("api/login.py", "session_identity", ASKS_USER): "display name: from the Profile roster for a User",
    ("api/roster.py", "_refuse_isolated_mode", READS_POSTURE):
        "Upstream posture: a one-Profile process creates and deletes no Profile",
    ("api/models.py", "profile_scoped_project_ids", FILTERS_ROWS):
        "the project ids of a given Profile (a session's), not what a caller may read",
}

# (file, function, spelling): callers not yet moved to the module (none left).
ALLOWLIST: set[tuple[str, str, str]] = set()


def _called_name(node) -> str | None:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _is_row_profile(node) -> bool:
    """``<row>.get("profile")`` or ``<row>["profile"]``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        return bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == "profile"
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "profile"
    return False


def _is_active(node) -> bool:
    if isinstance(node, ast.Call):
        return _called_name(node) in ACTIVE_CALLS
    return isinstance(node, ast.Name) and node.id in ACTIVE_NAMES


def _spellings(tree, rel: str):
    """(line, spelling) for each place in *tree* that decides Profile reach on its own."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in USER_QUESTIONS:
            yield node.lineno, ASKS_USER
        elif name == "_is_isolated_profile_mode" and rel != POSTURE_MODULE:
            yield node.lineno, READS_POSTURE
        elif name == "_profiles_match" and len(node.args) >= 2:
            a, b = node.args[0], node.args[1]
            if (_is_row_profile(a) and _is_active(b)) or (_is_row_profile(b) and _is_active(a)):
                yield node.lineno, FILTERS_ROWS


def _outermost_function_at(tree, line: int) -> str:
    containing = [
        (node.lineno, node.name) for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno
    ]
    return min(containing)[1] if containing else "<module>"


def deciders(root: Path = REPO) -> list[tuple[str, int, str, str]]:
    """(file, line, function, spelling) for every decider outside the policy modules under *root*."""
    found = []
    for path in [root / "server.py", *sorted((root / "api").rglob("*.py"))]:
        rel = path.relative_to(root).as_posix()
        if rel in POLICY_MODULES or not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for line, spelling in _spellings(tree, rel):
            found.append((rel, line, _outermost_function_at(tree, line), spelling))
    return sorted(found)


def test_only_session_ownership_decides_which_profiles_a_request_may_read():
    offenders = [
        f"{rel}:{line} in {func}() {spelling}"
        for rel, line, func, spelling in deciders()
        if (rel, func, spelling) not in NOT_SCOPING and (rel, func, spelling) not in ALLOWLIST
    ]
    assert not offenders, (
        "Ask session ownership which Profiles this request may read "
        "(api.session_ownership.request_session_ownership().profile_reach(...)):\n  "
        + "\n  ".join(offenders)
    )


def test_every_listed_place_still_matches():
    """A stale entry fails, so the lists only shrink."""
    found = {(rel, func, spelling) for rel, _line, func, spelling in deciders()}
    stale = sorted(f"{rel} {func}() {spelling}" for rel, func, spelling in {*NOT_SCOPING, *ALLOWLIST} - found)
    assert not stale, "Remove these entries; they no longer match:\n  " + "\n  ".join(stale)


def test_the_lists_do_not_overlap():
    assert not set(NOT_SCOPING) & ALLOWLIST


def _caught(source: str, rel: str = "api/routes.py") -> list[str]:
    return [spelling for _line, spelling in _spellings(ast.parse(source), rel)]


def test_each_spelling_is_caught():
    source = (
        "def f(rows, p):\n"
        "    a = caller_is_user()\n"
        "    b = access.caller_bound_profile()\n"
        "    c = _is_isolated_profile_mode()\n"
        "    d = profiles._is_isolated_profile_mode()\n"
        "    e = [r for r in rows if _profiles_match(r.get('profile'), active_profile)]\n"
        "    g = _profiles_match(p['profile'], get_active_profile_name())\n"
        "    h = _profiles_match(active, p.get('profile'))\n"
    )
    assert _caught(source) == [ASKS_USER, ASKS_USER, READS_POSTURE, READS_POSTURE,
                               FILTERS_ROWS, FILTERS_ROWS, FILTERS_ROWS]


def test_the_near_misses_are_not_caught():
    source = (
        "def f(meta, name, requested_profile):\n"
        "    a = _profiles_match(meta.get('profile'), requested_profile)\n"
        "    b = _profiles_match(name, active_profile)\n"
        "    c = reach.includes(meta.get('profile'))\n"
    )
    assert _caught(source) == []
    assert _caught("x = _is_isolated_profile_mode()\n", rel=POSTURE_MODULE) == []
