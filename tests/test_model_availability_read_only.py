"""Ticket 17 (availability): model discovery decides which providers are available without writing.

The Agent's ``list_available_providers()`` runs ``get_auth_status()`` for every
provider. For Nous that resolves runtime credentials (refresh, persist
auth.json, the shared Nous store), for Qwen it refreshes an expiring CLI token,
and for an OAuth-shaped plugin provider it reads through ``load_pool()``, which
seeds and persists. ``/api/models`` is a GET, so the WebUI builds the same list
from status checks that only read. Users may still see the Deployment's logins
(ticket 12); only the writes go.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

import api.config as config


class _Writes(Exception):
    """A status check that writes was reached."""


@pytest.fixture
def agent(monkeypatch):
    """Fake hermes_cli modules whose writing status checks raise _Writes."""
    calls: list[str] = []
    pkg = types.ModuleType("hermes_cli")
    pkg.__path__ = []
    models = types.ModuleType("hermes_cli.models")
    auth = types.ModuleType("hermes_cli.auth")
    auth_nous = types.ModuleType("hermes_cli.auth_nous")
    auth_qwen = types.ModuleType("hermes_cli.auth_qwen")
    plugins = types.ModuleType("hermes_cli.auth_plugin_providers")

    models.CANONICAL_PROVIDERS = [
        types.SimpleNamespace(slug=slug)
        for slug in ("nous", "qwen-oauth", "zai", "plugin-oauth", "plugin-key", "openrouter")
    ]

    def _list_available_providers():
        raise _Writes("list_available_providers runs every provider's writing status")

    models.list_available_providers = _list_available_providers
    models._provider_has_credentials = lambda pid: pid == "openrouter"

    state = {"qwen_expiry_ms": int(time.time() * 1000) + 3_600_000, "plugin_rows": []}

    def _get_auth_status(pid):
        calls.append(f"get_auth_status:{pid}")
        if pid in ("nous", "qwen-oauth", "plugin-oauth"):
            raise _Writes(f"get_auth_status({pid}) refreshes or seeds")
        return {"configured": pid == "zai", "key_source": "env"}

    auth.get_auth_status = _get_auth_status
    auth._registry_lookup = lambda pid: types.SimpleNamespace(
        auth_type={"plugin-oauth": "oauth_device_code", "plugin-key": "api_key"}.get(pid, "api_key"))
    auth._STATUS_BY_AUTH_TYPE = {
        "api_key": "get_api_key_provider_status",
        "oauth_device_code": "get_plugin_oauth_auth_status",
        "oauth_external": "get_plugin_oauth_auth_status",
    }

    def _read_credential_pool(pid):
        calls.append(f"read_credential_pool:{pid}")
        return list(state["plugin_rows"])

    auth.read_credential_pool = _read_credential_pool

    def _nous_local():
        calls.append("get_nous_auth_status_local")
        return {"logged_in": True, "source": "auth_store_local"}

    auth_nous.get_nous_auth_status_local = _nous_local

    def _resolve_qwen(*, refresh_if_expiring=True, force_refresh=False):
        calls.append(f"resolve_qwen:refresh_if_expiring={refresh_if_expiring}")
        if refresh_if_expiring or force_refresh:
            raise _Writes("a Qwen refresh rewrites the CLI token file")
        return {"source": "qwen-cli", "expires_at_ms": state["qwen_expiry_ms"]}

    auth_qwen.resolve_qwen_runtime_credentials = _resolve_qwen
    plugins.PLUGIN_MIRRORED_PROVIDERS = {"plugin-oauth", "plugin-key"}

    for name, module in {
        "hermes_cli": pkg, "hermes_cli.models": models, "hermes_cli.auth": auth,
        "hermes_cli.auth_nous": auth_nous, "hermes_cli.auth_qwen": auth_qwen,
        "hermes_cli.auth_plugin_providers": plugins,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(calls=calls, state=state)


def _available(rows):
    return sorted(row["id"] for row in rows if row["authenticated"])


def test_availability_never_runs_a_status_check_that_writes(agent):
    rows = config._read_only_available_providers()

    assert [row["id"] for row in rows] == [
        "nous", "qwen-oauth", "zai", "plugin-oauth", "plugin-key", "openrouter", "custom"]
    # plugin-oauth has no live pool row: registered (configured) counts as available, as in the Agent.
    assert _available(rows) == ["nous", "openrouter", "plugin-oauth", "qwen-oauth", "zai"]
    assert "get_nous_auth_status_local" in agent.calls
    assert "resolve_qwen:refresh_if_expiring=False" in agent.calls
    assert "read_credential_pool:plugin-oauth" in agent.calls
    assert not {"get_auth_status:nous", "get_auth_status:qwen-oauth", "get_auth_status:plugin-oauth"} & set(agent.calls)


def test_an_expired_qwen_token_is_unavailable_and_not_refreshed(agent):
    agent.state["qwen_expiry_ms"] = int(time.time() * 1000) - 1

    assert config._read_only_auth_status("qwen-oauth")["logged_in"] is False
    assert agent.calls == ["resolve_qwen:refresh_if_expiring=False"]


def test_a_plugin_oauth_login_is_read_from_the_pool_without_seeding(agent):
    agent.state["plugin_rows"] = [
        {"access_token": "expired", "expires_at_ms": int(time.time() * 1000) - 1},
        {"access_token": "live", "expires_at_ms": int(time.time() * 1000) + 60_000},
    ]
    assert config._read_only_auth_status("plugin-oauth") == {"configured": True, "logged_in": True}

    agent.state["plugin_rows"] = [{"access_token": "expired", "expires_at_ms": 1}]
    assert config._read_only_auth_status("plugin-oauth")["logged_in"] is False


def test_get_available_models_uses_the_read_only_list(agent, monkeypatch):
    """/api/models' provider detection goes through the read-only list and status, never the Agent's."""
    monkeypatch.setattr(config, "_read_only_auth_status", _recording(config._read_only_auth_status, agent.calls))
    config.invalidate_models_cache()
    try:
        config.get_available_models()
    finally:
        config.invalidate_models_cache()

    assert "get_nous_auth_status_local" in agent.calls  # the #1567 Nous double-check, read-only
    assert not {"get_auth_status:nous", "get_auth_status:qwen-oauth", "get_auth_status:plugin-oauth"} & set(agent.calls)


def _recording(fn, calls):
    def wrapper(pid):
        calls.append(f"status:{pid}")
        return fn(pid)
    return wrapper


def test_a_missing_agent_binding_fails_the_list_not_each_provider(agent, monkeypatch):
    monkeypatch.delitem(sys.modules, "hermes_cli.auth_nous")
    monkeypatch.setitem(sys.modules, "hermes_cli.auth_nous", types.ModuleType("hermes_cli.auth_nous"))

    with pytest.raises((ImportError, AttributeError)):
        config._read_only_available_providers()
