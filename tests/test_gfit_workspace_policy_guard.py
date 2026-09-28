"""GFIT-CoWork: one place answers "what may this request touch?" -- the Workspace policy.

Each request's Workspace policy (``api.workspace_policy``), chosen from the
request's Admission, answers every Workspace question. This guard reads the
application source and fails when code outside the Workspace policy and
Admission modules asks "is the caller a User?" itself:

- calls ``caller_is_user()`` or ``caller_bound_profile()``;
- compares a role with ``ROLE_MEMBER`` (or the literal ``"member"``);
- confines at the call site with ``confine_to_member_workspace()``.

Some callers ask for a reason that is not Workspace confinement (which Profile
a request is bound to, a display name, login). They are listed in
:data:`NOT_WORKSPACE`, each with its reason, and stay. :data:`ALLOWLIST` names
today's Workspace callers; the migration tickets move them to the policy and
empty it. Entries name the file, the enclosing function and the question, so a
function that asks for both reasons (the GET dispatcher) is listed once for each. An entry that no longer matches anything fails too, so both lists
only shrink.

Limits: a caller is named by file, enclosing function and question, so the same
question asked again inside a listed function is not seen; and only the spellings above are
recognised.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POLICY_MODULES = {"api/access.py", "api/workspace_policy.py"}

ASKS = "asks caller_is_user()"
BOUND = "asks caller_bound_profile()"
CONFINES = "confines at the call site"
ROLE = "compares a role with ROLE_MEMBER"

# (file, function, question): why it asks, when that is not Workspace confinement.
NOT_WORKSPACE: dict[tuple[str, str, str], str] = {
    ("api/member_login.py", "attempt_login", ROLE): "login: creates a new User's default Workspace from an explicit Profile",
    ("api/member_login.py", "session_identity", ASKS): "display name: from the Profile roster for a User",
    ("api/profiles.py", "_is_isolated_profile_mode", BOUND): "Profile binding: a User's request is an isolated request",
    ("api/profiles.py", "_isolated_profile_name", BOUND): "Profile binding: the Profile a request is bound to",
    ("api/profiles.py", "_isolated_profile_home", BOUND): "Profile binding: the home of the Profile a request is bound to",
    ("api/routes.py", "_session_profile_mismatch", BOUND): "session ownership: a User must not learn another Profile's name",
    ("api/routes.py", "_guard_pinned_profile_request", BOUND): "Profile binding: a User may not name another Profile",
    ("api/routes.py", "handle_get", ROLE): "the app shell: no extension tags for a User",
}

# (file, function, question): today's Workspace callers, moved to the policy by tickets 06-08.
ALLOWLIST: set[tuple[str, str, str]] = {
    # 06: choosing a Workspace
    ("api/workspace.py", "_profile_default_workspace", ASKS),
    ("api/workspace.py", "_clean_workspace_list", ASKS),
    ("api/workspace.py", "get_profile_default_workspace", ASKS),
    ("api/workspace.py", "get_last_workspace", ASKS),
    ("api/workspace.py", "resolve_trusted_workspace", ASKS),
    ("api/workspace.py", "validate_workspace_to_add", ASKS),
    ("api/workspace.py", "ensure_member_workspace", ASKS),
    ("api/workspace.py", "_caller_workspace_root", BOUND),
    ("api/workspace.py", "_resolve_member_workspace", CONFINES),
    ("api/routes.py", "_handle_workspace_add", CONFINES),
    # 07: file operations
    ("api/workspace.py", "_trusted_workspace_roots", ASKS),
    ("api/workspace.py", "confine_to_member_workspace", ASKS),
    ("api/workspace.py", "safe_resolve_ws", CONFINES),
    ("api/helpers.py", "safe_resolve", CONFINES),
    ("api/routes.py", "handle_get", CONFINES),                 # git status
    ("api/routes.py", "_git_session_and_workspace", CONFINES),
    ("api/routes.py", "_handle_media", BOUND),
    ("api/worktrees.py", "_confine_worktree", CONFINES),
}


def _parsed_sources():
    """(repo-relative path, syntax tree) for each application source file."""
    for path in [REPO / "server.py", *sorted((REPO / "api").rglob("*.py"))]:
        rel = path.relative_to(REPO).as_posix()
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)


def _enclosing_functions(tree) -> list[tuple[int, int, str]]:
    return [
        (node.lineno, node.end_lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _outermost_function_at(functions, line: int) -> str:
    """The outermost function containing *line*, or ``<module>``: a closure counts as its function."""
    containing = [f for f in functions if f[0] <= line <= f[1]]
    return min(containing, key=lambda f: f[0])[2] if containing else "<module>"


QUESTIONS = {"caller_is_user": ASKS, "caller_bound_profile": BOUND, "confine_to_member_workspace": CONFINES}


def _names_role_member(node) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id == "ROLE_MEMBER")
        or (isinstance(node, ast.Attribute) and node.attr == "ROLE_MEMBER")
        or (isinstance(node, ast.Constant) and node.value == "member")
    )


def _questions(tree):
    """(line, what) for each place that asks "is the caller a User?"."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in QUESTIONS:
                yield node.lineno, QUESTIONS[name]
        elif isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            operands = [node.left, *node.comparators]
            if any(_names_role_member(o) for o in operands) and any(
                isinstance(o, ast.Attribute) and o.attr == "role" or isinstance(o, ast.Name) and o.id == "role"
                for o in operands
            ):
                yield node.lineno, ROLE


