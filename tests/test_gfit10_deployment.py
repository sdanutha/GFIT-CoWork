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
ADMIN = "521740"


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

def test_compose_runs_hermes_agent_and_gfit_cowork_with_persistent_volumes():
    compose = _compose()
    services = compose["services"]
    assert set(services) == {"hermes-agent", "gfit-cowork"}
    declared = set(compose.get("volumes") or {})
    for name, service in services.items():
        mounted = {str(v).split(":", 1)[0] for v in service.get("volumes", [])}
        assert "hermes-home" in mounted, name
        assert mounted & declared, name
        assert service.get("restart") == "unless-stopped", name
    assert {"hermes-home", "hermes-agent-src"} <= declared


def test_several_deployments_can_share_one_server():
    compose = _compose()
    # The project name comes from the config, so each Team gets its own
    # containers, network and volumes.
    assert "${TEAM" in COMPOSE.read_text(encoding="utf-8").split("services:", 1)[0]
    for name, service in compose["services"].items():
        assert "container_name" not in service, f"{name}: a fixed name would clash between Deployments"
    # Only GFIT-CoWork is published, on loopback, on the Deployment's own port.
    assert "ports" not in compose["services"]["hermes-agent"]
    (port,) = compose["services"]["gfit-cowork"]["ports"]
    assert port.startswith("127.0.0.1:${GFIT_PORT"), port
    assert port.endswith(":8787"), port


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
        "HERMES_WEBUI_ADMIN_USERS", "API_SERVER_KEY",
    ):
        assert key in example, key
    assert example["HERMES_WEBUI_DIRECTORY"] == "ldap"
    assert example["HERMES_WEBUI_LDAP_URL"].startswith("ldaps://")
    # Both services read the Deployment's config file.
    for name, service in _compose()["services"].items():
        assert service.get("env_file") in (".env", [".env"]), name


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


def test_the_guide_covers_a_new_team_a_new_member_and_the_pilot():
    text = GUIDE.read_text(encoding="utf-8").lower()
    for heading in ("add a new team", "add a member", "pilot"):
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
    assert {"hermes-agent", "gfit-cowork"} <= set(config["services"])


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
        "HERMES_WEBUI_ADMIN_USERS": ADMIN,
        "HERMES_WEBUI_TEST_NETWORK_BLOCK": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
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
        ADMIN: {"password": PASSWORD, "display_name": "Admin"},
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
