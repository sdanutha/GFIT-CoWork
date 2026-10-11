"""Credential discovery never writes auth.json (ticket 16).

Model and provider discovery (``/api/models``, ``/api/models/live``,
``get_available_models``, ``_custom_provider_pool_credentials``) answers "which
providers have pool credentials, and which key and base URL does a custom
provider use" from the request Profile's own ``auth.json``, read as it is on
disk. It never calls the Agent's pool readers: ``load_pool`` seeds entries
(config keys, environment keys, singleton files), prunes and normalizes them,
and persists the result, and ``select()`` persists too; ``read_credential_pool``
falls back to the root Profile's store and follows the Agent's ``HERMES_HOME``,
which on a User's request thread is the Deployment's, not the User's.

So discovery creates or changes no ``auth.json``, persists no
environment-derived key, and answers only from the request Profile's own
entries. Without the Profile's identity it reads nothing. The credential-pool
recovery reads (``_pool_entry_payloads``) in a read-only Profile scope read the
same way.
"""
from __future__ import annotations

import json
import logging
import sys
import types

import pytest

import api.config as config
import api.profiles as profiles

SECRET = "sk-live-0123456789-SECRET"
CUSTOM = "custom:bothub"
A, B = "521740", "671278"


def _entry(label, key, base_url="https://bothub.example/v1", **extra):
    return {"id": label, "label": label, "source": "manual", "auth_type": "api_key",
            "access_token": key, "base_url": base_url, **extra}


def _write(path, pool):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "providers": {}, "credential_pool": pool}), encoding="utf-8")
    return path.read_bytes()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """A Deployment root (the default Profile) with Users A and B; the Agent's readers are tripwires."""
    root = tmp_path / "hermes"
    for uid in (A, B):
        (root / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", root)
    profiles._invalidate_root_profile_cache()
    monkeypatch.setattr(config, "_CREDENTIAL_POOL_CACHE", {})
    monkeypatch.setattr(config, "_CREDENTIAL_READ_WARNED", {})
    calls = []

    def tripwire(name):
        def reader(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"discovery called {name}")
        return reader

    for package in ("agent", "hermes_cli"):
        module = types.ModuleType(package)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, package, module)
    pool_module = types.ModuleType("agent.credential_pool")
    pool_module.load_pool = tripwire("load_pool")
    monkeypatch.setitem(sys.modules, "agent.credential_pool", pool_module)
    auth_module = types.ModuleType("hermes_cli.auth")
    auth_module.read_credential_pool = tripwire("read_credential_pool")
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth_module)
    previous = getattr(config._thread_ctx, "block_process_env_fallback", False)
    yield types.SimpleNamespace(
        root=root, calls=calls, auth=lambda uid: root / "profiles" / uid / "auth.json",
    )
    config._thread_ctx.block_process_env_fallback = previous
    profiles._invalidate_root_profile_cache()


@pytest.fixture(params=["request thread", "read-only scope"])
def scope(request):
    """Discovery runs on the request thread (static catalog) and in the read-only Profile scope."""
    return request.param


def _as_user(monkeypatch, uid, scope="request thread"):
    import api.access as access
    from api.access import ROLE_USER, Admitted

    monkeypatch.setattr(access._request, "admission", Admitted(ROLE_USER, uid), raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)
    monkeypatch.setattr(profiles._tls, "profile", uid, raising=False)
    config._thread_ctx.block_process_env_fallback = scope == "read-only scope"


def test_a_users_own_pool_credentials_are_read_from_their_auth_json(deployment, monkeypatch, scope):
    before = _write(deployment.auth(A), {CUSTOM: [_entry("work", f"{SECRET}-a")]})
    _as_user(monkeypatch, A, scope)
    assert config._has_explicit_pool_credentials(CUSTOM) is True
    assert config._custom_provider_pool_credentials(CUSTOM) == (f"{SECRET}-a", "https://bothub.example/v1")
    assert deployment.calls == []
    assert deployment.auth(A).read_bytes() == before


def test_the_selected_entry_is_the_first_usable_one_by_priority(deployment, monkeypatch):
    _write(deployment.auth(A), {CUSTOM: [
        _entry("later", "k-later", priority=2),
        _entry("dead", "k-dead", priority=0, last_status="dead"),
        _entry("first", "k-first", "https://first.example/v1", priority=1),
    ]})
    _as_user(monkeypatch, A)
    assert config._custom_provider_pool_credentials(CUSTOM) == ("k-first", "https://first.example/v1")


