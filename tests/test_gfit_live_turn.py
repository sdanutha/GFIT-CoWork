"""GFIT-CoWork: one Stop request and one speech request in the web app.

Both Stop paths (composer and sidebar) send the cancel request through
``requestStreamCancel`` (via ``api()``, once), and every read-aloud caller
through ``requestSpeech``. These tests run the real functions from
``static/ui.js`` in node against a stand-in browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.js_source_extract import extract_function

ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _run(body: str) -> dict:
    functions = "\n".join(
        extract_function(UI_JS, name, prefix=prefix)
        for prefix, name in (
            ("async function", "requestStreamCancel"),
            ("function", "requestSpeech"),
        )
    )
    script = f"""
global.S = {{activeStreamId: null}};
global.document = {{baseURI: 'http://app.test/base/'}};
global.location = {{href: 'http://app.test/base/'}};
const calls = [];
{functions}
(async () => {{
{body}
}})().then(result => console.log(JSON.stringify(result)));
"""
    out = subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def test_stop_is_one_request_through_api_with_no_retries():
    result = _run("""
global.api = async (path, opts) => { calls.push({path, opts}); return {ok: true, cancelled: true}; };
const answer = await requestStreamCancel('stream 1');
return {answer, calls};
""")
    assert result["answer"] == {"ok": True, "body": {"ok": True, "cancelled": True}, "error": None}
    assert len(result["calls"]) == 1
    assert result["calls"][0]["path"] == "api/chat/cancel?stream_id=stream%201"
    assert result["calls"][0]["opts"]["retries"] == 0


def test_a_failed_or_redirected_stop_is_not_ok():
    result = _run("""
global.api = async () => { throw new Error('Session not found'); };
const failed = await requestStreamCancel('s');
global.api = async () => undefined;
const redirected = await requestStreamCancel('s');
return {failed: {ok: failed.ok, error: String(failed.error && failed.error.message)}, redirected};
""")
    assert result == {
        "failed": {"ok": False, "error": "Session not found"},
        "redirected": {"ok": False, "body": None, "error": None},
    }


def test_speech_is_one_post_of_the_callers_payload():
    result = _run("""
global.fetch = (url, opts) => { calls.push({url, opts}); return Promise.resolve('response'); };
const answer = await requestSpeech({text: 'hi', engine: 'edge'});
return {answer, calls};
""")
    assert result["answer"] == "response"
    assert result["calls"] == [{
        "url": "http://app.test/base/api/tts",
        "opts": {"method": "POST", "headers": {"Content-Type": "application/json"},
                 "body": json.dumps({"text": "hi", "engine": "edge"}, separators=(",", ":"))},
    }]
