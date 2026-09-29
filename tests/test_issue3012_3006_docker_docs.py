from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKER_MD = (REPO / "docs" / "docker.md").read_text(encoding="utf-8")


def test_docker_docs_explain_host_localhost_for_api_urls():
    """#3012: container localhost is not the Docker host localhost."""
    assert "API base URL set to localhost fails from Docker" in DOCKER_MD
    assert "Inside a container, `localhost` means *that container*" in DOCKER_MD
    assert "host.docker.internal" in DOCKER_MD
    assert "host.containers.internal" in DOCKER_MD
    assert "host-gateway" in DOCKER_MD


def test_related_issues_index_references_3012():
    related = DOCKER_MD[DOCKER_MD.index("## Related issues"):]
    assert "#3012" in related
