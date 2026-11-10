"""`ghl keys paste` and `ghl config set-location`. No network, no real clipboard."""
import json
import re

import pytest

pytest.importorskip("click")

from click.testing import CliRunner  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from cli_anything.gohighlevel.utils import clipboard, doctor
from cli_anything.gohighlevel.utils.doctor import Check

PIT = "pit-" + "1" * 8 + "-2222-3333-4444-" + "5" * 12
REFRESH = "AMf-" + "a" * 246
LOC = "AbCdEfGhIjKlMnOpQrSt"
URL = "https://app.gohighlevel.com/v2/location/" + LOC + "/dashboard"


def _run(*args, input=None):
    return CliRunner().invoke(cli_mod.cli, list(args), input=input)


def _all(r):
    return r.output


@pytest.fixture(autouse=True)
def no_real_clipboard(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test reached the real clipboard")
    monkeypatch.setattr(clipboard, "read_clipboard", boom)
    monkeypatch.setattr(clipboard, "clear_clipboard", boom)


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GHL_CLI_HOME", str(tmp_path))
    for n in ("GHL_API_KEY", "GHL_FIREBASE_REFRESH_TOKEN", "GHL_LOCATION_ID", "GHL_APP_DOMAIN"):
        monkeypatch.delenv(n, raising=False)
    return tmp_path


@pytest.fixture
def ok_checks(tmp_home, monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", LOC)
    monkeypatch.setattr(doctor, "run_doctor",
                        lambda only=None, location_id=None: [Check("GHL key", True, "for Acme")])


def _leaks(out, secret):
    assert secret not in out
    for i in range(0, len(secret) - 8):
        assert secret[i:i + 8] not in out


def test_paste_api_key_saves_and_checks(tmp_home, ok_checks):
    r = _run("keys", "paste", "api-key", "--from-stdin", input=PIT + "\n")
    assert r.exit_code == 0 and "saved, works" in r.output
    _leaks(_all(r), PIT)
    assert PIT in (tmp_home / "keys.env").read_text()


def test_paste_workflow_token_saves_and_runs_token_check(tmp_home, monkeypatch):
    seen = []
    monkeypatch.setattr(doctor, "run_doctor",
                        lambda only=None, location_id=None: seen.append(only) or [Check("Workflow token", True, "ok")])
    r = _run("keys", "paste", "workflow-token", "--from-stdin", input=REFRESH + "\r\n")
    assert r.exit_code == 0 and "saved, works" in r.output
    assert seen == ["workflow-token"]
    _leaks(_all(r), REFRESH)
    assert "GHL_FIREBASE_REFRESH_TOKEN=" + REFRESH in (tmp_home / "keys.env").read_text()


def test_failing_check_prints_saved_but_without_key(tmp_home, monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", LOC)
    monkeypatch.setattr(doctor, "run_doctor", lambda only=None, location_id=None: [
        Check("GHL key", False, "GHL refused the key", "Tick the contacts box in GHL.")])
    r = _run("keys", "paste", "api-key", "--from-stdin", input=PIT)
    assert r.exit_code == 1
    assert "saved, but: GHL refused the key" in r.output
    assert "fix: Tick the contacts box in GHL." in r.output
    _leaks(_all(r), PIT)
    assert PIT in (tmp_home / "keys.env").read_text()


def test_api_key_without_location_skips_checks(tmp_home, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("checks must not run")
    monkeypatch.setattr(doctor, "run_doctor", boom)
    r = _run("keys", "paste", "api-key", "--from-stdin", input=PIT)
    assert r.exit_code == 0 and "saved. Next: set your sub-account." in r.output
    _leaks(_all(r), PIT)


@pytest.mark.parametrize("kind,bad,word", [
    ("api-key", "https://app.gohighlevel.com/v2/location/" + LOC + "/x", "web address"),
    ("api-key", REFRESH, "login token"),
    ("api-key", PIT[:20], "too short"),
    ("api-key", "   ", "empty"),
    ("workflow-token", PIT, "Private Integration key"),
    ("workflow-token", "www.example.com/abc", "web address"),
])
def test_wrong_input_is_rejected_without_echo(tmp_home, kind, bad, word):
    r = _run("keys", "paste", kind, "--from-stdin", input=bad)
    assert r.exit_code == 2
    assert not (tmp_home / "keys.env").exists()
    out = _all(r)
    if bad.strip():
        assert word in out and "characters" in out
    else:
        assert "it's empty" in out
    if bad.strip():
        _leaks(out, bad.strip())


# Newer GHL keys carry a short segment after "pit-" (reported from a member's setup,
# Nov 2026). The exact segment is GHL's business; any of these shapes must be accepted.
NEW_PITS = [
    "pit-" + "na-" + "1" * 8 + "-2222-3333-4444-" + "5" * 12,
    "pit-" + "Ab-" + "a" * 8 + "-bbbb-cccc-dddd-" + "e" * 12,
    "pit-" + "eu1-" + "9" * 8 + "-8888-7777-6666-" + "f" * 12,
]


@pytest.mark.parametrize("key", NEW_PITS + [PIT])
def test_old_and_new_key_formats_are_accepted(tmp_home, ok_checks, key):
    r = _run("keys", "paste", "api-key", "--from-stdin", input=key + "\n")
    assert r.exit_code == 0 and "saved, works" in r.output, r.output
    _leaks(_all(r), key)
    assert key in (tmp_home / "keys.env").read_text()


@pytest.mark.parametrize("bad,word", [
    ("pit-" + "1" * 8, "too short"),                       # cut-off copy
    ("pit-" + "-" * 40, "not the right shape"),             # no real segments
    ("pit-" + "a1b2" * 40, "not the right shape"),          # one long blob, no hyphen
    ("Bearer " + PIT, "not the right shape"),               # extra text copied with it
    ("pit-" + "1" * 8 + "-2222 3333-4444-" + "5" * 12, "not the right shape"),  # space inside
])
def test_loose_shape_still_catches_a_wrong_clipboard(tmp_home, bad, word):
    r = _run("keys", "paste", "api-key", "--from-stdin", input=bad)
    assert r.exit_code == 2, r.output
    assert word in _all(r)
    assert not (tmp_home / "keys.env").exists()


def test_new_key_format_is_not_mistaken_for_the_workflow_token(tmp_home):
    r = _run("keys", "paste", "workflow-token", "--from-stdin", input=NEW_PITS[0])
    assert r.exit_code == 2 and "Private Integration key" in _all(r)


def test_workflow_rejection_points_at_the_snippet_doc(tmp_home):
    r = _run("keys", "paste", "workflow-token", "--from-stdin", input="nope")
    assert r.exit_code == 2 and "docs/get-firebase-token.md" in _all(r)


def test_clipboard_is_read_and_cleared_after_saving(tmp_home, ok_checks, monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: "  " + PIT + "\r\n")
    monkeypatch.setattr(clipboard, "clear_clipboard", lambda: calls.append("cleared"))
    r = _run("keys", "paste", "api-key")
    assert r.exit_code == 0 and calls == ["cleared"]
    _leaks(_all(r), PIT)


def test_clear_failure_is_ignored(tmp_home, ok_checks, monkeypatch):
    def bad():
        raise clipboard.ClipboardUnavailable("x")
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: PIT)
    monkeypatch.setattr(clipboard, "clear_clipboard", bad)
    r = _run("keys", "paste", "api-key")
    assert r.exit_code == 0 and "saved, works" in r.output


def test_clipboard_not_cleared_on_rejection(tmp_home, monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: "https://x.example/abc")
    monkeypatch.setattr(clipboard, "clear_clipboard", lambda: calls.append(1))
    r = _run("keys", "paste", "api-key")
    assert r.exit_code == 2 and calls == []


def test_no_clipboard_on_this_os_points_at_the_key_file(tmp_home, monkeypatch):
    def nope():
        raise clipboard.ClipboardUnavailable("Clipboard access is not supported on this system.")
    monkeypatch.setattr(clipboard, "read_clipboard", nope)
    r = _run("keys", "paste", "workflow-token")
    out = _all(r)
    assert r.exit_code == 2
    assert str(tmp_home / "keys.env") in out and "GHL_FIREBASE_REFRESH_TOKEN=" in out
    assert "text editor" in out and "Never paste a key into a chat" in out
    # stdin can only be fed from the chat in Claude Code, so the message must not suggest it
    assert "--from-stdin" not in out
    assert "\u2014" not in out


def test_set_location_from_whitelabel_url(tmp_home):
    url = "https://app.example-agency.com/v2/location/" + LOC + "/dashboard"
    r = _run("config", "set-location", url)
    assert r.exit_code == 0
    assert f"Sub-account set: {LOC} (domain https://app.example-agency.com)" in r.output
    data = json.loads((tmp_home / "config.json").read_text())
    assert data["location_id"] == LOC and data["app_domain"] == "https://app.example-agency.com"


def test_set_location_bare_id(tmp_home):
    r = _run("config", "set-location", LOC)
    assert r.exit_code == 0 and r.output.strip() == f"Sub-account set: {LOC}"


def test_set_location_refuses_the_agency_page(tmp_home):
    r = _run("config", "set-location", "https://app.gohighlevel.com/agency_dashboard/abc")
    assert r.exit_code == 2 and "sub-account" in _all(r)
    assert not (tmp_home / "config.json").exists()


def test_from_stdin_does_not_touch_the_clipboard(tmp_home, ok_checks):
    r = _run("keys", "paste", "api-key", "--from-stdin", input=PIT)
    assert r.exit_code == 0  # autouse stub raises if clear/read were called


def test_key_inside_a_check_detail_or_fix_is_scrubbed(tmp_home, monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", LOC)
    monkeypatch.setattr(doctor, "run_doctor", lambda only=None, location_id=None: [
        Check("GHL key", False, "refused " + PIT, "retry with " + PIT)])
    r = _run("keys", "paste", "api-key", "--from-stdin", input=PIT)
    assert r.exit_code == 1 and "saved, but: refused <your key>" in r.output
    assert "fix: retry with <your key>" in r.output
    _leaks(r.output, PIT)


# -- final review: an empty clipboard read may be a blocked clipboard ------------------

@pytest.mark.parametrize("kind,name", [("api-key", "GHL_API_KEY"),
                                       ("workflow-token", "GHL_FIREBASE_REFRESH_TOKEN")])
@pytest.mark.parametrize("empty", ["", "  \r\n"])
def test_empty_clipboard_also_offers_the_key_file(tmp_home, monkeypatch, kind, name, empty):
    """pbpaste answers an empty string with exit 0 when the clipboard is blocked, which looked
    exactly like 'you didn't copy it'. One more line gives the file route."""
    cleared = []
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: empty)
    monkeypatch.setattr(clipboard, "clear_clipboard", lambda: cleared.append(1))
    r = _run("keys", "paste", kind)
    out = _all(r)
    assert r.exit_code == 2 and cleared == []
    assert "it's empty" in out
    hint = next(ln for ln in out.splitlines() if ln.startswith("If you did copy it"))
    assert "blocking the clipboard" in hint
    assert str(tmp_home / "keys.env") in hint and f"{name}=" in hint and "text editor" in hint
    assert "Never paste a key into a chat" in hint
    assert "--from-stdin" not in out and "—" not in out
    assert not (tmp_home / "keys.env").exists()


def test_empty_stdin_does_not_blame_the_clipboard(tmp_home):
    r = _run("keys", "paste", "api-key", "--from-stdin", input="")
    assert r.exit_code == 2 and "it's empty" in _all(r)
    assert "blocking the clipboard" not in _all(r)


def test_wrong_clipboard_content_does_not_blame_the_clipboard(tmp_home, monkeypatch):
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: "https://x.example/abc")
    monkeypatch.setattr(clipboard, "clear_clipboard", lambda: None)
    r = _run("keys", "paste", "api-key")
    assert r.exit_code == 2 and "blocking the clipboard" not in _all(r)