def test_another_profiles_or_the_deployments_pool_is_never_used(deployment, monkeypatch, scope):
    _write(deployment.auth(A), {CUSTOM: [_entry("a", f"{SECRET}-a")]})
    root_bytes = _write(deployment.root / "auth.json", {CUSTOM: [_entry("deployment", f"{SECRET}-root")]})
    _as_user(monkeypatch, B, scope)
    assert config._has_explicit_pool_credentials(CUSTOM) is False
    assert config._custom_provider_pool_credentials(CUSTOM) == ("", "")
    assert config._pool_entry_payloads(CUSTOM) == []
    assert deployment.auth(B).exists() is False
    assert (deployment.root / "auth.json").read_bytes() == root_bytes
    assert deployment.calls == []


def test_an_unresolved_profile_reads_nothing(deployment, monkeypatch):
    _write(deployment.root / "auth.json", {CUSTOM: [_entry("deployment", f"{SECRET}-root")]})
    import api.access as access

    monkeypatch.setattr(access._request, "admission", None, raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)  # no Admission: refused
    assert config._credential_pool_profile_tag() == ""
    assert config._has_explicit_pool_credentials(CUSTOM) is False
    assert config._custom_provider_pool_credentials(CUSTOM) == ("", "")
    assert deployment.calls == []


def test_the_read_only_scope_recovery_read_is_the_profiles_own(deployment, monkeypatch):
    _write(deployment.auth(A), {"openrouter": [_entry("a", f"{SECRET}-a", last_status="exhausted")]})
    _write(deployment.root / "auth.json", {"anthropic": [_entry("deployment", f"{SECRET}-root")]})
    _as_user(monkeypatch, A, "read-only scope")
    assert [e["label"] for e in config._pool_entry_payloads("openrouter")] == ["a"]
    assert config._pool_entry_payloads("anthropic") == []  # no root fallback
    assert deployment.calls == []


@pytest.mark.parametrize("missing", ["absent", "broken"])
def test_discovery_does_not_need_the_agents_readers(deployment, monkeypatch, caplog, missing):
    before = _write(deployment.auth(A), {CUSTOM: [_entry("work", "k-a")]})
    if missing == "absent":
        monkeypatch.setitem(sys.modules, "agent.credential_pool", None)
        monkeypatch.setitem(sys.modules, "hermes_cli.auth", None)
    caplog.set_level(logging.DEBUG)
    _as_user(monkeypatch, A)
    assert config._custom_provider_pool_credentials(CUSTOM) == ("k-a", "https://bothub.example/v1")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert deployment.auth(A).read_bytes() == before


@pytest.mark.parametrize("content", [
    f'{{"credential_pool": {{"{CUSTOM}": [{{"access_token": "{SECRET}"',  # truncated JSON
    b"\xff\xfe" + SECRET.encode(),  # not UTF-8
], ids=["malformed", "undecodable"])
def test_an_unreadable_auth_json_is_no_credentials_warned_without_its_content(
    deployment, monkeypatch, caplog, content,
):
    path = deployment.auth(A)
    (path.write_bytes if isinstance(content, bytes) else path.write_text)(content)
    before = path.read_bytes()
    caplog.set_level(logging.DEBUG)
    _as_user(monkeypatch, A)
    assert config._has_explicit_pool_credentials(CUSTOM) is False
    assert config._custom_provider_pool_credentials(CUSTOM) == ("", "")
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and all(CUSTOM in m for m in warnings)
    assert SECRET not in caplog.text
    assert path.read_bytes() == before and deployment.calls == []



def test_a_users_static_catalog_discovery_writes_no_auth_json(deployment, monkeypatch):
    """/api/models' request-thread fallback under A's Admission: the Deployment's env key is not persisted."""
    monkeypatch.setenv("OPENROUTER_API_KEY", f"{SECRET}-deployment-env")
    custom = {"custom_providers": [{"name": "bothub", "base_url": "https://bothub.example/v1"}]}
    monkeypatch.setattr(config, "get_config", lambda: custom)
    monkeypatch.setattr(config, "cfg", custom)
    root_bytes = _write(deployment.root / "auth.json", {CUSTOM: [_entry("deployment", f"{SECRET}-root")]})
    _as_user(monkeypatch, A)
    result = config._static_models_catalog_without_live_probes()
    assert isinstance(result, dict)
    assert deployment.calls == []
    assert not deployment.auth(A).exists() and not deployment.auth(B).exists()
    assert (deployment.root / "auth.json").read_bytes() == root_bytes
    assert config._has_explicit_pool_credentials("openrouter") is False
    assert config._has_explicit_pool_credentials(CUSTOM) is False  # the Deployment's pool is not A's

# ── The real Hermes Agent: what discovery used to write (skipped when it is not installed) ──


