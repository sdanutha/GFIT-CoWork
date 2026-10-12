from __future__ import annotations

import pytest

# Talks to the shared test server (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("test_server")

# These cases run through ``streaming._resolve_runtime_connection_bundle``, the
# function every agent-construction path in streaming.py calls. Each one also
# runs with a FOREIGN ambient runtime dict (another provider's endpoint, key,
# wire protocol, pool and ACP transport), which is what production passes in:
# none of it may reach a ``custom:*`` route.
_FOREIGN_RUNTIME = {
    "provider": "anthropic",
    "api_key": "foreign-key-sentinel",
    "base_url": "https://foreign.example/v1",
    "api_mode": "anthropic_messages",
    "credential_pool": ["foreign-pool-sentinel"],
    "acp_command": "foreign-acp",
    "acp_args": ["--foreign"],
}

_RUNTIMES = pytest.mark.parametrize(
    "runtime", [None, _FOREIGN_RUNTIME], ids=["no-runtime", "foreign-runtime"]
)


def _bundle(provider, api_key, base_url, runtime, **kwargs):
    import copy
    import api.streaming as streaming

    return streaming._resolve_runtime_connection_bundle(
        provider, api_key, base_url, copy.deepcopy(runtime), **kwargs
    )


def _assert_no_foreign_fields(bundle):
    for field in ("api_mode", "credential_pool", "acp_command", "acp_args"):
        assert bundle[field] is None, field
    assert bundle["route_error"] is None


@_RUNTIMES
def test_keyless_named_custom_provider_uses_placeholder_and_generic_custom(monkeypatch, runtime):
    import api.streaming as streaming

    monkeypatch.setattr(
        streaming,
        "resolve_custom_provider_connection",
        lambda provider: (None, "http://gpu.local:8000/v1"),
    )

    bundle = _bundle("custom:gpu-local-8000", None, None, runtime)

    assert bundle["provider"] == "custom"
    assert bundle["api_key"] == "dummy-key"
    assert bundle["base_url"] == "http://gpu.local:8000/v1"
    _assert_no_foreign_fields(bundle)


@_RUNTIMES
def test_named_custom_provider_preserves_configured_key(monkeypatch, runtime):
    import api.streaming as streaming

    monkeypatch.setattr(
        streaming,
        "resolve_custom_provider_connection",
        lambda provider: ("real-key", "http://gpu.local:8000/v1"),
    )

    bundle = _bundle("custom:gpu-local-8000", None, None, runtime)

    assert bundle["provider"] == "custom"
    assert bundle["api_key"] == "real-key"
    assert bundle["base_url"] == "http://gpu.local:8000/v1"
    _assert_no_foreign_fields(bundle)


def test_named_custom_provider_keeps_existing_runtime_base_url(monkeypatch):
    import api.streaming as streaming

    monkeypatch.setattr(
        streaming,
        "resolve_custom_provider_connection",
        lambda provider: (None, "http://config.example/v1"),
    )

    bundle = _bundle("custom:runtime-local", None, "http://runtime.example/v1", None)

    assert bundle["provider"] == "custom"
    assert bundle["api_key"] == "dummy-key"
    assert bundle["base_url"] == "http://runtime.example/v1"


def test_named_custom_provider_displaces_a_foreign_runtime_base_url(monkeypatch):
    """A runtime dict for a DIFFERENT endpoint does not keep its URL or key."""
    import api.streaming as streaming

    monkeypatch.setattr(
        streaming,
        "resolve_custom_provider_connection",
        lambda provider: (None, "http://config.example/v1"),
    )

    bundle = _bundle(
        "custom:runtime-local",
        "foreign-key-sentinel",
        "https://foreign.example/v1",
        _FOREIGN_RUNTIME,
    )

    assert bundle["provider"] == "custom"
    assert bundle["base_url"] == "http://config.example/v1"
    assert bundle["api_key"] == "dummy-key"
    _assert_no_foreign_fields(bundle)


