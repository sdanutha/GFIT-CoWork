from pathlib import Path
import re
import shutil

import pytest
try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except Exception:  # pragma: no cover - dependency optional
    PlaywrightError = None
    sync_playwright = None


REPO = Path(__file__).parent.parent
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
PANEL_JS = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
BOOT_JS = (REPO / "static" / "boot.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _iter_root_skin_blocks(css):
    selector_re = re.compile(r'(:root(?:\.dark)?\[data-skin="[^"]+"\][^{]*?)\{')
    for selector_match in selector_re.finditer(css):
        selector = selector_match.group(1).strip()
        idx = selector_match.end()
        depth = 1
        end = idx
        while end < len(css) and depth:
            ch = css[end]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            end += 1
        if depth:
            continue
        yield selector, css[idx : end - 1]


def _get_root_skin_block(css, skin):
    target = f':root[data-skin="{skin}"]'
    for selector, block in _iter_root_skin_blocks(css):
        if selector == target:
            return block
    return ""


def _font_stack_offenders(css):
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    pattern = re.compile(r"(?i)\b(font-family|font)\s*:\s*([^;]+);")
    offenders = []
    for _, declaration in pattern.findall(cleaned):
        value = declaration.strip().lower()
        if ("monospace" not in value and "ui-monospace" not in value):
            continue
        if value == "inherit":
            continue
        offenders.append(declaration)
    return offenders


def test_typography_root_tokens_are_defined():
    assert '--font-ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;' in CSS
    assert '--font-conversation:var(--font-ui);' in CSS
    assert '--font-mono:ui-monospace,"SFMono-Regular","SF Mono",Menlo,Consolas,"Liberation Mono",monospace;' in CSS


def test_msg_body_uses_conversation_font():
    assert '.msg-body{font-family:var(--font-conversation);font-size:var(--message-body-font-size);line-height:var(--message-body-line-height);' in CSS


def test_builtin_skin_msg_body_rules_use_conversation_font():
    for skin in ("graphite", "codex", "terracotta", "github"):
        selector = (
            f':root[data-skin="{skin}"] .msg-body'
            '{font-family:var(--font-conversation);font-size:13px;font-weight:430;letter-spacing:0;line-height:1.6;}'
        )
        assert selector in CSS


def test_no_skin_msg_body_rules_use_root_font_ui_token():
    assert not re.search(
        r':root(?:\.dark)?\[data-skin="[^"]+"\]\s*\.msg-body\s*\{[^{}]*\bfont-family\s*:\s*var\(--font-ui\)\s*;[^{}]*\}',
        CSS,
        re.S,
    )


def test_no_skin_redefines_font_conversation():
    for selector, block in _iter_root_skin_blocks(CSS):
        if '--font-conversation:' in block:
            raise AssertionError(f'Unexpected skin-level --font-conversation: {selector}')


def test_nous_skin_keeps_monospace_ui_default():
    assert _get_root_skin_block(CSS, "nous")
    assert re.search(
        r'--font-ui:"SF Mono","Roboto Mono","Courier New",monospace;',
        _get_root_skin_block(CSS, "nous"),
        re.S,
    )


def test_geist_and_neon_skins_set_expected_font_ui_tokens():
    geist_block = _get_root_skin_block(CSS, "geist-contrast")
    assert geist_block
    assert "--font-ui:\"Geist\",\"Geist Sans\",-apple-system,BlinkMacSystemFont,\"Segoe UI\",Helvetica,Arial,sans-serif;" in geist_block
    for skin in ("neon", "neon-soft", "neon-paint"):
        block = _get_root_skin_block(CSS, skin)
        assert block, f"Missing {skin} root skin block"
        assert "--font-ui:system-ui,-apple-system,sans-serif;" in block


def test_no_stale_var_mono_usage_or_literal_monospace_font_family_stacks():
    assert 'var(--mono' not in CSS
    offending_lines = _font_stack_offenders(CSS)
    assert not offending_lines, f"Unexpected literal monospace font stacks: {offending_lines[:4]}"


def test_edit_surfaces_use_font_contract_tokens_structurally():
    assert re.search(
        r"\.msg-edit-area\{[^}]*font-family:var\(--font-conversation\)",
        CSS,
        re.S,
    ), ".msg-edit-area must use var(--font-conversation)"
    assert re.search(
        r'<textarea id="previewEditArea"[^>]*style="[^"]*font-family:var\(--font-mono\)',
        INDEX_HTML,
    ), "#previewEditArea must use var(--font-mono)"


def test_playwright_regression_ensures_font_contract_for_syntax_and_edit_surfaces():
    preview_style_match = re.search(
        r'<textarea id="previewEditArea"[^>]*style="([^"]*)"',
        INDEX_HTML,
    )
    assert preview_style_match, "Could not locate #previewEditArea inline style in index.html"

    fixture_html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>{CSS}</style>
  <style>
    :root{{
      --font-ui:"UiFontSentinel";
      --font-conversation:"ConvoFontSentinel";
      --font-mono:"MonoFontSentinel";
    }}
  </style>
  <style>
    code[class*="language-"],pre[class*="language-"]{{font-family: "PrismIntruder", sans-serif;}}
  </style>
</head>
<body>
  <button id="buttonSentinel">button</button>
  <input id="inputSentinel">
  <select id="selectSentinel"><option>select</option></select>
  <textarea id="textareaSentinel"></textarea>
  <div class="msg-body">
    <p>inline <code id="inlineCode" class="language-js">token</code> sample.</p>
    <pre id="fencedPre" class="language-js"><code id="fencedCode" class="language-js">fenced token</code></pre>
    <input class="markdown-table-filter" id="markdownTableFilter">
    <table>
      <thead><tr><th><button class="markdown-table-sort" id="markdownTableSort">sort</button></th></tr></thead>
      <tbody><tr><td id="markdownTableCell">row</td></tr></tbody>
    </table>
  </div>
  <textarea class="msg-edit-area" id="msgEditArea"></textarea>
  <textarea id="previewEditArea" style="{preview_style_match.group(1)}"></textarea>
  <div class="detail-form-row">
    <textarea id="detailFormTextarea"></textarea>
  </div>
</body>
</html>
"""

    if sync_playwright is None:
        pytest.skip("playwright is unavailable; run `playwright install chromium`")

    with sync_playwright() as playwright:
        browser = None
        try:
            try:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except PlaywrightError as exc:
                if "Executable doesn't exist at" in str(exc):
                    pytest.skip("playwright chromium executable is unavailable; run `playwright install chromium`")
                raise
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(fixture_html)

            inline_code_font = page.eval_on_selector(
                "#inlineCode",
                "el => getComputedStyle(el).fontFamily",
            )
            fenced_pre_font = page.eval_on_selector(
                "#fencedPre",
                "el => getComputedStyle(el).fontFamily",
            )
            fenced_code_font = page.eval_on_selector(
                "#fencedCode",
                "el => getComputedStyle(el).fontFamily",
            )
            message_edit_font = page.eval_on_selector(
                "#msgEditArea",
                "el => getComputedStyle(el).fontFamily",
            )
            preview_edit_font = page.eval_on_selector(
                "#previewEditArea",
                "el => getComputedStyle(el).fontFamily",
            )
            component_fonts = page.evaluate(
                """
                () => Object.fromEntries(
                  ["markdownTableFilter", "markdownTableSort", "markdownTableCell", "detailFormTextarea"]
                    .map(id => [id, getComputedStyle(document.getElementById(id)).fontFamily])
                )
                """
            )
            native_control_fonts = page.evaluate(
                """
                () => Object.fromEntries(
                  ["buttonSentinel", "inputSentinel", "selectSentinel", "textareaSentinel"]
                    .map(id => [id, getComputedStyle(document.getElementById(id)).fontFamily])
                )
                """
            )
            runtime_token_font = page.evaluate("""
                () => {
                  const style = document.createElement('style');
                  style.id = 'runtime-font-sentinel';
                  style.textContent = ':root { --font-mono: "RuntimeMono", monospace; }';
                  document.head.appendChild(style);
                  return getComputedStyle(document.documentElement).getPropertyValue('--font-mono');
                }
            """)
            edited_runtime_token_font = page.evaluate("""
                () => {
                  const style = document.getElementById('runtime-font-sentinel');
                  style.textContent = ':root { --font-mono: "EditedRuntimeMono", monospace; }';
                  return getComputedStyle(document.documentElement).getPropertyValue('--font-mono');
                }
            """)
            linked_stylesheet_font = page.evaluate("""
                async () => {
                  const link = document.createElement('link');
                  const encodedCss = encodeURIComponent(':root { --font-mono: "LinkedRuntimeMono", monospace; }');
                  link.rel = 'stylesheet';
                  link.href = 'data:text/css,' + encodedCss;
                  return await new Promise((resolve) => {
                    link.onload = () => {
                      resolve(getComputedStyle(document.documentElement).getPropertyValue('--font-mono'));
                    };
                    link.onerror = () => {
                      resolve(getComputedStyle(document.documentElement).getPropertyValue('--font-mono'));
                    };
                    document.head.appendChild(link);
                  });
                }
            """)
        finally:
            if browser is not None:
                browser.close()

    assert "MonoFontSentinel" in inline_code_font
    assert "MonoFontSentinel" in fenced_pre_font
    assert "MonoFontSentinel" in fenced_code_font
    assert "ConvoFontSentinel" in message_edit_font
    assert "MonoFontSentinel" in preview_edit_font
    for control_id, font in native_control_fonts.items():
        assert "UiFontSentinel" in font, f"{control_id} computed {font!r}"
    assert "UiFontSentinel" in component_fonts["markdownTableFilter"]
    assert "MonoFontSentinel" in component_fonts["markdownTableSort"]
    assert "MonoFontSentinel" in component_fonts["markdownTableCell"]
    assert component_fonts["markdownTableSort"] == component_fonts["markdownTableCell"]
    assert "MonoFontSentinel" in component_fonts["detailFormTextarea"]
    assert "RuntimeMono" in runtime_token_font
    assert "EditedRuntimeMono" in edited_runtime_token_font
    assert "LinkedRuntimeMono" in linked_stylesheet_font



def test_first_party_technical_js_uses_font_mono_contract():
    assert 'font-family:var(--font-mono)' in PANEL_JS
    assert "font-family:var(--font-ui)" in BOOT_JS
