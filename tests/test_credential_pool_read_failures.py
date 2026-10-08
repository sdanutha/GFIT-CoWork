"""A credential-pool read that fails is reported, not taken for "no credentials" (ticket 15).

The root Profile's ``_pool_entry_payloads`` outside a read-only scope reads
through ``agent.credential_pool.load_pool`` behind the Profile-scoped cache. A
missing capability is "no pool credentials", silently. When the capability
imports but the read fails (an ImportError raised inside it, any other error),
the result is the same fail-soft "no pool credentials", and the server logs an
actionable warning that names the read, the provider and the error type, never
a credential value or the error's text. A failure is never cached and never
answered from another Profile's cache entry.

The custom-provider fallback that takes a key and base URL from the pool (the
model list, and ``/api/models/live``) is credential discovery: it reads the
Profile's own auth.json and never the Agent's readers (ticket 16). An
unreadable auth.json is "no pool credentials", warned without its content.
"""
from __future__ import annotations

import importlib.abc
import logging
import sys
import types
from urllib.parse import urlparse

import pytest

import api.config as config

SECRET = "sk-live-0123456789-SECRET"
PROVIDER = "openrouter"


class Entry:
    source = "manual"
    label = "work key"
    key_source = ""
    runtime_api_key = SECRET
    base_url = "https://pool.example/v1"
    inference_base_url = None

    def to_dict(self):
        return {"source": self.source, "label": self.label, "access_token": SECRET}


class Pool:
    def __init__(self, entries):
        self._entries = entries

    def entries(self):
        return self._entries

    def select(self):
        return self._entries[0] if self._entries else None


def _package(monkeypatch, name):
    package = types.ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)


def _install_load_pool(monkeypatch, load_pool):
    _package(monkeypatch, "agent")
    module = types.ModuleType("agent.credential_pool")
    module.load_pool = load_pool
    monkeypatch.setitem(sys.modules, "agent.credential_pool", module)


class _BrokenImport(importlib.abc.MetaPathFinder):
    def __init__(self, fullname, error):
        self.fullname, self.error = fullname, error

    def find_spec(self, fullname, path, target=None):
        if fullname == self.fullname:
            raise self.error
        return None


def _broken_import(monkeypatch, fullname, error):
    _package(monkeypatch, fullname.split(".")[0])
    monkeypatch.delitem(sys.modules, fullname, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BrokenImport(fullname, error), *sys.meta_path])


def _raise(error):
    def fail(*_args, **_kwargs):
        raise error
    return fail


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    profile = {"tag": "/profiles/700001/auth.json"}
    monkeypatch.setattr(config, "_CREDENTIAL_POOL_CACHE", {})
    monkeypatch.setattr(config, "_CREDENTIAL_READ_WARNED", {}, raising=False)
    monkeypatch.setattr(config, "_credential_pool_profile_tag", lambda: profile["tag"])
    monkeypatch.setattr(config, "_resolve_provider_alias", lambda pid: pid)
    monkeypatch.setattr(config, "_request_profile_is_root", lambda: True)  # load_pool's Profile: the Agent's own
    previous = getattr(config._thread_ctx, "block_process_env_fallback", False)
    config._thread_ctx.block_process_env_fallback = False
    yield profile
    config._thread_ctx.block_process_env_fallback = previous


@pytest.fixture
def read():
    """The Agent read behind _pool_entry_payloads: the root Profile's cached load_pool."""
    return "load_pool"


def _install(monkeypatch, read, fn):
    _install_load_pool(monkeypatch, fn)


def _module(read):
    return "agent.credential_pool"


def _healthy(read):
    return lambda pid: Pool([Entry()])


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == "api.config"]


def _no_secret(caplog):
    assert SECRET not in caplog.text
    for record in caplog.records:
        assert record.exc_info is None or SECRET not in str(record.exc_info[1])


# ── _pool_entry_payloads ──


def test_an_absent_capability_is_no_pool_credentials_silently(monkeypatch, caplog, read):
    monkeypatch.setitem(sys.modules, _module(read), None)  # import raises ModuleNotFoundError naming it
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    assert _warnings(caplog) == []


def test_a_capability_without_the_imported_name_is_absent_silently(monkeypatch, caplog, read):
    _install(monkeypatch, read, None)
    delattr(sys.modules[_module(read)], "load_pool")
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    assert _warnings(caplog) == []


