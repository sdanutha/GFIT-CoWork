"""
Regression tests: a test run must not rewrite the real agent's launchers.

Every Hermes Agent entry point imports ``hermes_bootstrap``, which runs
``hermes_cli.venv_sync.prepare_launch()`` against the real agent checkout at
import time. With HERMES_HOME pointed at the test state dir, the agent's
package-manager state looks missing there, so a self-managed checkout tries to
finish a "source update": it installs a Python under the test dir and calls
``publish_launchers()``, which rewrites ``<agent>/.hermes/bin/hermes`` and
``hermes-acp`` to exec that temp Python. Once the test dir is gone, ``hermes``
is broken.

``HERMES_DISABLE_LAZY_INSTALLS=1`` is the agent's switch for hermetic test
harnesses: ``prepare_launch()`` returns before touching anything. conftest must
set it at module level, before any test imports the agent, so the pytest
process and the test server subprocess (whose env is copied from it) both
inherit it.
"""

import pytest


def test_conftest_disables_agent_launch_preparation():
    import os

    assert os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") == "1"


def test_prepare_launch_never_publishes_launchers(monkeypatch):
    from tests.conftest import HERMES_AGENT

    if HERMES_AGENT is None:
        pytest.skip("hermes-agent not found")
    venv_sync = pytest.importorskip("hermes_cli.venv_sync")
    if not hasattr(venv_sync, "prepare_launch"):
        pytest.skip("hermes-agent predates prepare_launch")

    calls = []

    def _refuse(name):
        def _spy(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"test run reached agent {name}")
        return _spy

    # Every step that can write to the agent checkout or install state fails
    # loudly instead of running, so a regression cannot do the damage itself.
    monkeypatch.setattr(venv_sync, "publish_launchers", _refuse("publish_launchers"))
    monkeypatch.setattr(venv_sync, "_finish_source_update", _refuse("_finish_source_update"))
    monkeypatch.setattr(venv_sync, "_sync_source_dependencies", _refuse("_sync_source_dependencies"))
    try:
        import hermes_cli.update_lock as update_lock
        monkeypatch.setattr(update_lock, "UpdateLock", _refuse("UpdateLock"))
    except ImportError:
        pass
    try:
        import hermes_cli.post_update as post_update
        monkeypatch.setattr(
            post_update, "step_adopt_blessed_checkout", _refuse("step_adopt_blessed_checkout")
        )
    except ImportError:
        pass

    assert venv_sync.prepare_launch(HERMES_AGENT, []) is None
    assert calls == []
