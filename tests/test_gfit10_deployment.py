"""GFIT-CoWork ticket 10: the Deployment kit.

One Docker Compose file plus one config file runs one Deployment (Hermes Agent
+ GFIT-CoWork). Several Deployments share a server behind a reverse proxy that
serves HTTPS and separates them by hostname.

These tests check the kit itself (``deploy/``), that the login rate limit still
works per person behind the proxy, and that two Deployments on the same
machine keep their Members apart.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests._gfit_server import PASSWORD, WRONG_PASSWORD, Client, gfit_server as _gfit_server

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
ENV_EXAMPLE = DEPLOY / "team.env.example"
CADDYFILE = DEPLOY / "caddy" / "Caddyfile.example"
GUIDE = DEPLOY / "README.md"

MEMBER = "600001"


def _env_example() -> dict[str, str]:
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ── the kit ─────────────────────────────────────────────────────────────────

def test_compose_runs_hermes_agent_gfit_cowork_and_the_dashboard_on_one_home():
    compose = _compose()
    services = compose["services"]
    assert set(services) == {"hermes-agent", "gfit-cowork", "hermes-dashboard"}
    for name, service in services.items():
        mounted = {str(v).split(":", 1)[0] for v in service.get("volumes", [])}
        # Everything a Deployment keeps is in its own folder's home/.
        assert "./home" in mounted, name
        assert service.get("restart") == "unless-stopped", name
    assert set(compose.get("volumes") or {}) == {"hermes-agent-src"}


def test_gfit_cowork_is_built_from_the_checkout():
    service = _compose()["services"]["gfit-cowork"]
    assert service["build"]["context"].startswith("${GFIT_COWORK_SRC"), service["build"]
    assert service["pull_policy"] == "build"
    assert 'export GFIT_COWORK_SRC=' in (DEPLOY / "cowork").read_text(encoding="utf-8")


def test_several_deployments_can_share_one_server():
    compose = _compose()
    # The project name comes from the config, so each Team gets its own
    # containers, network and volumes.
    assert "${TEAM" in COMPOSE.read_text(encoding="utf-8").split("services:", 1)[0]
    for name, service in compose["services"].items():
        assert "container_name" not in service, f"{name}: a fixed name would clash between Deployments"
    # Only GFIT-CoWork and the dashboard are published, on loopback, on the
    # Deployment's own ports. The gateway is never published.
    assert "ports" not in compose["services"]["hermes-agent"]
    (port,) = compose["services"]["gfit-cowork"]["ports"]
    assert port.startswith("127.0.0.1:${GFIT_PORT"), port
    assert port.endswith(":8787"), port
    (port,) = compose["services"]["hermes-dashboard"]["ports"]
    assert port.startswith("127.0.0.1:${DASHBOARD_PORT"), port
    assert port.endswith(":9119"), port


def test_the_config_example_lists_every_value_the_compose_file_needs():
    text = COMPOSE.read_text(encoding="utf-8")
    needed = {
        m.group(1) for m in re.finditer(r"\$\{([A-Z0-9_]+)(:?\?[^}]*)?\}", text)
    }
    example = _env_example()
    assert needed <= set(example), f"missing from team.env.example: {sorted(needed - set(example))}"
    for key in (
        "TEAM", "GFIT_PORT", "GFIT_HOSTNAME",
        "HERMES_WEBUI_DIRECTORY", "HERMES_WEBUI_LDAP_URL", "HERMES_WEBUI_LDAP_BIND_FORMAT",
        "HERMES_WEBUI_LDAP_DOMAIN", "HERMES_WEBUI_LDAP_BASE_DN", "HERMES_WEBUI_LDAP_CA_CERT",
        "API_SERVER_KEY",
    ):
        assert key in example, key
    # There is no Admin (ADR 0006): the example does not offer the old setting.
    assert "HERMES_WEBUI_ADMIN_USERS" not in example
    assert "HERMES_WEBUI_ADMIN_USERS" not in ENV_EXAMPLE.read_text(encoding="utf-8")
    assert example["HERMES_WEBUI_DIRECTORY"] == "ldap"
    assert example["HERMES_WEBUI_LDAP_URL"].startswith("ldaps://")
    # Hermes Agent and GFIT-CoWork read the Deployment's config file. The
    # dashboard gets only its own settings, not the provider or AD values.
    services = _compose()["services"]
    for name in ("hermes-agent", "gfit-cowork"):
        assert services[name].get("env_file") in (".env", [".env"]), name
    assert "env_file" not in services["hermes-dashboard"]


def test_the_reverse_proxy_serves_each_deployment_on_its_own_hostname():
    text = CADDYFILE.read_text(encoding="utf-8")
    sites = re.findall(r"^([a-z0-9.-]+)\s*\{(.*?)^\}", text, re.M | re.S)
    hosts = {host.split(".", 1)[0]: body for host, body in sites}
    assert {"sales", "account"} <= set(hosts), hosts.keys()
    upstreams = {
        name: re.search(r"reverse_proxy\s+(127\.0\.0\.1:\d+)", body).group(1)
        for name, body in hosts.items()
    }
    assert upstreams["sales"] != upstreams["account"]
    # HTTPS only: no site is served over plain http://.
    assert not re.search(r"^http://", text, re.M)


def test_the_guide_covers_a_new_team_a_new_user_and_the_pilot():
    text = GUIDE.read_text(encoding="utf-8").lower()
    for heading in ("add a new team", "add a user", "pilot"):
        assert heading in text, heading
    assert "team.env.example" in text
    assert "caddyfile" in text


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_docker_compose_accepts_the_template(tmp_path):
    deployment = tmp_path / "sales"
    deployment.mkdir()
    shutil.copy(COMPOSE, deployment / "docker-compose.yml")
    shutil.copy(ENV_EXAMPLE, deployment / ".env")
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=deployment, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0 and "is not a docker command" in result.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["name"] == "gfit-sales"
    assert {"hermes-agent", "gfit-cowork", "hermes-dashboard"} <= set(config["services"])


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kwargs)


@pytest.fixture
def kit(tmp_path):
    """A copy of the kit's scripts and template, so Deployments are created
    in tmp_path/deployments and never in the checkout."""
    root = tmp_path / "deploy"
    root.mkdir()
    for name in ("new-deployment", "cowork", "team.env.example", "docker-compose.yml"):
        shutil.copy2(DEPLOY / name, root / name)
    return root


def _env_of(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def test_new_deployment_fills_in_ports_and_secrets(kit):
    result = _run([str(kit / "new-deployment"), "sales", "1"])
    assert result.returncode == 0, result.stderr
    folder = kit / "deployments" / "sales"
    env = _env_of(folder / ".env")
    assert env["TEAM"] == "sales"
    assert (env["GFIT_PORT"], env["DASHBOARD_PORT"]) == ("20187", "20119")
    assert env["GFIT_HOSTNAME"] == "sales.cowork.gfit.co.th"
    for key in ("API_SERVER_KEY", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "HERMES_DASHBOARD_BASIC_AUTH_SECRET"):
        assert len(env[key]) >= 16 and not env[key].startswith("replace-with"), key
    # The AD values are the Operator's to fill in.
    assert env["HERMES_WEBUI_LDAP_URL"] == ""
    assert (folder / ".env").stat().st_mode & 0o077 == 0
    for sub in ("home", "workspace", "certs"):
        assert (folder / sub).is_dir(), sub
    # A second Deployment cannot take the same ports or the same name.
    assert _run([str(kit / "new-deployment"), "account", "1"]).returncode != 0
    assert _run([str(kit / "new-deployment"), "sales", "2"]).returncode != 0
    second = _run([str(kit / "new-deployment"), "account", "2"])
    assert second.returncode == 0, second.stderr
    assert _env_of(kit / "deployments" / "account" / ".env")["API_SERVER_KEY"] != env["API_SERVER_KEY"]


def test_cowork_refuses_to_start_a_deployment_that_is_not_filled_in(kit):
    assert _run([str(kit / "new-deployment"), "sales", "1"]).returncode == 0
    result = _run([str(kit / "cowork"), "sales", "up", "-d"])
    assert result.returncode == 1
    for missing in ("HERMES_WEBUI_LDAP_URL", "OPENROUTER_API_KEY", "certs/ad-ca.crt"):
        assert missing in result.stderr, missing
    assert _run([str(kit / "cowork"), "nobody", "ps"]).returncode == 1
    assert _run([str(kit / "cowork"), "../sales", "ps"]).returncode == 1


# ── the gateway key in the default Profile's .env ──────────────────────────
#
# With two or more Profiles, Hermes Agent v0.21.6 runs its gateway multiplexed
# and reads API_SERVER_KEY only from the default Profile's .env (home/.env),
# not from the container's environment. Without the key there, the api_server
# never starts after a restart and GFIT-CoWork cannot reach the gateway.
# deploy/cowork copies the Deployment's key into home/.env before it starts
# anything (ADR 0008).

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
"""


