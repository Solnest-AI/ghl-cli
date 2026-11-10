"""`ghl doctor`: one check that tells a member which of their keys is broken.

Two audiences use this:
  * an agency owner: Firebase JWT claims have locations=[], and the locations the
    session reaches come from the agency route (list_accessible_locations).
  * a sub-account user on someone else's agency: claims carry a non-empty
    `locations` list and the agency route is refused for them, so the claims list
    is used and no agency call is made.

Never put a key or token value in a Check. Details and fixes are fixed strings
plus location ids and names only.
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
from dataclasses import dataclass
from typing import Optional

import requests

from cli_anything.gohighlevel.utils import ghl_client as api

API_KEY_NAME = "GHL key"
TOKEN_NAME = "Workflow token"
REACH_NAME = "Location reach"

CHANGED_ON_THEIR_SIDE = "GHL changed something on its side. This is not your setup."
GHL_TROUBLE = "GHL is having trouble right now. Try again in a few minutes."
GHL_TROUBLE_FIX = "run ghl doctor again in a few minutes; if it keeps happening, run ghl update"
WRONG_SUB_ACCOUNT = "GHL could not find workflows for {loc}. The sub-account may be wrong."
WRONG_SUB_ACCOUNT_FIX = ("copy the address bar from inside the right sub-account and run "
                         "ghl config set-location with it, then run ghl doctor again")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def decode_claims(jwt: str) -> dict:
    """Claims of a JWT (no signature check), or {} if it cannot be decoded."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


# -- GHL key ---------------------------------------------------------------

def _http_message(exc: requests.exceptions.HTTPError) -> str:
    resp = exc.response
    try:
        body = resp.json()
        return str(body.get("message") or body.get("msg") or "").lower()
    except Exception:
        return ""


def check_api_key(location_id: Optional[str]) -> Check:
    """GET /locations/{id} with the Private Integration key."""
    if not os.environ.get("GHL_API_KEY", "").strip():
        return Check(API_KEY_NAME, False, "no GHL key is saved yet",
                     "ghl keys paste api-key")
    if not (location_id or "").strip():
        return Check(API_KEY_NAME, False, "no sub-account is set",
                     "ghl config set-location")
    try:
        data = api.get(f"/locations/{location_id}")
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        msg = _http_message(exc)
        if status == 403 or (status == 401 and "not accessible" in msg):
            return Check(API_KEY_NAME, False,
                         "this key belongs to a different sub-account",
                         "make the key inside the sub-account you set, then run "
                         "ghl keys paste api-key (or run ghl config set-location "
                         "to point at the sub-account the key belongs to)")
        if status == 401 and "scope" in msg:
            return Check(API_KEY_NAME, False,
                         "the key works but is missing a permission",
                         "open your Private Integration in GoHighLevel (Settings, "
                         "Private Integrations), a required permission box is "
                         "unticked. Tick the permission boxes it needs, save, then "
                         "run ghl doctor again")
        if status == 401:
            return Check(API_KEY_NAME, False, "GHL refused the key",
                         "the key was revoked or mistyped. Make a fresh one in your "
                         "Private Integration, then run ghl keys paste api-key")
        return Check(API_KEY_NAME, False, f"GHL answered with HTTP {status}",
                     "wait a minute, then run ghl doctor again")
    except requests.exceptions.RequestException:
        return Check(API_KEY_NAME, False, "could not reach GoHighLevel",
                     _OFFLINE_FIX)
    loc = (data or {}).get("location") if isinstance(data, dict) else None
    name = ((loc or {}).get("name") or "").strip() or location_id
    return Check(API_KEY_NAME, True, f"for {name}")


# -- Workflow token --------------------------------------------------------

_TOKEN_MISSING_FIX = "ghl keys paste workflow-token"
_TOKEN_REVOKED_FIX = ("the browser login token stopped working (this happens after "
                      "a password change, for example). Grab a fresh one, then run "
                      "ghl keys paste workflow-token")


_OFFLINE_FIX = "check your internet connection, then run ghl doctor again"
_RATE_LIMIT_FIX = "wait a minute, then run ghl doctor again. Your token is not the problem"


def _new_token_manager():
    from cli_anything.gohighlevel.utils.ghl_internal_client import TokenManager
    return TokenManager()


