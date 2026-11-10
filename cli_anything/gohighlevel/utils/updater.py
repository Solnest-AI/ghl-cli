"""`ghl update`: bring a member's copy of the GHL CLI to the latest GitHub release.

Ported from the Listing Optimizer's scripts/update.py. A member's copy is a plain folder (the
install is a download, and scripts/detach_from_github.py unlinks a clone), so there is no
`git pull`. Every fix ships as a GitHub release of Solnest-AI/ghl-cli; this downloads that
release's source and replaces the tool's own files.

What it never touches: .env, .venv, .git, .pytest_cache, .update-backup, and the settings home
(~/.ghl-cli or GHL_CLI_HOME), which lives outside this folder altogether. Only paths in the
release are written, and only paths the previous release shipped (the manifest,
.ghl-files.json) are removed. A file a member made in this folder that no release ships is
kept. Every file it replaces or removes is copied to .update-backup/<time>/ first, so `--undo`
is one step, and a failure halfway puts everything back before it exits. A folder still linked
to git (a clone or a worktree) is refused.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath

# The install root is the folder holding VERSION (the one __init__.py reads), never the cwd.
ROOT = Path(__file__).resolve().parents[3]
REPO = "Solnest-AI/ghl-cli"
MANIFEST = ".ghl-files.json"
BACKUP_DIR = ".update-backup"
KEEP_BACKUPS = 3
UA = {"User-Agent": "ghl-cli-updater"}

# Never written, never removed, whatever a manifest or tarball says.
PROTECTED_FILES = {".env"}
PROTECTED_DIRS = (".venv/", ".git/", f"{BACKUP_DIR}/", ".pytest_cache/")


class UpdateError(RuntimeError):
    """The update could not run; the folder is unchanged (or was put back)."""


def _protected(rel: str) -> bool:
    return (rel in PROTECTED_FILES or rel == MANIFEST
            or (rel.startswith(".env.") and rel != ".env.example")
            or rel.startswith(PROTECTED_DIRS))


def parse_version(text) -> tuple[int, ...] | None:
    m = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def current_version(root: Path) -> str | None:
    try:
        v = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if parse_version(v) else None


# -- GitHub ------------------------------------------------------------------
def latest_tag(timeout: float = 20) -> str:
    """The latest release's tag. github.com redirects /releases/latest to /releases/tag/<tag>;
    unlike the API, that is not limited to 60 requests an hour per network."""
    req = urllib.request.Request(f"https://github.com/{REPO}/releases/latest", headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"could not reach GitHub ({type(e).__name__})") from e
    m = re.search(r"/releases/tag/([^/?#]+)$", final)
    if not m or not parse_version(m.group(1)):
        raise UpdateError("GitHub has no published release of the GHL CLI yet")
    return m.group(1)


def download(tag: str, timeout: float = 60) -> bytes:
    url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"could not download {tag} from GitHub ({type(e).__name__})") from e


def read_tarball(blob: bytes) -> dict[str, bytes]:
    """{relative path: content} from a GitHub source tarball. Anything that is not a plain file
    or folder inside the one top folder (a link, an absolute path, a ..) refuses the whole
    release: nothing is written."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            members = tar.getmembers()
            # GitHub's pax_global_header is metadata, not a path.
            tops = {PurePosixPath(m.name).parts[0] for m in members
                    if m.name and not m.name.startswith("/") and m.type not in (tarfile.XGLTYPE, tarfile.XHDTYPE)}
            for m in members:
                p = PurePosixPath(m.name)
                if m.name.startswith(("/", "\\")) or ".." in p.parts or len(tops) != 1:
                    raise UpdateError(f"the release contains an unsafe path ({m.name}); nothing was changed")
                if m.isdir() or m.type == tarfile.XGLTYPE or m.type == tarfile.XHDTYPE:
                    continue
                if not m.isfile():
                    raise UpdateError(f"the release contains a link or device ({m.name}); nothing was changed")
                rel = "/".join(p.parts[1:])
                if rel:
                    files[rel] = tar.extractfile(m).read()
    except (tarfile.TarError, OSError, EOFError) as e:
        raise UpdateError(f"the downloaded release is not a valid archive ({type(e).__name__})") from e
    if "VERSION" not in files or not parse_version(files["VERSION"].decode("utf-8", "replace")):
        raise UpdateError("the downloaded release has no VERSION file; nothing was changed")
    return files


