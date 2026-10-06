"""Regression tests for #5682 profile query boot switching."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
SESSIONS_JS_PATH = REPO_ROOT / "static" / "sessions.js"
BOOT_JS_PATH = REPO_ROOT / "static" / "boot.js"
PANELS_JS_PATH = REPO_ROOT / "static" / "panels.js"
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
SESSIONS_JS = SESSIONS_JS_PATH.read_text(encoding="utf-8")
BOOT_JS = BOOT_JS_PATH.read_text(encoding="utf-8")
PANELS_JS = PANELS_JS_PATH.read_text(encoding="utf-8")
UI_JS = UI_JS_PATH.read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(source: str) -> str:
    result = subprocess.run(
        [NODE],
        input=source,
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def _node_prelude() -> str:
    return f"""
const sessionsSrc = {SESSIONS_JS!r};
const bootSrc = {BOOT_JS!r};
function extractFunc(src, name) {{
  const re = new RegExp('(?:async\\\\s+)?function\\\\s+' + name + '\\\\s*\\\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {{
    if (src[i] === '{{') depth++;
    else if (src[i] === '}}') depth--;
    i++;
  }}
  return src.slice(start, i);
}}
function evalSession(name) {{
  globalThis[name] = (0, eval)('(' + extractFunc(sessionsSrc, name) + ')');
}}
function evalBoot(name) {{
  globalThis[name] = (0, eval)('(' + extractFunc(bootSrc, name) + ')');
}}
"""


def test_prefill_cleanup_still_strips_q_prompt_and_send():
    source = _node_prelude() + """
function applyUrl(rel) {
  const next = new URL(rel, 'https://example.test');
  window.location.href = next.href;
  window.location.pathname = next.pathname;
  window.location.search = next.search;
  window.location.hash = next.hash;
}
global.window = {
  location: {},
  history: {
    state: null,
    calls: [],
    replaceState(state, title, url) {
      this.calls.push({ state, title, url });
      this.state = state;
      applyUrl(url);
    }
  }
};
global.document = { baseURI: 'https://example.test/app/' };
applyUrl('/app/?q=hello&prompt=hi&send=1&keep=1#frag');
evalSession('_consumeComposerPrefillParamsFromLocation');
_consumeComposerPrefillParamsFromLocation();
console.log(JSON.stringify({
  url: window.location.pathname + window.location.search + window.location.hash,
  historyCalls: window.history.calls,
}));
"""
    payload = json.loads(_run_node(source))
    assert payload["url"] == "/app/?keep=1#frag"
    assert payload["historyCalls"] == [{"state": None, "title": "", "url": "/app/?keep=1#frag"}]
