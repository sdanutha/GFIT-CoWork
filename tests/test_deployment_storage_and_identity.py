"""Where a Deployment keeps its data and whom its containers run as (ADR 0009).

HERMES_HOME_DIR / HERMES_WORKSPACE_DIR may move home/ and workspace/ out of
the Deployment's folder; every container runs as 10000:10000 unless the
Operator picks another service user; the containers have readable names and
keep the agent image's /opt/data in volumes.
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

FAKE_DOCKER = """#!/bin/sh
case " $* " in
  *" ps "*) exit 0 ;;
esac
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
"""

linux_unprivileged = pytest.mark.skipif(
    not hasattr(os, "getuid") or os.getuid() == 0 or os.uname().sysname != "Linux",
    reason="checks what an unprivileged Operator on Linux gets",
)
needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")


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


def _as_test_user():
    return {**os.environ, "GFIT_UID": str(os.getuid()), "GFIT_GID": str(os.getgid())}


@pytest.fixture
def kit(tmp_path):
    root = tmp_path / "deploy"
    root.mkdir()
    for name in ("new-deployment", "cowork", "team.env.example", "docker-compose.yml"):
        shutil.copy2(DEPLOY / name, root / name)
    return root


@pytest.fixture
def ready(kit, tmp_path):
    """A filled-in Deployment owned by the test user, and a fake docker."""
    assert _run([str(kit / "new-deployment"), "sales", "1"], env=_as_test_user()).returncode == 0
    folder = kit / "deployments" / "sales"
    _set_env(
        folder / ".env",
        HERMES_WEBUI_LDAP_URL="ldaps://ad.example.test",
        HERMES_WEBUI_LDAP_DOMAIN="example.test",
        HERMES_WEBUI_LDAP_BASE_DN="DC=example,DC=test",
        OPENROUTER_API_KEY="test-provider-key",
    )
    (folder / "certs" / "ad-ca.crt").write_text("test-only\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "FAKE_DOCKER_LOG": str(tmp_path / "docker.log")}

    def cowork(*args):
        return _run([str(kit / "cowork"), "sales", *args], env=env)

    return folder, cowork


# ── the service user ────────────────────────────────────────────────────────

def test_a_new_deployment_runs_as_10000_by_default():
    env = _env_of(DEPLOY / "team.env.example")
    assert (env["UID"], env["GID"]) == ("10000", "10000")
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("${UID:-10000}") == 3 and compose.count("${GID:-10000}") == 3


@linux_unprivileged
def test_new_deployment_asks_for_sudo_when_it_cannot_give_the_folders_to_10000(kit):
    result = _run([str(kit / "new-deployment"), "sales", "1"],
                  env={k: v for k, v in os.environ.items() if k not in ("GFIT_UID", "GFIT_GID")})
    assert result.returncode == 1
    assert "sudo" in result.stderr and "10000:10000" in result.stderr
    assert not (kit / "deployments" / "sales").exists()


def test_new_deployment_writes_the_chosen_service_user(kit):
    result = _run([str(kit / "new-deployment"), "sales", "1"], env=_as_test_user())
    assert result.returncode == 0, result.stderr
    env = _env_of(kit / "deployments" / "sales" / ".env")
    assert (env["UID"], env["GID"]) == (str(os.getuid()), str(os.getgid()))


def test_new_deployment_never_runs_the_containers_as_root(kit):
    result = _run([str(kit / "new-deployment"), "sales", "1"], env={**os.environ, "GFIT_UID": "0", "GFIT_GID": "0"})
    assert result.returncode == 1
    assert not (kit / "deployments" / "sales").exists()


# ── HERMES_HOME_DIR ─────────────────────────────────────────────────────────

def test_cowork_writes_the_gateway_key_into_a_moved_home(ready, tmp_path):
    folder, cowork = ready
    moved = tmp_path / "data" / "sales-home"
    moved.mkdir(parents=True)
    _set_env(folder / ".env", HERMES_HOME_DIR=str(moved))
    result = cowork("up", "-d")
    assert result.returncode == 0, result.stderr
    key = _env_of(folder / ".env")["API_SERVER_KEY"]
    assert (moved / ".env").read_text(encoding="utf-8") == f"API_SERVER_KEY={key}\n"
    assert not (folder / "home" / ".env").exists()


def test_cowork_refuses_a_moved_home_that_is_missing(ready, tmp_path):
    folder, cowork = ready
    _set_env(folder / ".env", HERMES_HOME_DIR=str(tmp_path / "nowhere"))
    result = cowork("up", "-d")
    assert result.returncode == 1
    assert "missing" in result.stderr


def test_cowork_refuses_a_moved_home_that_is_a_symlink(ready, tmp_path):
    folder, cowork = ready
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "home-link"
    link.symlink_to(real, target_is_directory=True)
    _set_env(folder / ".env", HERMES_HOME_DIR=str(link))
    result = cowork("up", "-d")
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert not (real / ".env").exists()


# ── the rendered config ─────────────────────────────────────────────────────

def _config(kit, team):
    result = _run([str(kit / "cowork"), team, "config", "--format", "json"])
    if result.returncode != 0 and "is not a docker command" in result.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _mounts(config, service):
    return {v["target"]: v for v in config["services"][service]["volumes"]}


@needs_compose
def test_home_and_workspace_default_to_the_deployments_folder(kit):
    assert _run([str(kit / "new-deployment"), "sales", "1"], env=_as_test_user()).returncode == 0
    folder = (kit / "deployments" / "sales").resolve()
    config = _config(kit, "sales")
    for service, target in (("hermes-agent", "/home/hermes/.hermes"),
                            ("hermes-dashboard", "/home/hermes/.hermes"),
                            ("gfit-cowork", "/home/hermeswebui/.hermes")):
        assert Path(_mounts(config, service)[target]["source"]).resolve() == folder / "home", service
    assert Path(_mounts(config, "gfit-cowork")["/workspace"]["source"]).resolve() == folder / "workspace"


@needs_compose
def test_hermes_home_dir_and_workspace_dir_move_the_data(kit, tmp_path):
    assert _run([str(kit / "new-deployment"), "sales", "1"], env=_as_test_user()).returncode == 0
    home, workspace = tmp_path / "disk" / "home", tmp_path / "disk" / "workspace"
    _set_env(kit / "deployments" / "sales" / ".env", HERMES_HOME_DIR=str(home), HERMES_WORKSPACE_DIR=str(workspace))
    config = _config(kit, "sales")
    for service, target in (("hermes-agent", "/home/hermes/.hermes"),
                            ("hermes-dashboard", "/home/hermes/.hermes"),
                            ("gfit-cowork", "/home/hermeswebui/.hermes")):
        assert _mounts(config, service)[target]["source"] == str(home), service
    assert _mounts(config, "gfit-cowork")["/workspace"]["source"] == str(workspace)


@needs_compose
def test_containers_have_readable_names_and_keep_opt_data_in_volumes(kit):
    assert _run([str(kit / "new-deployment"), "sales", "1"], env=_as_test_user()).returncode == 0
    config = _config(kit, "sales")
    names = {name: service["container_name"] for name, service in config["services"].items()}
    assert names == {"hermes-agent": "gfit-sales-agent", "gfit-cowork": "gfit-sales-cowork",
                     "hermes-dashboard": "gfit-sales-dashboard"}
    for service, volume in (("hermes-agent", "agent-data"), ("hermes-dashboard", "dashboard-data")):
        mount = _mounts(config, service)["/opt/data"]
        assert (mount["type"], mount["source"]) == ("volume", volume), service
    assert config["volumes"]["agent-data"]["name"] == "gfit-sales_agent-data"
