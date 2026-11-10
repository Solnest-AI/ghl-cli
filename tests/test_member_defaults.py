"""Member edition defaults: one version, no silent fallback to Jay's account."""
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

import cli_anything.gohighlevel as gohighlevel
from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from cli_anything.gohighlevel.utils import ghl_client as api

ROOT = Path(__file__).resolve().parent.parent


def entry_point_names(setup_path):
    text = (ROOT / setup_path).read_text()
    block = re.search(r'"console_scripts"\s*:\s*\[(.*?)\]', text, re.S).group(1)
    return set(re.findall(r'"([\w-]+)=', block))


def test_version_comes_from_the_VERSION_file():
    version = (ROOT / "VERSION").read_text().strip()
    assert version == gohighlevel.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    # Never below 2.1.0: the old Skool zip and Jay's CLI print 2.0.0, so the updater
    # would never see anything at or under that as new.
    assert tuple(map(int, version.split("."))) >= (2, 1, 0)
    assert version in CliRunner().invoke(cli_mod.cli, ["--version"], obj={}).output


def test_changelog_has_an_entry_for_this_version():
    version = (ROOT / "VERSION").read_text().strip()
    assert f"## {version}" in (ROOT / "CHANGELOG.md").read_text()


def test_no_location_set_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.delenv("GHL_LOCATION_ID", raising=False)
    with pytest.raises(SystemExit):
        api._get_location_id()
    err = capsys.readouterr().err
    assert "No GoHighLevel sub-account is set" in err and "ghl config set-location" in err


def test_no_code_falls_back_to_a_real_location():
    pat = re.compile(r'environ\.get\(\s*"GHL_LOCATION_ID"\s*,\s*"[A-Za-z0-9]{20}"')
    files = [
        *(ROOT / "cli_anything").rglob("*.py"),
        *(ROOT / "builders").glob("*.py"),
        *(ROOT / "scripts").glob("*.py"),
    ]
    assert [str(f) for f in files if pat.search(f.read_text())] == []


def test_examples_never_send_members_to_jays_inbox():
    text = "".join(f.read_text() for f in (ROOT / "builders").glob("*.py"))
    jays = ("jay" + "@leadgenjay.com", "jayfeldman11" + "@gmail.com", "jay" + "@consulti.ai")
    assert not any(a in text for a in jays)


def test_only_the_ghl_commands_install():
    assert entry_point_names("setup.py") == {"ghl", "cli-anything-gohighlevel"}


# -- final review ---------------------------------------------------------------

def test_no_ghl_key_points_at_keys_paste_not_an_export(monkeypatch, capsys):
    """The old text said `export GHL_API_KEY='your-api-key-here'`, which nudges a chat into
    asking for the key. Same exit code, new advice."""
    monkeypatch.delenv("GHL_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        api._get_token()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ghl keys paste api-key" in err and "never paste it into a chat" in err
    assert "export" not in err and "your-api-key-here" not in err and "—" not in err


def test_no_workflow_token_points_at_keys_paste(monkeypatch, capsys):
    from cli_anything.gohighlevel.utils import ghl_internal_client as internal
    for name in ("GHL_FIREBASE_REFRESH_TOKEN", "GHL_FIREBASE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exc:
        internal.TokenManager().get_token()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ghl keys paste workflow-token" in err and "never paste it into a chat" in err
    assert "Set GHL_FIREBASE_REFRESH_TOKEN" not in err and "—" not in err


def test_auth_check_without_a_token_points_at_keys_paste(monkeypatch):
    monkeypatch.delenv("GHL_FIREBASE_REFRESH_TOKEN", raising=False)
    r = CliRunner().invoke(cli_mod.cli, ["--experimental", "workflows", "auth-check"], obj={})
    assert r.exit_code == 4
    assert "ghl keys paste workflow-token" in r.output
    assert ".env" not in r.output and "—" not in r.output


def test_repl_banner_shows_the_real_version(monkeypatch):
    from cli_anything.gohighlevel.utils import repl_skin
    seen = {}

    class FakeSkin:
        def __init__(self, software, version="?", **k):
            seen["version"] = version

        def print_banner(self):
            pass

        def create_prompt_session(self):
            return None

        def get_input(self, session, **k):
            return "exit"

        def print_goodbye(self):
            pass

    monkeypatch.setattr(repl_skin, "ReplSkin", FakeSkin)
    r = CliRunner().invoke(cli_mod.cli, [], obj={})
    assert r.exit_code == 0, r.output
    assert seen["version"] == gohighlevel.__version__ == (ROOT / "VERSION").read_text().strip()


def _strict_cp1252():
    import io
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def _main_that_echoes(monkeypatch, text):
    import sys
    import click
    from cli_anything.gohighlevel.utils import settings

    @click.command()
    def fake():
        click.echo(text)
        click.echo(text, err=True)

    monkeypatch.setattr(settings, "load_settings", lambda *a, **k: [])
    monkeypatch.setattr(cli_mod, "cli", fake)
    monkeypatch.setattr(sys, "argv", ["ghl"])


def test_windows_pipe_output_survives_an_emoji(monkeypatch):
    """Click keeps a non-console cp1252 stream strict, so on Windows a contact named with an
    emoji crashed plain-text output when stdout is a pipe (Claude Code's shell tool)."""
    import sys
    out, err = _strict_cp1252(), _strict_cp1252()
    with pytest.raises(UnicodeEncodeError):          # the stream really is strict
        _strict_cp1252().write("\U0001F525")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _main_that_echoes(monkeypatch, "Lead \U0001F525 hot → call")
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code in (0, None)
    for stream in (out, err):
        stream.flush()
        assert stream.buffer.getvalue().decode("cp1252") == "Lead ? hot ? call\n"


def test_other_platforms_keep_their_streams_as_they_are(monkeypatch):
    import sys
    out = _strict_cp1252()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "stdout", out)
    cli_mod._tolerant_windows_output()
    assert out.errors == "strict"


def test_a_stream_without_reconfigure_is_left_alone(monkeypatch):
    import io
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", None)
    cli_mod._tolerant_windows_output()       # must not raise
