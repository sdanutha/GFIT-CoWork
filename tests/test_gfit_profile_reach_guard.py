"""GFIT-CoWork: one place answers "which Profiles may this request read?" -- session ownership.

Each request's session ownership adapter (``api.session_ownership``) answers
which Profiles the request may read (``profile_reach``). This guard reads the
application source (``server.py`` and ``api/``) and fails when code outside the
policy modules decides that on its own:

- asks the Admission helpers whether the caller is a User
  (``caller_is_user()``, ``caller_bound_profile()``), or compares a role with
  ``ROLE_USER`` (``admission.role == ROLE_USER``, ``role != "member"``, the
  stored value from before the rename, or ``request_admission().role == "user"``;
  a bare ``"user"`` is also a chat message's role, so it counts only against an
  Admission's role);
- reads Upstream's isolated profile mode (``_is_isolated_profile_mode()``);
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

ASKS_USER = "asks the Admission whether the caller is a User"
READS_POSTURE = "reads isolated profile mode"
FILTERS_ROWS = "compares a row's Profile with the active Profile"

USER_QUESTIONS = frozenset({"caller_is_user", "caller_bound_profile"})
ACTIVE_CALLS = frozenset({"get_active_profile_name", "_get_active_profile_name"})
ACTIVE_NAMES = frozenset({"active", "active_profile", "current_profile"})

POSTURE = "Upstream posture: one Profile per process"

# (file, function, spelling): why it matches but does not scope a request.
NOT_SCOPING: dict[tuple[str, str, str], str] = {
    ("api/login.py", "session_identity", ASKS_USER): "display name: from the Profile roster for a User",
    ("api/login.py", "attempt_login", ASKS_USER):
        "login: runs before the request has an Admission; makes a User's Workspace",
    ("api/routes.py", "_app_shell_for_role", ASKS_USER): "the app shell: no extension tags for a User",
    ("api/profiles.py", "get_active_profile_name", READS_POSTURE): POSTURE + ": the active Profile is the isolated one",
    ("api/profiles.py", "_resolve_profile_home_for_name", READS_POSTURE): POSTURE + ": every lookup clamps to it",
    ("api/profiles.py", "get_active_hermes_home", READS_POSTURE): POSTURE + ": the active home is the isolated one",
    ("api/profiles.py", "_home_for_scheduled_cron_job", READS_POSTURE): POSTURE + ": a scheduled run stays in it",
    ("api/profiles.py", "install_cron_scheduler_profile_isolation", READS_POSTURE):
        POSTURE + ": a scheduled run's event names it",
    ("api/profiles.py", "init_profile_state", READS_POSTURE): POSTURE + ": startup state",
    ("api/profiles.py", "switch_profile", READS_POSTURE): POSTURE + ": no switch away from it",
    ("api/profiles.py", "list_profiles_api", READS_POSTURE): POSTURE + ": the one Profile listed",
    ("api/profiles.py", "create_profile_api", READS_POSTURE): POSTURE + ": no Profile is created",
    ("api/profiles.py", "delete_profile_api", READS_POSTURE): POSTURE + ": no Profile is deleted",
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


def _names_role_user(node) -> bool:
    """``ROLE_USER``, or the stored value from before the rename (``"member"``)."""
    return (
        (isinstance(node, ast.Name) and node.id == "ROLE_USER")
        or (isinstance(node, ast.Attribute) and node.attr == "ROLE_USER")
        or (isinstance(node, ast.Constant) and node.value == "member")
    )


def _is_admission_role(node) -> bool:
    """``<admission>.role`` or ``request_admission().role``: never a chat message's role."""
    if not (isinstance(node, ast.Attribute) and node.attr == "role"):
        return False
    owner = node.value
    if isinstance(owner, ast.Call):
        func = owner.func
        return (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)) == "request_admission"
    return isinstance(owner, ast.Name) and "admission" in owner.id


def _compares_with_the_user_role(operands) -> bool:
    """A role compared with the User role. The literal ``"user"`` is also a chat
    message's role, so it counts only against an Admission's role."""
    if any(_names_role_user(o) for o in operands):
        return any(_names_role(o) for o in operands)
    return any(isinstance(o, ast.Constant) and o.value == "user" for o in operands) and any(
        _is_admission_role(o) for o in operands
    )


def _names_role(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "role") or (isinstance(node, ast.Name) and node.id == "role")


def _spellings(tree):
    """(line, spelling) for each place in *tree* that decides Profile reach on its own."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            operands = [node.left, *node.comparators]
            if _compares_with_the_user_role(operands):
                yield node.lineno, ASKS_USER
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in USER_QUESTIONS:
            yield node.lineno, ASKS_USER
        elif name == "_is_isolated_profile_mode":
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
        for line, spelling in _spellings(tree):
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


def _caught(source: str) -> list[str]:
    return [spelling for _line, spelling in sorted(_spellings(ast.parse(source)))]


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
        "    i = admission.role == ROLE_USER\n"
        "    k = role != access.ROLE_USER\n"
        "    m = 'member' == request_admission().role\n"
        "    n = request_admission().role == 'user'\n"
    )
    assert _caught(source) == [ASKS_USER, ASKS_USER, READS_POSTURE, READS_POSTURE,
                               FILTERS_ROWS, FILTERS_ROWS, FILTERS_ROWS, ASKS_USER, ASKS_USER, ASKS_USER,
                               ASKS_USER]


def test_the_near_misses_are_not_caught():
    source = (
        "def f(meta, name, requested_profile):\n"
        "    a = _profiles_match(meta.get('profile'), requested_profile)\n"
        "    b = _profiles_match(name, active_profile)\n"
        "    c = reach.includes(meta.get('profile'))\n"
        "    d = admission.role == ROLE_ADMIN\n"
        "    e = message['role'] == 'user'\n"
        "    f = role == 'user'\n"
    )
    assert _caught(source) == []
