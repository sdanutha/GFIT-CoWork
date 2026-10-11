"""GFIT-CoWork: the server always runs as the Deployment's ``default`` Profile (ticket 11).

Hermes's command line keeps a sticky active Profile in ``~/.hermes/active_profile``
(``hermes profile use <name>``). Upstream's WebUI started in that Profile. In
GFIT-CoWork a Profile is a User's: after an Operator ran ``hermes profile use
<employee ID>`` and the server restarted, the server took that User's Profile as
its own (HERMES_HOME and their private ``.env`` in the process environment), so
their secrets reached every other User's agent run and the Deployment's own keys
dropped out. A request's Profile comes from its Admission only; the process's
Profile is the Deployment's, whatever the sticky file says. Startup says when it
ignores one.

Each case starts a fresh interpreter: startup changes the process environment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
USER_X = "521740"
USER_Y = "671278"

_PROBE = r"""
import json, os, sys
sys.path.insert(0, %(repo)r)
import api.config as cfg
import api.profiles as profiles
from api.login import startup_check
out = {
    "process_profile": profiles.get_active_profile_name(),
    "hermes_home": os.environ.get("HERMES_HOME"),
    "x_provider_key": os.environ.get("ANTHROPIC_API_KEY"),
    "x_secret": os.environ.get("T11_X_ONLY"),
    "deployment_key": os.environ.get("OPENROUTER_API_KEY"),
    "deployment_secret": os.environ.get("T11_DEPLOY_ONLY"),
    "startup_lines": startup_check().lines,
}
with profiles.profile_env_for_background_worker(%(user_y)r, "ticket 11 probe"):
    out["x_secret_in_y_scope"] = os.getenv("T11_X_ONLY")
print("PROBE" + json.dumps(out))
"""


def _world(root: Path, sticky: str | None) -> Path:
    home = root / "hermes"
    for uid in (USER_X, USER_Y):
        (home / "profiles" / uid).mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  default: deploy-model\n  provider: openrouter\n")
    (home / ".env").write_text("OPENROUTER_API_KEY=sk-deploy-key\nT11_DEPLOY_ONLY=deploy\n")
    (home / "profiles" / USER_X / "config.yaml").write_text("model:\n  default: x-model\n  provider: anthropic\n")
    (home / "profiles" / USER_X / ".env").write_text("ANTHROPIC_API_KEY=sk-x-private\nT11_X_ONLY=x-secret\n")
    (home / "profiles" / USER_Y / ".env").write_text("")
    if sticky is not None:
        (home / "active_profile").write_text(sticky)
    return home


def _start(tmp_path: Path, sticky: str | None) -> dict:
    home = _world(tmp_path, sticky)
    (tmp_path / "users.json").write_text(json.dumps({USER_X: {"password": "x"}, USER_Y: {"password": "y"}}))
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("HERMES_", "OPENROUTER_", "ANTHROPIC_", "T11_"))
    }
    env.update({
        "HERMES_HOME": str(home),
        "HERMES_BASE_HOME": str(home),
        "HERMES_WEBUI_STATE_DIR": str(tmp_path / "state"),
        "HERMES_WEBUI_DIRECTORY": "memory",
        "HERMES_WEBUI_DIRECTORY_USERS": str(tmp_path / "users.json"),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    })
    done = subprocess.run(
        [sys.executable, "-c", _PROBE % {"repo": str(REPO), "user_y": USER_Y}],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert line, done.stdout[-2000:] + done.stderr[-2000:]
    result = json.loads(line[len("PROBE"):])
    result["home"] = str(home)
    return result


@pytest.fixture
def sticky_user(tmp_path):
    return _start(tmp_path, USER_X)


def test_the_server_runs_as_default_when_the_sticky_profile_is_a_user(sticky_user):
    assert sticky_user["process_profile"] == "default"
    assert sticky_user["hermes_home"] == sticky_user["home"]


def test_a_users_private_env_never_enters_the_server_process(sticky_user):
    assert sticky_user["x_provider_key"] in (None, "")
    assert sticky_user["x_secret"] is None


def test_the_deployments_env_stays_the_servers(sticky_user):
    assert sticky_user["deployment_key"] == "sk-deploy-key"
    assert sticky_user["deployment_secret"] == "deploy"


def test_a_users_secret_does_not_reach_another_users_agent_scope(sticky_user):
    assert sticky_user["x_secret_in_y_scope"] is None


def test_startup_says_it_ignores_the_sticky_profile(sticky_user):
    lines = "\n".join(sticky_user["startup_lines"])
    assert USER_X in lines and "default" in lines, sticky_user["startup_lines"]


@pytest.mark.parametrize("sticky", [None, "", "default"])
def test_no_warning_and_the_same_process_when_the_sticky_profile_is_default(tmp_path, sticky):
    result = _start(tmp_path, sticky)
    assert result["process_profile"] == "default"
    assert result["hermes_home"] == result["home"]
    assert result["deployment_key"] == "sk-deploy-key"
    assert not any("active profile" in line.lower() for line in result["startup_lines"])