@pytest.fixture
def ready(kit, tmp_path):
    """A Deployment that is filled in, and a fake `docker` that only records
    that it was called."""
    assert _run([str(kit / "new-deployment"), "sales", "1"]).returncode == 0
    folder = kit / "deployments" / "sales"
    env = folder / ".env"
    text = env.read_text(encoding="utf-8")
    for key, value in (
        ("HERMES_WEBUI_LDAP_URL", "ldaps://ad.example.test"),
        ("HERMES_WEBUI_LDAP_DOMAIN", "example.test"),
        ("HERMES_WEBUI_LDAP_BASE_DN", "DC=example,DC=test"),
        ("OPENROUTER_API_KEY", "test-provider-key"),
    ):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    env.write_text(text, encoding="utf-8")
    (folder / "certs" / "ad-ca.crt").write_text("test-only\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    run_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(log)}

    def cowork(*args):
        return _run([str(kit / "cowork"), "sales", *args], env=run_env)

    return folder, cowork, log, _env_of(env)["API_SERVER_KEY"]


def _key_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("API_SERVER_KEY=")]


def test_cowork_up_writes_the_deployment_key_into_a_fresh_home(ready):
    folder, cowork, log, key = ready
    result = cowork("up", "-d")
    assert result.returncode == 0, result.stderr
    home_env = folder / "home" / ".env"
    assert _key_lines(home_env) == [f"API_SERVER_KEY={key}"]
    assert home_env.stat().st_mode & 0o777 == 0o600
    assert log.read_text(encoding="utf-8").strip().startswith("compose ")
    # The key is never printed.
    assert key not in result.stdout + result.stderr


