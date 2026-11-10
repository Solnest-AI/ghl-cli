"""InternalGHLClient must treat a plain-text 2xx answer as success.

GHL's tag-create route answers HTTP 200, text/plain, body "OK" (seen live
2026-10-18). The client fed that to json.loads and reported an error, so every
tag creation looked like a failure while it had in fact worked.
"""
from cli_anything.gohighlevel.utils import ghl_internal_client as ic


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Tokens:
    def get_token(self):
        return "firebase-jwt"

    def force_refresh(self):
        return "firebase-jwt"


def _client(monkeypatch, body):
    monkeypatch.setattr(ic.urllib.request, "urlopen",
                        lambda req, context=None, timeout=None: _Resp(body))
    client = ic.InternalGHLClient(_Tokens(), "LocSourceAAAAAAAAAA1")
    client._session_jwt = "session-jwt"
    return client


def test_plain_text_success_is_not_an_error(monkeypatch):
    client = _client(monkeypatch, b"OK")
    result = client.request("POST", "/workflow/LocSourceAAAAAAAAAA1/tags/create", {"tag": "x"})
    assert not result.get("_error")
    assert client.create_location_tag("x") is True


def test_json_success_is_still_parsed(monkeypatch):
    client = _client(monkeypatch, b'{"id": "abc"}')
    assert client.request("POST", "/workflow/LocSourceAAAAAAAAAA1", {"name": "x"}) == {"id": "abc"}
