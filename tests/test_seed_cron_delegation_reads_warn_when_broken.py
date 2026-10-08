"""Ticket 15, Class B group 3: seed skills, recent cron completions and delegation retry warn when broken.

Each degraded to "absent" on any ImportError around both the Agent import and
the call, so an ImportError raised inside the Agent read as "not installed":
skills silently not seeded, no cron toasts, a delegation retry never armed.
Now the import alone decides "absent" (quiet, as before); a broken import or a
failing call is warned, keeping the same degraded result.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import api.profiles as profiles_mod
import api.routes as routes
from api import process_event_utils

BROKEN = ModuleNotFoundError("No module named 'ruamel'", name="ruamel")


def _broken_module(name):
    """A module whose names fail to import the way a broken Agent's do (PEP 562 __getattr__)."""
    module = types.ModuleType(name)

    def __getattr__(attr):
        raise BROKEN

    module.__getattr__ = __getattr__
    return module


def _package(monkeypatch, name):
    pkg = types.ModuleType(name)
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, name, pkg)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ── seed_profile_skills ─────────────────────────────────────────────────────


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    monkeypatch.setattr(profiles_mod, "_DEFAULT_HERMES_HOME", home)
    return home


def _create(monkeypatch, module):
    _package(monkeypatch, "hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", module)
    with patch.object(profiles_mod, "list_profiles_api", return_value=[]):
        return profiles_mod.create_profile_api("seeded")


def test_a_broken_agent_is_warned_when_skills_are_not_seeded(profile_home, monkeypatch, caplog):
    module = _broken_module("hermes_cli.profiles")
    module.create_profile = lambda name, **kw: (profile_home / "profiles" / name).mkdir(parents=True)
    assert _create(monkeypatch, module)["name"] == "seeded"
    warnings = _warnings(caplog)
    assert warnings and "ruamel" in warnings[0] and "seeded" in warnings[0]


def test_an_older_agent_without_the_seeder_stays_quiet(profile_home, monkeypatch, caplog):
    module = types.ModuleType("hermes_cli.profiles")
    module.create_profile = lambda name, **kw: (profile_home / "profiles" / name).mkdir(parents=True)
    assert _create(monkeypatch, module)["name"] == "seeded"
    assert _warnings(caplog) == []


# ── /api/crons/recent ───────────────────────────────────────────────────────


class _JSONHandler:
    def __init__(self):
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


def _recent():
    handler = _JSONHandler()
    routes._handle_cron_recent(handler, SimpleNamespace(query="since=0"))
    return handler.status, json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_no_cron_module_reports_no_completions_quietly(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "cron", None)
    monkeypatch.setitem(sys.modules, "cron.jobs", None)
    assert _recent() == (200, {"completions": [], "since": 0.0})
    assert _warnings(caplog) == []


def test_a_broken_cron_module_is_warned(monkeypatch, caplog):
    _package(monkeypatch, "cron")
    monkeypatch.setitem(sys.modules, "cron.jobs", _broken_module("cron.jobs"))
    assert _recent() == (200, {"completions": [], "since": 0.0})
    assert any("ruamel" in m for m in _warnings(caplog))


def test_an_import_error_inside_list_jobs_is_warned(monkeypatch, caplog):
    _package(monkeypatch, "cron")
    jobs = types.ModuleType("cron.jobs")

    def list_jobs(include_disabled=True):
        raise BROKEN

    jobs.list_jobs = list_jobs
    monkeypatch.setitem(sys.modules, "cron.jobs", jobs)
    assert _recent() == (200, {"completions": [], "since": 0.0})
    assert any("ruamel" in m for m in _warnings(caplog))


# ── async delegation retry ──────────────────────────────────────────────────


EVENT = {"type": "async_delegation", "delegation_id": "d-1"}


def _schedule():
    return process_event_utils.schedule_async_delegation_claim_retry(EVENT, object(), delay=0)


def test_no_delegation_module_is_quiet(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "tools", None)
    monkeypatch.setitem(sys.modules, "tools.async_delegation", None)
    assert _schedule() is False
    assert _warnings(caplog) == []


def test_an_older_agent_without_the_reader_is_quiet(monkeypatch, caplog):
    _package(monkeypatch, "tools")
    monkeypatch.setitem(sys.modules, "tools.async_delegation", types.ModuleType("tools.async_delegation"))
    assert _schedule() is False
    assert _warnings(caplog) == []


def test_a_broken_delegation_module_is_warned(monkeypatch, caplog):
    _package(monkeypatch, "tools")
    monkeypatch.setitem(sys.modules, "tools.async_delegation", _broken_module("tools.async_delegation"))
    assert _schedule() is False
    assert any("ruamel" in m and "d-1" in m for m in _warnings(caplog))


def test_a_failing_delegation_read_is_warned_without_its_text(monkeypatch, caplog):
    _package(monkeypatch, "tools")
    module = types.ModuleType("tools.async_delegation")

    def get_durable_delegation(_id):
        raise RuntimeError("secret-text")

    module.get_durable_delegation = get_durable_delegation
    monkeypatch.setitem(sys.modules, "tools.async_delegation", module)
    assert _schedule() is False
    assert any("RuntimeError" in m for m in _warnings(caplog))
    assert "secret-text" not in caplog.text
