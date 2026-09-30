"""Regression coverage for #6619 / #6620 — extension dotfile handling.

`_is_safe_relative_path()` is shared by static serving, asset URLs and manifest
paths, and MUST stay strict (no dot-prefixed segment ever reachable) — loosening
it once let `/extensions/.env`, `/extensions/.git/config`, etc. return HTTP 200.
"""
import pytest

from api.extensions import _is_safe_relative_path


class TestSharedValidatorStaysStrict:
    """Static serving / assets / manifest must never reach a hidden file."""

    @pytest.mark.parametrize("rel", [
        ".env", ".secret", ".git", ".git/config", ".git/hooks/pre-commit",
        ".gitkeep", ".gitignore", ".env.example",
        "screenshots/.gitkeep", "sub/.env.example", "a/.git/config",
    ])
    def test_rejects_every_dotfile(self, rel):
        assert _is_safe_relative_path(rel) is False

    @pytest.mark.parametrize("rel", ["index.html", "assets/style.css", "a/b/c.js"])
    def test_allows_normal_paths(self, rel):
        assert _is_safe_relative_path(rel) is True

    @pytest.mark.parametrize("rel", ["../evil", "a/../../etc", "a/./b", "..", ".", "", "a\x00b", "a\\b"])
    def test_still_blocks_traversal_and_junk(self, rel):
        assert _is_safe_relative_path(rel) is False
