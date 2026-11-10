#!/usr/bin/env python3
"""Fresh-history export of the member edition for the public repo.

    python scripts/export_public.py SRC DEST [--denylist PATH] [--no-denylist] [--release]

Copies `git ls-files` of SRC minus EXCLUDE into DEST, runs the sanitize gate on DEST, refuses (exit 1,
DEST removed) on any finding, then `git init -b main` and ONE commit `ghl-cli member edition <VERSION>`
authored by a neutral identity. Never pushes. DEST must not already hold files.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sanitize_check as sc  # noqa: E402

# Internal planning docs, Jay's non-shipping extras, and his old one-shot installer (setup.sh supersedes
# it), plus his branded marketing diagram (shows a command that does not exist). Anything that writes a
# .env beside the code ships nowhere: the member edition keeps keys in ~/.ghl-cli/keys.env, and the
# .venv/bin/ghl that members run never reads that .env (set-firebase-token.sh). .superpowers/ is the
# build's own notes (ignored by git too).
EXCLUDE = ("docs/superpowers/", "docs/cli-alternatives.md", "install.sh",
           "docs/how-it-works.png", "docs/how-it-works.excalidraw",
           "scripts/set-firebase-token.sh", ".superpowers/")

AUTHOR_NAME = "Solnest AI"
AUTHOR_EMAIL = "solnest-ai@users.noreply.github.com"


def _excluded(rel: str) -> bool:
    return any(rel == e or (e.endswith("/") and rel.startswith(e)) for e in EXCLUDE)


def export(src: Path, dest: Path, denylist: list[str] | None = None, release: bool = False) -> list[str]:
    """Return [] on success, or the scan findings (DEST removed) on refusal."""
    src, dest = Path(src).resolve(), Path(dest).resolve()
    if dest.exists() and not dest.is_dir():
        sys.exit(f"export: {dest} is a file, not a folder; refusing")
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"export: {dest} already exists and is not empty; refusing to overwrite it")
    if denylist is None:
        denylist = sc.load_denylist(sc.DEFAULT_DENYLIST)
    if not any(denylist):
        print("export: no usable denylist; a release export without one checks nothing", file=sys.stderr)
        sys.exit(2)
    ls = subprocess.run(["git", "-C", str(src), "ls-files", "-z"], capture_output=True, check=True)
    rels = [n for n in ls.stdout.decode("utf-8").split("\0") if n and not _excluded(n)]
    version = (src / "VERSION").read_text().strip()
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    try:
        for rel in rels:
            s = src / rel
            if not (s.is_file() or s.is_symlink()):
                continue
            copied.append(rel)
            t = dest / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t, follow_symlinks=False)
        findings = sc.scan(dest, denylist, release=release, files=copied)
        if findings:
            shutil.rmtree(dest, ignore_errors=True)
            return findings
        # core.hooksPath=/dev/null: this is a throwaway release snapshot, the sanitize gate above is
        # its gate, and a machine-wide commit hook must not rewrite or block it.
        # GIT_AUTHOR_*/GIT_COMMITTER_* env vars beat -c user.*, so strip them; never sign with a
        # personal key even if the machine has commit.gpgsign=true.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        g = ["git", "-C", str(dest), "-c", "core.hooksPath=/dev/null",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        run = dict(check=True, capture_output=True, env=env)
        subprocess.run(g + ["init", "-q", "-b", "main"], **run)
        subprocess.run(g + ["add", "-A"], **run)
        subprocess.run(g + ["-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}",
                            "commit", "-q", "-m", f"ghl-cli member edition {version}"], **run)
        got = subprocess.run(g + ["log", "-1", "--format=%an|%ae|%cn|%ce|%G?"], text=True, **run).stdout.strip()
        want = f"{AUTHOR_NAME}|{AUTHOR_EMAIL}|{AUTHOR_NAME}|{AUTHOR_EMAIL}|N"
        if got != want:
            sys.exit(f"export: commit identity/signature check failed ({got!r}); export removed")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("dest")
    ap.add_argument("--denylist", default=str(sc.DEFAULT_DENYLIST))
    ap.add_argument("--release", action="store_true", help="markers in shipped files fail the export")
    a = ap.parse_args(argv)
    deny = sc.load_denylist(Path(a.denylist))
    findings = export(Path(a.src), Path(a.dest), deny, a.release)
    if findings:
        for f in findings:
            print(f)
        print(f"REFUSED: {len(findings)} finding(s); {a.dest} removed")
        return 1
    print(f"exported to {a.dest} (1 commit, no push)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
