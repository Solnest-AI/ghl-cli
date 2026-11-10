#!/usr/bin/env python3
"""Release gate: nothing that ships may contain Ryan's ids, keys, paths, or any student or property name.

    python scripts/sanitize_check.py [ROOT] [--denylist PATH] [--no-denylist] [--release]

Findings print as `path:line: <pattern name>`. The matched text is NEVER printed, and denylist hits
are reported by entry number (`denylist entry #N`, the 1-based line in the denylist file), so this
tool's output is always safe to paste. Exit 0 = clean, 1 = findings, 2 = the gate could not run
(missing denylist: a gate that goes green because it is blind is worse than no gate).

Every flagged literal below is built in pieces so this file passes its own gate with no exemption.
`VERIFY IN BUILD` markers in shipped docs are reported as a separate "unverified" count; with
--release (the real public release) they are findings. Files under tests/ may mention the marker.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_DENYLIST = Path.home() / ".ghl-cli-release" / "denylist.txt"

# Jay's own example data. Allowed only in his example workflows (builders/, docs/workflows-*).
JAY_LOCATION_ID = "YB8rMdFShc" + "HGcZGW87mA"
JAY_EMAILS = ("jay" + "@leadgenjay.com", "jayfeldman11" + "@gmail.com", "jay" + "@consulti.ai")
JAY_ALLOWED_PREFIXES = ("builders/", "docs/workflows-")

# GHL's public Firebase web key lives in exactly one file.
FIREBASE_ALLOWED_FILE = "cli_anything/gohighlevel/utils/ghl_internal_client.py"

MARKER = "VERIFY IN " + "BUILD"

# Binary files cannot be scanned, so a binary file is itself a finding unless it is listed here.
# Empty on purpose: nothing binary ships once EXCLUDE has dropped docs/how-it-works.png.
BINARY_ALLOW: tuple[str, ...] = ()

# (name, compiled regex, applies-everywhere?)  Generic patterns apply to every scanned file.
GENERIC = [
    # Any GHL Private Integration key shape, old (pit-<uuid>) or newer (pit-<segment>-<uuid>).
    # A real key has digits; the docs placeholder pit-xxxxxxxx-xxxx-... has none and is skipped.
    ("pit-token", re.compile(r"\bpit-(?=[A-Za-z0-9_-]{30,})(?=[A-Za-z0-9_-]*?\d)"
                             r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+", re.I)),
    ("amf-token", re.compile(r"AM" + r"f-[A-Za-z0-9_-]{40,}")),
    ("home-path", re.compile(r"/Users/[A-Za-z0-9_]+/")),
    ("trust" + "stack", re.compile("trust" + "stack", re.I)),
    ("location-id-fallback",
     re.compile(r"GHL_LOCATION_ID[^\n]{0,60}?[\"'][A-Za-z0-9]{20}[\"']")),
]
FIREBASE_RE = re.compile(r"AI" + r"za[0-9A-Za-z_-]{35}")
JAY_ID_RE = re.compile(re.escape(JAY_LOCATION_ID))
JAY_EMAIL_RE = re.compile("|".join(re.escape(e) for e in JAY_EMAILS), re.I)


def load_denylist(path: Path) -> list[str]:
    """One entry per line, blanks kept so list index + 1 == the line number in the file."""
    p = Path(path).expanduser()
    if not p.is_file():
        print(f"sanitize_check: denylist not found at {p} (use --no-denylist only for a dry run)",
              file=sys.stderr)
        sys.exit(2)
    entries = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()]
    if not any(entries):
        print(f"sanitize_check: denylist at {p} has no entries; an empty denylist checks nothing",
              file=sys.stderr)
        sys.exit(2)
    return entries


def _files(root: Path) -> list[Path]:
    """Files to scan when the caller did not pass an explicit list. Only `.git` is ever skipped: a
    directory such as venv/ or node_modules/ is shipped by `git add -A`, so it must be scanned."""
    root = Path(root)
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
        if out.returncode == 0:
            names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
            return sorted(root / n for n in names if (root / n).is_file() or (root / n).is_symlink())
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        found.extend(Path(dirpath) / f for f in filenames)
    return sorted(found)


def _read_text(p: Path) -> str | None:
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data[:8192]:
        return None  # binary
    return data.decode("utf-8", errors="replace")


def _line_names(line: str, rel: str, deny_lc, jay_ok: bool, patterns_only_path: bool = False) -> list[str]:
    names: list[str] = []
    for name, rx in GENERIC:
        if rx.search(line):
            names.append(name)
    if not patterns_only_path and rel != FIREBASE_ALLOWED_FILE and FIREBASE_RE.search(line):
        names.append("firebase-key")
    if not jay_ok:
        if JAY_ID_RE.search(line):
            names.append("jay-location-id")
        if JAY_EMAIL_RE.search(line):
            names.append("jay-email")
    low = line.lower()
    for i, d in deny_lc:
        if d in low:
            names.append(f"denylist entry #{i}")
    return names


def scan_full(root: Path, deny: list[str], release: bool = False,
              files: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (findings, unverified). `unverified` is `path:line` for each marker outside tests/;
    with release=True those are ALSO findings. `files` (relative paths) scans exactly that list; the
    export passes the files it copied so nothing git will ship can be skipped."""
    root = Path(root)
    deny_lc = [(i, d.lower()) for i, d in enumerate(deny, 1) if d]
    findings: list[str] = []
    unverified: list[str] = []
    paths = [root / f for f in files] if files is not None else _files(root)
    for p in sorted(paths):
        rel = p.relative_to(root).as_posix()
        jay_ok = rel.startswith(JAY_ALLOWED_PREFIXES)
        in_tests = rel.startswith("tests/")
        # the path itself: a file or folder named after a denylist entry leaks just as well
        for name in _line_names(rel, rel, deny_lc, jay_ok, patterns_only_path=True):
            findings.append(f"{rel}: {name}")
        if p.is_symlink():
            findings.append(f"{rel}: symlink not allowed")
            continue
        text = _read_text(p)
        if text is None:
            if rel not in BINARY_ALLOW:
                findings.append(f"{rel}: binary file not scanned")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            names = _line_names(line, rel, deny_lc, jay_ok)
            if MARKER in line and not in_tests:
                unverified.append(f"{rel}:{n}")
                if release:
                    names.append("unverified-marker")
            findings.extend(f"{rel}:{n}: {name}" for name in names)
    return findings, unverified


def scan(root: Path, deny: list[str], release: bool = False, files: list[str] | None = None) -> list[str]:
    return scan_full(root, deny, release, files)[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--denylist", default=str(DEFAULT_DENYLIST))
    ap.add_argument("--no-denylist", action="store_true", help="dry run without the external denylist")
    ap.add_argument("--release", action="store_true", help="markers in shipped files are findings")
    a = ap.parse_args(argv)
    deny = [] if a.no_denylist else load_denylist(Path(a.denylist))
    findings, unverified = scan_full(Path(a.root), deny, a.release)
    if a.no_denylist:
        print("NOT A RELEASE CHECK: running without the denylist (dry run only)")
    for f in findings:
        print(f)
    if unverified and not a.release:
        print(f"unverified: {len(unverified)} marker(s) in shipped files (release candidate OK, "
              f"--release fails on these)")
    if findings:
        print(f"FAIL: {len(findings)} finding(s)")
        return 1
    print("OK: clean" + (" (denylist skipped)" if a.no_denylist else f" ({len([d for d in deny if d])} denylist entries)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
