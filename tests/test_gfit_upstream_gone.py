"""GFIT-CoWork: features that reached Upstream are gone (upstream-gone tickets 03, 04).

A Deployment is upgraded by rebuilding its image, so the update check and its
routes are gone. The extension Gallery installed code from Upstream's registry,
so its routes are gone too, while extensions from the configured extension
folder keep working. A removed route answers like any route the server does not
know: 404 for the Admin, and the Admin gate's refusal for a User.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).

Never run these against code that still has the update routes: as the Admin,
``/api/updates/force`` runs ``git checkout``/``clean``/``reset --hard`` on the
checkout the server runs from.
"""
from __future__ import annotations

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ADMIN = "521740"
USER = "600001"
UNKNOWN = "/api/gfit-unclassified-probe"

REMOVED = [
    ("GET", "/api/updates/check", None),
    ("POST", "/api/updates/check", {"force": True}),
    ("POST", "/api/updates/apply", {"target": "webui"}),
    ("POST", "/api/updates/force", {"target": "webui"}),
    ("POST", "/api/updates/clear_lock", {"target": "webui"}),
    ("POST", "/api/updates/summary", {"updates": {}}),
]

GALLERY_REMOVED = [
    ("GET", "/api/extensions/registry", None),
    ("POST", "/api/extensions/install", {"id": "x", "download_url": "https://example.invalid/x.zip", "sha256": "0" * 64}),
    ("POST", "/api/extensions/uninstall", {"id": "x"}),
]

EXTENSION_ROUTES_KEPT = [
    ("GET", "/api/extensions/status", None),
    ("POST", "/api/extensions/toggle", {"id": "no-such-extension", "enabled": False}),
    ("POST", "/api/extensions/sidecar-proxy-consent", {"id": "no-such-extension", "approved": False}),
    ("GET", "/api/extensions/no-such-extension/sidecar/health", None),
]


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(
        monkeypatch, tmp_path, users={ADMIN: "Admin", USER: "User"},
        profile_names=[USER], admins=ADMIN,
    ) as s:
        yield s


@pytest.mark.parametrize("who,expected", [(ADMIN, 404), (USER, 403)])
@pytest.mark.parametrize("method,path,body", REMOVED + GALLERY_REMOVED)
def test_removed_route_answers_like_an_unknown_route(srv, who, expected, method, path, body):
    client = srv.logged_in(who)
    unknown_status, unknown_payload, _ = client.request(method, UNKNOWN, body)
    status, payload, _ = client.request(method, path, body)
    assert status == unknown_status == expected, (who, method, path, payload)
    assert payload == unknown_payload, (who, method, path, payload)


@pytest.mark.parametrize("method,path,body", EXTENSION_ROUTES_KEPT)
def test_extension_routes_for_the_extension_folder_still_answer_the_admin(srv, method, path, body):
    admin = srv.logged_in(ADMIN)
    unknown_payload = admin.request(method, UNKNOWN, body)[1]
    status, payload, _ = admin.request(method, path, body)
    assert status < 500, (method, path, payload)
    assert payload != unknown_payload, (method, path, payload)


def test_extension_status_no_longer_reports_gallery_installs(srv, monkeypatch, tmp_path):
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    monkeypatch.setenv("HERMES_WEBUI_EXTENSION_DIR", str(ext_dir))
    status, body, _ = srv.logged_in(ADMIN).get("/api/extensions/status")
    assert status == 200
    assert body["extension_dir_configured"] is True
    assert body["extension_dir_valid"] is True
    assert "gallery_installed" not in body


def test_settings_report_both_versions_and_no_update_settings(srv):
    status, body, _ = srv.logged_in(ADMIN).get("/api/settings")
    assert status == 200
    assert body["webui_version"]
    assert body["agent_version"]
    for gone in ("update_channel", "update_channel_version", "check_for_updates",
                 "ignore_agent_updates", "whats_new_summary_enabled"):
        assert gone not in body, gone


STALE_UPDATE_SETTINGS = {
    "check_for_updates": False,
    "update_channel": "experimental",
    "ignore_agent_updates": True,
    "whats_new_summary_enabled": True,
}


def test_a_settings_file_with_the_old_update_keys_loads_and_saves_without_them(monkeypatch, tmp_path):
    import json

    import api.config as config

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_path)
    settings_path.write_text(json.dumps({"send_key": "ctrl+enter", **STALE_UPDATE_SETTINGS}), encoding="utf-8")

    loaded = config.load_settings()
    assert loaded["send_key"] == "ctrl+enter"
    assert not set(STALE_UPDATE_SETTINGS) & set(loaded)

    config.save_settings({"send_key": "enter", **STALE_UPDATE_SETTINGS})
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["send_key"] == "enter"
    assert not set(STALE_UPDATE_SETTINGS) & set(on_disk)


def _slash_matches(prefix: str, agent_commands: list) -> list:
    """Run the real slash-command menu from static/commands.js under node."""
    import json
    import subprocess
    from pathlib import Path

    commands_js = (Path(__file__).resolve().parents[1] / "static" / "commands.js").read_text(encoding="utf-8")
    script = f"""
    const vm = require('vm');
    const ctx = {{
      console, window: {{}},
      localStorage: {{ getItem(){{return null;}}, setItem(){{}}, removeItem(){{}} }},
      t: key => key,
    }};
    vm.createContext(ctx);
    vm.runInContext({json.dumps(commands_js)}, ctx);
    vm.runInContext('_agentCommandCache = ' + {json.dumps(json.dumps(agent_commands))} + ';', ctx);
    const names = vm.runInContext('getMatchingCommands(' + {json.dumps(json.dumps(prefix))} + ').map(c => c.name)', ctx);
    process.stdout.write(JSON.stringify(names));
    """
    proc = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def test_the_slash_menu_offers_no_pet_command():
    agent = [{"name": "pet", "description": "Desktop Companion command", "cli_only": True}]
    assert "pet" not in _slash_matches("pe", agent)
    assert "pet" not in _slash_matches("pe", [])


def test_the_cron_gateway_notice_does_not_link_to_upstream():
    from pathlib import Path

    panels = (Path(__file__).resolve().parents[1] / "static" / "panels.js").read_text(encoding="utf-8")
    start = panels.index("function _cronGatewayNoticeHtml")
    notice = panels[start:panels.index("async function loadCronGatewayNotice", start)]
    assert "github.com" not in notice
