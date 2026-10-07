"""GFIT-CoWork: a route loads a session its request names only through load_owned_session.

A guard on the source of the route and upload modules (architecture review
round 6, candidate 2): no ``try`` that loads a session and answers
"Session not found" 404 on ``KeyError`` (the copy the accessor replaced), and
no plain session load whose id comes straight from the request (directly, or through a local name that took it from the
request). Loads by an id the server chose (worker threads, the session list)
are not flagged. Each exception says why its route loads the session itself.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("api/routes.py", "api/upload.py")
LOADERS = {"get_session", "_get_or_materialize_session", "get_session_for_file_ops"}
REQUEST_FIELDS = ("body[", "body.get(", "parse_qs(", "qs[", "qs.get(", "query[", "query.get(", "fields")

# (module, function): why it loads a request-named session itself.
_FALLS_BACK = "a miss falls back to the session's CLI/state.db record (a different answer than 404)"
_BEST_EFFORT = (
    "a best-effort lookup after the dispatch guard asked session ownership about the same id; "
    "a miss is not answered"
)
ALLOWED = {
    ("api/routes.py", "_handle_chat_start"): (
        "a miss claims a CLI/TUI session (Upstream's chat-start claim); the dispatch "
        "guard has already asked session ownership for Users and the Admin"
    ),
    ("api/routes.py", "_handle_session_get"): (
        "the detail load has its own refusal: listed rows, Claude Code rows, the Admin's read-only marking"
    ),
    ("api/routes.py", "_get_api_git_info"): _FALLS_BACK,
    ("api/routes.py", "_handle_list_dir"): _FALLS_BACK,
    ("api/routes.py", "_post_api_session_archive"): _FALLS_BACK,
    ("api/routes.py", "_post_api_session_delete"): _BEST_EFFORT,
    ("api/routes.py", "_post_api_session_clear"): _BEST_EFFORT,
    ("api/routes.py", "_handle_session_sse_stream"): _BEST_EFFORT,
    ("api/routes.py", "_memory_project_context_workspace"): _BEST_EFFORT,
    ("api/routes.py", "_handle_approval_respond"): _BEST_EFFORT,
    ("api/routes.py", "_handle_handoff_summary"): _BEST_EFFORT,
}


def _answers_session_not_found(statement) -> bool:
    """``return bad(handler, "Session not found", 404)`` or the ``j(...)`` form: an HTTP answer."""
    return (
        isinstance(statement, ast.Return)
        and isinstance(statement.value, ast.Call)
        and ast.unparse(statement.value.func) in ("bad", "j")
        and "Session not found" in ast.unparse(statement)
        and "404" in ast.unparse(statement)
    )


def _loads(nodes) -> bool:
    return any(
        isinstance(node, ast.Call) and ast.unparse(node.func) in LOADERS
        for statement in nodes
        for node in ast.walk(statement)
    )


def _from_request(node, request_names) -> bool:
    """Does *node* take its value from the request (directly, or via a name that did)?"""
    text = ast.unparse(node)
    if text.startswith(REQUEST_FIELDS) or any(field in text for field in ("body[", "body.get(", "parse_qs(")):
        return True
    return any(isinstance(sub, ast.Name) and sub.id in request_names for sub in ast.walk(node))


def findings(sources: dict) -> set:
    found = set()
    for module, source in sources.items():
        tree = ast.parse(source)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Names that hold a value taken from the request, in source order.
            request_names = {"body", "query", "qs", "fields"} & {a.arg for a in function.args.args}
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and _from_request(node.value, request_names):
                    request_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            for node in ast.walk(function):
                if isinstance(node, ast.Try) and _loads(node.body) and any(
                    handler.type is not None and ast.unparse(handler.type) == "KeyError"
                    and len(handler.body) == 1 and _answers_session_not_found(handler.body[0])
                    for handler in node.handlers
                ):
                    found.add((module, function.name))
                if (
                    isinstance(node, ast.Call) and ast.unparse(node.func) in LOADERS and node.args
                    and _from_request(node.args[0], request_names)
                ):
                    found.add((module, function.name))
    return found


def test_request_named_sessions_load_through_the_accessor():
    sources = {module: (ROOT / module).read_text(encoding="utf-8") for module in MODULES}
    assert findings(sources) - set(ALLOWED) == set()


def test_every_exception_is_still_needed():
    sources = {module: (ROOT / module).read_text(encoding="utf-8") for module in MODULES}
    assert set(ALLOWED) <= findings(sources)


def test_the_guard_finds_both_shapes():
    source = '''
def copied(handler, body):
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)

def direct(handler, body):
    s = _get_or_materialize_session(body["session_id"])

def via_a_name(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    s = get_session(sid, metadata_only=True)

def owned(handler, body):
    s = load_owned_session(handler, body["session_id"], load=get_session)

def server_chosen(session_id):
    try:
        s = get_session(session_id)
    except KeyError:
        return None
'''
    assert findings({"m.py": source}) == {("m.py", "copied"), ("m.py", "direct"), ("m.py", "via_a_name")}