# -- Manifest ----------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(root: Path) -> dict:
    try:
        m = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) and isinstance(m.get("files"), dict) else {"files": {}}
    except (OSError, ValueError):
        return {"files": {}}


def write_manifest(root: Path, paths, version: str | None = None) -> None:
    """Record the tool's own files (and their content) so the next update knows what it may
    replace or remove, and which ones the member edited."""
    files = {}
    for rel in paths:
        p = root / rel
        if p.is_file() and not _protected(rel):
            files[rel] = _sha(p.read_bytes())
    _write(root / MANIFEST, json.dumps({"version": version or current_version(root), "files": files},
                                       indent=1, sort_keys=True).encode())


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".ghl-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# -- Update / undo -----------------------------------------------------------
def update(root: Path = ROOT, force: bool = False) -> dict:
    if (root / ".git").exists():  # a directory (clone) or a file (linked worktree)
        raise UpdateError("this folder is still linked to git: update it with `git pull --ff-only`, "
                          "or run setup, which unlinks an untouched copy")
    have = current_version(root)
    tag = latest_tag()
    want = tag.lstrip("v")
    if have and parse_version(have) >= parse_version(want) and not force:
        return {"status": "current", "from": have, "to": have}
    files = read_tarball(download(tag))
    shipped = files["VERSION"].decode("utf-8", "replace").strip().lstrip("v")
    if parse_version(shipped) != parse_version(want):
        raise UpdateError(f"the release {tag} says it is version {shipped}; nothing was changed")
    new = {rel: data for rel, data in files.items() if not _protected(rel)}
    old = _load_manifest(root)["files"]
    gone = sorted(rel for rel in set(old) - set(new) if not _protected(rel))
    edited = sorted(rel for rel, h in old.items()
                    if (root / rel).is_file() and _sha((root / rel).read_bytes()) != h)
    setup_changed = "setup.py" in new and (
        not (root / "setup.py").is_file() or (root / "setup.py").read_bytes() != new["setup.py"])

    backup = root / BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    existed, added = [], []
    for rel in sorted(set(new) | set(gone)):
        src = root / rel
        if src.is_file():
            _write(backup / rel, src.read_bytes())
            existed.append(rel)
        elif rel in new:
            added.append(rel)
    if (root / MANIFEST).is_file():
        _write(backup / MANIFEST, (root / MANIFEST).read_bytes())
    _write(backup / "undo.json", json.dumps({"from": have, "to": want, "existed": existed,
                                             "added": added, "had_manifest": (root / MANIFEST).is_file()},
                                            indent=1).encode())
    try:
        for rel, data in new.items():
            _write(root / rel, data)
        for rel in gone:
            (root / rel).unlink(missing_ok=True)
        write_manifest(root, list(new), want)
    except OSError as e:
        undo(root, backup)
        raise UpdateError(f"the update stopped halfway ({type(e).__name__}: {e}) and the previous "
                          f"version was put back; close other programs using this folder and try again") from e
    _prune(root / BACKUP_DIR)
    return {"status": "updated", "from": have or "unknown", "to": want, "added": added, "removed": gone,
            "edited": [rel for rel in edited if rel in new or rel in gone], "backup": str(backup),
            "setup_changed": setup_changed}


