"""GFIT-CoWork: the web app shows the Admin's read-only view of another Profile's session.

- The session view shows "Read only: <owner>'s session" and disables the
  composer when the detail load says ``read_only_reason: "other_profile"``.
- Such a session is never forkable, even when its source would allow it (cron).
- The Admin's session list marks other Profiles' rows read-only, so the row
  actions that write are hidden as for any read-only session.
- The cron detail of another Profile's job no longer tells a caller who may
  not switch to switch.
- Every locale has the banner text.

The JS functions run in node against a tiny DOM stand-in; the list marking is
an HTTP test against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._gfit_server import gfit_server as _gfit_server

REPO = Path(__file__).resolve().parent.parent
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")
PANELS_JS = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"


def _extract_function(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    assert start != -1, f"Could not find function {name}"
    depth = 0
    for idx in range(source.find("{", start), len(source)):
        depth += {"{": 1, "}": -1}.get(source[idx], 0)
        if depth == 0:
            return source[start:idx + 1]
    pytest.fail(f"Could not extract {name}")


def _run_node(script: str) -> dict:
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


VIEW_HARNESS = """
const els = {
  sessionReadOnlyBanner: {hidden: true, textContent: ''},
  msg: {disabled: false, dataset: {}, placeholder: 'Message Hermes…'},
  composerWrap: {classes: new Set(), classList: {toggle(name, on) { on ? els.composerWrap.classes.add(name) : els.composerWrap.classes.delete(name); }}},
};
const $ = id => els[id] || null;
const t = (key, arg) => key === 'session_other_profile_read_only' ? `Read only: ${arg}'s session` : key;
let S = {session: null};
%s
function view(session) {
  S.session = session;
  syncReadOnlySessionView();
  return {banner: {hidden: els.sessionReadOnlyBanner.hidden, text: els.sessionReadOnlyBanner.textContent},
          msgDisabled: els.msg.disabled, answersHidden: els.composerWrap.classes.has('other-profile-read-only')};
}
const out = {
  other: view({read_only: true, read_only_reason: 'other_profile', owner_profile: '%s', owner_label: 'Bob (%s)'}),
  own: view({session_id: 'mine'}),
  imported: view({read_only: true}),
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_another_profiles_session_shows_the_banner_and_disables_the_composer():
    out = _run_node(VIEW_HARNESS % (_extract_function(UI_JS, "syncReadOnlySessionView"), BOB, BOB))

    assert out["other"] == {"banner": {"hidden": False, "text": f"Read only: Bob ({BOB})'s session"},
                            "msgDisabled": True, "answersHidden": True}
    # Leaving it re-enables the composer and hides the banner.
    assert out["own"] == {"banner": {"hidden": True, "text": ""}, "msgDisabled": False, "answersHidden": False}
    # Other read-only sessions keep today's behaviour (no banner, composer as before).
    assert out["imported"] == {"banner": {"hidden": True, "text": ""}, "msgDisabled": False, "answersHidden": False}


def test_pending_approvals_and_clarify_questions_are_not_answerable_there():
    style = (REPO / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.other-profile-read-only #approvalBtns[^{]*\{[^}]*\}", style)
    assert rule and "#clarifyChoices" in rule.group(0) and ".clarify-response" in rule.group(0)
    assert "display:none" in rule.group(0)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_another_profiles_session_is_never_forkable():
    script = "\n".join([
        _extract_function(SESSIONS_JS, "_isReadOnlySession"),
        _extract_function(SESSIONS_JS, "_isBranchableReadOnlySession"),
        "console.log(JSON.stringify({",
        "  cron: _isBranchableReadOnlySession({read_only: true, source_tag: 'cron'}),",
        "  otherCron: _isBranchableReadOnlySession({read_only: true, source_tag: 'cron', read_only_reason: 'other_profile'}),",
        "}));",
    ])
    assert _run_node(script) == {"cron": True, "otherCron": False}


def test_the_composer_has_a_read_only_banner():
    assert 'id="sessionReadOnlyBanner"' in INDEX_HTML
    banner = re.search(r'<div[^>]*id="sessionReadOnlyBanner"[^>]*>', INDEX_HTML).group(0)
    assert "hidden" in banner
    assert "syncReadOnlySessionView" in _extract_function(UI_JS, "syncTopbar")


def test_every_locale_has_the_banner_text():
    locales = re.findall(r"^  (?:'([\w-]+)'|(\w+)): \{$", I18N_JS, flags=re.M)
    assert len(locales) >= 15
    values = re.findall(r"^\s+session_other_profile_read_only: '([^']*)',$", I18N_JS, flags=re.M)
    assert len(values) == len(locales)
    assert all("{0}" in value for value in values)


def test_the_cron_detail_does_not_tell_a_caller_who_may_not_switch_to_switch():
    detail = _extract_function(PANELS_JS, "_renderCronDetail")
    assert "may_switch_profile" in detail
    assert "Only its owner can run or edit it." in detail


# ── The Admin's session list marks other Profiles' rows read-only ────────────

@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


def _session_with_a_message(client, text) -> str:
    from api.models import get_session

    status, body, _ = client.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    session = get_session(sid)
    session.messages = [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
    session.title = f"chat {text}"
    session.save()
    return sid


def test_the_admins_session_list_marks_other_profiles_rows_read_only(srv):
    bob_sid = _session_with_a_message(srv.logged_in(BOB), "bob")
    admin = srv.logged_in(ADMIN)
    own_sid = _session_with_a_message(admin, "admin")

    status, body, _ = admin.get("/api/sessions?all_profiles=1")

    assert status == 200, body
    rows = {row["session_id"]: row for row in body["sessions"]}
    assert rows[bob_sid]["read_only"] is True
    assert rows[bob_sid]["read_only_reason"] == "other_profile"
    assert rows[bob_sid]["owner_profile"] == BOB
    assert not rows[own_sid].get("read_only")


def test_a_users_session_list_is_unchanged(srv):
    alice = srv.logged_in(ALICE)
    sid = _session_with_a_message(alice, "alice")

    status, body, _ = alice.get("/api/sessions")

    row = next(row for row in body["sessions"] if row["session_id"] == sid)
    assert not row.get("read_only")
    assert "read_only_reason" not in row
