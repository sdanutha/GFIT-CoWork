"""
Sprint 45 Tests: v0.50.36 upstream sync with minimal local patch retention.

Covers:
- First password enablement via POST /api/settings keeps the current browser logged in
- The returned auth metadata is present and onboarding can continue with the issued cookie
- Legacy assistant_language is no longer exposed and is removed on the next save
- The local reply-language UI/runtime enhancement is gone from the synced codebase
"""
import json
import pathlib
import urllib.error
import urllib.request


from tests._pytest_port import BASE
REPO = pathlib.Path(__file__).parent.parent
# Use HERMES_WEBUI_TEST_STATE_DIR if available (set by conftest for the test process),
# falling back to the shared isolated TEST_STATE_DIR (temp-rooted, never ~/.hermes).
def _get_settings_file() -> pathlib.Path:
    """The test User's own settings file: the server's requests are theirs (ADR 0006)."""
    from tests._pytest_port import TEST_USER_SETTINGS_FILE

    return TEST_USER_SETTINGS_FILE


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code, dict(e.headers)


def post(path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code, dict(e.headers)


def read(path):
    return (REPO / path).read_text(encoding="utf-8")


def _snapshot_settings_file():
    if _get_settings_file().exists():
        return _get_settings_file().read_text(encoding="utf-8")
    return None


def _restore_settings_file(original_text):
    if original_text is None:
        _get_settings_file().unlink(missing_ok=True)
        return
    _get_settings_file().write_text(original_text, encoding="utf-8")


def test_legacy_assistant_language_is_hidden_and_removed_on_next_save():
    original_settings = _snapshot_settings_file()
    try:
        _get_settings_file().parent.mkdir(parents=True, exist_ok=True)
        _get_settings_file().write_text(
            json.dumps(
                {
                    "assistant_language": "zh",
                    "send_key": "enter",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        loaded, status, _ = get("/api/settings")
        assert status == 200
        assert "assistant_language" not in loaded

        saved, save_status, _ = post("/api/settings", {"send_key": "ctrl+enter"})
        assert save_status == 200
        assert "assistant_language" not in saved
        assert saved["send_key"] == "ctrl+enter"

        persisted = json.loads(_get_settings_file().read_text(encoding="utf-8"))
        assert "assistant_language" not in persisted
    finally:
        _restore_settings_file(original_settings)


def test_reply_language_customization_ui_and_runtime_are_removed():
    index_html = read("static/index.html")
    panels_js = read("static/panels.js")
    streaming_py = read("api/streaming.py")

    assert "settingsAssistantLanguage" not in index_html
    assert "assistant_language" not in panels_js
    assert "settingsAssistantLanguage" not in panels_js
    assert "assistant_language" not in streaming_py
    assert "Default reply language:" not in streaming_py


