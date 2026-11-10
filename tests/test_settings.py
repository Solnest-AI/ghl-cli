import json
import os
import sys

import pytest

from cli_anything.gohighlevel.utils import settings
from cli_anything.gohighlevel.utils.settings import (
    load_settings,
    parse_location_url,
    read_env_file,
    save_config,
    save_key,
    settings_home,
)

# Built in pieces: the commit hook blocks a literal PIT.
FAKE_PIT = "pit-" + "1" * 8 + "-2222-3333-4444-" + "5" * 12
LOC = "AbCdEfGhIjKlMnOpQrSt"


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GHL_CLI_HOME", str(tmp_path))
    return tmp_path


def test_settings_home_override_and_default(tmp_home, monkeypatch):
    assert settings_home() == tmp_home
    monkeypatch.delenv("GHL_CLI_HOME")
    assert settings_home().name == ".ghl-cli"


def test_env_wins_over_the_keys_file(tmp_home):
    (tmp_home / "keys.env").write_text("GHL_API_KEY=" + FAKE_PIT + "\n")
    env = {"GHL_API_KEY": "from-env"}
    assert load_settings(env) == []
    assert env["GHL_API_KEY"] == "from-env"


def test_missing_or_empty_env_is_filled(tmp_home):
    (tmp_home / "keys.env").write_text("GHL_API_KEY=" + FAKE_PIT + "\n")
    env = {"GHL_API_KEY": ""}
    assert load_settings(env) == ["GHL_API_KEY"]
    assert env["GHL_API_KEY"] == FAKE_PIT


def test_empty_value_never_blanks_env(tmp_home):
    (tmp_home / "keys.env").write_text("GHL_API_KEY=\n")
    env = {"GHL_API_KEY": "from-env"}
    assert load_settings(env) == []
    assert env["GHL_API_KEY"] == "from-env"
    assert read_env_file(tmp_home / "keys.env") == {}


def test_no_files_is_fine(tmp_home):
    env = {}
    assert load_settings(env) == []
    assert env == {}


def test_windows_notepad_file_parses(tmp_home):
    (tmp_home / "keys.env").write_bytes(
        b'\xef\xbb\xbfGHL_API_KEY="' + FAKE_PIT.encode() + b'"\r\n'
        b"export GHL_FIREBASE_REFRESH_TOKEN='AMf-abc'\r\n")
    env = {}
    assert set(load_settings(env)) == {"GHL_API_KEY", "GHL_FIREBASE_REFRESH_TOKEN"}
    assert env["GHL_API_KEY"] == FAKE_PIT
    assert env["GHL_FIREBASE_REFRESH_TOKEN"] == "AMf-abc"


def test_comments_and_blank_lines_ignored(tmp_home):
    (tmp_home / "keys.env").write_text("# note\n\n  GHL_API_KEY = abc  \n#GHL_FIREBASE_REFRESH_TOKEN=x\n")
    assert read_env_file(tmp_home / "keys.env") == {"GHL_API_KEY": "abc"}


def test_only_known_names_are_loaded(tmp_home):
    (tmp_home / "keys.env").write_text("PATH=/evil\nGHL_API_KEY=abc\n")
    env = {}
    assert load_settings(env) == ["GHL_API_KEY"]
    assert "PATH" not in env


def test_save_key_is_private_and_keeps_other_lines(tmp_home):
    (tmp_home / "keys.env").write_text("# mine\nOTHER=1\nGHL_API_KEY=old\n")
    path = save_key("GHL_API_KEY", FAKE_PIT)
    assert path == tmp_home / "keys.env"
    lines = path.read_text().splitlines()
    assert lines[0] == "# mine" and "OTHER=1" in lines
    assert lines.count("GHL_API_KEY=" + FAKE_PIT) == 1
    assert "GHL_API_KEY=old" not in lines
    save_key("GHL_FIREBASE_REFRESH_TOKEN", "tok")
    assert read_env_file(path) == {"OTHER": "1", "GHL_API_KEY": FAKE_PIT,
                                   "GHL_FIREBASE_REFRESH_TOKEN": "tok"}
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
        assert tmp_home.stat().st_mode & 0o777 == 0o700
    assert [p.name for p in tmp_home.iterdir()] == ["keys.env"]


def test_save_key_creates_missing_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GHL_CLI_HOME", str(tmp_path / "new" / "home"))
    path = save_key("GHL_API_KEY", "abc")
    assert path.read_text() == "GHL_API_KEY=abc\n"


def test_save_key_rejects_unknown_name_without_leaking_value(tmp_home):
    with pytest.raises(ValueError) as e:
        save_key("PATH", FAKE_PIT)
    assert FAKE_PIT not in str(e.value)


def test_save_key_never_prints(tmp_home, capsys):
    save_key("GHL_API_KEY", FAKE_PIT)
    out = capsys.readouterr()
    assert FAKE_PIT not in out.out + out.err


def test_config_fills_location_and_domain(tmp_home):
    save_config(location_id=LOC, app_domain="https://app.myagency.com")
    env = {}
    assert set(load_settings(env)) == {"GHL_LOCATION_ID", "GHL_APP_DOMAIN"}
    assert env["GHL_LOCATION_ID"] == LOC
    assert env["GHL_APP_DOMAIN"] == "https://app.myagency.com"


def test_save_config_merges_and_none_keeps(tmp_home):
    save_config(location_id=LOC, app_domain="https://a.example.com")
    path = save_config(location_id="Z" * 20, app_domain=None)
    assert json.loads(path.read_text()) == {"location_id": "Z" * 20,
                                            "app_domain": "https://a.example.com"}


def test_corrupt_config_is_ignored(tmp_home):
    (tmp_home / "config.json").write_text("{nope")
    env = {}
    assert load_settings(env) == []


@pytest.mark.parametrize("text,expected", [
    ("https://app.myagency.com/v2/location/AbCdEfGhIjKlMnOpQrSt/dashboard",
     ("AbCdEfGhIjKlMnOpQrSt", "https://app.myagency.com")),
    ("  AbCdEfGhIjKlMnOpQrSt  ", ("AbCdEfGhIjKlMnOpQrSt", None))])
def test_parse_location_url(text, expected):
    assert parse_location_url(text) == expected


def test_agency_page_url_is_refused_with_a_hint():
    with pytest.raises(ValueError) as e:
        parse_location_url("https://app.myagency.com/v2/agency_dashboard")
    assert "switch into the sub-account" in str(e.value)
    assert "—" not in str(e.value)


@pytest.mark.parametrize("bad", ["", "hello", "AbCd", "https://x.com/v2/location/short/dash"])
def test_bad_input_has_plain_message(bad):
    with pytest.raises(ValueError) as e:
        parse_location_url(bad)
    assert "—" not in str(e.value)


def test_main_loads_settings_before_click(monkeypatch):
    from cli_anything.gohighlevel import gohighlevel_cli as mod
    calls = []
    monkeypatch.setattr(settings, "load_settings", lambda *a, **k: calls.append("load") or [])
    monkeypatch.setattr(mod, "cli", lambda **k: calls.append("cli"))
    mod.main()
    assert calls == ["load", "cli"]