def test_a_working_read_returns_the_entries(monkeypatch, caplog, read):
    _install(monkeypatch, read, _healthy(read))
    caplog.set_level(logging.DEBUG)
    payloads = config._pool_entry_payloads(PROVIDER)
    assert [p["label"] for p in payloads] == ["work key"]
    assert _warnings(caplog) == []


@pytest.mark.parametrize("error", [
    ModuleNotFoundError("No module named 'cryptography'", name="cryptography"),
    ImportError("cannot import name 'Fernet' from 'cryptography.fernet'", name="cryptography.fernet"),
    "its-package-lacks-a-name",
], ids=["dependency-missing", "inner-name-missing", "package-name-missing"])
def test_an_import_error_inside_the_capability_is_reported(monkeypatch, caplog, read, error):
    if error == "its-package-lacks-a-name":  # `from agent import helper` inside the module
        package = _module(read).split(".")[0]
        error = ImportError(f"cannot import name 'helper' from '{package}'", name=package)
    _broken_import(monkeypatch, _module(read), error)
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    [warning] = _warnings(caplog)
    message = warning.getMessage()
    assert "credential pool" in message and PROVIDER in message and error.name in message
    _no_secret(caplog)


def test_an_import_error_raised_by_the_read_is_reported(monkeypatch, caplog, read):
    _install(monkeypatch, read, _raise(ModuleNotFoundError("No module named 'keyring'", name="keyring")))
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    [warning] = _warnings(caplog)
    assert "keyring" in warning.getMessage() and PROVIDER in warning.getMessage()


@pytest.mark.parametrize("error", [
    RuntimeError(f"refresh failed for token {SECRET}"),
    ValueError(f"malformed entry {{'access_token': '{SECRET}'}}"),
    OSError(13, "Permission denied", f"/profiles/700001/auth.json#{SECRET}"),
], ids=["RuntimeError", "ValueError", "OSError"])
def test_any_other_read_failure_is_reported_without_its_text(monkeypatch, caplog, read, error):
    _install(monkeypatch, read, _raise(error))
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    [warning] = _warnings(caplog)
    message = warning.getMessage()
    assert "could not read the credential pool" in message.lower()
    assert PROVIDER in message and type(error).__name__ in message
    _no_secret(caplog)


def test_a_failed_read_is_not_cached_and_not_answered_from_another_profile(monkeypatch, caplog, isolated):
    loads = []

    def load_pool(pid):
        loads.append(isolated["tag"])
        if isolated["tag"].startswith("/profiles/700001"):
            raise RuntimeError(f"broken store {SECRET}")
        return Pool([Entry()])

    _install_load_pool(monkeypatch, load_pool)
    isolated["tag"] = "/profiles/700002/auth.json"
    assert len(config._pool_entry_payloads(PROVIDER)) == 1  # 700002's pool, cached under its tag
    isolated["tag"] = "/profiles/700001/auth.json"
    assert config._pool_entry_payloads(PROVIDER) == []  # not 700002's entry
    assert config._pool_entry_payloads(PROVIDER) == []  # retried, not cached
    assert loads == ["/profiles/700002/auth.json", "/profiles/700001/auth.json", "/profiles/700001/auth.json"]
    assert set(config._CREDENTIAL_POOL_CACHE) == {("/profiles/700002/auth.json", PROVIDER)}
    _no_secret(caplog)


def test_the_cache_still_serves_a_working_profile(monkeypatch):
    loads = []
    _install_load_pool(monkeypatch, lambda pid: loads.append(pid) or Pool([Entry()]))
    config._pool_entry_payloads(PROVIDER)
    config._pool_entry_payloads(PROVIDER)
    assert loads == [PROVIDER]


def test_a_repeated_failure_is_warned_once_until_the_cause_changes(monkeypatch, caplog, read):
    _install(monkeypatch, read, _raise(RuntimeError("down")))
    caplog.set_level(logging.DEBUG)
    for _ in range(3):
        config._pool_entry_payloads(PROVIDER)
    assert len(_warnings(caplog)) == 1
    _install(monkeypatch, read, _raise(OSError("disk")))
    config._pool_entry_payloads(PROVIDER)
    assert len(_warnings(caplog)) == 2


