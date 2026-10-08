"""The Nous and Copilot model catalogs are read-only and Profile-scoped (ticket 17).

Model discovery (``/api/models/live`` and the ``/api/models`` live rebuild)
asks the Agent's ``provider_model_ids()`` for a provider's catalog. For two
providers that call resolves credentials with side effects:

- Nous: ``resolve_nous_runtime_credentials()`` refreshes the OAuth grant (a
  single-use refresh token), quarantines it on a rejected refresh, persists the
  Profile's (or, through the root fallback, the Deployment's) auth.json, and
  copies the grant into the Deployment-wide ``shared/nous_auth.json``.
- Copilot: the GitHub token comes from the environment or the host ``gh`` CLI,
  then the pool with the root-Profile fallback, then the host's
  ``~/.copilot/config.json``: the Deployment's logins, for any User.

For these, discovery uses only the request Profile's own credentials, read from
its own auth.json and never written: Nous lists the live catalog with the
Profile's unexpired invoke key (no refresh), else the curated manifest; Copilot
exchanges the Profile's own pool token (or its own environment's token in the
Profile scope), else the curated list. The root Profile keeps the Agent's
Copilot catalog (its credentials are the host's). Every other provider, and a
configured relay, still goes to ``provider_model_ids()``.
"""
from __future__ import annotations

import json
import logging
import sys
import types
from urllib.parse import urlparse

import pytest

import api.config as config
import api.profiles as profiles

A, B = "521740", "671278"
SECRET = "sk-live-0123456789-SECRET"


