"""GFIT-CoWork ticket 02: the Directory seam (username normalisation + in-memory Directory)."""
import json

import pytest

from api import directory
from api.directory import Identity, InMemoryDirectory, normalize_username


@pytest.mark.parametrize(
    "raw",
    ["521740", "GFIT\\521740", "gfit\\521740", "521740@gfit.co.th", "  521740  ", "ABC123@GFIT.CO.TH"],
)
def test_normalize_strips_domain_prefix_and_suffix_and_lowercases(raw):
    expected = "abc123" if "ABC" in raw else "521740"
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "default",            # the built-in Profile, never a User's own
        "has space",
        "../etc",
        "a/b",
        "-leading-dash",
        "x" * 65,
        "5217\n40",
        "GFIT\\",
        "@gfit.co.th",
        "a\\b\\c",
    ],
)
def test_normalize_refuses_names_outside_profile_name_rules(raw):
    assert normalize_username(raw) is None


def test_in_memory_directory_confirms_correct_password():
    d = InMemoryDirectory({"521740": {"password": "s3cret", "display_name": "Somchai Jaidee"}})
    assert d.authenticate("521740", "s3cret") == Identity("521740", "Somchai Jaidee")


def test_in_memory_directory_accepts_every_username_form():
    d = InMemoryDirectory({"521740": {"password": "s3cret", "display_name": "Somchai"}})
    for form in ("GFIT\\521740", "521740@gfit.co.th", "521740"):
        assert d.authenticate(form, "s3cret") == Identity("521740", "Somchai")


def test_in_memory_directory_display_name_defaults_to_employee_id():
    d = InMemoryDirectory({"521740": {"password": "s3cret"}})
    assert d.authenticate("521740", "s3cret") == Identity("521740", "521740")


@pytest.mark.parametrize(
    "username,password",
    [("521740", "wrong"), ("521740", ""), ("999999", "s3cret"), ("", "s3cret"), ("default", "s3cret")],
)
def test_in_memory_directory_refuses_wrong_password_or_unknown_user(username, password):
    d = InMemoryDirectory({"521740": {"password": "s3cret"}})
    assert d.authenticate(username, password) is None


def test_in_memory_directory_refuses_non_string_password():
    d = InMemoryDirectory({"521740": {"password": "s3cret"}})
    assert d.authenticate("521740", None) is None
    assert d.authenticate("521740", 123) is None


def test_directory_not_configured_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_DIRECTORY", raising=False)
    assert directory.is_directory_enabled() is False
    assert directory.get_directory() is None


def test_memory_directory_loaded_from_users_file(monkeypatch, tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"GFIT\\521740": {"password": "pw", "display_name": "Somchai"}}))
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY_USERS", str(users))
    assert directory.is_directory_enabled() is True
    assert directory.get_directory().authenticate("521740", "pw") == Identity("521740", "Somchai")


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"521740": "pw"}'])
def test_memory_directory_with_missing_or_malformed_users_file_refuses_everyone(monkeypatch, tmp_path, content):
    users = tmp_path / "users.json"
    if content is not None:
        users.write_text(content)
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY_USERS", str(users))
    assert directory.is_directory_enabled() is True
    assert directory.get_directory().authenticate("521740", "pw") is None


def test_unknown_directory_kind_is_enabled_but_refuses_everyone(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "carrier-pigeon")
    assert directory.is_directory_enabled() is True
    assert directory.get_directory().authenticate("521740", "pw") is None