def test_without_a_profile_identity_the_pool_is_not_read(monkeypatch, caplog, isolated, read):
    # An unresolvable auth store gives no Profile tag: no read, so no failure to warn of (ticket 15).
    isolated["tag"] = ""
    calls = []
    _install(monkeypatch, read, lambda pid: calls.append(pid))
    caplog.set_level(logging.DEBUG)
    assert config._pool_entry_payloads(PROVIDER) == []
    assert calls == [] and _warnings(caplog) == []
    assert config._CREDENTIAL_READ_WARNED == {} and config._CREDENTIAL_POOL_CACHE == {}


# ── The custom-provider fallback: /api/models/live and the model list (credential discovery) ──


CUSTOM = "custom:test-gateway"


def _profile_pool(isolated, tmp_path, provider, *, content=None):
    """The request Profile's auth.json holds a pool entry for *provider* (or raw *content*)."""
    import json

    path = tmp_path / "auth.json"
    if content is None:
        entry = {"id": "e1", "label": "work key", "source": "manual", "auth_type": "api_key",
                 "access_token": SECRET, "base_url": "https://pool.example/v1"}
        content = json.dumps({"version": 1, "credential_pool": {provider: [entry]}})
    (path.write_bytes if isinstance(content, bytes) else path.write_text)(content)
    isolated["tag"] = str(path)
    return path


AGENT_READS: list = []


def _no_agent_reads(monkeypatch):
    """load_pool records any call; discovery must make none (asserted by the tests)."""
    AGENT_READS.clear()
    _install_load_pool(monkeypatch, lambda pid: AGENT_READS.append(pid) or Pool([Entry()]))


UNREADABLE = {
    "malformed": f'{{"credential_pool": {{"{CUSTOM}": [{{"access_token": "{SECRET}"',
    "undecodable": b"\xff\xfe" + SECRET.encode(),
}


