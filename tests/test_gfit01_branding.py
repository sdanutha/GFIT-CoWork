"""GFIT-CoWork ticket 01: every User sees GFIT-CoWork, not Hermes WebUI.

Tests go through the served pages (the HTTP seam) where possible. Text that
names Hermes Agent itself keeps the Hermes name (CONTEXT.md, ADR 0001).
"""
import json
import pathlib
import re
import tomllib
import urllib.request

from tests.conftest import TEST_BASE

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = "GFIT-CoWork"


def _get(path):
    with urllib.request.urlopen(TEST_BASE + path, timeout=10) as r:
        return r.read().decode("utf-8")


def test_index_shell_tab_title_and_titlebar_say_gfit_cowork():
    html = _get("/")
    assert f"<title>{BRAND}</title>" in html
    assert f'<meta name="apple-mobile-web-app-title" content="{BRAND}">' in html
    assert f'id="appTitlebarTitle">{BRAND}</span>' in html


def test_index_shell_logos_are_gfit_cowork_marks():
    html = _get("/")
    assert f'aria-label="{BRAND} logo"' in html
    assert "Hermes caduceus" not in html


def test_manifest_names_gfit_cowork():
    data = json.loads(_get("/manifest.json"))
    assert data["name"] == BRAND
    assert data["short_name"] == BRAND
    desc = data.get("description", "")
    assert "Web UI" not in desc and "WebUI" not in desc


def test_login_page_says_gfit_cowork():
    html = _get("/login")
    assert f"<title>{BRAND} " in html
    assert f"<h1>{BRAND}</h1>" in html


def test_share_page_says_gfit_cowork():
    html = _get("/share/does-not-matter")
    assert f"<title>Shared Conversation - {BRAND}</title>" in html
    assert "Hermes WebUI" not in html


def test_favicon_svg_is_gfit_cowork_mark():
    for name in ("favicon.svg", "favicon-512.svg"):
        svg = _get(f"/static/{name}")
        assert f"{BRAND} logo" in svg, name


def test_runtime_tab_title_uses_app_name_not_assistant_name():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert f"const APP_NAME='{BRAND}';" in ui
    for src in ("ui.js", "boot.js", "panels.js"):
        js = (ROOT / "static" / src).read_text(encoding="utf-8")
        for line in re.findall(r"document\.title\s*=[^;]*;", js):
            assert "assistantDisplayName" not in line, (src, line)


def test_english_locale_names_the_web_app_gfit_cowork():
    i18n = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
    en = i18n.split("\n  it: {", 1)[0]
    assert "Hermes WebUI" not in en and "Hermes Web UI" not in en
    assert f"onboarding_title: 'Welcome to {BRAND}'" in en
    # Hermes Agent keeps its own name.
    assert "Hermes Agent" in en or "Hermes gateway" in en


def test_package_and_project_name_is_gfit_cowork():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "gfit-cowork"
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"].startswith("gfit-cowork")


def test_readme_introduces_gfit_cowork_as_fork_of_hermes_webui():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    head = readme[:2000]
    assert f"# {BRAND}" in head
    assert "hermes-webui" in head and "MIT" in head


def test_license_keeps_original_copyright():
    lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic
    assert "Copyright" in lic
