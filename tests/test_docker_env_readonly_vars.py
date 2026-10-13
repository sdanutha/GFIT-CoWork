"""The Docker image installs the system packages the agent and GFIT-CoWork need."""
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestDockerfileSystemPackages:
    """Pin Dockerfile system-package dependencies (#1686 Cluster 1)."""

    def test_dockerfile_installs_xz_utils(self):
        """xz-utils is required to extract xz-compressed tarballs (e.g.
        Node.js distribution archives) — without it, agent install paths
        that download xz-compressed deps fail with `xz: Cannot exec`."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"\bxz-utils\b", dockerfile), (
            "Dockerfile must install xz-utils (apt package) — without it, "
            "any tarball decompression of .tar.xz files fails with "
            "`xz: Cannot exec: No such file or directory`"
        )

    def test_dockerfile_installs_git(self):
        """git is needed for any agent-install path that clones a repo, plus
        for the runtime ``git describe`` that powers WEBUI_VERSION detection
        in non-baked images."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^\s*git\s*\\?\s*$", dockerfile, re.MULTILINE), (
            "Dockerfile must install git (apt package) — required for "
            "version detection (`git describe`) and any agent install path "
            "that clones a repo"
        )
