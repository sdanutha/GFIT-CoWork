"""``./scripts/test.sh --changed`` picks the test files a change touches."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("changed_tests", ROOT / "scripts" / "changed_tests.py")
changed_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changed_tests)

TESTS = {
    "tests/test_ui.py": 'UI_JS = ROOT / "static" / "ui.js"',
    "tests/test_config.py": "from api.config import load_settings",
    "tests/test_streams.py": "from tests._streams import open_stream",
    "tests/test_other.py": "def test_nothing(): pass",
}


def test_a_changed_test_file_runs_itself():
    assert changed_tests.select(["tests/test_other.py"], TESTS) == ["tests/test_other.py"]


def test_a_changed_static_file_selects_tests_naming_it():
    assert changed_tests.select(["static/ui.js"], TESTS) == ["tests/test_ui.py"]


def test_a_changed_module_selects_tests_importing_it():
    assert changed_tests.select(["api/config.py"], TESTS) == ["tests/test_config.py"]


def test_a_changed_test_helper_selects_tests_importing_it():
    assert changed_tests.select(["tests/_streams.py"], TESTS) == ["tests/test_streams.py"]


def test_a_change_to_shared_test_setup_selects_the_whole_suite():
    assert changed_tests.select(["docs/x.md", "tests/conftest.py"], TESTS) == ["tests/"]


def test_unrelated_changes_select_nothing():
    assert changed_tests.select(["docs/TESTING.md", "api/unused.py"], TESTS) == []
