"""Regression coverage for a Docker-compose gap found in the field.

1. Gateway API not reachable out of the box. A compose file set
   ``HERMES_API_URL=http://hermes-agent:8642`` on the WebUI but never
   configured the agent to listen on 8642. The agent image only starts its
   API-server listener when ``API_SERVER_KEY`` is a usable value (>=16
   chars); ``API_SERVER_ENABLED`` alone does nothing. The Deployment kit
   (``deploy/docker-compose.yml``, ADR 0005) forwards ``API_SERVER_KEY`` from
   ``.env`` and binds the listener on 0.0.0.0, and the WebUI receives the
   matching ``HERMES_WEBUI_GATEWAY_API_KEY`` so its health probe authenticates.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"


# ── 1: gateway API server forwarding ───────────────────────────────────────


def test_agent_service_enables_api_server():
    """The agent service must carry the API-server env block so the key set
    in .env gets a listening 8642 without editing the compose file."""
    src = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- API_SERVER_ENABLED=true" in src, (
        "deploy: agent must declare API_SERVER_ENABLED."
    )
    assert "- API_SERVER_HOST=0.0.0.0" in src, (
        "deploy: API server must bind 0.0.0.0 — the default 127.0.0.1 is "
        "unreachable from the WebUI container over the compose network."
    )
    assert "- API_SERVER_KEY=${API_SERVER_KEY:?" in src, (
        "deploy: agent must require API_SERVER_KEY from .env so the gateway "
        "API listener can start (the agent requires a usable key, >=16 chars)."
    )


def test_webui_service_forwards_gateway_api_key():
    """The WebUI must receive the same API_SERVER_KEY so its /health/detailed
    probe authenticates instead of 401ing into 'Gateway endpoint not
    reachable'."""
    src = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- HERMES_WEBUI_GATEWAY_API_KEY=${API_SERVER_KEY}" in src, (
        "deploy: WebUI must forward HERMES_WEBUI_GATEWAY_API_KEY from the "
        "same API_SERVER_KEY so the gateway health probe authenticates."
    )


def test_env_example_documents_api_server_key():
    """team.env.example must tell the Admin the API server needs a usable
    API_SERVER_KEY (16+ chars) shared between GFIT-CoWork and the gateway."""
    example = (DEPLOY / "team.env.example").read_text(encoding="utf-8")
    assert "API_SERVER_KEY=" in example, (
        "team.env.example must document API_SERVER_KEY."
    )
    assert "16+ characters" in example, (
        "team.env.example must state the API_SERVER_KEY length floor "
        "(16+ chars) — shorter keys are silently ignored by the agent."
    )
    assert "between GFIT-CoWork and Hermes Agent's gateway" in example, (
        "team.env.example must say the key is shared by GFIT-CoWork and the "
        "gateway, so the Admin sets it once."
    )
