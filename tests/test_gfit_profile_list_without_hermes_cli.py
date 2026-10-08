"""The Admin's Profile list does not depend on hermes_cli (upstream-gone ticket 12).

Without Hermes Agent's hermes_cli the list used to hold only ``default``, so
the Admin could not see, disable or delete anyone's Profile. The rows are now
built from GFIT-CoWork's own Profile paths and name rule.
"""
from __future__ import annotations

import sys

import api.profiles as profiles


def test_named_profiles_are_listed_when_hermes_cli_cannot_be_imported(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    (home / "profiles" / "600001").mkdir(parents=True)
    (home / "profiles" / "600002").mkdir()
    (home / "profiles" / "Not A Profile").mkdir()
    (home / "profiles" / "600001" / "config.yaml").write_text(
        "model:\n  default: some-model\n  provider: some-provider\n", encoding="utf-8"
    )
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", home)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)  # import raises ImportError

    rows = profiles._build_profile_rows_fast()

    assert [r["name"] for r in rows] == ["default", "600001", "600002"]
    first = rows[1]
    assert (first["model"], first["provider"]) == ("some-model", "some-provider")
    assert first["gateway_running"] is None  # not probed: unknown, not "not running"
    assert rows[2]["model"] is None