def check_workflow_token(token_mgr=None) -> tuple[Check, Optional[dict]]:
    """Will Google mint a JWT from the saved browser login token.

    Always one real Google check (a saved pass can outlive a revoked token), with
    the 429 retries built in. The pass it gets is kept in `token_mgr` and saved,
    so the reach check and the next commands reuse it instead of asking again.
    """
    has_token = (os.environ.get("GHL_FIREBASE_REFRESH_TOKEN", "").strip()
                 or os.environ.get("GHL_FIREBASE_TOKEN", "").strip())
    if not has_token:
        return (Check(TOKEN_NAME, False, "no workflow token is saved yet",
                      _TOKEN_MISSING_FIX), None)
    tm = token_mgr if token_mgr is not None else _new_token_manager()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            kind, jwt = tm.verify()
    except Exception:
        return (Check(TOKEN_NAME, False, "could not check the saved token", _OFFLINE_FIX), None)
    if kind == "ok":
        return Check(TOKEN_NAME, True, "Google accepts the saved token"), decode_claims(jwt or "")
    if kind == "rejected":
        return (Check(TOKEN_NAME, False, "Google would not accept the saved token",
                      _TOKEN_REVOKED_FIX), None)
    if kind == "rate_limited":
        return (Check(TOKEN_NAME, False, "Google is rate limiting token checks right now",
                      _RATE_LIMIT_FIX), None)
    return (Check(TOKEN_NAME, False, "could not reach Google to check the saved token",
                  _OFFLINE_FIX), None)


# -- Location reach --------------------------------------------------------

def reachable_locations(client, claims: dict) -> list[tuple[str, Optional[str]]]:
    """(id, name) pairs this session can reach. Names are None for sub-account users."""
    claim_locs = claims.get("locations")
    if isinstance(claim_locs, list) and claim_locs:
        return [(str(lid), None) for lid in claim_locs]
    company = claims.get("company_id")
    if not company:
        return []
    out = []
    for item in client.list_accessible_locations(company):
        lid = item.get("id") or item.get("_id")
        if lid:
            out.append((lid, item.get("name")))
    return out


def _label(pair: tuple[str, Optional[str]]) -> str:
    lid, name = pair
    return f"{lid} ({name})" if name else lid


def check_location_reach(client, claims: dict) -> Check:
    """GET /workflow/{loc}/list through the browser-session client."""
    loc = client.location_id
    result = client.request("GET", f"/workflow/{loc}/list")
    if result is None:
        pairs = reachable_locations(client, claims)
        if pairs:
            shown = ", ".join(_label(p) for p in pairs[:5])
            more = f" and {len(pairs) - 5} more" if len(pairs) > 5 else ""
            detail = f"GHL refused {loc}. This login can reach: {shown}{more}"
        else:
            detail = f"GHL refused {loc}, and this login lists no sub-accounts"
        return Check(REACH_NAME, False, detail,
                     "run ghl config set-location with a sub-account this login can reach, "
                     "or paste the workflow token from the browser session that "
                     "owns your sub-account (ghl keys paste workflow-token)")
    if isinstance(result, dict) and result.get("_error"):
        code = result.get("code")
        if not code:
            return Check(REACH_NAME, False, "could not reach GoHighLevel", _OFFLINE_FIX)
        code = int(code)
        if code == 429:
            return Check(REACH_NAME, False, "GHL is rate limiting you right now",
                         "wait a minute, then run ghl doctor again")
        if 500 <= code <= 599:
            # An outage, not a change: the softer message first (ruling R19).
            return Check(REACH_NAME, False, GHL_TROUBLE, GHL_TROUBLE_FIX)
        if 400 <= code <= 499:
            # 401/403 come back as None above, so this is 400/404 and the like:
            # usually a sub-account id GHL does not know.
            return Check(REACH_NAME, False, WRONG_SUB_ACCOUNT.format(loc=loc), WRONG_SUB_ACCOUNT_FIX)
        return Check(REACH_NAME, False, CHANGED_ON_THEIR_SIDE, "ghl update")
    if isinstance(result, dict) and "rows" not in result:
        return Check(REACH_NAME, False, CHANGED_ON_THEIR_SIDE, "ghl update")
    return Check(REACH_NAME, True, "can list workflows")


# -- Runner ----------------------------------------------------------------

def _make_client(token_mgr, location_id: str):
    from cli_anything.gohighlevel.utils.ghl_internal_client import InternalGHLClient
    return InternalGHLClient(token_mgr, location_id)


def run_doctor(only: Optional[str] = None, location_id: Optional[str] = None) -> list[Check]:
    """Run the checks. `only` is None, "api-key" or "workflow-token"."""
    if only not in (None, "api-key", "workflow-token"):
        raise ValueError(f"unknown check: {only}")
    loc = (location_id or os.environ.get("GHL_LOCATION_ID", "")).strip()
    checks: list[Check] = []
    if only in (None, "api-key"):
        checks.append(check_api_key(loc))
    if only in (None, "workflow-token"):
        # One token manager for both checks: the reach check reuses the pass the
        # token check just got instead of asking Google a second time.
        token_mgr = _new_token_manager()
        token_check, claims = check_workflow_token(token_mgr)
        checks.append(token_check)
        if only is None and token_check.ok:
            if not loc:
                checks.append(Check(REACH_NAME, False, "no sub-account is set",
                                    "ghl config set-location"))
            else:
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        checks.append(check_location_reach(
                            _make_client(token_mgr, loc), claims or {}))
                except SystemExit:
                    checks.append(Check(REACH_NAME, False,
                                        "the saved token stopped working mid-check",
                                        _TOKEN_REVOKED_FIX))
    return checks