@pytest.fixture
def real_agent(tmp_path, monkeypatch):
    """The installed Agent's readers, a Deployment root with Users A and B, and nothing stubbed."""
    pytest.importorskip("agent.credential_pool")
    pytest.importorskip("hermes_cli.auth")
    root = tmp_path / "hermes"
    for uid in (A, B):
        (root / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", root)
    profiles._invalidate_root_profile_cache()
    monkeypatch.setattr(config, "_CREDENTIAL_POOL_CACHE", {})
    previous = getattr(config._thread_ctx, "block_process_env_fallback", False)
    yield root
    config._thread_ctx.block_process_env_fallback = previous
    profiles._invalidate_root_profile_cache()


def _tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("auth.json")}


def test_a_users_discovery_persists_no_deployment_environment_key(real_agent, monkeypatch):
    """The critical case: A has no key, the Deployment's environment has one; A discovers models."""
    from api.providers import _provider_has_key

    monkeypatch.setenv("OPENROUTER_API_KEY", f"{SECRET}-deployment-env")
    before = _tree(real_agent)
    _as_user(monkeypatch, A)
    config._has_explicit_pool_credentials("openrouter")
    _provider_has_key("openrouter")
    config._custom_provider_pool_credentials("openrouter")
    assert _tree(real_agent) == before == {}  # no auth.json created, A's or the Deployment's
    assert config._has_explicit_pool_credentials("openrouter") is False  # the Deployment's key is not A's pool


def test_custom_provider_discovery_seeds_no_config_entry(real_agent, monkeypatch, scope):
    """A custom provider with a key in A's config and one manual pool entry: no config:* row is added."""
    home = real_agent / "profiles" / A
    (home / "config.yaml").write_text(
        "custom_providers:\n- name: bothub\n  base_url: https://bothub.example/v1\n"
        f"  api_key: {SECRET}-config\n",
        encoding="utf-8",
    )
    before = _write(home / "auth.json", {CUSTOM: [_entry("manual", f"{SECRET}-manual")]})
    _as_user(monkeypatch, A, scope)
    monkeypatch.setenv("HERMES_HOME", str(home))  # the scope's binding of the Agent's home
    assert config._custom_provider_pool_credentials(CUSTOM) == (f"{SECRET}-manual", "https://bothub.example/v1")
    assert config._has_explicit_pool_credentials(CUSTOM) is True
    assert (home / "auth.json").read_bytes() == before


# ── The picker lists what the agent can reach (ticket 12: Users may use the Deployment's logins) ──


def test_the_picker_lists_providers_the_agent_reaches_through_the_deployments_pool(deployment, monkeypatch, scope):
    """The Agent's read_credential_pool falls back, per provider, to the root Profile's pool when the
    Profile has no entries for it; discovery lists the same providers, reading both files as they are."""
    own = _write(deployment.auth(A), {"openrouter": [_entry("a", "k-a")], "anthropic": []})
    root_bytes = _write(deployment.root / "auth.json", {
        "deepseek": [_entry("deployment", f"{SECRET}-root")],
        "openrouter": [_entry("deployment", f"{SECRET}-root")],
        "copilot": [{**_entry("gh auth token", "gho-x"), "source": "gh_cli"}],  # ambient: not listed
    })
    _as_user(monkeypatch, A, scope)
    store = json.loads(own)

    assert config._pool_provider_ids(store) == {"openrouter", "deepseek"}
    assert config._has_explicit_pool_credentials("deepseek") is False  # keys stay the Profile's own
    assert deployment.calls == []
    assert deployment.auth(A).read_bytes() == own
    assert (deployment.root / "auth.json").read_bytes() == root_bytes


def test_a_profiles_own_entries_shadow_the_deployments_for_that_provider(deployment, monkeypatch):
    own = _write(deployment.auth(A), {"deepseek": [{**_entry("gh auth token", "x"), "source": "gh_cli"}]})
    _write(deployment.root / "auth.json", {"deepseek": [_entry("deployment", f"{SECRET}-root")]})
    _as_user(monkeypatch, A)
    # The Agent uses A's (ambient-only) entries for deepseek, not the root's; nothing explicit to list.
    assert config._pool_provider_ids(json.loads(own)) == set()


def test_an_unresolved_profile_lists_no_pool_provider(deployment, monkeypatch):
    _write(deployment.root / "auth.json", {"deepseek": [_entry("deployment", f"{SECRET}-root")]})
    import api.access as access

    monkeypatch.setattr(access._request, "admission", None, raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)
    assert config._pool_provider_ids({}) == set()


def test_an_unreadable_deployment_pool_lists_only_the_profiles_own(deployment, monkeypatch, caplog):
    own = _write(deployment.auth(A), {"openrouter": [_entry("a", "k-a")]})
    (deployment.root / "auth.json").write_text(f'{{"credential_pool": {{"deepseek": "{SECRET}', encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    _as_user(monkeypatch, A)
    assert config._pool_provider_ids(json.loads(own)) == {"openrouter"}
    assert SECRET not in caplog.text
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
