"""GoHighLevel internal API client for workflow creation.

Uses Firebase JWT auth against backend.leadconnectorhq.com.
Adapted from ghl-superspeed-v3-main/lib/engine.py (2026-03-25).

EXPERIMENTAL: Gated behind --experimental flag in CLI.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

BASE_URL = "https://backend.leadconnectorhq.com"
# Several internal routes 401 with "version header was not found" without this.
API_VERSION = "2021-07-28"
# NOT A SECRET. This is GoHighLevel's *public* Firebase Web API key, shipped in
# their own browser bundle (projectId "highlevel-backend"). Firebase Web API keys
# identify a project; they authorize nothing on their own. The actual credential is
# GHL_FIREBASE_REFRESH_TOKEN in .env, which is gitignored.
# Secret scanners flag the AIza* prefix - this one is a known false positive.
FIREBASE_API_KEY = os.environ.get(
    "GHL_FIREBASE_API_KEY", "AIzaSyB_w3vXmsI7WeQtrIOkjR6xTRVN5uOieiE"
)
CTX = ssl.create_default_context()

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Keys to strip from GET responses before PUT (avoids validation errors)
STRIP_KEYS = frozenset([
    "_id", "id", "__v", "createdAt", "updatedAt", "companyId", "locationId",
    "companyAge", "creationSource", "originType", "deleted",
    "isTriggerBucketMigrated", "permissionMeta",
])


# -- Google pass (Firebase ID token) -----------------------------------------
# Google hands out a one-hour pass for the saved workflow (refresh) token. Every
# ghl command is its own process, so asking Google once per command meant ten
# requests for ten commands, and Google answers 429 to that pace. The pass is
# saved next to keys.env (same 0600 lock) and reused until shortly before it
# expires: about one Google request per hour instead of one per command.
#
# Kinds returned by exchange_refresh_token():
#   "ok"            a pass came back
#   "rejected"      HTTP 400/401/403: the workflow token itself is dead
#   "rate_limited"  HTTP 429, still 429 after the retries
#   "unreachable"   network failure, timeout, 5xx, or a reply with no pass

PASS_FILE = "workflow-pass.json"
PASS_MARGIN = 300          # stop using a pass 5 minutes before it expires
PASS_MAX_AGE = 3600        # never trust a pass for longer than Google's hour
RETRY_WAITS = (2, 4, 8)    # seconds between tries on a 429 (3 retries)
RETRY_AFTER_CAP = 30       # never wait longer than this on a Retry-After header
FRESH_WINDOW = 60          # a pass this new is not refreshed again on a 401

_sleep = time.sleep        # tests replace this


def _pass_path():
    from cli_anything.gohighlevel.utils import settings
    return settings.settings_home() / PASS_FILE


def _fingerprint(refresh_token: str) -> str:
    """Non-reversible tag of the workflow token, so a pass is never reused for another token."""
    import hashlib
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]


def _jwt_exp(jwt: str) -> Optional[float]:
    import base64
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _expiry(id_token: str, expires_in: Any, now: float) -> float:
    """When the pass stops working: the JWT's exp claim, capped at Google's hour."""
    try:
        secs = float(expires_in)
    except (TypeError, ValueError):
        secs = PASS_MAX_AGE
    cap = now + min(secs, PASS_MAX_AGE)
    exp = _jwt_exp(id_token)
    return min(exp, cap) if exp else cap


def _retry_after(exc: urllib.error.HTTPError) -> Optional[float]:
    try:
        value = float((exc.headers or {}).get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= RETRY_AFTER_CAP else None


def exchange_refresh_token(refresh_token: str) -> tuple[str, Optional[str], float]:
    """Ask Google for a pass: (kind, id_token, expires_at). Retries a 429 up to 3 times.

    The token value only goes into the request body. It is never returned,
    logged or put in an exception message.
    """
    if not refresh_token:
        return "rejected", None, 0.0
    body = f"grant_type=refresh_token&refresh_token={refresh_token}".encode()
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            req = urllib.request.Request(
                f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=CTX, timeout=10) as r:
                data = json.loads(r.read())
            id_token = (data.get("id_token") or "") if isinstance(data, dict) else ""
            if not id_token:
                return "unreachable", None, 0.0
            now = time.time()
            return "ok", id_token, _expiry(id_token, data.get("expires_in"), now)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt < len(RETRY_WAITS):
                    wait = _retry_after(exc)
                    _sleep(wait if wait is not None else RETRY_WAITS[attempt])
                    continue
                return "rate_limited", None, 0.0
            if exc.code in (400, 401, 403):
                return "rejected", None, 0.0
            return "unreachable", None, 0.0
        except Exception:
            return "unreachable", None, 0.0
    return "rate_limited", None, 0.0  # not reached


def load_saved_pass(refresh_token: str) -> tuple[Optional[str], float]:
    """(id_token, expires_at) of the saved pass for this workflow token, or (None, 0)."""
    try:
        data = json.loads(_pass_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 0.0
    if not isinstance(data, dict) or data.get("fp") != _fingerprint(refresh_token):
        return None, 0.0
    token, exp = data.get("id_token"), data.get("expires_at")
    if not isinstance(token, str) or not token or not isinstance(exp, (int, float)):
        return None, 0.0
    return token, float(exp)


def save_pass(refresh_token: str, id_token: str, expires_at: float) -> None:
    """Best effort: a pass that cannot be saved just means the next command asks Google."""
    from cli_anything.gohighlevel.utils import settings
    try:
        home = settings._ensure_home()
        text = json.dumps({"fp": _fingerprint(refresh_token), "id_token": id_token,
                           "expires_at": expires_at})
        settings._atomic_write(home / PASS_FILE, text + "\n", 0o600)
    except Exception:
        pass


def forget_saved_pass() -> None:
    try:
        _pass_path().unlink()
    except OSError:
        pass


_ADVICE = {
    "rejected": (
        "Error: Google would not accept the saved workflow token.\n"
        "It may be revoked or expired (a GoHighLevel password change does this).\n"
        "Get a new one with the DevTools snippet in docs/get-firebase-token.md, then run:\n"
        "ghl keys paste workflow-token"),
    "rate_limited": (
        "Error: Google is rate limiting the workflow token right now. Your token is fine.\n"
        "Wait a minute, then try again."),
    "unreachable": (
        "Error: could not reach Google to refresh the workflow token.\n"
        "Check your internet connection, then try again."),
}


class TokenManager:
    """Firebase ID token for the internal API.

    Token sources (in priority order):
    1. The pass in memory, until PASS_MARGIN before it expires
    2. The pass saved in ~/.ghl-cli/workflow-pass.json for this same workflow token
    3. A new pass from Google for GHL_FIREBASE_REFRESH_TOKEN (saved for the next command)
    4. A direct Firebase token (GHL_FIREBASE_TOKEN), used as is and never saved
    """

    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._from_google_at: float = 0.0

    def _usable(self, expires_at: float, margin: float = PASS_MARGIN) -> bool:
        return expires_at - time.time() > margin

    def _take(self, token: str, expires_at: float, from_google: bool) -> str:
        self._token, self._expires_at = token, expires_at
        if from_google:
            self._from_google_at = time.time()
        return token

    def get_token(self) -> str:
        """Get a valid Firebase JWT token."""
        if self._token and self._usable(self._expires_at):
            return self._token

        refresh_token = os.environ.get("GHL_FIREBASE_REFRESH_TOKEN", "").strip()
        if refresh_token:
            saved, saved_exp = load_saved_pass(refresh_token)
            if saved and self._usable(saved_exp):
                return self._take(saved, saved_exp, from_google=False)
            kind, token = self._ask_google(refresh_token)
            if kind == "ok":
                return token
            # Google said "not now" (busy or unreachable), but a pass inside its
            # last 5 minutes still works: use it rather than stop. A rejected
            # token gets no such grace.
            if kind != "rejected":
                for held, held_exp in ((self._token, self._expires_at), (saved, saved_exp)):
                    if held and self._usable(held_exp, margin=30):
                        return self._take(held, held_exp, from_google=False)
            print(_ADVICE[kind], file=sys.stderr)
            sys.exit(1)

        token = os.environ.get("GHL_FIREBASE_TOKEN", "").strip()
        if token:
            return self._take(token, time.time() + 3000, from_google=False)

        print(
            "Error: No workflow token is saved yet.\n"
            "Run: ghl keys paste workflow-token (copy it first with the snippet in "
            "docs/get-firebase-token.md; never paste it into a chat)",
            file=sys.stderr,
        )
        sys.exit(1)

    def _ask_google(self, refresh_token: str) -> tuple[str, Optional[str]]:
        kind, token, expires_at = exchange_refresh_token(refresh_token)
        if kind == "ok" and token:
            self._take(token, expires_at, from_google=True)
            save_pass(refresh_token, token, expires_at)
        elif kind == "rejected":
            forget_saved_pass()
        return kind, token

    def verify(self) -> tuple[str, Optional[str]]:
        """Check the workflow token with Google itself (ghl doctor, auth-check).

        Always asks Google, because a saved pass can outlive a revoked token by
        up to an hour. A good answer becomes the saved pass, so the commands
        after it do not ask again. Returns (kind, jwt).
        """
        refresh_token = os.environ.get("GHL_FIREBASE_REFRESH_TOKEN", "").strip()
        if not refresh_token:
            direct = os.environ.get("GHL_FIREBASE_TOKEN", "").strip()
            if direct:
                return "ok", self._take(direct, time.time() + 3000, from_google=False)
            return "rejected", None
        return self._ask_google(refresh_token)

    def force_refresh(self) -> str:
        """GHL refused the pass (401/403): drop it and get a new one.

        A pass Google issued in the last minute is not the problem (GHL refuses
        a location the login cannot reach with the same 401), so it is kept and
        Google is not asked again. Without this, every refused call in a loop
        would be one more Google request, which is the rate limit all over again.
        """
        if self._token and time.time() - self._from_google_at < FRESH_WINDOW:
            return self._token
        self._token, self._expires_at = None, 0.0
        forget_saved_pass()
        return self.get_token()


class InternalGHLClient:
    """GHL internal API client (backend.leadconnectorhq.com).

    Uses Firebase JWT auth with token-id header (NOT Authorization: Bearer).
    """

    def __init__(self, token_mgr: TokenManager, location_id: str):
        self.token_mgr = token_mgr
        self.location_id = location_id
        self._call_count = 0
        self._session_jwt: Optional[str] = None

    # -- GHL session JWT -------------------------------------------------
    # The web app sends TWO credentials on every internal call:
    #   token-id:      the Firebase JWT (identity)
    #   Authorization: Bearer <GHL session JWT>  (authorization)
    # The session JWT comes from GET /oauth/2/login/current and carries
    # authClass/authClassId claims. Verified against live traffic 2026-08-25.
    # token-id alone still works for accessible locations, but sending both
    # matches the current protocol and is more future-proof.
    def _get_session_jwt(self) -> Optional[str]:
        if self._session_jwt:
            return self._session_jwt
        result = self._do_request("GET", "/oauth/2/login/current", None,
                                  self.token_mgr.get_token(), with_session=False)
        if isinstance(result, dict) and not result.get("_error"):
            self._session_jwt = result.get("jwt")
        return self._session_jwt

    def whoami(self) -> Optional[dict]:
        """Identity + company for the Firebase session (no location needed)."""
        return self.request("GET", "/oauth/2/login/current")

    def list_accessible_locations(self, company_id: str) -> list:
        """Locations this Firebase session can actually reach.

        Used to explain a 401: the configured GHL_LOCATION_ID may simply belong
        to a different company than the browser session the token came from.
        """
        r = self.request("GET", f"/locations/search?companyId={company_id}&limit=100")
        if isinstance(r, dict) and not r.get("_error"):
            return r.get("locations") or r.get("data") or []
        return []

    @property
    def call_count(self) -> int:
        return self._call_count

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Optional[dict]:
        """Make an API request with auto-retry on 401."""
        token = self.token_mgr.get_token()
        result = self._do_request(method, path, body, token)

        # Retry on auth failure
        if result is None:
            token = self.token_mgr.force_refresh()
            self._session_jwt = None
            result = self._do_request(method, path, body, token)

        return result

    def _do_request(
        self, method: str, path: str, body: dict | None, token: str,
        with_session: bool = True,
    ) -> Optional[dict]:
        self._call_count += 1
        safe_token = token.encode("ascii", "ignore").decode("ascii").strip()
        headers = {
            "token-id": safe_token,
            "channel": "APP",
            "source": "WEB_USER",
            "version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": CHROME_UA,
        }
        if with_session:
            session_jwt = self._get_session_jwt()
            if session_jwt:
                headers["Authorization"] = f"Bearer {session_jwt}"
        url = f"{BASE_URL}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
                text = resp.read().decode()
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except ValueError:
                    # A 2xx that is not JSON is still a success: tags/create
                    # answers text/plain "OK" (seen live 2026-10-18).
                    return {"_text": text[:500]}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return None  # Signal retry
            error_body = e.read().decode() if e.fp else ""
            return {"_error": True, "code": e.code, "message": error_body[:200]}
        except Exception as ex:
            return {"_error": True, "message": str(ex)}

    def create_location_tag(self, tag: str) -> bool:
        """Create a tag at location level (required before using in triggers)."""
        result = self.request(
            "POST", f"/workflow/{self.location_id}/tags/create", {"tag": tag}
        )
        return bool(result and not result.get("_error"))
