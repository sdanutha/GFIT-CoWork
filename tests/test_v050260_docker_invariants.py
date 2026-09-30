"""Regression tests for v0.50.260 — Docker compose file invariants.

PR #1428 fixed a UID/GID mismatch between the agent container and the webui
container. This module pins that invariant on the Deployment kit
(`deploy/docker-compose.yml`, ADR 0005) and the related documentation:

- Both services take their IDs from the same source (`${UID}` / `${GID}`)
- The kit's Compose files parse and the WebUI reaches the gateway service
- `docs/docker.md` exists and covers the recurring failure modes
- Stale README references to `/root/.hermes` are gone (the agent images
  use `/home/hermes/.hermes`)
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY_COMPOSE = REPO / "deploy" / "docker-compose.yml"


# ── 1: UID/GID alignment between the Deployment's services (PR #1428) ─────


def test_deployment_aligns_agent_uid_with_webui():
    """REGRESSION (#1399, fixed in #1428): the agent and the WebUI must take
    their UID/GID from the same source. Before #1428 the agent had no
    HERMES_UID/HERMES_GID at all and used the image default of 10000, while
    the webui used 1000 — files the agent wrote to the shared hermes-home
    volume were unreadable by the webui."""
    src = DEPLOY_COMPOSE.read_text(encoding="utf-8")

    # Agent must declare HERMES_UID/HERMES_GID
    assert "HERMES_UID=${UID:-1000}" in src, (
        "deploy: hermes-agent must set HERMES_UID=${UID:-1000} so it "
        "matches the webui's WANTED_UID=${UID:-1000}. Before #1428 the agent "
        "ran as the image default (10000), causing PermissionError on the "
        "shared hermes-home volume."
    )
    assert "HERMES_GID=${GID:-1000}" in src, (
        "deploy: hermes-agent must set HERMES_GID=${GID:-1000}"
    )

    # WebUI must use ${UID}/${GID} (same source)
    assert "WANTED_UID=${UID:-1000}" in src
    assert "WANTED_GID=${GID:-1000}" in src

    # The pre-#1428 default of 10000 must NOT appear anywhere
    # (negative-pattern guard prevents revert)
    assert "HERMES_UID:-10000" not in src, (
        "Pre-#1428 default (HERMES_UID:-10000) must not return — that's the "
        "bug shape. All UIDs should pull from ${UID:-1000}."
    )
    assert "HERMES_GID:-10000" not in src


# ── 2: docs/docker.md comprehensive guide ──────────────────────────────────


def test_docs_docker_md_exists_and_covers_failure_modes():
    """The docs/docker.md guide must exist and cover the recurring failure
    modes seen in #1399, #1389, #858, #681, #668."""
    p = REPO / "docs" / "docker.md"
    assert p.exists(), "docs/docker.md must exist as the comprehensive guide"
    src = p.read_text(encoding="utf-8")

    # Must mention each documented failure mode by issue ref
    for issue in ("#1389", "#1399", "#858", "#681"):
        assert issue in src, (
            f"docs/docker.md must reference issue {issue} so users searching "
            f"for the symptom find the right diagnostic path."
        )


# ── 3: stale /root/.hermes references removed from README ──────────────────


def test_readme_no_stale_root_hermes_path():
    """REGRESSION: the README's two-container Docker section used to claim
    'the agent writes to /root/.hermes' which is wrong — current agent
    images use /home/hermes/.hermes. Stale paths confuse users reading
    the README to debug their own setup."""
    src = (REPO / "README.md").read_text(encoding="utf-8")
    assert "/root/.hermes" not in src, (
        "README.md must not reference /root/.hermes — the current agent "
        "image uses /home/hermes/.hermes. Stale paths in docs are worse "
        "than no docs at all."
    )


def test_readme_links_to_docker_md():
    """The README Docker section should point at docs/docker.md for the
    deep dive so we don't have to keep two copies of the same content
    in sync."""
    src = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/docker.md" in src, (
        "README.md should reference docs/docker.md so users with deeper "
        "needs (volumes, UID/GID, upgrades) find the full guide."
    )


# ── 4: the kit's compose files all parse as valid YAML ─────────────────────


def test_compose_files_parse_as_valid_yaml():
    """Every compose file in the Deployment kit must parse as valid YAML —
    without this guard, a stray indentation or unquoted ${VAR} could ship a
    broken compose file that breaks `docker compose up` for every Team."""
    import yaml

    for path in (DEPLOY_COMPOSE, REPO / "deploy" / "caddy" / "docker-compose.yml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise AssertionError(f"{path} is not valid YAML: {e}") from e
        assert isinstance(data, dict), f"{path} must parse to a dict"
        assert "services" in data, f"{path} must define a `services:` block"


def test_deployment_webui_points_at_gateway_service():
    """REGRESSION (#4483): the WebUI service needs a compose network URL for
    the gateway container. Cron listing reads shared state, but scheduled
    ticking and the Tasks/System gateway health pill need a reachable gateway
    base URL from inside the WebUI container."""
    import yaml

    data = yaml.safe_load(DEPLOY_COMPOSE.read_text(encoding="utf-8"))
    env = data["services"]["gfit-cowork"]["environment"]
    assert "HERMES_API_URL=http://hermes-agent:8642" in env, (
        "deploy: gfit-cowork must point at hermes-agent over the compose "
        "network so gateway health and scheduled ticking work."
    )


# ── 5: Docker localhost troubleshooting (#3012) ─────────────────────────────


def test_onboarding_docs_cover_linux_host_gateway_for_container_localhost():
    """REGRESSION (#3012): local-provider onboarding docs must include the
    Linux Docker host-gateway shape, not only Docker Desktop's
    `host.docker.internal` shortcut."""
    src = (REPO / "docs" / "onboarding.md").read_text(encoding="utf-8")
    assert "host.docker.internal" in src
    assert "host-gateway" in src
    assert "extra_hosts" in src
    assert "api.local" in src
    assert "localhost" in src and "container" in src
