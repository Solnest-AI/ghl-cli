"""GoHighLevel REST API client with bearer token authentication."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlencode

import requests

BASE_URL = "https://services.leadconnectorhq.com"

# Path-based API version routing — different GHL endpoints require different versions
VERSION_MAP = {
    "/conversations/": "2021-04-15",
    "/calendars/": "2021-04-15",
    "/contacts/": "2021-07-28",
    "/opportunities/": "2021-07-28",
    "/workflows/": "2021-07-28",
    "/campaigns/": "2021-07-28",
    "/invoices/": "2021-07-28",
    "/payments/": "2021-07-28",
    "/emails/": "2021-07-28",
    "/forms/": "2021-07-28",
    "/locations/": "2021-07-28",
    "/social-media-posting/": "2021-07-28",
    "/proposals/": "2021-07-28",
}
DEFAULT_VERSION = "2021-07-28"


def _version_for_path(path: str) -> str:
    """Resolve API version based on request path prefix."""
    for prefix, version in VERSION_MAP.items():
        if path.startswith(prefix):
            return version
    return DEFAULT_VERSION


def _get_token() -> str:
    """Get bearer token from GHL_API_KEY env var."""
    token = os.environ.get("GHL_API_KEY", "").strip()
    if not token:
        print(
            "Error: No GHL key is saved yet.\n"
            "Run: ghl keys paste api-key (copy the key from your Private Integration first; "
            "never paste it into a chat)",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def _get_location_id() -> str:
    """Get location ID from the GHL_LOCATION_ID env var. No default."""
    location_id = os.environ.get("GHL_LOCATION_ID", "").strip()
    if not location_id:
        print(
            "Error: No GoHighLevel sub-account is set.\n"
            "Run: ghl config set-location <the URL from your browser while inside the sub-account>",
            file=sys.stderr,
        )
        sys.exit(1)
    return location_id


def _headers(version: str | None = None, path: str = "") -> dict[str, str]:
    """Build request headers with auth and auto-resolved version."""
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": version or _version_for_path(path),
    }



# ── Transient-failure retry ───────────────────────────────────────────────
# GoHighLevel intermittently answers a perfectly valid request with
#   401 {"statusCode":401,"message":"Command timed out"}
# which is a backend timeout mislabelled as an auth error. Observed on
# /users/ at roughly a 40% rate (2026-08-25). Without a retry the CLI reports a
# fake auth failure and sends people off rotating tokens that were never wrong.
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_401_MESSAGES = ("command timed out", "timeout")
MAX_RETRIES = 3
BACKOFF_BASE = 0.6


def _is_transient(resp: "requests.Response") -> bool:
    if resp.status_code in RETRY_STATUS:
        return True
    if resp.status_code == 401:
        try:
            msg = (resp.json().get("message") or "").lower()
        except Exception:
            return False
        return any(m in msg for m in RETRY_401_MESSAGES)
    return False


def _send(method: str, url: str, **kwargs) -> "requests.Response":
    """Issue a request, retrying transient GHL failures with backoff."""
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        if _is_transient(resp) and attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        return resp
    raise last  # pragma: no cover


def get(path: str, params: dict[str, Any] | None = None, version: str | None = None) -> dict:
    """Make a GET request to the GHL API."""
    url = f"{BASE_URL}{path}"
    resp = _send("GET", url, headers=_headers(version, path), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def post(path: str, data: dict[str, Any] | None = None, version: str | None = None) -> dict:
    """Make a POST request to the GHL API."""
    url = f"{BASE_URL}{path}"
    resp = _send("POST", url, headers=_headers(version, path), json=data or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def put(path: str, data: dict[str, Any] | None = None, version: str | None = None) -> dict:
    """Make a PUT request to the GHL API."""
    url = f"{BASE_URL}{path}"
    resp = _send("PUT", url, headers=_headers(version, path), json=data or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def delete(path: str, data: dict[str, Any] | None = None, version: str | None = None) -> dict:
    """Make a DELETE request to the GHL API.

    Some GHL endpoints (notably DELETE /contacts/{id}/tags) require a JSON body.
    `data` is only sent when provided, so bodyless DELETEs are unaffected.
    """
    url = f"{BASE_URL}{path}"
    kwargs = {"headers": _headers(version, path), "timeout": 30}
    if data is not None:
        kwargs["json"] = data
    resp = _send("DELETE", url, **kwargs)
    resp.raise_for_status()
    try:
        return resp.json()
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        return {"status": "deleted", "statusCode": resp.status_code}


def format_output(data: Any, as_json: bool = False) -> str:
    """Format API response for display."""
    if as_json:
        return json.dumps(data, indent=2, default=str)
    if isinstance(data, dict):
        return _format_dict(data)
    if isinstance(data, list):
        return _format_list(data)
    return str(data)


def _label(item: dict, fallback_index: int = 0) -> str:
    """Best human-readable label for a GHL record.

    GHL record shapes are inconsistent — contacts have contactName/firstName,
    social posts have summary, orders have contactName, emails have subject.
    Falling straight through to `id` renders lists as unreadable ID dumps.
    """
    for key in ("name", "title", "contactName", "fullName", "subject", "summary"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    first, last = item.get("firstName") or "", item.get("lastName") or ""
    full = f"{first} {last}".strip()
    if full:
        return full

    for key in ("email", "phone", "friendlyName"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    ident = item.get("id") or item.get("_id")
    return str(ident) if ident else f"item {fallback_index}"


def _format_dict(d: dict, indent: int = 0) -> str:
    """Format a dict for human-readable display."""
    lines = []
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(_format_dict(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{prefix}{k}: [{len(v)} items]")
            for i, item in enumerate(v[:5]):
                if isinstance(item, dict):
                    lines.append(f"{prefix}  - {_label(item, i)}")
                else:
                    lines.append(f"{prefix}  - {item}")
            if len(v) > 5:
                lines.append(f"{prefix}  ... and {len(v) - 5} more")
        else:
            lines.append(f"{prefix}{k}: {v}")
    return "\n".join(lines)


def _format_list(items: list, indent: int = 0) -> str:
    """Format a list for human-readable display."""
    lines = []
    prefix = "  " * indent
    for i, item in enumerate(items):
        if isinstance(item, dict):
            lines.append(f"{prefix}{i + 1}. {_label(item, i)}")
            for k, v in item.items():
                if k not in ("name", "title") and not isinstance(v, (dict, list)):
                    lines.append(f"{prefix}   {k}: {v}")
        else:
            lines.append(f"{prefix}{i + 1}. {item}")
    return "\n".join(lines)
