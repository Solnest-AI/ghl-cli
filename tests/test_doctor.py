"""`ghl doctor`: no network. api.get, TokenManager.get_token and the internal
client factory are all faked."""
import base64
import json

import pytest

pytest.importorskip("click")

import requests  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from cli_anything.gohighlevel.utils import doctor
from cli_anything.gohighlevel.utils import ghl_client as api
from cli_anything.gohighlevel.utils import ghl_internal_client as internal
from fake_ghl import FakeGHL

LOC = "AbCdEfGhIjKlMnOpQrSt"
LOC_A = "LocationAaaaaaaaaaaa"
LOC_B = "LocationBbbbbbbbbbbb"
COMPANY = "CompanyCccccccccccc"


def make_jwt(**claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return enc({"alg": "none"}) + "." + enc(claims) + ".sig"


def http_error(status, message=""):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps({"message": message}).encode()
    return requests.exceptions.HTTPError(response=resp)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GHL_API_KEY", "key-SENTINEL-api")
    monkeypatch.setenv("GHL_FIREBASE_REFRESH_TOKEN", "tok-SENTINEL-refresh")
    monkeypatch.setenv("GHL_LOCATION_ID", LOC)


def fake_get(result=None, error=None):
    def _get(path, *a, **k):
        if error:
            raise error
        return result
    return _get


def test_api_key_ok_names_the_location(env, monkeypatch):
    monkeypatch.setattr(api, "get", fake_get({"location": {"name": "Acme Plumbing"}}))
    c = doctor.check_api_key(LOC)
    assert c.ok and c.name == "GHL key"
    assert c.detail == "for Acme Plumbing"


def test_scope_error_names_the_boxes(env, monkeypatch):
    monkeypatch.setattr(api, "get", fake_get(error=http_error(401, "This route is not authorized for this scope")))
    c = doctor.check_api_key(LOC)
    assert not c.ok
    assert "Private Integration" in c.fix and "unticked" in c.fix


@pytest.mark.parametrize("status,msg", [(403, "forbidden"), (401, "Location is not accessible")])
def test_key_for_another_subaccount(env, monkeypatch, status, msg):
    monkeypatch.setattr(api, "get", fake_get(error=http_error(status, msg)))
    assert "different sub-account" in doctor.check_api_key(LOC).detail


def test_missing_keys_point_at_keys_paste(monkeypatch):
    monkeypatch.delenv("GHL_API_KEY", raising=False)
    monkeypatch.delenv("GHL_FIREBASE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("GHL_FIREBASE_TOKEN", raising=False)
    assert "ghl keys paste api-key" in doctor.check_api_key(LOC).fix
    assert "ghl keys paste workflow-token" in doctor.check_workflow_token()[0].fix


def test_missing_location_points_at_set_location(env):
    assert "ghl config set-location" in doctor.check_api_key(None).fix


def _google_says(monkeypatch, kind, jwt=None):
    monkeypatch.setattr(internal.TokenManager, "verify", lambda self: (kind, jwt))


def test_revoked_workflow_token_says_fresh_one(env, monkeypatch):
    _google_says(monkeypatch, "rejected")
    c, claims = doctor.check_workflow_token()
    assert not c.ok and claims is None
    assert "password change" in c.fix and "ghl keys paste workflow-token" in c.fix


def test_offline_is_not_reported_as_a_revoked_token(env, monkeypatch):
    _google_says(monkeypatch, "unreachable")
    c, claims = doctor.check_workflow_token()
    assert not c.ok and claims is None
    assert "internet connection" in c.fix
    assert "fresh" not in c.fix and "keys paste" not in c.fix


def test_rejected_token_says_fresh_one(env, monkeypatch):
    _google_says(monkeypatch, "rejected")
    c, _ = doctor.check_workflow_token()
    assert "fresh one" in c.fix and "ghl keys paste workflow-token" in c.fix


def test_rate_limit_is_not_reported_as_offline_or_revoked(env, monkeypatch):
    """Austin's setup: Google answered 429 and doctor said it could not reach Google."""
    _google_says(monkeypatch, "rate_limited")
    c, claims = doctor.check_workflow_token()
    assert not c.ok and claims is None
    assert "rate limiting" in c.detail and "wait a minute" in c.fix
    assert "internet" not in c.fix and "fresh" not in c.fix and "keys paste" not in c.fix


def test_ok_token_returns_its_claims(env, monkeypatch):
    _google_says(monkeypatch, "ok", make_jwt(company_id=COMPANY))
    c, claims = doctor.check_workflow_token()
    assert c.ok and claims["company_id"] == COMPANY


def test_probe_classifies_google_answers(monkeypatch):
    import urllib.error
    import urllib.request

    def raiser(exc):
        def _open(*a, **k):
            raise exc
        return _open
    monkeypatch.setattr(internal, "_sleep", lambda s: None)
    for exc, want in [(urllib.error.URLError("no route"), "unreachable"),
                      (TimeoutError(), "unreachable"),
                      (urllib.error.HTTPError("u", 400, "bad", {}, None), "rejected"),
                      (urllib.error.HTTPError("u", 429, "slow", {}, None), "rate_limited"),
                      (urllib.error.HTTPError("u", 503, "down", {}, None), "unreachable")]:
        monkeypatch.setattr(urllib.request, "urlopen", raiser(exc))
        assert internal.exchange_refresh_token("tok")[0] == want


def test_token_step_stderr_chatter_is_hidden(env, monkeypatch, capsys):
    def chatty(self):
        import sys
        print("Error: refresh failed", file=sys.stderr)
        return "rejected", None
    monkeypatch.setattr(internal.TokenManager, "verify", chatty)
    doctor.check_workflow_token()
    assert capsys.readouterr().err == ""


def test_doctor_asks_google_once_and_reach_reuses_the_pass(env, monkeypatch):
    """Doctor used to build two token managers, so every run asked Google twice."""
    import cli_anything.gohighlevel.utils.ghl_internal_client as ic
    calls = []

    def fake_exchange(tok):
        calls.append(1)
        return "ok", make_jwt(company_id=COMPANY, locations=[]), 9e12
    monkeypatch.setattr(ic, "exchange_refresh_token", fake_exchange)
    monkeypatch.setattr(api, "get", fake_get({"location": {"name": "Acme Plumbing"}}))
    server = FakeGHL()
    seen = []

    def make_client(tm, loc):
        seen.append(tm.get_token())
        return server.client(loc)
    monkeypatch.setattr(doctor, "_make_client", make_client)
    checks = doctor.run_doctor(None, location_id=LOC)
    assert [c.ok for c in checks] == [True, True, True], checks
    assert len(calls) == 1 and seen and seen[0].count(".") == 2


def test_reach_network_error_has_no_http_none(env):
    server = FakeGHL()
    server.list_override = {"_error": True, "message": "timed out"}
    c = doctor.check_location_reach(server.client(LOC), {})
    assert "None" not in c.detail and "internet connection" in c.fix


@pytest.mark.parametrize("code", [400, 404, 410, 422])
def test_reach_4xx_points_at_the_sub_account(env, code):
    """R19: a non-auth, non-429 4xx is usually a sub-account id GHL does not know."""
    server = FakeGHL()
    server.list_override = {"_error": True, "code": code, "message": "gone"}
    c = doctor.check_location_reach(server.client(LOC), {})
    assert not c.ok and LOC in c.detail and "sub-account may be wrong" in c.detail
    assert "ghl config set-location" in c.fix and "ghl update" not in c.fix
    assert c.detail != doctor.CHANGED_ON_THEIR_SIDE


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_reach_5xx_is_trouble_not_a_change(env, code):
    """R19: an outage gets the softer message first, with ghl update only if it persists."""
    server = FakeGHL()
    server.list_override = {"_error": True, "code": code, "message": "x"}
    c = doctor.check_location_reach(server.client(LOC), {})
    assert not c.ok
    assert c.detail == "GHL is having trouble right now. Try again in a few minutes."
    assert c.fix.startswith("run ghl doctor again") and "if it keeps happening, run ghl update" in c.fix


def test_reach_429_still_says_wait(env):
    server = FakeGHL()
    server.list_override = {"_error": True, "code": 429, "message": "slow down"}
    c = doctor.check_location_reach(server.client(LOC), {})
    assert c.detail == "GHL is rate limiting you right now"


def test_subaccount_user_sees_their_own_locations(env):
    server = FakeGHL()
    server.refuse_auth = True
    claims = {"type": "account", "locations": [LOC_A, LOC_B]}
    c = doctor.check_location_reach(server.client(LOC), claims)
    assert not c.ok and LOC_A in c.detail and LOC_B in c.detail
    assert not any("/locations/search" in call[2] for call in server.calls)


def test_agency_owner_reach_uses_company_search(env):
    server = FakeGHL()
    server.refuse_auth = True
    server.locations = [{"id": LOC_A, "name": "Alpha"}, {"_id": LOC_B, "name": "Beta"}]
    claims = {"company_id": COMPANY, "locations": []}
    c = doctor.check_location_reach(server.client(LOC), claims)
    assert LOC_A in c.detail and LOC_B in c.detail
    assert any("/locations/search" in call[2] for call in server.calls)
    assert doctor.reachable_locations(server.client(LOC), claims) == [(LOC_A, "Alpha"), (LOC_B, "Beta")]


def test_location_reach_ok(env):
    server = FakeGHL()
    assert doctor.check_location_reach(server.client(LOC), {}).ok


def test_changed_workflow_api_says_update(env):
    server = FakeGHL()
    server.list_override = {"weird": 1}
    c = doctor.check_location_reach(server.client(LOC), {})
    assert not c.ok and c.fix == "ghl update"
    assert c.detail == "GHL changed something on its side. This is not your setup."
    # A dict without rows is an API shape change; an outage is not (see the 5xx test).
    server.list_override = {"rows_moved": []}
    assert doctor.check_location_reach(server.client(LOC), {}).fix == "ghl update"


def test_only_flag_runs_one_check(env, monkeypatch):
    monkeypatch.setattr(api, "get", fake_get({"location": {"name": "Acme Plumbing"}}))
    assert [c.name for c in doctor.run_doctor("api-key")] == ["GHL key"]


@pytest.fixture
def wired(env, monkeypatch):
    """Everything faked; returns the fake server."""
    server = FakeGHL()
    monkeypatch.setattr(api, "get", fake_get({"location": {"name": "Acme Plumbing"}}))
    jwt = make_jwt(type="agency", company_id=COMPANY, locations=[])
    monkeypatch.setattr(internal.TokenManager, "get_token", lambda self: jwt)
    monkeypatch.setattr(internal.TokenManager, "verify", lambda self: ("ok", jwt))
    monkeypatch.setattr(doctor, "_make_client", lambda tm, loc: server.client(loc))
    monkeypatch.setattr(internal, "InternalGHLClient", lambda tm, loc: server.client(loc))
    return server


def test_doctor_command_all_ok_and_needs_no_experimental(wired):
    result = CliRunner().invoke(cli_mod.cli, ["doctor"], obj={})
    assert result.exit_code == 0, result.output
    assert "  GHL key: OK for Acme Plumbing" in result.output
    assert "Workflow token: OK" in result.output and "Location reach: OK" in result.output


def test_doctor_command_exit_1_with_fix_line(wired):
    wired.refuse_auth = True
    result = CliRunner().invoke(cli_mod.cli, ["doctor"], obj={})
    assert result.exit_code == 1
    assert "Location reach: PROBLEM" in result.output and "     fix: " in result.output


def test_member_text_has_no_em_dash_or_contact_author():
    import inspect
    src = inspect.getsource(doctor)
    # Strings only matter; the module has none, so the whole file is a safe superset.
    assert "—" not in src
    assert "contact the author" not in src.lower()


@pytest.mark.parametrize("args,expect", [
    (["doctor"], "GHL key:"),
    (["doctor", "--only", "api-key"], "GHL key:"),
    (["--experimental", "workflows", "auth-check"], "[1/3]"),
])
@pytest.mark.parametrize("failing", [False, True])
def test_never_prints_key_values(wired, monkeypatch, args, expect, failing):
    if failing:
        wired.refuse_auth = True
        monkeypatch.setattr(api, "get", fake_get(error=http_error(401, "not authorized for this scope")))
    result = CliRunner().invoke(cli_mod.cli, args, obj={})
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.output.strip()
    assert expect in result.output
    assert "SENTINEL" not in result.output + result.stderr


# -- final review ---------------------------------------------------------------

_REAL_API_GET = api.get     # captured at import, before any fixture patches it


def test_auth_check_lists_locations_even_with_no_ghl_key_saved(wired, monkeypatch):
    """_get_token() exits (SystemExit) when no GHL key is saved; the location-name lookup in
    auth-check only caught Exception, so the reachable-locations list was never printed."""
    monkeypatch.delenv("GHL_API_KEY")
    monkeypatch.setattr(api, "get", _REAL_API_GET)
    monkeypatch.setattr(requests, "request", lambda *a, **k: pytest.fail("no network in tests"))
    wired.refuse_auth = True
    wired.locations = [{"id": LOC_A, "name": "Alpha"}, {"_id": LOC_B, "name": "Beta"}]
    result = CliRunner().invoke(cli_mod.cli, ["--experimental", "workflows", "auth-check"], obj={})
    assert result.exit_code == 4, result.output
    assert "[3/3]" in result.output and "REFUSED" in result.output
    assert LOC_A in result.output and LOC_B in result.output
    assert "Traceback" not in result.output
