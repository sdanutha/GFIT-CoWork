from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS  = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS    = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
STYLE_CSS  = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

LOCALE_COUNT = 15  # en, it, ja, ru, es, de, zh, zh-Hant, pt, ko, fr, tr, pl, vi, cs


def test_help_nav_button_present():
    assert 'data-settings-section="help"' in INDEX_HTML
    assert "switchSettingsSection('help',{fromSidebarItem:true})" in INDEX_HTML
    assert 'data-i18n="settings_tab_help"' in INDEX_HTML


def _help_pane() -> str:
    start = INDEX_HTML.index('id="settingsPaneHelp"')
    return INDEX_HTML[start:INDEX_HTML.index('</main>', start)]


def test_help_pane_present():
    assert 'id="settingsPaneHelp"' in INDEX_HTML


def test_help_pane_sends_no_one_to_upstream():
    """GFIT-CoWork: problems go to the Deployment's Admin, not to Upstream's GitHub or site."""
    pane = _help_pane()
    assert "<a " not in pane
    assert "get-hermes.ai" not in pane
    assert "github.com" not in pane
    assert 'data-i18n="settings_help_issue_label"' in pane
    assert 'data-i18n="settings_help_issue_desc"' in pane
    assert "Admin" in pane


def test_panels_js_allowlist_includes_help():
    assert "name==='help'" in PANELS_JS


def test_panels_js_map_includes_help():
    assert "help:'Help'" in PANELS_JS


def test_panels_js_foreach_includes_help():
    assert "'help'" in PANELS_JS
    assert "settingsPaneHelp" not in PANELS_JS or 'settingsPane'+'{map[key]}' not in PANELS_JS
    # Simpler: confirm the forEach array string contains help
    assert ",'help'," in PANELS_JS or ",'help']" in PANELS_JS


def test_i18n_help_keys_present_in_all_locales():
    assert I18N_JS.count("settings_tab_help") == LOCALE_COUNT
    assert I18N_JS.count("settings_help_issue_label") == LOCALE_COUNT
    assert I18N_JS.count("settings_help_issue_desc") == LOCALE_COUNT
    for gone in ("settings_help_docs_label", "settings_help_docs_desc",
                 "settings_help_docs_link", "settings_help_issue_link"):
        assert gone not in I18N_JS, gone
    assert "open a new one on GitHub" not in I18N_JS