def test_cowork_replaces_a_mismatched_key_and_keeps_everything_else(ready):
    folder, cowork, _, key = ready
    home_env = folder / "home" / ".env"
    home_env.write_text(
        "# Hermes settings\n"
        "TERMINAL_TIMEOUT=60\n"
        "API_SERVER_KEY=a-random-key-the-dashboard-made-0000\n"
        "OPENROUTER_API_KEY=profile-key\n"
        "API_SERVER_KEY=\n"
        "LAST_LINE_WITHOUT_NEWLINE=1",
        encoding="utf-8",
    )
    home_env.chmod(0o644)
    result = cowork("restart")
    assert result.returncode == 0, result.stderr
    lines = home_env.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if not line.startswith("API_SERVER_KEY=")] == [
        "# Hermes settings", "TERMINAL_TIMEOUT=60", "OPENROUTER_API_KEY=profile-key", "LAST_LINE_WITHOUT_NEWLINE=1",
    ]
    assert _key_lines(home_env) == [f"API_SERVER_KEY={key}"]
    assert home_env.stat().st_mode & 0o777 == 0o600
    assert key not in result.stdout + result.stderr
    assert "a-random-key-the-dashboard-made" not in result.stdout + result.stderr


def test_cowork_up_again_leaves_a_synchronized_home_untouched(ready):
    folder, cowork, _, key = ready
    assert cowork("up", "-d").returncode == 0
    home_env = folder / "home" / ".env"
    before = home_env.stat()
    content = home_env.read_bytes()
    for _ in range(2):
        assert cowork("up", "-d").returncode == 0
    after = home_env.stat()
    assert home_env.read_bytes() == content
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    # Not regenerated: the Deployment's key is unchanged.
    assert _env_of(folder / ".env")["API_SERVER_KEY"] == key


def test_commands_that_start_nothing_do_not_touch_home(ready):
    folder, cowork, _, _ = ready
    for args in (("ps",), ("logs", "--tail", "5"), ("config", "--quiet")):
        assert cowork(*args).returncode == 0
    assert not (folder / "home" / ".env").exists()


def test_cowork_refuses_a_symlinked_home_env(ready, tmp_path):
    folder, cowork, log, key = ready
    outside = tmp_path / "outside.env"
    outside.write_text("KEEP=1\n", encoding="utf-8")
    (folder / "home" / ".env").symlink_to(outside)
    result = cowork("up", "-d")
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert outside.read_text(encoding="utf-8") == "KEEP=1\n"
    assert not log.exists(), "docker must not run when the key cannot be synchronized"
    assert key not in result.stdout + result.stderr


def test_cowork_refuses_a_symlinked_home(ready, tmp_path):
    folder, cowork, log, _ = ready
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (folder / "home").rmdir()
    (folder / "home").symlink_to(elsewhere)
    result = cowork("up", "-d")
    assert result.returncode == 1
    assert not (elsewhere / ".env").exists()
    assert not log.exists()