def test_custom_provider_bundle_resolves_connection_within_profile_scope(monkeypatch):
    """Finding #3: the custom endpoint AND credential are resolved inside the
    session's OWN profile scope.

    ``resolve_custom_provider_connection`` reads both the base_url and the API key
    from one ``get_config()``/env snapshot. On a detached worker thread (no
    inherited request-profile TLS/env) an unbound call reads the DEFAULT profile,
    pairing a named profile's endpoint with the default profile's key. The
    bundle helper must run that lookup under the passed profile scope.
    """
    import contextlib
    import api.streaming as streaming
    import api.profiles as profiles

    scope_state = {"active": False, "name": None}

    @contextlib.contextmanager
    def _fake_scope(name, purpose="", logger_override=None):
        scope_state["active"] = True
        scope_state["name"] = name
        try:
            yield
        finally:
            scope_state["active"] = False

    monkeypatch.setattr(profiles, "profile_scope_for_detached_worker", _fake_scope)

    seen = {}

    def _fake_conn(provider):
        # Endpoint + credential must both be read WHILE the profile scope is active.
        seen["scope_active"] = scope_state["active"]
        seen["scope_name"] = scope_state["name"]
        return ("team-key", "https://team.example/v1")

    monkeypatch.setattr(streaming, "resolve_custom_provider_connection", _fake_conn)

    bundle = _bundle("custom:team", None, None, _FOREIGN_RUNTIME, profile_name="team")

    assert seen["scope_active"] is True, "connection resolved OUTSIDE the profile scope"
    assert seen["scope_name"] == "team"
    assert bundle["provider"] == "custom"
    assert bundle["api_key"] == "team-key"
    assert bundle["base_url"] == "https://team.example/v1"
    _assert_no_foreign_fields(bundle)


def test_non_custom_provider_is_unchanged(monkeypatch):
    import api.streaming as streaming

    called = False

    def _unexpected(provider):
        nonlocal called
        called = True
        return (None, None)

    monkeypatch.setattr(streaming, "resolve_custom_provider_connection", _unexpected)

    bundle = _bundle("openrouter", None, None, None)

    assert (bundle["provider"], bundle["api_key"], bundle["base_url"]) == ("openrouter", None, None)
    assert called is False


def test_custom_provider_env_name_is_posix_safe():
    import api.config as config

    assert config._api_key_env_name("custom:gpu.local-8000") == "CUSTOM_GPU_LOCAL_8000_API_KEY"
    assert config._api_key_env_name("custom:10.8.71.41:8080") == "CUSTOM_10_8_71_41_8080_API_KEY"
    assert config._api_key_env_name("custom/foo bar") == "CUSTOM_FOO_BAR_API_KEY"


def test_resolve_custom_provider_connection_prefers_sanitized_env(monkeypatch):
    import api.config as config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: {
            "custom_providers": [
                {"name": "gpu.local-8000", "base_url": "http://gpu.local:8000/v1"},
            ],
        },
    )
    monkeypatch.setenv("CUSTOM_GPU_LOCAL_8000_API_KEY", "sanitized-key")
    monkeypatch.setenv("CUSTOM:GPU.LOCAL-8000_API_KEY", "legacy-key")

    api_key, base_url = config.resolve_custom_provider_connection("custom:gpu.local-8000")

    assert api_key == "sanitized-key"
    assert base_url == "http://gpu.local:8000/v1"


def test_resolve_custom_provider_connection_falls_back_to_legacy_env(monkeypatch, caplog):
    import logging
    import api.config as config

    config._LEGACY_CUSTOM_API_KEY_ENV_WARNED.clear()
    monkeypatch.setattr(
        config,
        "get_config",
        lambda: {
            "custom_providers": [
                {"name": "gpu.local-8000", "base_url": "http://gpu.local:8000/v1"},
            ],
        },
    )
    monkeypatch.delenv("CUSTOM_GPU_LOCAL_8000_API_KEY", raising=False)
    monkeypatch.setenv("CUSTOM:GPU.LOCAL-8000_API_KEY", "legacy-key")

    with caplog.at_level(logging.WARNING, logger="api.config"):
        api_key, _base_url = config.resolve_custom_provider_connection("custom:gpu.local-8000")

    assert api_key == "legacy-key"
    assert "CUSTOM_GPU_LOCAL_8000_API_KEY" in caplog.text
