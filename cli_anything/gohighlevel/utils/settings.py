"""Member settings store: ~/.ghl-cli (or $GHL_CLI_HOME).

keys.env holds the two secrets, config.json holds the non-secrets. The process
environment always wins, so a wrapper that exports its own variables keeps working.
Nothing in here prints or logs a value.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import MutableMapping, Optional, Tuple

KEY_NAMES = ("GHL_API_KEY", "GHL_FIREBASE_REFRESH_TOKEN")
CONFIG_ENV = {"location_id": "GHL_LOCATION_ID", "app_domain": "GHL_APP_DOMAIN"}

_LOCATION_RE = re.compile(r"^(https?://[^/\s?#]+)/(?:\S*?/)?location/([A-Za-z0-9]{20})(?:[/?#]|$)")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{20}$")


def settings_home() -> Path:
    override = os.environ.get("GHL_CLI_HOME")
    return Path(override) if override else Path.home() / ".ghl-cli"


def _chmod(path: Path, mode: int) -> None:
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ensure_home() -> Path:
    home = settings_home()
    home.mkdir(parents=True, exist_ok=True)
    _chmod(home, 0o700)
    return home


def read_env_file(path: Path) -> dict:
    """Parse a KEY=VALUE file. Tolerates BOM, CRLF, `export `, quotes, comments. Drops empty values."""
    try:
        text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return {}
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and value:
            out[name] = value
    return out


def _read_config(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(environ: MutableMapping[str, str] = os.environ) -> list:
    """Fill missing or empty variables from keys.env and config.json. Returns names set, never values."""
    home = settings_home()
    set_names = []

    def fill(name, value):
        if value and not environ.get(name):
            environ[name] = str(value)
            set_names.append(name)

    keys = read_env_file(home / "keys.env")
    for name in KEY_NAMES:
        fill(name, keys.get(name))
    config = _read_config(home / "config.json")
    for field, env_name in CONFIG_ENV.items():
        fill(env_name, config.get(field))
    return set_names


def _atomic_write(path: Path, text: str, mode: Optional[int]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if mode is not None:
            _chmod(Path(tmp), mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_key(name: str, value: str) -> Path:
    if name not in KEY_NAMES:
        raise ValueError("Unknown key name. Expected one of: " + ", ".join(KEY_NAMES))
    value = (value or "").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("The key value is empty or has line breaks. Paste it again as one line.")
    path = _ensure_home() / "keys.env"
    try:
        existing = path.read_bytes().decode("utf-8-sig", errors="replace").splitlines()
    except OSError:
        existing = []
    kept = []
    for line in existing:
        probe = line.strip()
        if probe.startswith("export "):
            probe = probe[7:].lstrip()
        if probe.partition("=")[0].strip() == name and not probe.startswith("#"):
            continue
        kept.append(line)
    kept.append(f"{name}={value}")
    _atomic_write(path, "\n".join(kept) + "\n", 0o600)
    if name == "GHL_FIREBASE_REFRESH_TOKEN":
        # A new workflow token makes the saved Google pass (workflow-pass.json) stale.
        try:
            (path.parent / "workflow-pass.json").unlink()
        except OSError:
            pass
    return path


def save_config(**fields: Optional[str]) -> Path:
    path = _ensure_home() / "config.json"
    data = _read_config(path)
    for k, v in fields.items():
        if v is not None:
            data[k] = v
    _atomic_write(path, json.dumps(data, indent=2) + "\n", 0o600)
    return path


def parse_location_url(text: str) -> Tuple[str, Optional[str]]:
    """Return (location_id, app_domain) from a sub-account URL or a bare 20-character id."""
    text = (text or "").strip()
    if _BARE_ID_RE.match(text):
        return text, None
    m = _LOCATION_RE.match(text)
    if m:
        return m.group(2), m.group(1)
    if text.lower().startswith(("http://", "https://")):
        raise ValueError(
            "That address is not inside a sub-account. In GoHighLevel, use the account "
            "switcher to switch into the sub-account you want, then copy the address bar "
            "and paste it again.")
    raise ValueError(
        "That does not look like a sub-account address or id. Open the sub-account in "
        "GoHighLevel, copy the address bar, and paste it again.")