def undo(root: Path = ROOT, backup: Path | None = None) -> dict:
    """Put back the files from before the last update (or the given backup)."""
    if backup is None:
        found = sorted(p for p in (root / BACKUP_DIR).glob("*") if (p / "undo.json").is_file()) \
            if (root / BACKUP_DIR).is_dir() else []
        if not found:
            raise UpdateError("there is no earlier version to go back to")
        backup = found[-1]
    meta = json.loads((backup / "undo.json").read_text(encoding="utf-8"))
    setup_changed = False
    for rel in meta.get("added") or []:
        if not _protected(rel):
            (root / rel).unlink(missing_ok=True)
            setup_changed = setup_changed or rel == "setup.py"
    for rel in meta.get("existed") or []:
        if not _protected(rel):
            data = (backup / rel).read_bytes()
            if rel == "setup.py":
                setup_changed = not (root / rel).is_file() or (root / rel).read_bytes() != data
            _write(root / rel, data)
    if meta.get("had_manifest") and (backup / MANIFEST).is_file():
        _write(root / MANIFEST, (backup / MANIFEST).read_bytes())
    else:
        (root / MANIFEST).unlink(missing_ok=True)
    shutil.rmtree(backup, ignore_errors=True)
    if (root / BACKUP_DIR).is_dir() and not any((root / BACKUP_DIR).iterdir()):
        (root / BACKUP_DIR).rmdir()
    return {"status": "undone", "to": meta.get("from") or "unknown", "from": meta.get("to"),
            "setup_changed": setup_changed}


def _prune(folder: Path) -> None:
    for old in sorted(p for p in folder.glob("*") if p.is_dir())[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


# -- Editable reinstall (needed when setup.py changed) ------------------------
def reinstall_command(root: Path = ROOT) -> list[str]:
    """The editable install, always. __version__ is read from the root VERSION at import, so a
    non-editable install would freeze it."""
    return ["uv", "pip", "install", "-e", str(root), "--python", sys.executable]


def find_uv() -> str | None:
    """uv on the PATH, else where its installer puts it (setup.sh and setup.ps1 look there
    too, because the installer does not always reach the PATH of an already-open shell)."""
    uv = shutil.which("uv")
    if uv:
        return uv
    if sys.platform == "win32":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        candidate = home / ".local" / "bin" / "uv.exe"
    else:
        candidate = Path.home() / ".local" / "bin" / "uv"
    return str(candidate) if candidate.is_file() else None


def reinstall(root: Path = ROOT) -> str:
    """Run the editable reinstall with uv, or say how to get there when uv is not here or fails."""
    cmd = reinstall_command(root)
    by_hand = shlex.join(cmd)
    uv = find_uv()
    if not uv:
        return ("packages: uv was not found, so the packages were not reinstalled. "
                "Rerun setup (SETUP.md Phase 1, step 1c), which finds or installs uv.")
    try:
        r = subprocess.run([uv, *cmd[1:]], capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError):
        return f"packages: the reinstall did not finish. Run this once: {by_hand}"
    if r.returncode != 0:
        return f"packages: the reinstall did not finish. Run this once: {by_hand}"
    return "packages: reinstalled"


# -- The check after an update runs the NEW code --------------------------------
def doctor_command(location_id: str | None = None) -> list[str]:
    """`ghl doctor` as a fresh process, so it imports the files the update just wrote."""
    cmd = [sys.executable, "-m", "cli_anything.gohighlevel.gohighlevel_cli"]
    if location_id:
        cmd += ["--location-id", location_id]
    return cmd + ["doctor"]


def run_doctor_subprocess(emit, location_id: str | None = None, root: Path = ROOT) -> int | None:
    """Run doctor in a new process, handing each output line to emit(). Returns its exit code
    (0 clean, anything else means doctor found a problem), or None when it could not start at
    all. The environment is inherited and doctor never prints key values."""
    try:
        proc = subprocess.Popen(doctor_command(location_id), cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in proc.stdout:
        emit(line.rstrip("\n"))
    return proc.wait()
