"""GFIT-CoWork: features that reached Upstream are gone (upstream-gone tickets 03, 04).

A Deployment is upgraded by rebuilding its image, so the update check and its
routes are gone. A removed route answers like any route the server does not
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


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(
        monkeypatch, tmp_path, users={ADMIN: "Admin", USER: "User"},
        profile_names=[USER], admins=ADMIN,
    ) as s:
        yield s


@pytest.mark.parametrize("method,path,body", REMOVED)
def test_removed_route_answers_the_admin_like_an_unknown_route(srv, method, path, body):
    admin = srv.logged_in(ADMIN)
    unknown = admin.request(method, UNKNOWN, body)[0]
    status, payload, _ = admin.request(method, path, body)
    assert status == unknown == 404, (method, path, payload)


@pytest.mark.parametrize("method,path,body", REMOVED)
def test_removed_route_answers_a_user_like_an_unknown_route(srv, method, path, body):
    user = srv.logged_in(USER)
    unknown = user.request(method, UNKNOWN, body)[0]
    status, payload, _ = user.request(method, path, body)
    assert status == unknown == 403, (method, path, payload)


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
