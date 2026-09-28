"""GFIT-CoWork: one place answers "who is calling?" -- the request's Admission.

Admission runs on every request from a Directory session and records its
answer (role and Profile) as the request's Admission (``api.access``). This
guard reads the application source and fails when code outside the Admission
module asks the question another way:

- reads the role from a session record (``info.get("role")``,
  ``session_info["role"]``), or
- reads the per-request Profile pin directly (``pinned_request_profile()``,
  ``_tls.pinned_profile``).

There is no allowlist: every earlier reader now asks the request's Admission.
A second guard fails when anything stores a pin of its own again (defines
``pin_request_profile()`` or sets a ``pinned_profile`` attribute), since a
request is pinned exactly when its Admission is a User's.

Limits: a session record is recognised by its variable name (``info`` or
``*session_info``, the names the codebase uses), so a record read under another
name is not seen; and a pin is recognised by those two names, so a pin stored
under another name is not seen.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADMISSION_MODULE = "api/access.py"

# A session record is named ``info`` or ``*session_info`` across the codebase;
# other dicts with a "role" key are chat messages and transcript records.
def _is_session_record(node) -> bool:
    return isinstance(node, ast.Name) and (node.id == "info" or node.id.endswith("session_info"))


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
    for rel, tree in _parsed_sources():
        if rel == ADMISSION_MODULE:
            continue
        functions = _enclosing_functions(tree)
        for line, what in _reads(tree):
            found.append((rel, line, _function_at(functions, line), what))
    return sorted(found)


def test_only_the_admission_module_says_who_is_calling():
    offenders = [f"{rel}:{line} in {func}() reads the {what}" for rel, line, func, what in _readers()]
    assert not offenders, (
        "Ask the request's Admission (api.access) who is calling, not the session "
        "record or the Profile pin:\n  " + "\n  ".join(offenders)
    )


def _pin_stores(tree):
    """(line, what) for each definition of a pin setter or store of a pin."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "pin_request_profile":
            yield node.lineno, "defines pin_request_profile()"
        elif (
            isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
            and node.attr == "pinned_profile"
        ):
            yield node.lineno, "stores a pinned_profile"
        elif (
            isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr"
            and len(node.args) >= 2 and _is_str(node.args[1], "pinned_profile")
        ):
            yield node.lineno, "stores a pinned_profile"


def test_no_separate_pin_is_stored():
    """A request is pinned exactly when its Admission is a User's; there is no pin of its own."""
    stores = sorted(
        f"{rel}:{line} {what}" for rel, tree in _parsed_sources() for line, what in _pin_stores(tree)
    )
    assert not stores, (
        "The pin is a view of the request's Admission (api.access.caller_bound_profile); "
        "do not store one apart from it:\n  " + "\n  ".join(stores)
    )


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


def test_the_pin_guard_catches_each_spelling():
    source = (
        "def pin_request_profile(name):\n"
        "    _tls.pinned_profile = name\n"
        "    setattr(_tls, 'pinned_profile', name)\n"
        "    return _tls.pinned_profile\n"       # a read, caught by the other guard
    )
    stores = sorted(_pin_stores(ast.parse(source)))
    assert stores == [
        (1, "defines pin_request_profile()"), (2, "stores a pinned_profile"), (3, "stores a pinned_profile"),
    ]