def _askers() -> list[tuple[str, int, str, str]]:
    """(file, line, function, what) for every asker outside the policy and Admission modules."""
    found = []
    for rel, tree in _parsed_sources():
        if rel in POLICY_MODULES:
            continue
        functions = _enclosing_functions(tree)
        for line, what in _questions(tree):
            found.append((rel, line, _outermost_function_at(functions, line), what))
    return sorted(found)


def test_only_the_workspace_policy_decides_workspace_confinement():
    offenders = [
        f"{rel}:{line} in {func}() {what}"
        for rel, line, func, what in _askers()
        if (rel, func, what) not in NOT_WORKSPACE and (rel, func, what) not in ALLOWLIST
    ]
    assert not offenders, (
        "Ask the request's Workspace policy (api.workspace_policy.request_workspace_policy) "
        "what this request may touch, not whether the caller is a User:\n  " + "\n  ".join(offenders)
    )


def test_every_listed_caller_still_asks():
    """A stale entry fails, so the lists only shrink."""
    asking = {(rel, func, what) for rel, _line, func, what in _askers()}
    stale = sorted(f"{rel} {func}() {what}" for rel, func, what in {*NOT_WORKSPACE, *ALLOWLIST} - asking)
    assert not stale, "Remove these entries; they no longer ask:\n  " + "\n  ".join(stale)


def test_the_lists_do_not_overlap():
    assert not set(NOT_WORKSPACE) & ALLOWLIST


def test_the_guard_catches_each_spelling():
    source = (
        "def f(admission, role):\n"
        "    a = caller_is_user()\n"
        "    b = access.caller_bound_profile()\n"
        "    c = confine_to_member_workspace(path)\n"
        "    d = admission.role == ROLE_MEMBER\n"
        "    e = role != access.ROLE_MEMBER\n"
        "    f = 'member' == admission.role\n"
        "    g = admission.role == ROLE_ADMIN\n"        # the Admin gate, not a User question
        "    h = message['role'] == 'user'\n"            # a chat message
        "    return name == 'member'\n"                  # not a role
    )
    assert sorted(_questions(ast.parse(source))) == [
        (2, "asks caller_is_user()"),
        (3, "asks caller_bound_profile()"),
        (4, "confines at the call site"),
        (5, "compares a role with ROLE_MEMBER"),
        (6, "compares a role with ROLE_MEMBER"),
        (7, "compares a role with ROLE_MEMBER"),
    ]


def test_a_closure_counts_as_its_function():
    source = (
        "def outer():\n"
        "    def inner():\n"
        "        return caller_is_user()\n"
        "    return inner\n"
    )
    tree = ast.parse(source)
    assert _outermost_function_at(_enclosing_functions(tree), 3) == "outer"
