"""GFIT-CoWork: one place answers "who is calling?" -- the request's Admission.

Admission runs on every request from a Directory session and records its
answer (role and Profile) as the request's Admission (``api.access``). This
guard reads the application source and fails when code outside the Admission
module asks the question another way:

- reads the role from a session record (``info.get("role")``,
  ``session_info["role"]``), or
- reads the per-request Profile pin directly (``pinned_request_profile()``,
  ``_tls.pinned_profile``).

:data:`ALLOWED_READERS` names the readers that predate the request's Admission,
with how many reads each function makes. Each migration ticket removes its
entries; a new reader, or a new read in an allowed function, fails the guard.

Limit: a session record is recognised by its variable name (``info`` or
``*session_info``, the names the codebase uses), so a record read under another
name is not seen.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADMISSION_MODULE = "api/access.py"

# A session record is named ``info`` or ``*session_info`` across the codebase;
# other dicts with a "role" key are chat messages and transcript records.
def _is_session_record(node) -> bool:
    return isinstance(node, ast.Name) and (node.id == "info" or node.id.endswith("session_info"))


# (file, enclosing function) -> (what it reads, how many reads). Removed ticket by ticket
# (.scratch/request-principal/issues/02-05) until the list is empty.
ALLOWED_READERS: dict[tuple[str, str], tuple[str, int]] = {
    # 02: the role comes from the request's Admission
    ("api/auth.py", "_refuse_admin_only_for_member"): ("session role", 1),
    ("api/member_login.py", "session_identity"): ("session role", 1),
    ("api/routes.py", "_directory_session_role"): ("session role", 1),
    ("api/routes.py", "handle_get"): ("session role", 1),
    # 03: the bound Profile comes from the request's Admission
    ("api/profiles.py", "_is_isolated_profile_mode"): ("pin", 1),
    ("api/profiles.py", "_isolated_profile_name"): ("pin", 1),
    ("api/profiles.py", "_isolated_profile_home"): ("pin", 1),
    ("api/profiles.py", "pinned_request_profile"): ("pin", 1),
    ("api/routes.py", "_session_profile_mismatch"): ("pin", 1),
    ("api/routes.py", "_guard_pinned_profile_request"): ("pin", 1),
    ("api/routes.py", "_handle_media"): ("pin", 1),
    # 04: the Workspace asks whether the caller is a User
    ("api/workspace.py", "member_workspace_root"): ("pin", 1),
}


def _source_files():
    yield REPO / "server.py"
    yield from sorted((REPO / "api").rglob("*.py"))


def _enclosing_functions(tree) -> list[tuple[int, int, str]]:
    return [
        (node.lineno, node.end_lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _function_at(functions, line: int) -> str:
    """The innermost function containing *line*, or ``<module>``."""
    containing = [f for f in functions if f[0] <= line <= f[1]]
    return max(containing, key=lambda f: f[0])[2] if containing else "<module>"


def _is_str(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _reads(tree):
    """(line, what) for each read of a session record's role or of the pin."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if (
                name == "get" and isinstance(func, ast.Attribute)
                and _is_session_record(func.value) and node.args and _is_str(node.args[0], "role")
            ):
                yield node.lineno, "session role"
            elif name == "pinned_request_profile":
                yield node.lineno, "pin"
            elif name == "getattr" and len(node.args) >= 2 and _is_str(node.args[1], "pinned_profile"):
                yield node.lineno, "pin"
        elif (
            isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
            and _is_session_record(node.value) and _is_str(node.slice, "role")
        ):
            yield node.lineno, "session role"
        elif (
            isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and node.attr == "pinned_profile"
        ):
            yield node.lineno, "pin"


def _readers() -> list[tuple[str, int, str, str]]:
    """(file, line, function, what) for every reader outside the Admission module."""
    found = []
    for path in _source_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == ADMISSION_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        functions = _enclosing_functions(tree)
        for line, what in _reads(tree):
            found.append((rel, line, _function_at(functions, line), what))
    return sorted(found)


def _read_counts() -> Counter:
    return Counter((rel, func) for rel, _, func, _ in _readers())


def test_only_the_admission_module_says_who_is_calling():
    counts = _read_counts()
    offenders = [
        f"{rel}:{line} in {func}() reads the {what}"
        for rel, line, func, what in _readers()
        if counts[rel, func] > ALLOWED_READERS.get((rel, func), ("", 0))[1]
    ]
    assert not offenders, (
        "Ask the request's Admission (api.access) who is calling, not the session "
        "record or the Profile pin:\n  " + "\n  ".join(offenders)
    )


def test_every_allowed_reader_still_reads():
    """An entry whose reads have moved to the request's Admission must be lowered or removed."""
    counts = _read_counts()
    stale = sorted(
        (key, allowed, counts[key])
        for key, (_, allowed) in ALLOWED_READERS.items()
        if counts[key] < allowed
    )
    assert not stale, f"Lower or remove these ALLOWED_READERS entries (allowed, found): {stale}"


def test_the_guard_catches_each_spelling():
    source = (
        "def f(info, session_info, handler):\n"
        "    a = info.get('role')\n"
        "    b = session_info['role']\n"
        "    c = pinned_request_profile()\n"
        "    d = getattr(_tls, 'pinned_profile', None)\n"
        "    e = _tls.pinned_profile\n"
        "    session_info['role'] = 'admin'\n"    # a write, not a read
        "    _tls.pinned_profile = None\n"        # a write, not a read
        "    record.get('role')\n"               # a transcript record, not a session
        "    return message.get('role')\n"       # a chat message, not a session
    )
    reads = sorted(_reads(ast.parse(source)))
    assert reads == [
        (2, "session role"), (3, "session role"), (4, "pin"), (5, "pin"), (6, "pin"),
    ]
