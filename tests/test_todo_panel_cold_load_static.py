import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent


def test_ensure_messages_loaded_hydrates_session_todo_state_sidecar():
    src = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "if(data.session.todo_state !== undefined)" in src
    assert "S.session.todo_state = data.session.todo_state" in src
    assert "delete S.session.todo_state" in src
    assert "_hydrateTodosFromSession(S.session)" in src
    assert "scheduleTodosRefresh()" in src


def test_load_todos_renders_single_source_of_truth_before_legacy_scan():
    src = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
    start = src.find("function loadTodos()")
    end = src.find("function _legacyTodosFromMessages()")

    assert start != -1
    assert end != -1
    load_todos = src[start:end]

    assert "if (S.todoStateMeta)" in load_todos
    assert "todos = Array.isArray(S.todos) ? S.todos : [];" in load_todos
    assert "todos = _legacyTodosFromMessages();" in load_todos
    assert load_todos.find("todos = Array.isArray(S.todos) ? S.todos : [];") < load_todos.find("todos = _legacyTodosFromMessages();")


def test_legacy_todos_fallback_still_uses_raw_session_messages():
    src = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")

    assert "function _legacyTodosFromMessages()" in src
    assert "const sourceMessages = (S.session && Array.isArray(S.session.messages) && S.session.messages.length) ? S.session.messages : S.messages;" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for shared todo renderer behavior test")
def test_shared_todo_renderer_outputs_consistent_status_markup(tmp_path):
    ui = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    start = ui.find("const TODO_STATUS_RENDERING=Object.freeze({")
    end = ui.find("function _todosPanelIsActive()", start)

    assert start != -1
    assert end != -1
    helper = ui[start:end]
    script = f"""
const helper = {helper!r};
const liCalls = [];
function li(name, size) {{
  liCalls.push([name, size]);
  return `<svg data-icon="${{name}}" data-size="${{size}}"></svg>`;
}}
function esc(value) {{
  return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function t(key) {{ return key === 'todos_no_active' ? 'No active task list in this session.' : key; }}
const api = new Function('li', 'esc', 't', helper + '; return {{TODO_STATUS_RENDERING, todoStatusKey, renderTodoRow, renderTodoRows, renderTodoEmptyState}};')(li, esc, t);
function assert(cond, msg) {{ if(!cond) throw new Error(msg); }}
const expected = {{
  pending: ['square', 'var(--muted)'],
  in_progress: ['loader', 'var(--blue)'],
  completed: ['check', 'rgba(100,200,100,.8)'],
  cancelled: ['x', 'rgba(200,100,100,.5)'],
}};
for (const [status, [icon, color]] of Object.entries(expected)) {{
  assert(api.TODO_STATUS_RENDERING[status].icon === icon, status + ' icon mismatch');
  assert(api.TODO_STATUS_RENDERING[status].color === color, status + ' color mismatch');
  const row = api.renderTodoRow({{id: status + '-id', content: status + ' content', status}}, {{metadata: true}});
  assert(row.includes(`data-icon="${{icon}}"`), status + ' row icon mismatch');
  assert(row.includes(color), status + ' row color mismatch');
  assert(row.includes(`${{status}}-id · ${{status}}`), status + ' metadata mismatch');
}}
assert(api.todoStatusKey('unknown') === 'pending', 'unknown statuses fall back to pending');
assert(api.renderTodoRows([{{id:'a', content:'A', status:'pending'}}, {{id:'b', text:'B', status:'completed'}}], {{metadata:true}}).includes('b · completed'), 'shared rows include metadata');
assert(api.renderTodoEmptyState({{centered:true}}).includes('No active task list in this session.'), 'empty state uses i18n text');
"""
    script_path = tmp_path / "shared_todo_renderer_test.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
