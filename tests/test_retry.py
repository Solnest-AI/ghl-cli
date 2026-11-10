"""Tests for transient-failure retry in the public API client.

GoHighLevel intermittently answers a valid request with
  401 {"statusCode": 401, "message": "Command timed out"}
which is a backend timeout wearing an auth error's clothes. Observed live on
/users/ at roughly a 40% rate (2026-08-25). Retrying it is the difference
between a working CLI and one that tells people their token is broken.

These use a fake transport, so they never touch the network.
"""
import json

import pytest
import requests

from cli_anything.gohighlevel.utils import ghl_client as api


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


TIMEOUT_401 = {"statusCode": 401, "message": "Command timed out"}
REAL_401 = {"statusCode": 401, "message": "The token is not authorized for this scope."}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)


@pytest.fixture
def transport(monkeypatch):
    """Queue up responses; records how many calls were made."""
    calls = []

    def install(responses):
        queue = list(responses)

        def fake(method, url, **kwargs):
            calls.append((method, url))
            item = queue.pop(0) if queue else queue_last
            if isinstance(item, Exception):
                raise item
            return item

        queue_last = responses[-1] if responses else FakeResponse(200)
        monkeypatch.setattr(api.requests, "request", fake)
        return calls

    return install


class TestTransientDetection:
    def test_401_command_timed_out_is_transient(self):
        assert api._is_transient(FakeResponse(401, TIMEOUT_401))

    def test_genuine_401_is_not_transient(self):
        """A real scope error must surface immediately, not be retried."""
        assert not api._is_transient(FakeResponse(401, REAL_401))

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_server_errors_are_transient(self, status):
        assert api._is_transient(FakeResponse(status))

    @pytest.mark.parametrize("status", [200, 201, 400, 403, 404, 422])
    def test_normal_responses_are_not_transient(self, status):
        assert not api._is_transient(FakeResponse(status))

    def test_unparseable_401_body_is_not_transient(self):
        resp = FakeResponse(401, ValueError("not json"))
        assert not api._is_transient(resp)


class TestRetryBehaviour:
    def test_retries_then_succeeds(self, transport):
        calls = transport([
            FakeResponse(401, TIMEOUT_401),
            FakeResponse(200, {"users": []}),
        ])
        resp = api._send("GET", "https://example.test/users/")
        assert resp.status_code == 200
        assert len(calls) == 2

    def test_gives_up_after_max_retries(self, transport):
        calls = transport([FakeResponse(503)] * 10)
        resp = api._send("GET", "https://example.test/x")
        assert resp.status_code == 503
        assert len(calls) == api.MAX_RETRIES

    def test_does_not_retry_a_real_auth_error(self, transport):
        calls = transport([FakeResponse(401, REAL_401)])
        resp = api._send("GET", "https://example.test/x")
        assert resp.status_code == 401
        assert len(calls) == 1, "a genuine 401 must not be retried"

    def test_does_not_retry_success(self, transport):
        calls = transport([FakeResponse(200, {"ok": True})])
        api._send("GET", "https://example.test/x")
        assert len(calls) == 1

    def test_does_not_retry_404(self, transport):
        calls = transport([FakeResponse(404)])
        api._send("GET", "https://example.test/x")
        assert len(calls) == 1

    def test_retries_connection_errors(self, transport):
        calls = transport([
            requests.exceptions.ConnectionError("boom"),
            FakeResponse(200, {"ok": True}),
        ])
        resp = api._send("GET", "https://example.test/x")
        assert resp.status_code == 200
        assert len(calls) == 2

    def test_reraises_connection_error_after_max_retries(self, transport):
        transport([requests.exceptions.ConnectionError("boom")] * 10)
        with pytest.raises(requests.exceptions.ConnectionError):
            api._send("GET", "https://example.test/x")

    def test_backoff_grows(self, monkeypatch, transport):
        slept = []
        monkeypatch.setattr(api.time, "sleep", lambda s: slept.append(s))
        transport([FakeResponse(503)] * 10)
        api._send("GET", "https://example.test/x")
        assert slept == sorted(slept) and len(set(slept)) > 1, "backoff should increase"


class TestVerbsUseRetry:
    @pytest.mark.parametrize("verb,fn", [
        ("GET", lambda: api.get("/x")),
        ("POST", lambda: api.post("/x", {})),
        ("PUT", lambda: api.put("/x", {})),
        ("DELETE", lambda: api.delete("/x")),
    ])
    def test_every_verb_retries(self, verb, fn, transport, monkeypatch):
        monkeypatch.setenv("GHL_API_KEY", "test-token")
        monkeypatch.setenv("GHL_LOCATION_ID", "test-loc")
        calls = transport([
            FakeResponse(401, TIMEOUT_401),
            FakeResponse(200, {"ok": True}),
        ])
        fn()
        assert len(calls) == 2, f"{verb} did not retry a transient failure"
        assert calls[0][0] == verb
