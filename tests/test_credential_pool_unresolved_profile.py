"""A credential-pool read needs the request's Profile identity (ticket 15).

The credential-pool cache is keyed by the active Profile's auth-store path.
When that path cannot be resolved (``get_active_hermes_home`` refuses: a
User's request outside its reach, a request with no Admission), the identity
is unknown, and unknown is not allowed: the read answers "no pool
credentials" without consulting or filling the cache, and without calling the
Agent's pool readers, which would read whatever ``HERMES_HOME`` the process
has (the server default, or another Profile's). A resolved Profile keeps its
cache, as before.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

import api.config as config
from api.profiles import ProfileNotReadable

SECRET = "sk-live-0123456789-SECRET"
PROVIDER = "custom:bothub"
A = "/profiles/a/auth.json"
B = "/profiles/b/auth.json"
DEFAULT = "/hermes/auth.json"  # what an unscoped Agent read would see


class Entry:
    source = "manual"
    key_source = ""
    inference_base_url = None

    def __init__(self, label):
        self.label = label
        self.runtime_api_key = f"{SECRET}-{label}"
        self.base_url = f"https://{label}.example/v1"

    def to_dict(self):
        return {"source": self.source, "label": self.label}


class Pool:
    def __init__(self, entries):
        self._entries = entries

    def entries(self):
        return list(self._entries)

    def select(self):
        return self._entries[0] if self._entries else None


POOLS = {A: Pool([Entry("a")]), B: Pool([Entry("b")]), DEFAULT: Pool([Entry("default")])}


@pytest.fixture
def profile(monkeypatch):
    """The active Profile's auth store; None is a scope that cannot be resolved."""
    active = {"path": A, "loads": [], "raw_reads": []}

    def auth_store_path():
        if active["path"] is None:
            raise ProfileNotReadable("someone-else")
        return active["path"]

    def load_pool(pid):
        # The Agent reads the process HERMES_HOME, not the request's Profile.
        path = active["path"] or DEFAULT
        active["loads"].append(path)
        return POOLS[path]

    def read_credential_pool(pid):
        path = active["path"] or DEFAULT
        active["raw_reads"].append(path)
        return [{"source": "manual", "label": POOLS[path].entries()[0].label}]

    _stub_agent(monkeypatch, load_pool, read_credential_pool)
    monkeypatch.setattr(config, "_get_auth_store_path", auth_store_path)
    yield active


def _stub_agent(monkeypatch, load_pool, read_credential_pool=None):
    for name in ("agent", "hermes_cli"):
        package = types.ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    pool_module = types.ModuleType("agent.credential_pool")
    pool_module.load_pool = load_pool
    monkeypatch.setitem(sys.modules, "agent.credential_pool", pool_module)
    auth_module = types.ModuleType("hermes_cli.auth")
    auth_module.read_credential_pool = read_credential_pool
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", auth_module)


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    monkeypatch.setattr(config, "_resolve_provider_alias", lambda pid: pid)
    monkeypatch.setattr(config, "_CREDENTIAL_POOL_CACHE", {})
    monkeypatch.setattr(config, "_CREDENTIAL_READ_WARNED", {})
    previous = getattr(config._thread_ctx, "block_process_env_fallback", False)
    config._thread_ctx.block_process_env_fallback = False
    yield
    config._thread_ctx.block_process_env_fallback = previous


def _labels(provider=PROVIDER):
    return [p["label"] for p in config._pool_entry_payloads(provider)]


def test_an_unresolvable_profile_has_no_cache_identity(profile):
    profile["path"] = None
    assert config._credential_pool_profile_tag() == ""


def test_each_resolved_profile_has_its_own_cache_entry(profile):
    assert _labels() == ["a"]
    profile["path"] = B
    assert _labels() == ["b"]
    assert set(config._CREDENTIAL_POOL_CACHE) == {(A, PROVIDER), (B, PROVIDER)}
    profile["path"] = A
    assert _labels() == ["a"]
    assert profile["loads"] == [A, B]  # A's second read was a cache hit


def test_an_unresolved_profile_neither_reads_nor_writes_the_cache(profile):
    stale = Pool([Entry("stale")])
    config._CREDENTIAL_POOL_CACHE[("", PROVIDER)] = (config.time.time(), stale)
    profile["path"] = None
    assert _labels() == []
    assert config._CREDENTIAL_POOL_CACHE == {("", PROVIDER): config._CREDENTIAL_POOL_CACHE[("", PROVIDER)]}
    assert config._has_explicit_pool_credentials(PROVIDER) is False


def test_an_unresolved_profile_does_not_fall_back_to_the_process_default(profile):
    profile["path"] = None
    assert _labels() == []
    assert profile["loads"] == []
    assert config._custom_provider_pool_credentials(PROVIDER) == ("", "")
    assert profile["loads"] == []


def test_an_unresolved_read_only_scope_does_not_read_the_process_default(profile):
    config._thread_ctx.block_process_env_fallback = True
    assert _labels() == ["a"]  # a resolved read-only scope reads its Profile
    profile["path"] = None
    assert _labels() == []
    assert profile["raw_reads"] == [A]