def _live_models(monkeypatch, *, base_url=None):
    """GET /api/models/live?provider=custom:test-gateway for a provider whose key is in the pool."""
    import io
    import json
    import urllib.request

    import api.profiles as profiles
    from api import routes

    routes._clear_live_models_cache()
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    entry = {"name": "Test Gateway", "models": ["chat-a"]}
    if base_url:
        entry["base_url"] = base_url
    config_data = {"model": {"provider": CUSTOM}, "custom_providers": [entry]}
    monkeypatch.setattr(config, "get_config", lambda: config_data)
    monkeypatch.setattr(routes, "cfg", config_data, raising=False)
    _package(monkeypatch, "hermes_cli")
    models = types.ModuleType("hermes_cli.models")
    models.provider_model_ids = lambda provider: []
    monkeypatch.setitem(sys.modules, "hermes_cli.models", models)
    _no_agent_reads(monkeypatch)
    requested = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(req, timeout=None):
        requested.append((req.full_url, dict(req.header_items())))
        return Response(json.dumps({"data": [{"id": "chat-a"}, {"id": "chat-b"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    payload = routes._handle_live_models(object(), urlparse(f"/api/models/live?provider={CUSTOM}"))
    return payload, requested


def test_live_models_uses_the_pool_key_from_the_profiles_auth_json(monkeypatch, caplog, isolated, tmp_path):
    path = _profile_pool(isolated, tmp_path, CUSTOM)
    before = path.read_bytes()
    caplog.set_level(logging.DEBUG)
    payload, requested = _live_models(monkeypatch)
    assert [url for url, _headers in requested] == ["https://pool.example/v1/models"]
    assert "chat-a" in [m["id"] for m in payload["models"]]
    assert _warnings(caplog) == [] and AGENT_READS == []
    assert path.read_bytes() == before
    _no_secret(caplog)


def test_live_models_keeps_a_configured_base_url_and_takes_only_the_missing_key(monkeypatch, isolated, tmp_path):
    _profile_pool(isolated, tmp_path, CUSTOM)
    payload, requested = _live_models(monkeypatch, base_url="https://configured.example/v1")
    [(url, headers)] = requested
    assert url == "https://configured.example/v1/models"
    assert headers.get("Authorization") == f"Bearer {SECRET}"


def test_live_models_without_a_pool_falls_back_silently(monkeypatch, caplog, isolated, tmp_path):
    isolated["tag"] = str(tmp_path / "auth.json")  # no auth.json at all
    caplog.set_level(logging.DEBUG)
    payload, requested = _live_models(monkeypatch)
    assert requested == [] and [m["id"] for m in payload["models"]] == ["chat-a"]
    assert _warnings(caplog) == []
    assert not (tmp_path / "auth.json").exists()


@pytest.mark.parametrize("kind", sorted(UNREADABLE))
def test_live_models_with_an_unreadable_auth_json_falls_back_and_warns(monkeypatch, caplog, isolated, tmp_path, kind):
    path = _profile_pool(isolated, tmp_path, CUSTOM, content=UNREADABLE[kind])
    before = path.read_bytes()
    caplog.set_level(logging.DEBUG)
    payload, requested = _live_models(monkeypatch)
    assert requested == [] and [m["id"] for m in payload["models"]] == ["chat-a"]
    assert "error" not in payload
    warnings = [w.getMessage() for w in _warnings(caplog)]
    assert warnings and all(CUSTOM in m for m in warnings)
    assert path.read_bytes() == before and AGENT_READS == []
    _no_secret(caplog)


def _model_list(monkeypatch, tmp_path, isolated, *, content=None, base_url=None):
    """get_available_models() for a custom provider whose key (and base URL) are in the pool.

    Returns (result, slug, probes, unchanged): *probes* are the (base_url,
    api_key) the model list probed the provider's /v1/models with; *unchanged*
    is whether the Profile's auth.json kept its bytes.
    """
    import json

    import api.profiles as profiles

    _package(monkeypatch, "hermes_cli")
    models = types.ModuleType("hermes_cli.models")
    models.list_available_providers = lambda: []
    models.provider_model_ids = lambda pid: []
    auth = types.ModuleType("hermes_cli.auth")
    auth.get_auth_status = lambda _pid: {}
    monkeypatch.setitem(sys.modules, "hermes_cli.models", models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth)
    _no_agent_reads(monkeypatch)
    slug = config._custom_provider_slug_from_name("Test Gateway")
    auth_path = _profile_pool(isolated, tmp_path, slug, content=content)
    before = auth_path.read_bytes()
    import io
    import socket
    import urllib.request

    probes = []

    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(req, timeout=None):
        auth = dict(req.header_items()).get("Authorization", "")
        probes.append((req.full_url.rsplit("/models", 1)[0], auth.removeprefix("Bearer ") or None))
        return Response(json.dumps({"data": [{"id": "chat-a"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    entry = {"name": "Test Gateway"}
    if base_url:
        entry["base_url"] = base_url
    old_cfg, old_mtime = dict(config.cfg), config._cfg_mtime
    config.cfg.clear()
    config.cfg.update({"model": {}, "custom_providers": [entry]})
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    try:
        result = config.get_available_models()
        return result, slug, probes, auth_path.read_bytes() == before
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()


def test_the_model_list_takes_a_custom_providers_key_and_base_url_from_the_pool(
    monkeypatch, tmp_path, caplog, isolated,
):
    caplog.set_level(logging.DEBUG)
    _result, _slug, probes, unchanged = _model_list(monkeypatch, tmp_path, isolated)
    assert probes == [("https://pool.example/v1", SECRET)]
    assert _warnings(caplog) == [] and AGENT_READS == []
    assert unchanged
    _no_secret(caplog)


def test_the_model_list_keeps_a_configured_base_url(monkeypatch, tmp_path, isolated):
    _result, _slug, probes, _unchanged = _model_list(monkeypatch, tmp_path, isolated,
                                                     base_url="https://configured.example/v1")
    assert probes == [("https://configured.example/v1", SECRET)]


@pytest.mark.parametrize("kind", sorted(UNREADABLE))
def test_the_model_list_with_an_unreadable_auth_json_still_lists_and_warns(
    monkeypatch, tmp_path, caplog, isolated, kind,
):
    caplog.set_level(logging.DEBUG)
    result, slug, probes, unchanged = _model_list(monkeypatch, tmp_path, isolated, content=UNREADABLE[kind])
    assert isinstance(result, dict) and "groups" in result and unchanged
    assert probes == []  # no key and no base URL from the pool: nothing to probe, as with no pool
    warnings = [w.getMessage() for w in _warnings(caplog)]
    assert any(slug in m for m in warnings), warnings
    _no_secret(caplog)
