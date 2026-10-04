"""Each request reads its own Profile's config, even with other Profiles' requests in flight.

Architecture review round 5, candidate 12. Two requests in different Profiles
run on two threads at once; neither may see the other's config values.
Deterministic barriers order the steps, so the test does not depend on timing.
"""

import threading

import pytest


@pytest.fixture
def two_profiles(tmp_path, monkeypatch):
    import api.config as config
    import api.profiles as profiles

    base = tmp_path / "hermes"
    (base / "profiles" / "alice").mkdir(parents=True)
    (base / "profiles" / "bob").mkdir(parents=True)
    (base / "config.yaml").write_text("model:\n  default: default-model\n", encoding="utf-8")
    (base / "profiles" / "alice" / "config.yaml").write_text(
        "model:\n  default: alice-model\n", encoding="utf-8"
    )
    (base / "profiles" / "bob" / "config.yaml").write_text(
        "model:\n  default: bob-model\n", encoding="utf-8"
    )
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(config, "_DEFAULT_HERMES_HOME", base, raising=False)
    with config._yaml_file_cache_lock:
        config._yaml_file_cache.clear()
    config.reload_config()
    yield config, profiles
    with config._yaml_file_cache_lock:
        config._yaml_file_cache.clear()
    profiles.clear_request_profile()


def _run_in_profile(profiles, name, steps):
    """Run ``steps`` on a thread whose request Profile is ``name``."""
    errors = []

    def target():
        profiles.set_request_profile(name)
        try:
            steps()
        except BaseException as exc:  # surfaced by the test thread
            errors.append(exc)
        finally:
            profiles.clear_request_profile()

    thread = threading.Thread(target=target, name=f"request-{name}")
    thread.start()
    return thread, errors


def _model(cfg):
    return (cfg.get("model") or {}).get("default")


def test_a_request_keeps_its_profiles_config_while_another_profile_loads(two_profiles):
    config, profiles = two_profiles
    alice_loaded = threading.Barrier(2)
    bob_loaded = threading.Barrier(2)
    seen = {}

    def alice():
        cfg = config.get_config()
        seen["alice_before"] = _model(cfg)
        alice_loaded.wait(timeout=5)
        bob_loaded.wait(timeout=5)
        # Alice's request goes on using the config it was handed.
        seen["alice_after"] = _model(cfg)
        seen["alice_again"] = _model(config.get_config())

    def bob():
        alice_loaded.wait(timeout=5)
        seen["bob"] = _model(config.get_config())
        bob_loaded.wait(timeout=5)

    t_alice, alice_errors = _run_in_profile(profiles, "alice", alice)
    t_bob, bob_errors = _run_in_profile(profiles, "bob", bob)
    t_alice.join(10)
    t_bob.join(10)

    assert not alice_errors and not bob_errors, alice_errors + bob_errors
    assert seen == {
        "alice_before": "alice-model",
        "bob": "bob-model",
        "alice_after": "alice-model",
        "alice_again": "alice-model",
    }


def test_many_requests_in_two_profiles_never_read_each_others_config(two_profiles):
    config, profiles = two_profiles
    start = threading.Barrier(8)
    wrong = []

    def reader(name):
        def steps():
            start.wait(timeout=5)
            for _ in range(200):
                cfg = config.get_config()
                got = _model(cfg)
                if got != f"{name}-model":
                    wrong.append((name, got))
        return steps

    threads = []
    for i in range(8):
        name = "alice" if i % 2 else "bob"
        threads.append(_run_in_profile(profiles, name, reader(name)))
    for thread, _errors in threads:
        thread.join(30)
    errors = [e for _t, errs in threads for e in errs]

    assert not errors, errors
    assert wrong == []


def test_config_readers_follow_the_requests_profile_not_the_shared_cache(two_profiles):
    # Module functions that read config (the default model, the models list)
    # answer for the request's Profile, whichever Profile loaded last.
    config, profiles = two_profiles
    alice_loaded = threading.Barrier(2)
    bob_loaded = threading.Barrier(2)
    seen = {}

    def alice():
        config.get_config()
        alice_loaded.wait(timeout=5)
        bob_loaded.wait(timeout=5)
        seen["alice"] = config.get_effective_default_model()

    def bob():
        alice_loaded.wait(timeout=5)
        seen["bob"] = config.get_effective_default_model()
        bob_loaded.wait(timeout=5)

    t_alice, alice_errors = _run_in_profile(profiles, "alice", alice)
    t_bob, bob_errors = _run_in_profile(profiles, "bob", bob)
    t_alice.join(10)
    t_bob.join(10)

    assert not alice_errors and not bob_errors, alice_errors + bob_errors
    assert seen == {"alice": "alice-model", "bob": "bob-model"}
    assert config.get_effective_default_model() == "default-model"


def test_a_request_without_a_profile_reads_the_process_profiles_config(two_profiles):
    config, profiles = two_profiles
    seen = {}

    def alice():
        seen["alice"] = _model(config.get_config())

    thread, errors = _run_in_profile(profiles, "alice", alice)
    thread.join(10)

    assert not errors, errors
    assert seen["alice"] == "alice-model"
    assert _model(config.get_config()) == "default-model"


# ── Guard: config.py reads config through _active_cfg() ───────────────────

# The functions that own the shared cache and the ``cfg`` override may read
# the alias; every other function reads the request's config.
_MAY_READ_THE_CFG_ALIAS = {
    "_cfg_has_in_memory_overrides",
    "get_config",
    "get_config_snapshot",
    "_active_cfg",
}


