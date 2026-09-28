"""GFIT-CoWork: one place answers "whose session is this?" -- session ownership.

Each request's session ownership adapter (``api.session_ownership``), chosen
from the request's Admission, answers every session ownership question. This
guard reads the application source and fails when code outside that module
decides session visibility on its own:

- compares a session's Profile with the active or bound Profile
  (``_profiles_match(<a session's profile>, <the active or bound Profile>)``);
- names a removed ownership helper (calls it, or defines it again).

A session's Profile is spelled ``getattr(<session>, "profile", ...)``,
``<session>.profile``, ``<session>.get("profile")``, ``<session>["profile"]``
or a variable named like ``session_profile``, where ``<session>`` is a name used
for sessions and session rows (:data:`SESSION_NAMES`). The active or bound
Profile is a call to ``get_active_profile_name``/``caller_bound_profile``, or a
variable named ``active_profile``, ``active``, ``bound`` or ``bound_profile``.

:data:`ALLOWLIST` names callers not yet moved to the module; it is empty.
Limits: only the spellings above are recognised, and a comparison with a
Profile the request names (``requested_profile``) or a lineage Profile is not
an ownership decision and is not flagged.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OWNERSHIP_MODULE = "api/session_ownership.py"

REMOVED_HELPERS = frozenset({
    "_session_visible_to_active_profile",
    "_session_profile_mismatch",
    "_is_profile_agnostic_foreign_session",
    "_bound_profile_owns_session_id",
    "_guard_bound_session_id",
    "_session_event_reaches_bound_profile",
    "_reject_invisible_session",
    "_stream_id_owner_session_id",
})

SESSION_NAMES = frozenset({
    "s", "session", "sess", "source", "existing", "stored_session", "row", "snapshot_session",
})
SESSION_PROFILE_NAMES = frozenset({
    "session_profile", "_session_profile", "existing_profile", "effective_profile",
    "_anchor_session_profile", "_recovery_session_profile",
})
ACTIVE_OR_BOUND_CALLS = frozenset({"get_active_profile_name", "_get_active_profile_name", "caller_bound_profile"})
ACTIVE_OR_BOUND_NAMES = frozenset({"active_profile", "active", "bound", "bound_profile"})

COMPARES = "compares a session's Profile with the active or bound Profile"
REMOVED = "names a removed ownership helper"

# (file, function, what): callers not yet moved to session ownership (none left).
ALLOWLIST: set[tuple[str, str, str]] = set()


def _called_name(node) -> str | None:
    func = node.func if isinstance(node, ast.Call) else None
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_is_session(node) -> bool:
    return isinstance(node, ast.Name) and node.id in SESSION_NAMES


def _is_profile_key(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "profile"


def _is_session_profile(node) -> bool:
    if isinstance(node, ast.Name):
        return node.id in SESSION_PROFILE_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr == "profile" and _receiver_is_session(node.value)
    if isinstance(node, ast.Subscript):
        return _receiver_is_session(node.value) and _is_profile_key(node.slice)
    if isinstance(node, ast.Call):
        name = _called_name(node)
        if name == "getattr" and len(node.args) >= 2:
            return _receiver_is_session(node.args[0]) and _is_profile_key(node.args[1])
        if name == "get" and isinstance(node.func, ast.Attribute) and node.args:
            return _receiver_is_session(node.func.value) and _is_profile_key(node.args[0])
    return False


def _is_active_or_bound(node) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ACTIVE_OR_BOUND_NAMES
    return isinstance(node, ast.Call) and _called_name(node) in ACTIVE_OR_BOUND_CALLS


def _decisions(tree) -> list[tuple[int, str]]:
    """(line, what) for each ownership decision in *tree*."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) == "_profiles_match" and len(node.args) >= 2:
            a, b = node.args[:2]
            if (_is_session_profile(a) and _is_active_or_bound(b)) or (
                _is_session_profile(b) and _is_active_or_bound(a)
            ):
                found.append((node.lineno, COMPARES))
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value
        if name in REMOVED_HELPERS:
            found.append((node.lineno, REMOVED))
    return found


def _outermost_function_at(tree, line: int) -> str:
    containing = [
        (node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno
    ]
    return min(containing)[1] if containing else "<module>"


def _application_sources():
    for path in [REPO / "server.py", *sorted((REPO / "api").rglob("*.py"))]:
        rel = path.relative_to(REPO).as_posix()
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)


def _deciders() -> list[tuple[str, int, str, str]]:
    found = []
    for rel, tree in _application_sources():
        if rel == OWNERSHIP_MODULE:
            continue
        for line, what in _decisions(tree):
            found.append((rel, line, _outermost_function_at(tree, line), what))
    return sorted(set(found))


def test_only_session_ownership_decides_whose_session_it_is():
    offenders = [
        f"{rel}:{line} in {func}() {what}"
        for rel, line, func, what in _deciders()
        if (rel, func, what) not in ALLOWLIST
    ]
    assert not offenders, (
        "Ask the request's session ownership (api.session_ownership.request_session_ownership) "
        "whose session this is, instead of deciding it here:\n    " + "\n    ".join(offenders)
    )


def test_every_allowlisted_caller_still_decides():
    deciding = {(rel, func, what) for rel, _line, func, what in _deciders()}
    stale = sorted(ALLOWLIST - deciding)
    assert not stale, f"Remove these entries; they no longer decide: {stale}"


# ── Self-tests: each spelling is caught, and the near misses are not ────────

CAUGHT = [
    "_profiles_match(getattr(s, 'profile', None), active_profile)",
    "_profiles_match(session.profile, get_active_profile_name())",
    "_profiles_match(row.get('profile'), bound)",
    "_profiles_match(existing['profile'], _get_active_profile_name())",
    "_profiles_match(active, session_profile)",
    "_profiles_match(effective_profile, caller_bound_profile())",
    "routes._session_visible_to_active_profile(p, h)",
    "_session_profile_mismatch(h, sid, p)",
    "def _reject_invisible_session(handler, session): pass",
    "monkeypatch.setattr(routes, '_stream_id_owner_session_id', f)",
]

NOT_CAUGHT = [
    "_profiles_match(p.get('profile'), active_profile)",  # a project
    "_profiles_match(getattr(s, 'profile', None), requested_profile)",  # a Profile the request names
    "_profiles_match(child_profile, snapshot_profile)",  # lineage
    "_profiles_match(name, active_profile)",  # a Profile name
    "request_session_ownership().refuse_session(sid)",
]


def _caught(source: str) -> bool:
    return bool(_decisions(ast.parse(source)))


def test_each_spelling_is_caught():
    assert [src for src in CAUGHT if not _caught(src)] == []


def test_the_near_misses_are_not_caught():
    assert [src for src in NOT_CAUGHT if _caught(src)] == []