def test_a_profile_switch_through_an_unresolved_scope_reuses_nothing(profile):
    assert _labels() == ["a"]
    profile["path"] = None
    assert _labels() == []
    assert config._custom_provider_pool_credentials(PROVIDER) == ("", "")
    profile["path"] = B
    assert _labels() == ["b"]
    assert config._custom_provider_pool_credentials(PROVIDER) == (f"{SECRET}-b", "https://b.example/v1")
    assert ("", PROVIDER) not in config._CREDENTIAL_POOL_CACHE


def test_repeated_unresolved_reads_share_nothing(profile):
    profile["path"] = None
    for _ in range(3):
        assert _labels() == []
        assert config._custom_provider_pool_credentials(PROVIDER) == ("", "")
    assert config._CREDENTIAL_POOL_CACHE == {}
    assert profile["loads"] == []


def test_invalidation_keeps_its_profile_scope(profile):
    _labels()
    profile["path"] = B
    _labels()
    profile["path"] = None
    config.invalidate_credential_pool_cache(PROVIDER)  # no identity: touches no Profile's entry
    assert set(config._CREDENTIAL_POOL_CACHE) == {(A, PROVIDER), (B, PROVIDER)}
    profile["path"] = A
    config.invalidate_credential_pool_cache(PROVIDER)
    assert set(config._CREDENTIAL_POOL_CACHE) == {(B, PROVIDER)}


def test_a_failed_read_under_one_profile_leaves_the_other_cached(profile, monkeypatch):
    profile["path"] = B
    _labels()
    profile["path"] = A
    monkeypatch.setattr(sys.modules["agent.credential_pool"], "load_pool", lambda pid: 1 / 0)
    assert _labels() == []
    assert set(config._CREDENTIAL_POOL_CACHE) == {(B, PROVIDER)}


def test_an_unresolved_read_logs_no_credential(profile, caplog):
    caplog.set_level(logging.DEBUG)
    _labels()
    config._custom_provider_pool_credentials(PROVIDER)
    profile["path"] = None
    _labels()
    config._custom_provider_pool_credentials(PROVIDER)
    assert SECRET not in caplog.text
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_code_with_no_caller_keeps_its_profile_identity():
    """Startup and worker threads (no request, no Admission) are unconfined: they resolve."""
    from api.profiles import get_active_hermes_home

    assert config._credential_pool_profile_tag() == str(get_active_hermes_home() / "auth.json")


def test_an_unexpected_resolution_failure_is_warned_once_without_its_text(profile, monkeypatch, caplog):
    monkeypatch.setattr(config, "_get_auth_store_path", lambda: (_ for _ in ()).throw(RuntimeError(SECRET)))
    caplog.set_level(logging.DEBUG)
    for _ in range(3):
        assert _labels() == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "RuntimeError" in warnings[0]
    assert SECRET not in caplog.text and profile["loads"] == []


# ── The real refusal: get_active_hermes_home under a request's Admission ──


def _served(monkeypatch, admission, profile_name):
    import api.access as access
    import api.profiles as profiles

    monkeypatch.setattr(access._request, "admission", admission, raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)
    monkeypatch.setattr(profiles._tls, "profile", profile_name, raising=False)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    import api.profiles as profiles

    hermes = tmp_path / "hermes"
    for uid in ("521740", "671278"):
        (hermes / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes)
    profiles._invalidate_root_profile_cache()
    loads = []
    _stub_agent(monkeypatch, lambda pid: loads.append(pid) or POOLS[DEFAULT])
    yield hermes, loads
    profiles._invalidate_root_profile_cache()


def test_a_users_own_profile_is_read_and_cached_under_it(deployment, monkeypatch):
    from api.access import ROLE_USER, Admitted

    hermes, loads = deployment
    _served(monkeypatch, Admitted(ROLE_USER, "521740"), "521740")
    assert _labels() == ["default"]  # the stub's pool; the point is the key
    assert set(config._CREDENTIAL_POOL_CACHE) == {(str(hermes / "profiles" / "521740" / "auth.json"), PROVIDER)}


@pytest.mark.parametrize("admission,profile_name", [
    (("user", "521740"), "671278"),  # a User's request bound to another User's Profile
    (None, "521740"),                # a Directory session with no Admission
], ids=["another-users-profile", "no-admission"])
def test_a_refused_request_reads_no_pool(deployment, monkeypatch, caplog, admission, profile_name):
    from api.access import Admitted

    _, loads = deployment
    _served(monkeypatch, Admitted(*admission) if admission else None, profile_name)
    caplog.set_level(logging.DEBUG)
    assert config._credential_pool_profile_tag() == ""
    assert _labels() == []
    assert config._custom_provider_pool_credentials(PROVIDER) == ("", "")
    assert loads == [] and config._CREDENTIAL_POOL_CACHE == {}
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