@pytest.mark.parametrize("bad", ["", "short", "replace-with-openssl-rand-hex-24"])
def test_cowork_refuses_a_deployment_without_a_usable_key(ready, bad):
    folder, cowork, log, _ = ready
    env = folder / ".env"
    env.write_text(re.sub(r"^API_SERVER_KEY=.*$", f"API_SERVER_KEY={bad}", env.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    result = cowork("up", "-d")
    assert result.returncode == 1
    assert "API_SERVER_KEY" in result.stderr
    assert not (folder / "home" / ".env").exists()
    assert not log.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write a read-only folder")
def test_cowork_fails_closed_when_home_env_cannot_be_written(ready):
    folder, cowork, log, key = ready
    home = folder / "home"
    home.chmod(0o500)
    try:
        result = cowork("up", "-d")
    finally:
        home.chmod(0o750)
    assert result.returncode == 1
    assert not log.exists()
    assert key not in result.stdout + result.stderr


# ── the rate limit behind the reverse proxy ────────────────────────────────

@pytest.fixture
def behind_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_WEBUI_TRUST_FORWARDED_FOR", "1")
    with _gfit_server(monkeypatch, tmp_path, users={MEMBER: "Member"}, profile_names=[MEMBER]) as s:
        yield s


def _login_from(srv, ip, password):
    return srv.client().request(
        "POST", "/api/auth/login", {"username": MEMBER, "password": password},
        headers={"X-Forwarded-For": ip},
    )[0]


def test_behind_the_proxy_the_rate_limit_is_per_person_not_per_proxy(behind_proxy):
    statuses = [_login_from(behind_proxy, "10.1.1.1", WRONG_PASSWORD) for _ in range(6)]
    assert statuses[-1] == 429, statuses
    # Someone else behind the same proxy can still log in.
    assert _login_from(behind_proxy, "10.2.2.2", PASSWORD) == 200


def test_without_the_opt_in_forwarded_addresses_are_ignored(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={MEMBER: "Member"}, profile_names=[MEMBER]) as srv:
        statuses = [_login_from(srv, f"10.1.1.{i}", WRONG_PASSWORD) for i in range(6)]
        assert statuses[-1] == 429, statuses


# ── two Deployments on one machine ─────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_deployment(root: Path, team: str, users_file: Path, profiles=()):
    from tests.conftest import HERMES_AGENT, SERVER_SCRIPT, VENV_PYTHON, _kill_process_tree, _wait_for_server

    home = root / team / "hermes"
    for name in profiles:
        (home / "profiles" / name).mkdir(parents=True)
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / team / "home"),
        "LANG": "C.UTF-8",
        "HERMES_HOME": str(home),
        "HERMES_BASE_HOME": str(home),
        "HERMES_WEBUI_STATE_DIR": str(root / team / "state"),
        "HERMES_WEBUI_DEFAULT_WORKSPACE": str(root / team / "workspace"),
        "HERMES_WEBUI_HOST": "127.0.0.1",
        "HERMES_WEBUI_PORT": str(port),
        "HERMES_WEBUI_DIRECTORY": "memory",
        "HERMES_WEBUI_DIRECTORY_USERS": str(users_file),
        "HERMES_WEBUI_TEST_NETWORK_BLOCK": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
        # Keeps the agent's import-time launch preparation from rewriting the
        # real agent launchers (see tests/conftest.py).
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    }
    if HERMES_AGENT:
        env["HERMES_WEBUI_AGENT_DIR"] = str(HERMES_AGENT)
    (root / team / "home").mkdir(parents=True)
    log = root / f"{team}.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [VENV_PYTHON or sys.executable, str(SERVER_SCRIPT)],
            cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT,
        )
    ok, reason = _wait_for_server(f"http://127.0.0.1:{port}", timeout=60, proc=proc, log_path=log)
    if not ok:
        _kill_process_tree(proc.pid)
        pytest.fail(f"{team} Deployment did not start: {reason}")
    return port, proc


@pytest.fixture
def two_deployments(tmp_path):
    from tests.conftest import _kill_process_tree

    # One company Directory, two Teams. The Member has a Profile in sales only.
    users = tmp_path / "directory-users.json"
    users.write_text(json.dumps({
        MEMBER: {"password": PASSWORD, "display_name": "Sales Member"},
    }))
    procs = []
    try:
        sales, proc = _start_deployment(tmp_path, "sales", users, profiles=[MEMBER])
        procs.append(proc)
        account, proc = _start_deployment(tmp_path, "account", users)
        procs.append(proc)
        yield sales, account
    finally:
        for proc in procs:
            _kill_process_tree(proc.pid)
            proc.wait(timeout=10)


def test_a_member_of_one_deployment_cannot_log_in_to_another(two_deployments):
    sales_port, account_port = two_deployments
    sales = Client(sales_port)

    status, body, _ = sales.login(MEMBER)
    assert status == 200, body

    # Same person, same company password, but no Profile in the other Team.
    status, body, _ = Client(account_port).login(MEMBER)
    assert status == 403, body

    # A session from one Deployment means nothing to the other.
    replay = Client(account_port)
    replay.cookies = dict(sales.cookies)
    assert replay.get("/api/auth/status")[1]["logged_in"] is False
    assert sales.get("/api/auth/status")[1]["logged_in"] is True