def _write(path, *, nous=None, pool=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    store = {"version": 1, "providers": {"nous": nous} if nous else {}, "credential_pool": pool or {}}
    path.write_text(json.dumps(store), encoding="utf-8")
    return path.read_bytes()


def _nous(label, *, usable=True):
    return {"agent_key": f"{'ok' if usable else 'expired'}-{label}-{SECRET}", "refresh_token": f"rt-{label}",
            "inference_base_url": f"https://{label}.inference.example/v1", "scope": "inference:invoke"}


def _pat(label, source="manual"):
    return {"id": label, "label": label, "source": source, "access_token": f"gho_{label}"}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """A Deployment (root) with Users A and B, and an Agent whose catalog calls are recorded."""
    root = tmp_path / "hermes"
    for uid in (A, B):
        (root / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", root)
    profiles._invalidate_root_profile_cache()
    calls = []

    def label(key):
        return str(key).split("-")[1] if "-" in str(key) else str(key)

    package = types.ModuleType("hermes_cli")
    package.__path__ = []
    models = types.ModuleType("hermes_cli.models")
    models.provider_model_ids = lambda pid, **kw: calls.append(("provider_model_ids", pid)) or [f"agent-{pid}"]
    models.get_curated_nous_model_ids = lambda: ["nous-curated"]
    models._chat_catalog_rows = lambda rows: list(rows)
    models._configured_relay_base_url = lambda pid: ""
    models._PROVIDER_MODELS = {"copilot": ["copilot-curated"]}

    def exchange(tokens):
        tokens = [t for t in tokens if t]
        calls.append(("copilot_exchange", tokens))
        return f"api-{tokens[0]}" if tokens else ""

    models._first_exchangeable_copilot_token = exchange
    models._fetch_github_models = lambda api_key=None, **kw: (
        calls.append(("copilot_models", api_key)) or [f"copilot-live-{api_key}"])
    auth = types.ModuleType("hermes_cli.auth")

    def fetch_nous_models(*, api_key, inference_base_url, **kw):
        calls.append(("nous_models", label(api_key), inference_base_url))
        return [f"nous-live-{label(api_key)}"]

    auth.fetch_nous_models = fetch_nous_models
    auth.resolve_nous_runtime_credentials = lambda **kw: calls.append(("nous_refresh", "-")) or {}
    constants = types.ModuleType("hermes_cli.auth_constants")
    constants.NOUS_INVOKE_JWT_MIN_TTL_SECONDS = 120
    auth_nous = types.ModuleType("hermes_cli.auth_nous")
    auth_nous._agent_key_is_usable = lambda state, min_ttl: str(state.get("agent_key", "")).startswith("ok-")
    for name, module in (("hermes_cli", package), ("hermes_cli.models", models),
                         ("hermes_cli.auth", auth), ("hermes_cli.auth_nous", auth_nous),
                         ("hermes_cli.auth_constants", constants)):
        monkeypatch.setitem(sys.modules, name, module)
    previous = getattr(config._thread_ctx, "block_process_env_fallback", False)
    yield types.SimpleNamespace(root=root, calls=calls, models=models, auth=auth,
                                home=lambda uid: root / "profiles" / uid)
    config._thread_ctx.block_process_env_fallback = previous
    profiles._invalidate_root_profile_cache()


def _as(monkeypatch, uid, *, scoped=True):
    import api.access as access
    from api.access import ROLE_USER, Admitted

    monkeypatch.setattr(access._request, "admission", Admitted(ROLE_USER, uid) if uid else None, raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)
    monkeypatch.setattr(profiles._tls, "profile", uid, raising=False)
    config._thread_ctx.block_process_env_fallback = scoped


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_a_user_without_nous_credentials_never_uses_the_deployments(agent, monkeypatch):
    _write(agent.root / "auth.json", nous=_nous("ROOT"))
    before = _files(agent.root)
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == ["nous-curated"]
    assert agent.calls == []  # no Agent catalog, no refresh, no fetch with the root key
    assert _files(agent.root) == before


def test_a_users_own_valid_nous_key_lists_the_live_catalog_without_a_refresh(agent, monkeypatch):
    before = _write(agent.home(A) / "auth.json", nous=_nous("A"))
    _write(agent.root / "auth.json", nous=_nous("ROOT"))
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == ["nous-live-A"]
    assert agent.calls == [("nous_models", "A", "https://A.inference.example/v1")]
    assert (agent.home(A) / "auth.json").read_bytes() == before
    assert not (agent.root / "shared").exists()


def test_each_user_lists_with_their_own_key(agent, monkeypatch):
    _write(agent.home(A) / "auth.json", nous=_nous("A"), pool={"copilot": [_pat("A")]})
    _write(agent.home(B) / "auth.json", nous=_nous("B"), pool={"copilot": [_pat("B")]})
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == ["nous-live-A"]
    assert config._provider_catalog_ids("copilot") == ["copilot-live-api-gho_A"]
    _as(monkeypatch, B)
    assert config._provider_catalog_ids("nous") == ["nous-live-B"]
    assert config._provider_catalog_ids("copilot") == ["copilot-live-api-gho_B"]
    assert ("provider_model_ids", "nous") not in agent.calls and ("provider_model_ids", "copilot") not in agent.calls


def test_an_expired_nous_key_is_not_refreshed_in_discovery(agent, monkeypatch):
    before = _files(agent.root) | {"a": _write(agent.home(A) / "auth.json", nous=_nous("A", usable=False))}
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == ["nous-curated"]
    assert agent.calls == []
    assert (agent.home(A) / "auth.json").read_bytes() == before["a"]


def test_copilot_uses_only_the_users_own_token(agent, monkeypatch):
    _write(agent.root / "auth.json", pool={"copilot": [_pat("ROOT")]})
    _write(agent.home(A) / "auth.json", pool={"copilot": [_pat("gh", source="gh_cli"), _pat("A")]})
    monkeypatch.setenv("GITHUB_TOKEN", "gho_DEPLOYMENT_ENV")
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("copilot") == ["copilot-live-api-gho_A"]
    assert ("copilot_exchange", ["gho_A"]) in agent.calls  # not the ambient gh entry, the env or the root pool
    _as(monkeypatch, B)
    assert config._provider_catalog_ids("copilot") == ["copilot-curated"]  # B has none: no host or root token
    assert not any("ROOT" in str(c) or "DEPLOYMENT" in str(c) for c in agent.calls)


def test_copilot_takes_the_users_own_environment_token_in_the_profile_scope(agent, monkeypatch):
    _as(monkeypatch, A, scoped=True)
    monkeypatch.setattr(config._thread_ctx, "env", {"COPILOT_GITHUB_TOKEN": "gho_AENV"}, raising=False)
    assert config._provider_catalog_ids("copilot") == ["copilot-live-api-gho_AENV"]
    _as(monkeypatch, A, scoped=False)  # outside the scope the process environment is the Deployment's
    monkeypatch.setattr(config._thread_ctx, "env", {}, raising=False)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gho_DEPLOYMENT_ENV")
    assert config._provider_catalog_ids("copilot") == ["copilot-curated"]


def test_the_root_profile_keeps_the_agents_copilot_catalog(agent, monkeypatch):
    _as(monkeypatch, None, scoped=False)
    monkeypatch.setattr(config, "_request_profile_is_root", lambda: True)
    monkeypatch.setattr(config, "_credential_pool_profile_tag", lambda: str(agent.root / "auth.json"))
    assert config._provider_catalog_ids("copilot") == ["agent-copilot"]
    assert config._provider_catalog_ids("nous") == ["nous-curated"]  # never a refresh, root included


def test_other_providers_and_a_configured_relay_still_ask_the_agent(agent, monkeypatch):
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("openrouter") == ["agent-openrouter"]
    agent.models._configured_relay_base_url = lambda pid: "https://relay.example/v1"
    assert config._provider_catalog_ids("nous") == ["agent-nous"]  # the relay path reaches no vendor fetcher


def test_an_unresolved_profile_reads_no_credentials(agent, monkeypatch):
    _write(agent.root / "auth.json", nous=_nous("ROOT"), pool={"copilot": [_pat("ROOT")]})
    _as(monkeypatch, None)  # a Directory session with no Admission
    assert config._credential_pool_profile_tag() == ""
    assert config._provider_catalog_ids("nous") == ["nous-curated"]
    assert config._provider_catalog_ids("copilot") == ["copilot-curated"]
    assert [c for c in agent.calls if c[0] != "copilot_exchange"] == []
    assert ("copilot_exchange", ["gho_ROOT"]) not in agent.calls


def test_an_absent_agent_catalog_is_no_live_ids(agent, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.models", None)
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == []
    assert config._read_live_provider_model_ids("nous") == []


def test_a_broken_catalog_falls_back_and_logs_no_secret(agent, monkeypatch, caplog):
    _write(agent.home(A) / "auth.json", nous=_nous("A"), pool={"copilot": [_pat("A")]})

    def broken(**kw):
        raise RuntimeError(f"upstream said {SECRET}")

    agent.auth.fetch_nous_models = broken
    agent.models._fetch_github_models = broken
    caplog.set_level(logging.DEBUG)
    _as(monkeypatch, A)
    assert config._provider_catalog_ids("nous") == ["nous-curated"]
    assert config._provider_catalog_ids("copilot") == ["copilot-curated"]
    assert SECRET not in caplog.text


def test_the_live_models_route_uses_the_profile_scoped_catalog(agent, monkeypatch, caplog):
    from api import routes

    _write(agent.home(A) / "auth.json", nous=_nous("A"))
    before = _files(agent.root)
    routes._clear_live_models_cache()
    monkeypatch.setattr(routes, "j", lambda _h, payload, status=200, extra_headers=None: payload)
    caplog.set_level(logging.DEBUG)
    _as(monkeypatch, A, scoped=False)  # the route binds the Profile scope itself
    payload = routes._get_api_models_live(object(), urlparse("/api/models/live?provider=nous"))
    assert [m["id"] for m in payload["models"]][:1] in (["nous-live-A"], ["@nous:nous-live-A"])
    assert ("provider_model_ids", "nous") not in agent.calls
    assert _files(agent.root) == before

    agent.models.provider_model_ids = lambda pid, **kw: (_ for _ in ()).throw(RuntimeError(SECRET))
    routes._clear_live_models_cache()
    routes._get_api_models_live(object(), urlparse("/api/models/live?provider=openrouter"))
    assert SECRET not in caplog.text


def test_the_models_rebuild_reads_nous_through_the_profile_scoped_catalog(agent, monkeypatch):
    _write(agent.home(A) / "auth.json", nous=_nous("A"))
    _as(monkeypatch, A)
    assert config._read_live_provider_model_ids("nous") == ["nous-live-A"]
    assert ("provider_model_ids", "nous") not in agent.calls
