"""GFIT-CoWork: one word per idea -- a User, and a User's request bound to their Profile.

``CONTEXT.md`` calls the person who logs in and owns one Profile a **User**
(_Avoid_: member), and says a User's request is **bound** to their Profile
(_Avoid_: pinned). This guard reads GFIT-CoWork's modules and fails when one
is named, or defines a function, class, parameter or module-level name, with
the word "member", "members" or "pinned" in it, naming each file and line.

A name is split into its words (``snake_case`` and ``CamelCase``), so
``_remember_...`` and ``membership`` are not the word "member". Local variables
are not names this guard reads, so a loop over archive members is not seen; a
*function* named for archive members would be, which is why only GFIT-CoWork's
modules are scanned, not upstream's archive code. The glossary also avoids "locked"; the guard does not check it, because
upstream uses it widely for real locks.

:data:`KEPT` lists names kept on purpose, each with its reason. :data:`ALLOWLIST`
names today's offenders; the migration tickets rename them and empty it. An
entry in either list that no longer matches anything fails, so both lists only
shrink.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# GFIT-CoWork's own modules, and the upstream modules that hold its access and Workspace code.
GFIT_MODULES = (
    "api/access.py",
    "api/auth.py",
    "api/directory.py",
    "api/ldap_directory.py",
    "api/member_login.py",
    "api/profiles.py",
    "api/roster.py",
    "api/routes.py",
    "api/workspace.py",
    "api/workspace_policy.py",
)

AVOIDED_WORDS = {"member", "members", "pinned"}

# (file, name): why it keeps the word.
KEPT: dict[tuple[str, str], str] = {
    ("api/access.py", "ROLE_MEMBER"): "the User role's constant, kept beside its stored value `member` (the value needs a migration; the spec leaves the name to the implementer)",
    ("api/routes.py", "_visible_pinned_lineage_ids"): "upstream: pinned sessions in the sidebar",
    ("api/routes.py", "_tts_resolve_pinned_addresses"): "upstream: TTS requests pinned to resolved addresses",
    ("api/routes.py", "_tts_resolve_pinned_address"): "upstream: TTS requests pinned to resolved addresses",
    ("api/routes.py", "_PinnedHTTPSConnection"): "upstream: TTS requests pinned to resolved addresses",
    ("api/routes.py", "_PinnedHTTPSHandler"): "upstream: TTS requests pinned to resolved addresses",
}

# (file, name): GFIT-CoWork names not yet renamed (one-word-per-idea tickets 02-04).
ALLOWLIST: set[tuple[str, str]] = {
    ("api/routes.py", "_guard_pinned_profile_request"),      # 03
    ("api/member_login.py", "member_login"),                 # 04: the module itself
}


def _words(name: str) -> set[str]:
    """The lower-case words of a ``snake_case`` or ``CamelCase`` name."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return {w.lower() for w in spaced.split("_") if w}


def _says_an_avoided_word(name: str) -> bool:
    return bool(_words(name) & AVOIDED_WORDS)


def _defined_names(tree) -> list[tuple[int, str]]:
    """(line, name) for each function, class, parameter and module-level name *tree* defines."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.lineno, node.name))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            found.extend((a.lineno, a.arg) for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found.extend((node.lineno, t.id) for t in targets if isinstance(t, ast.Name))
    return found


def _offending_names() -> list[tuple[str, int, str]]:
    """(file, line, name) for every GFIT-CoWork name that says an avoided word."""
    found = []
    for rel in GFIT_MODULES:
        path = REPO / rel
        if _says_an_avoided_word(path.stem):
            found.append((rel, 0, path.stem))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        found.extend((rel, line, name) for line, name in _defined_names(tree) if _says_an_avoided_word(name))
    return sorted(found)


def test_gfit_code_says_user_and_bound():
    offenders = [
        f"{rel}:{line} {name}"
        for rel, line, name in _offending_names()
        if (rel, name) not in KEPT and (rel, name) not in ALLOWLIST
    ]
    assert not offenders, (
        "Use CONTEXT.md's words: \"User\" (not Member) and \"bound\" (not pinned):\n  "
        + "\n  ".join(offenders)
    )


def test_every_listed_name_is_still_there():
    """A stale entry fails, so the lists only shrink."""
    present = {(rel, name) for rel, _line, name in _offending_names()}
    stale = sorted(f"{rel} {name}" for rel, name in {*KEPT, *ALLOWLIST} - present)
    assert not stale, "Remove these entries; the name is gone:\n  " + "\n  ".join(stale)


def test_the_lists_do_not_overlap():
    assert not set(KEPT) & ALLOWLIST


def test_every_scanned_module_exists():
    missing = [rel for rel in GFIT_MODULES if not (REPO / rel).is_file()]
    assert not missing, f"Update GFIT_MODULES; these moved: {missing}"


def test_the_guard_catches_each_spelling():
    source = (
        "MEMBER_ENDPOINTS = ()\n"
        "ROLE_ADMIN = 'admin'\n"
        "def member_may_call(method, path): ...\n"
        "def _refuse_admin_only_for_member(handler): ...\n"
        "def _guard_pinned_profile_request(handler): ...\n"
        "class MemberSession: ...\n"
        "def clean(workspaces, member_root): ...\n"
        "def _remember_trusted_auth_session(handler): ...\n"   # "remember", not "member"
        "def remove_members(ids): ...\n"
        "class _PinnedHTTPSHandler: ...\n"
        "class MEMBERSession: ...\n"
        "def membership_ok(ids): ...\n"                      # "membership", not "member"
        "def extract(zf):\n"
        "    for member in zf.infolist():\n"                 # a local variable: not read
        "        pass\n"
    )
    names = [name for _line, name in _defined_names(ast.parse(source)) if _says_an_avoided_word(name)]
    assert sorted(names) == sorted([
        "MEMBER_ENDPOINTS",
        "member_may_call",
        "_refuse_admin_only_for_member",
        "_guard_pinned_profile_request",
        "MemberSession",
        "member_root",
        "remove_members",
        "_PinnedHTTPSHandler",
        "MEMBERSession",
    ])


def test_a_module_name_is_read_too():
    assert _says_an_avoided_word("member_login")
    assert not _says_an_avoided_word("login")
