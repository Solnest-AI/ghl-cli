"""Spec section 8: a fake key goes through member commands and never comes out.

test_doctor.py and test_selftest.py cover doctor, auth-check and selftest. This file adds the
rest of the member surface (config set-location, skill install, update) and one of Jay's own
commands on an API error path, so `_handle_error` is proven not to print the key either. The
keys come from a scratch keys.env through the real settings loader, every network edge is faked,
and every case also asserts its exit code and expected text so none of them passes vacuously.
"""
import base64
import io
import json
import sys
import tarfile

import pytest

pytest.importorskip("click")

import requests  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod  # noqa: E402
from cli_anything.gohighlevel.utils import doctor, settings  # noqa: E402
from cli_anything.gohighlevel.utils import ghl_client as api  # noqa: E402
from cli_anything.gohighlevel.utils import ghl_internal_client as internal  # noqa: E402
from cli_anything.gohighlevel.utils import updater  # noqa: E402
from fake_ghl import FakeGHL  # noqa: E402

LOC = "LeakTestLocAaaaaaaaa"
URL = "https://app.gohighlevel.com/v2/location/" + LOC + "/dashboard"
API_KEY = "key-SENTINEL-api"
REFRESH = "tok-SENTINEL-refresh"
MARK = "SENTINEL"


def _jwt(**claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return enc({"alg": "none"}) + "." + enc(claims) + "." + MARK + "sig"


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """Keys in a scratch keys.env, loaded by the real settings loader into the environment."""
    home = tmp_path / "ghl-home"
    monkeypatch.setenv("GHL_CLI_HOME", str(home))
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_path / "skills"))
    for name in ("GHL_API_KEY", "GHL_FIREBASE_REFRESH_TOKEN", "GHL_FIREBASE_TOKEN",
                 "GHL_LOCATION_ID", "GHL_APP_DOMAIN"):
        monkeypatch.delenv(name, raising=False)
    settings.save_key("GHL_API_KEY", API_KEY)
    settings.save_key("GHL_FIREBASE_REFRESH_TOKEN", REFRESH)
    settings.save_config(location_id=LOC)
    loaded = {}
    settings.load_settings(loaded)
    assert loaded["GHL_API_KEY"] == API_KEY and loaded["GHL_FIREBASE_REFRESH_TOKEN"] == REFRESH
    for name, value in loaded.items():
        monkeypatch.setenv(name, value)
    # Nothing in this file may reach the network.
    monkeypatch.setattr(requests, "request", lambda *a, **k: pytest.fail("unfaked network call"))
    return tmp_path


def _clean(result):
    """The command finished on its own terms (no traceback) and never showed a key."""
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    everything = result.output + (result.stderr or "")
    assert MARK not in everything
    assert "Traceback" not in everything


def test_config_set_location_never_prints_a_key(keyed):
    r = CliRunner().invoke(cli_mod.cli, ["config", "set-location", URL], obj={})
    assert r.exit_code == 0
    assert f"Sub-account set: {LOC} (domain https://app.gohighlevel.com)" in r.output
    _clean(r)


def test_skill_install_never_prints_or_writes_a_key(keyed, monkeypatch):
    bin_dir = keyed / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / ("ghl.exe" if sys.platform == "win32" else "ghl")).write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    r = CliRunner().invoke(cli_mod.cli, ["skill", "install"], obj={})
    assert r.exit_code == 0, r.output
    assert "Skill written:" in r.output
    _clean(r)
    written = (keyed / "skills" / "ghl-cli" / "SKILL.md").read_text()
    assert MARK not in written


def _release(files: dict, top="ghl-cli-2.2.0") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_update_never_prints_a_key_even_when_the_check_after_fails(keyed, monkeypatch):
    root = keyed / "ghl-cli"
    old = {"VERSION": "2.1.0\n", "setup.py": "# s\n", "cli_anything/tool.py": "v1\n"}
    for rel, body in old.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    (root / ".env").write_text(f"GHL_API_KEY={API_KEY}\n")      # an old copy's .env: kept, never shown
    updater.write_manifest(root, list(old))
    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater, "latest_tag", lambda: "v2.2.0")
    monkeypatch.setattr(updater, "download", lambda tag: _release(
        {"VERSION": "2.2.0\n", "setup.py": "# s\n", "cli_anything/tool.py": "v2\n"}))
    # The post-update check runs in-process with the real doctor and the sentinel keys; the
    # network edges answer with failures so every PROBLEM/fix line gets printed.
    monkeypatch.setattr(updater, "run_doctor_subprocess", lambda *a, **k: None)
    resp = requests.Response()
    resp.status_code = 401
    resp._content = b'{"message": "Invalid JWT"}'
    monkeypatch.setattr(api, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.HTTPError(response=resp)))
    monkeypatch.setattr(internal.TokenManager, "get_token",
                        lambda self: _jwt(type="agency", company_id="CompanyLeak", locations=[]))
    monkeypatch.setattr(internal.TokenManager, "verify",
                        lambda self: ("ok", _jwt(type="agency", company_id="CompanyLeak", locations=[])))
    server = FakeGHL()
    server.refuse_auth = True
    monkeypatch.setattr(doctor, "_make_client", lambda tm, loc: server.client(loc))
    r = CliRunner().invoke(cli_mod.cli, ["update"], obj={})
    assert r.exit_code == 0, r.output
    assert "Updated 2.1.0 -> 2.2.0" in r.output
    assert "GHL key: PROBLEM GHL refused the key" in r.output
    assert "Location reach: PROBLEM" in r.output
    assert "Next: ghl skill install" in r.output
    _clean(r)
    assert (root / ".env").read_text() == f"GHL_API_KEY={API_KEY}\n"


def test_api_error_path_never_prints_the_key(keyed, monkeypatch):
    """One of Jay's commands, answered 401: _handle_error prints GHL's message, not the request."""
    sent = []

    def fake_request(method, url, **kw):
        sent.append(kw.get("headers", {}).get("Authorization", ""))
        resp = requests.Response()
        resp.status_code = 401
        resp.url = url
        resp._content = b'{"statusCode": 401, "message": "Invalid JWT"}'
        resp.request = requests.Request(method, url, headers=kw.get("headers")).prepare()
        return resp

    monkeypatch.setattr(requests, "request", fake_request)
    r = CliRunner().invoke(cli_mod.cli, ["contacts", "list", "--limit", "1"], obj={})
    assert r.exit_code == 1
    assert "API Error (401): Invalid JWT" in r.output
    assert sent and all(MARK in h for h in sent)      # the key really went out in the header
    _clean(r)