def _functions_reading_the_cfg_alias(source: str) -> set[str]:
    import ast

    def binds_cfg(fn) -> bool:
        if any(isinstance(n, ast.Global) and "cfg" in n.names for n in ast.walk(fn)):
            return False
        args = fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs
        if any(a.arg == "cfg" for a in args):
            return True
        return any(
            isinstance(n, ast.Name) and n.id == "cfg" and isinstance(n.ctx, ast.Store)
            for n in ast.walk(fn)
        )

    readers = set()

    def visit(node, enclosed: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = binds_cfg(child)
                if not enclosed and not local and any(
                    isinstance(n, ast.Name) and n.id == "cfg" and isinstance(n.ctx, ast.Load)
                    for n in ast.walk(child)
                ):
                    readers.add(child.name)
                visit(child, enclosed or local)
            else:
                visit(child, enclosed)

    visit(ast.parse(source), False)
    return readers


def test_config_module_functions_read_the_requests_config_not_the_cfg_alias():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "api" / "config.py").read_text(encoding="utf-8")
    assert _functions_reading_the_cfg_alias(source) - _MAY_READ_THE_CFG_ALIAS == set()


def test_the_guard_finds_a_function_that_reads_the_cfg_alias():
    source = "cfg = {}\n\ndef leaky():\n    return cfg.get('model')\n\ndef fine():\n    cfg = _active_cfg()\n    return cfg\n"
    assert _functions_reading_the_cfg_alias(source) == {"leaky"}


# ── Readers outside the config module (architecture review round 6, candidate 3) ──


class _CatalogConsulted(Exception):
    pass


def _no_catalog(*_args, **_kwargs):
    raise _CatalogConsulted()


def _write_config(base, profile, text):
    path = base / "config.yaml" if profile is None else base / "profiles" / profile / "config.yaml"
    path.write_text(text, encoding="utf-8")


def _reload(config):
    with config._yaml_file_cache_lock:
        config._yaml_file_cache.clear()
    config._cfg_views.clear()
    config.reload_config()


def test_a_session_model_on_a_provider_of_the_requests_profile_is_kept(two_profiles, tmp_path, monkeypatch):
    import api.routes as routes

    config, profiles = two_profiles
    _write_config(tmp_path / "hermes", "alice", "model:\n  default: alice-model\nproviders:\n  alpha:\n    base_url: http://alpha.invalid/v1\n")
    _reload(config)
    monkeypatch.setattr(routes, "get_available_models", _no_catalog)

    profiles.set_request_profile("alice")
    try:
        assert routes._resolve_compatible_session_model_state("@alpha:m1", "alpha") == ("@alpha:m1", "alpha", False)
    finally:
        profiles.clear_request_profile()


def test_a_provider_of_another_profile_is_not_taken_for_the_requests_profile(two_profiles, tmp_path, monkeypatch):
    import api.routes as routes

    config, profiles = two_profiles
    # The shared cache's Profile (the root) configures `alpha`; bob does not.
    _write_config(tmp_path / "hermes", None, "model:\n  default: default-model\nproviders:\n  alpha:\n    base_url: http://alpha.invalid/v1\n")
    _reload(config)
    monkeypatch.setattr(routes, "get_available_models", _no_catalog)

    profiles.set_request_profile("bob")
    try:
        # Not one of bob's providers: the resolver must ask the catalog.
        with pytest.raises(_CatalogConsulted):
            routes._resolve_compatible_session_model_state("@alpha:m1", "alpha")
    finally:
        profiles.clear_request_profile()


def test_the_goal_turn_budget_follows_the_requests_profile(two_profiles, tmp_path):
    from api import goals

    config, profiles = two_profiles
    _write_config(tmp_path / "hermes", "alice", "model:\n  default: alice-model\ngoals:\n  max_turns: 7\n")
    _write_config(tmp_path / "hermes", None, "model:\n  default: default-model\ngoals:\n  max_turns: 31\n")
    _reload(config)

    profiles.set_request_profile("alice")
    try:
        assert goals._default_max_turns() == 7
    finally:
        profiles.clear_request_profile()
    assert goals._default_max_turns() == 31


# ── Guard: no module outside the config module reads the ``cfg`` alias ──────


def _modules_reading_the_cfg_alias(sources: dict) -> dict:
    """{module: [line, ...]} where a module other than the config module reads the alias."""
    import ast

    found = {}
    for name, source in sources.items():
        tree = ast.parse(source)
        config_names = {"api.config"}
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "api.config":
                hits += [node.lineno for alias in node.names if alias.name == "cfg"]
            if isinstance(node, ast.ImportFrom) and node.module == "api":
                config_names |= {alias.asname or alias.name for alias in node.names if alias.name == "config"}
            if isinstance(node, ast.Import):
                config_names |= {alias.asname for alias in node.names if alias.name == "api.config" and alias.asname}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "cfg" and ast.unparse(node.value) in config_names:
                hits.append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and ast.unparse(node.args[0]) in config_names
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "cfg"
            ):
                hits.append(node.lineno)
        if hits:
            found[name] = sorted(hits)
    return found


def test_no_module_outside_the_config_module_reads_the_cfg_alias():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [p for p in (root / "api").rglob("*.py") if p.name != "config.py" or p.parent != root / "api"]
    files.append(root / "server.py")
    sources = {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in files}
    assert _modules_reading_the_cfg_alias(sources) == {}


def test_the_guard_finds_every_spelling_of_the_cfg_alias():
    sources = {
        "a.py": "from api.config import cfg as _active_cfg\n",
        "b.py": "from api import config as _config\nx = getattr(_config, 'cfg', {})\n",
        "c.py": "import api.config as c\nx = c.cfg\n",
        "d.py": "import api.config\nx = api.config.cfg\n",
        "e.py": "from api import config\nx = config.get_config()\ncfg = {}\n",
    }
    assert _modules_reading_the_cfg_alias(sources) == {"a.py": [1], "b.py": [2], "c.py": [2], "d.py": [2]}
