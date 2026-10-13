"""A Development Deployment: the deploy kit on a developer's machine (ADR 0009).

`deploy/new-deployment --dev` makes one, and `deploy/cowork` adds the
development overlay to it. These tests drive the kit's scripts from the outside,
with a fake `docker` that records its calls, and render the Compose config with
the real Docker CLI (no daemon needed) to check what gets published.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
KIT_FILES = ("new-deployment", "cowork", "team.env.example", "docker-compose.yml", "docker-compose.dev.yml")

FAKE_DOCKER = """#!/bin/sh
case " $* " in
  *" ps "*) exit 0 ;;
esac
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
"""


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kwargs)


def _env_of(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def _set_env(path: Path, **values: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _service_user_is_the_test_user(monkeypatch):
    """A Team's Deployment belongs to 10000:10000, which needs root (ADR 0009);
    the tests run unprivileged."""
    if hasattr(os, "getuid"):
        monkeypatch.setenv("GFIT_UID", str(os.getuid()))
        monkeypatch.setenv("GFIT_GID", str(os.getgid()))


@pytest.fixture
def kit(tmp_path):
    """A copy of the kit, so Deployments are created in tmp_path and never in the checkout."""
    root = tmp_path / "deploy"
    root.mkdir()
    for name in KIT_FILES:
        shutil.copy2(DEPLOY / name, root / name)
    return root


@pytest.fixture
def fake_docker(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(log)}
    return env, log


needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")


@needs_openssl
def test_new_deployment_dev_makes_a_development_deployment_with_the_mock_directory(kit):
    result = _run([str(kit / "new-deployment"), "--dev", "dev", "0"])
    assert result.returncode == 0, result.stderr
    folder = kit / "deployments" / "dev"
    env = _env_of(folder / ".env")
    assert env["GFIT_DEV"] == "1"
    assert env["GFIT_HOSTNAME"] == "localhost"
    assert env["DEV_BIND"] == "127.0.0.1"
    assert (env["GFIT_PORT"], env["DASHBOARD_PORT"], env["GATEWAY_PORT"], env["A2A_PORT"], env["DEBUG_PORT"]) == (
        "8787", "9119", "8642", "9900", "5678")
    assert env["HERMES_WEBUI_LDAP_URL"] == "ldaps://mock-ldap:636"
    assert env["HERMES_WEBUI_LDAP_BIND_FORMAT"] == "uid={username},ou=people,dc=gfit,dc=local"
    assert env["HERMES_WEBUI_LDAP_USER_FILTER"] == "(uid={username})"
    # The provider key comes from `hermes setup`, not from a placeholder.
    assert env["OPENROUTER_API_KEY"] == ""
    # The mock Directory's throwaway CA is the Deployment's AD CA.
    ca = (folder / "certs" / "mock-ldap" / "ca.crt").read_bytes()
    assert ca and (folder / "certs" / "ad-ca.crt").read_bytes() == ca
    assert (folder / "certs" / "mock-ldap" / "server.crt").is_file()
    assert not (folder / "certs" / "mock-ldap" / "ca.key").exists()
    assert "http://localhost:8787" in result.stdout


@needs_openssl
def test_development_deployments_get_ports_of_their_own(kit):
    assert _run([str(kit / "new-deployment"), "--dev", "dev", "0"]).returncode == 0
    assert _run([str(kit / "new-deployment"), "--dev", "dev2", "0"]).returncode != 0
    assert _run([str(kit / "new-deployment"), "--dev", "dev2", "3"]).returncode == 0
    env = _env_of(kit / "deployments" / "dev2" / ".env")
    assert (env["GFIT_PORT"], env["DASHBOARD_PORT"], env["GATEWAY_PORT"], env["A2A_PORT"], env["DEBUG_PORT"]) == (
        "20387", "20319", "20342", "20300", "20378")


def test_a_development_deployment_is_always_on_localhost(kit):
    result = _run([str(kit / "new-deployment"), "--dev", "dev", "0", "dev.cowork.gfit.co.th"])
    assert result.returncode == 1
    assert "localhost" in result.stderr
    assert not (kit / "deployments" / "dev").exists()


@needs_openssl
def test_cowork_adds_the_development_overlay_to_a_development_deployment(kit, fake_docker):
    env, log = fake_docker
    assert _run([str(kit / "new-deployment"), "--dev", "dev", "0"]).returncode == 0
    result = _run([str(kit / "cowork"), "dev", "up", "-d"], env=env)
    assert result.returncode == 0, result.stderr
    call = log.read_text(encoding="utf-8").split()
    files = [call[i + 1] for i, arg in enumerate(call) if arg == "-f"]
    assert [Path(f).name for f in files] == ["docker-compose.yml", "docker-compose.dev.yml"]


def test_cowork_never_adds_the_overlay_to_a_teams_deployment(kit, fake_docker):
    env, log = fake_docker
    assert _run([str(kit / "new-deployment"), "sales", "1"]).returncode == 0
    assert _run([str(kit / "cowork"), "sales", "ps"], env=env).returncode == 0
    assert _run([str(kit / "cowork"), "sales", "logs"], env=env).returncode == 0
    assert "docker-compose.dev.yml" not in log.read_text(encoding="utf-8")


@needs_openssl
def test_cowork_refuses_development_mode_off_localhost(kit, fake_docker):
    env, log = fake_docker
    assert _run([str(kit / "new-deployment"), "--dev", "dev", "0"]).returncode == 0
    _set_env(kit / "deployments" / "dev" / ".env", GFIT_HOSTNAME="dev.cowork.gfit.co.th")
    for args in (["up", "-d"], ["ps"], ["logs"]):
        result = _run([str(kit / "cowork"), "dev", *args], env=env)
        assert result.returncode == 1, args
        assert "localhost" in result.stderr
    assert not log.exists()


# ── the rendered Compose config ─────────────────────────────────────────────

needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")


def _config(kit, team):
    result = _run([str(kit / "cowork"), team, "config", "--format", "json"])
    if result.returncode != 0 and "is not a docker command" in result.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _published(config, service):
    return sorted(
        (p.get("host_ip", ""), int(p["published"]), int(p["target"]))
        for p in config["services"][service].get("ports", [])
    )


@needs_compose
@needs_openssl
def test_a_development_deployment_publishes_everything_on_loopback(kit):
    assert _run([str(kit / "new-deployment"), "--dev", "dev", "0"]).returncode == 0
    config = _config(kit, "dev")
    assert _published(config, "gfit-cowork") == [("127.0.0.1", 5678, 5678), ("127.0.0.1", 8787, 8787)]
    assert _published(config, "hermes-agent") == [("127.0.0.1", 8642, 8642), ("127.0.0.1", 9900, 9900)]
    assert _published(config, "hermes-dashboard") == [("127.0.0.1", 9119, 9119)]
    assert _published(config, "mock-ldap") == [("127.0.0.1", 1389, 389), ("127.0.0.1", 1636, 636)]
    cowork = config["services"]["gfit-cowork"]
    assert cowork["build"]["target"] == "dev"
    assert cowork["environment"]["HERMES_WEBUI_SECURE"] == "0"
    assert cowork["environment"]["GFIT_DEBUGPY_PORT"] == "5678"
    mounts = {v["target"]: v for v in cowork["volumes"]}
    # The checkout is the app; its venv stays out of the checkout.
    assert mounts["/app"]["type"] == "bind"
    assert Path(mounts["/app"]["source"]).resolve() == kit.parent.resolve()
    assert mounts["/app/venv"]["type"] == "volume"


@needs_compose
@needs_openssl
def test_dev_bind_opens_a_development_deployment_to_the_network(kit):
    assert _run([str(kit / "new-deployment"), "--dev", "dev", "0"]).returncode == 0
    _set_env(kit / "deployments" / "dev" / ".env", DEV_BIND="0.0.0.0")
    config = _config(kit, "dev")
    for service in ("gfit-cowork", "hermes-agent", "hermes-dashboard", "mock-ldap"):
        assert {ip for ip, _, _ in _published(config, service)} == {"0.0.0.0"}, service


@needs_compose
def test_a_teams_deployment_publishes_only_the_app_and_the_dashboard_on_loopback(kit):
    assert _run([str(kit / "new-deployment"), "sales", "1"]).returncode == 0
    config = _config(kit, "sales")
    assert _published(config, "gfit-cowork") == [("127.0.0.1", 20187, 8787)]
    assert _published(config, "hermes-dashboard") == [("127.0.0.1", 20119, 9119)]
    # The gateway and A2A are never published (ADR 0003, ADR 0008).
    assert _published(config, "hermes-agent") == []
    assert "mock-ldap" not in config["services"]
    assert "target" not in config["services"]["gfit-cowork"]["build"]
