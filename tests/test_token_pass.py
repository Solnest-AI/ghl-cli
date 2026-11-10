"""The Google pass is saved and reused across commands, and a 429 is retried.

Found on a member's setup (Nov 2026): every ghl command is its own process and
asked Google for a fresh one-hour pass, so ten commands in a few minutes were ten
Google requests and Google answered 429. The CLI then said the workflow token
"may be revoked", which it was not. No network here: urlopen is faked.
"""
import base64
import io
import json
import os
import stat
import sys
import time
import urllib.error

import pytest

from cli_anything.gohighlevel.utils import ghl_internal_client as ic
from cli_anything.gohighlevel.utils import settings

REFRESH = "AMf-" + "r" * 300 + "SENTINEL"
OTHER_REFRESH = "AMf-" + "q" * 300 + "SENTINEL"


def make_jwt(exp, n=0):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return enc({"alg": "none"}) + "." + enc({"exp": exp, "n": n, "user_id": "u"}) + ".sigSENTINEL"


class FakeGoogle:
    """Stands in for urlopen against securetoken.googleapis.com."""

    def __init__(self, answers=None, lifetime=3600):
        self.calls = 0
        self.bodies = []
        self.answers = list(answers or [])   # each: "ok", an int status, or an exception
        self.lifetime = lifetime

    def __call__(self, req, context=None, timeout=None):
        assert "securetoken.googleapis.com" in req.full_url, req.full_url
        self.calls += 1
        self.bodies.append(req.data)
        answer = self.answers.pop(0) if self.answers else "ok"
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):
            code, headers = answer
            raise urllib.error.HTTPError(req.full_url, code, "x", headers, io.BytesIO(b"{}"))
        if isinstance(answer, int):
            raise urllib.error.HTTPError(req.full_url, answer, "x", {}, io.BytesIO(b"{}"))
        token = make_jwt(int(time.time()) + self.lifetime, self.calls)
        payload = json.dumps({"id_token": token, "expires_in": str(self.lifetime)}).encode()
        return _Resp(payload)


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def google(monkeypatch, tmp_path):
    monkeypatch.setenv("GHL_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GHL_FIREBASE_REFRESH_TOKEN", REFRESH)
    monkeypatch.delenv("GHL_FIREBASE_TOKEN", raising=False)
    fake = FakeGoogle()
    monkeypatch.setattr(ic.urllib.request, "urlopen", fake)
    sleeps = []
    monkeypatch.setattr(ic, "_sleep", sleeps.append)
    fake.sleeps = sleeps
    return fake


def pass_file():
    return settings.settings_home() / ic.PASS_FILE


# -- reuse across commands ------------------------------------------------------

def test_ten_commands_ask_google_once(google):
    """Each TokenManager() is one ghl command. Ten of them: one Google request, not ten."""
    tokens = {ic.TokenManager().get_token() for _ in range(10)}
    assert google.calls == 1
    assert len(tokens) == 1


def test_saved_pass_is_locked_like_keys_env_and_holds_no_workflow_token(google):
    ic.TokenManager().get_token()
    p = pass_file()
    text = p.read_text()
    assert REFRESH not in text and "r" * 40 not in text
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700


def test_pass_is_not_reused_for_a_different_workflow_token(google, monkeypatch):
    ic.TokenManager().get_token()
    monkeypatch.setenv("GHL_FIREBASE_REFRESH_TOKEN", OTHER_REFRESH)
    ic.TokenManager().get_token()
    assert google.calls == 2


def test_saving_a_new_workflow_token_throws_the_old_pass_out(google):
    ic.TokenManager().get_token()
    assert pass_file().exists()
    settings.save_key("GHL_FIREBASE_REFRESH_TOKEN", OTHER_REFRESH)
    assert not pass_file().exists()


def test_saving_the_api_key_keeps_the_pass(google):
    ic.TokenManager().get_token()
    settings.save_key("GHL_API_KEY", "pit-" + "a1" * 20)
    assert pass_file().exists()


def test_pass_near_expiry_is_replaced(google):
    google.lifetime = ic.PASS_MARGIN - 10       # already inside the 5-minute margin
    ic.TokenManager().get_token()
    ic.TokenManager().get_token()
    assert google.calls == 2


def test_long_running_command_refreshes_in_memory_pass_before_it_expires(google, monkeypatch):
    tm = ic.TokenManager()
    first = tm.get_token()
    later = time.time() + 3600 - ic.PASS_MARGIN + 5
    monkeypatch.setattr(ic.time, "time", lambda: later)
    second = tm.get_token()
    assert google.calls == 2 and first != second


def test_corrupt_pass_file_is_ignored_and_rewritten(google):
    pass_file().parent.mkdir(parents=True, exist_ok=True)
    pass_file().write_text("{not json")
    token = ic.TokenManager().get_token()
    assert google.calls == 1
    assert json.loads(pass_file().read_text())["id_token"] == token


