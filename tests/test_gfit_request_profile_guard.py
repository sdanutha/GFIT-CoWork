"""GFIT-CoWork: only the Admission module sets the request's Profile.

The request's Profile is decided once per request, from the request's
Admission or (login off) the profile cookie (``api.access``). This guard reads
the application source (``server.py`` and ``api/``) and fails when code
outside that module sets it on its own:

- calls ``set_request_profile(...)``;
- assigns the thread-local ``profile`` attribute (``_tls.profile = ...``).

:data:`NOT_A_REQUEST` names the places that match but do not decide a
request's Profile, each with its reason. :data:`ALLOWLIST` is empty.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADMISSION_MODULE = "api/access.py"

CALLS = "calls set_request_profile"
ASSIGNS = "assigns the thread-local profile"

# (file, function, spelling): why it matches but does not decide a request's Profile.
NOT_A_REQUEST: dict[tuple[str, str, str], str] = {
    ("api/profiles.py", "set_request_profile", ASSIGNS): "the setter itself",
    ("api/profiles.py", "clear_request_profile", ASSIGNS): "the end of every request clears it",
    ("api/profiles.py", "profile_scope_for_detached_worker", CALLS):
        "a detached worker thread: no request, the spawning request's Profile carried over",
}

# (file, function, spelling): callers not yet moved to the Admission module (none left).
ALLOWLIST: set[tuple[str, str, str]] = set()


def _spellings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "set_request_profile":
                yield node.lineno, CALLS
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "profile" and (
                    isinstance(target.value, ast.Name) and target.value.id == "_tls"
                ):
                    yield node.lineno, ASSIGNS


def _outermost_function_at(tree, line: int) -> str:
    containing = [
        (node.lineno, node.name) for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno
    ]
    return min(containing)[1] if containing else "<module>"


def setters() -> list[tuple[str, int, str, str]]:
    found = []
    for path in [REPO / "server.py", *sorted((REPO / "api").rglob("*.py"))]:
        rel = path.relative_to(REPO).as_posix()
        if rel == ADMISSION_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for line, spelling in _spellings(tree):
            found.append((rel, line, _outermost_function_at(tree, line), spelling))
    return sorted(found)


def test_only_the_admission_module_sets_the_requests_profile():
    offenders = [
        f"{rel}:{line} in {func}() {spelling}"
        for rel, line, func, spelling in setters()
        if (rel, func, spelling) not in NOT_A_REQUEST and (rel, func, spelling) not in ALLOWLIST
    ]
    assert not offenders, (
        "The request's Profile is decided in api.access (settle_request_profile):\n  " + "\n  ".join(offenders)
    )


def test_every_listed_place_still_matches():
    found = {(rel, func, spelling) for rel, _line, func, spelling in setters()}
    stale = sorted(f"{rel} {func}() {spelling}" for rel, func, spelling in {*NOT_A_REQUEST, *ALLOWLIST} - found)
    assert not stale, "Remove these entries; they no longer match:\n  " + "\n  ".join(stale)


def test_each_spelling_is_caught():
    source = (
        "def f(name):\n"
        "    set_request_profile(name)\n"
        "    profiles.set_request_profile(name)\n"
        "    _tls.profile = name\n"
    )
    assert [s for _l, s in sorted(_spellings(ast.parse(source)))] == [CALLS, CALLS, ASSIGNS]


def test_the_near_misses_are_not_caught():
    source = (
        "def f(name, request):\n"
        "    request.profile = name\n"
        "    clear_request_profile()\n"
        "    _tls.cron_profile_depth = 1\n"
    )
    assert list(_spellings(ast.parse(source))) == []