def test_direct_session_token_is_never_saved(google, monkeypatch):
    monkeypatch.delenv("GHL_FIREBASE_REFRESH_TOKEN")
    monkeypatch.setenv("GHL_FIREBASE_TOKEN", "direct.jwt.SENTINEL")
    assert ic.TokenManager().get_token() == "direct.jwt.SENTINEL"
    assert google.calls == 0 and not pass_file().exists()


def test_unwritable_home_still_works(google, monkeypatch):
    monkeypatch.setattr(settings, "_atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert ic.TokenManager().get_token().count(".") == 2


# -- 429 handling -------------------------------------------------------------

def test_429_is_retried_then_succeeds(google):
    google.answers = [429, 429, "ok"]
    assert ic.TokenManager().get_token()
    assert google.calls == 3 and google.sleeps == [2, 4]


def test_retry_after_header_is_respected_but_capped(google):
    google.answers = [(429, {"Retry-After": "7"}), (429, {"Retry-After": "9999"}), "ok"]
    ic.TokenManager().get_token()
    assert google.sleeps == [7.0, 4]


def test_429_that_never_clears_says_rate_limit_not_revoked(google, capsys):
    google.answers = [429] * 10
    with pytest.raises(SystemExit) as exc:
        ic.TokenManager().get_token()
    assert exc.value.code == 1
    assert google.calls == 4 and google.sleeps == [2, 4, 8]
    err = capsys.readouterr().err
    assert "rate limiting" in err and "Your token is fine" in err
    assert "revoked" not in err and "SENTINEL" not in err


def test_429_falls_back_to_a_pass_that_still_works(google, monkeypatch):
    google.lifetime = ic.PASS_MARGIN - 60       # saved, inside the margin, still valid 4 min
    held = ic.TokenManager().get_token()
    google.answers = [429] * 10
    assert ic.TokenManager().get_token() == held


def test_offline_says_internet_not_revoked(google, capsys):
    google.answers = [urllib.error.URLError("no route")]
    with pytest.raises(SystemExit):
        ic.TokenManager().get_token()
    err = capsys.readouterr().err
    assert "internet connection" in err and "revoked" not in err


def test_rejected_token_says_revoked_and_drops_the_pass(google, capsys):
    google.lifetime = ic.PASS_MARGIN - 60
    ic.TokenManager().get_token()
    assert pass_file().exists()
    google.answers = [400]
    with pytest.raises(SystemExit):
        ic.TokenManager().get_token()
    err = capsys.readouterr().err
    assert "revoked or expired" in err and "ghl keys paste workflow-token" in err
    assert not pass_file().exists()


# -- verify (doctor, auth-check) ------------------------------------------------

def test_verify_always_asks_google_and_saves_the_answer(google):
    ic.TokenManager().get_token()
    kind, jwt = ic.TokenManager().verify()
    assert kind == "ok" and google.calls == 2
    assert json.loads(pass_file().read_text())["id_token"] == jwt
    ic.TokenManager().get_token()
    assert google.calls == 2


@pytest.mark.parametrize("answer,kind", [(429, "rate_limited"), (401, "rejected"),
                                         (urllib.error.URLError("x"), "unreachable")])
def test_verify_reports_the_kind(google, answer, kind):
    google.answers = [answer] * 4
    assert ic.TokenManager().verify() == (kind, None)


# -- GHL refusing the pass --------------------------------------------------------

def test_401_on_a_saved_pass_drops_it_and_gets_a_new_one(google):
    ic.TokenManager().get_token()
    tm = ic.TokenManager()
    old = tm.get_token()                        # from the file
    new = tm.force_refresh()
    assert google.calls == 2 and new != old
    assert json.loads(pass_file().read_text())["id_token"] == new


def test_401_storm_on_a_fresh_pass_does_not_hammer_google(google, monkeypatch):
    """A location the login cannot reach answers 401 on every call. That must not be one
    Google request per call."""
    def refused(req, context=None, timeout=None):
        if "securetoken" in req.full_url:
            return google(req, context, timeout)
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(ic.urllib.request, "urlopen", refused)
    client = ic.InternalGHLClient(ic.TokenManager(), "LocSourceAAAAAAAAAA1")
    for _ in range(10):
        assert client.request("GET", "/workflow/LocSourceAAAAAAAAAA1/list") is None
    assert google.calls == 1


def test_no_token_value_is_ever_printed(google, capsys):
    google.answers = [429] * 4 + [400]
    for _ in range(2):
        with pytest.raises(SystemExit):
            ic.TokenManager().get_token()
    out = capsys.readouterr()
    assert "SENTINEL" not in out.out + out.err
