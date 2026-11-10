"""Release tooling: scripts/sanitize_check.py (the gate) and scripts/export_public.py (fresh-history
export). Every value the gate flags is built in pieces here, so this file passes its own gate."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_public as ep  # noqa: E402
import sanitize_check as sc  # noqa: E402

PIT = "pit-" + "1" * 8 + "-2222-3333-4444-" + "5" * 12
JAY_LOC = "YB8rMdFShc" + "HGcZGW87mA"
JAY_MAIL = "jay" + "@leadgenjay" + ".com"
AMF = "AM" + "f-" + "x" * 45
MARKER = "VERIFY IN " + "BUILD"
SECRET_DENY = "zzquux-made-up-entry"
DL = ["unused-made-up-entry"]


def put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_planted_pit_found_without_its_value(tmp_path):
    put(tmp_path, "cli_anything/x.py", f'a = 1\nKEY = "{PIT}"\n')
    out = sc.scan(tmp_path, [])
    assert out == ["cli_anything/x.py:2: pit-token"]
    assert all(PIT not in f and "1" * 8 not in f for f in out)


def test_new_format_pit_found_and_placeholder_skipped(tmp_path):
    """The gate hard-coded the pit-<uuid> shape, so a newer key would have shipped unflagged."""
    new = "pit-" + "na-" + "1" * 8 + "-2222-3333-4444-" + "5" * 12
    upper = "PIT-" + "A1" * 4 + "-2222-3333-4444-" + "5" * 12
    placeholder = "pit-" + "x" * 8 + "-xxxx-xxxx-xxxx-" + "x" * 12
    put(tmp_path, "a.env", f"GHL_API_KEY={new}\n")
    put(tmp_path, "b.md", f"key: {upper}\n")
    put(tmp_path, "c.env", f"GHL_API_KEY={placeholder}\n")
    put(tmp_path, "d.md", "split-the-difference spit-take cock" + "pit-" + "1234567890-" * 2 + "12345\n")
    out = sc.scan(tmp_path, [])
    assert out == ["a.env:1: pit-token", "b.md:1: pit-token"]
    assert all("1" * 8 not in f for f in out)


def test_amf_token_and_home_path_and_vendor_name(tmp_path):
    put(tmp_path, "a.txt", f"t = {AMF}\n")
    put(tmp_path, "b.txt", "see /Users/" + "someone/Documents/x\n")
    put(tmp_path, "c.txt", "Trust" + "Stack was here\n")
    names = sorted(f.split(": ", 1)[1] for f in sc.scan(tmp_path, []))
    assert names == ["amf-token", "home-path", "trust" + "stack"]


def test_literal_location_fallback_flagged(tmp_path):
    put(tmp_path, "d.py", 'loc = os.environ.get("GHL_LOCATION_ID", "' + "A" * 20 + '")\n')
    assert [f.split(": ", 1)[1] for f in sc.scan(tmp_path, [])] == ["location-id-fallback"]
    put(tmp_path, "d.py", 'loc = os.environ.get("GHL_LOCATION_ID", "")\n')
    assert sc.scan(tmp_path, []) == []


def test_jay_id_and_emails_allowed_only_in_example_dirs(tmp_path):
    for rel in ("builders/a.md", "docs/workflows-map.md", "docs/workflows-live.json"):
        put(tmp_path, rel, f"{JAY_LOC} {JAY_MAIL}\n")
    assert sc.scan(tmp_path, []) == []
    put(tmp_path, "cli_anything/m.py", f'X = "{JAY_LOC}"\n')
    put(tmp_path, "README.md", f"mail {JAY_MAIL}\n")
    got = sorted(sc.scan(tmp_path, []))
    assert got == ["README.md:1: jay-email", "cli_anything/m.py:1: jay-location-id"]


def test_public_firebase_key_allowed_only_in_internal_client(tmp_path):
    key = "AIza" + "Sy" + "A" * 33
    put(tmp_path, "cli_anything/gohighlevel/utils/ghl_internal_client.py", f'K = "{key}"\n')
    assert sc.scan(tmp_path, []) == []
    # the generic patterns never match AIza keys, but a stray copy elsewhere is still caught
    put(tmp_path, "docs/note.md", f"{key}\n")
    assert [f.split(": ", 1)[1] for f in sc.scan(tmp_path, [])] == ["firebase-key"]


def test_denylist_entry_reported_by_number_never_text(tmp_path):
    put(tmp_path, "notes.md", f"hello {SECRET_DENY} there\n")
    out = sc.scan(tmp_path, ["other-entry", SECRET_DENY])
    assert out == ["notes.md:1: denylist entry #2"]
    assert SECRET_DENY not in "".join(out)


def test_denylist_is_case_insensitive_and_skips_blank_lines(tmp_path):
    put(tmp_path, "n.md", "Has ZZQUUX-MADE-UP-ENTRY in it\n")
    out = sc.scan(tmp_path, ["", SECRET_DENY])
    assert out == ["n.md:1: denylist entry #2"]


def test_load_denylist(tmp_path):
    f = put(tmp_path, "dl.txt", "one\n\n  two  \n")
    assert sc.load_denylist(f) == ["one", "", "two"]
    with pytest.raises(SystemExit):
        sc.load_denylist(tmp_path / "missing.txt")
    for body in ("", "\n  \n\n"):
        with pytest.raises(SystemExit) as e:
            sc.load_denylist(put(tmp_path, "empty.txt", body))
        assert e.value.code == 2


def test_git_dir_ignored_but_no_other_dir_is_skipped(tmp_path):
    put(tmp_path, ".git/config", PIT)
    assert sc.scan(tmp_path, []) == []
    for d in ("venv", ".venv", "node_modules", "__pycache__", ".pytest_cache"):
        put(tmp_path, f"docs/{d}/x.md", f"{PIT}\n")
    got = sorted(f.split(": ")[0] for f in sc.scan(tmp_path, []))
    assert len(got) == 5 and all(g.endswith("x.md:1") for g in got)


def test_binary_file_is_a_finding_unless_allowlisted(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG\x00\x00" + PIT.encode())
    assert sc.scan(tmp_path, []) == ["img.png: binary file not scanned"]
    sc.BINARY_ALLOW = ("img.png",)
    try:
        assert sc.scan(tmp_path, []) == []
    finally:
        sc.BINARY_ALLOW = ()


def test_utf16_with_bom_is_decoded_and_scanned(tmp_path):
    (tmp_path / "u.txt").write_bytes(("x\n" + PIT + "\n").encode("utf-16"))
    assert sc.scan(tmp_path, []) == ["u.txt:2: pit-token"]


def test_paths_are_scanned(tmp_path):
    put(tmp_path, "docs/" + SECRET_DENY + "/a.md", "clean\n")
    put(tmp_path, "trust" + "stack-notes.md", "clean\n")
    got = sorted(sc.scan(tmp_path, ["", SECRET_DENY]))
    assert got == ["docs/" + SECRET_DENY + "/a.md: denylist entry #2",
                   "trust" + "stack-notes.md: trust" + "stack"]


def test_symlink_is_a_finding(tmp_path):
    put(tmp_path, "real.md", "ok\n")
    (tmp_path / "link.md").symlink_to(tmp_path / "real.md")
    assert sc.scan(tmp_path, []) == ["link.md: symlink not allowed"]


def test_scan_with_explicit_file_list_scans_exactly_those(tmp_path):
    put(tmp_path, "a.md", f"{PIT}\n")
    put(tmp_path, "b.md", f"{PIT}\n")
    assert sc.scan(tmp_path, [], files=["b.md"]) == ["b.md:1: pit-token"]


def test_marker_modes(tmp_path):
    put(tmp_path, "SETUP.md", f"Click here. {MARKER} (U5)\n")
    put(tmp_path, "tests/test_docs.py", f'assert "{MARKER}" in t\n')
    assert sc.scan(tmp_path, []) == []
    assert sc.scan(tmp_path, [], release=True) == ["SETUP.md:1: unverified-marker"]
    findings, unverified = sc.scan_full(tmp_path, [])
    assert findings == [] and unverified == ["SETUP.md:1"]


def test_cli_exit_codes_and_unverified_count(tmp_path):
    put(tmp_path, "SETUP.md", f"x {MARKER}\n")
    base = [sys.executable, str(ROOT / "scripts" / "sanitize_check.py"), str(tmp_path),
            "--no-denylist"]
    r = subprocess.run(base, capture_output=True, text=True)
    assert r.returncode == 0 and "unverified: 1" in r.stdout and "NOT A RELEASE CHECK" in r.stdout
    r = subprocess.run(base + ["--release"], capture_output=True, text=True)
    assert r.returncode == 1 and "unverified-marker" in r.stdout
    put(tmp_path, "k.py", f'K="{PIT}"\n')
    r = subprocess.run(base, capture_output=True, text=True)
    assert r.returncode == 1 and PIT not in r.stdout + r.stderr


# ---- export ---------------------------------------------------------------------------------

def git(cwd, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=T",
                           "-c", "user.email=t@example.invalid", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_src(tmp_path, extra=None):
    src = tmp_path / "src"
    files = {"VERSION": "9.9.9\n", "README.md": "hi\n", "cli_anything/a.py": "x = 1\n",
             "docs/superpowers/plan.md": "internal\n", "docs/cli-alternatives.md": "alt\n",
             "install.sh": "#!/bin/sh\n", "docs/how-it-works.png": "png\n",
             "docs/how-it-works.excalidraw": "{}\n", "docs/keep.md": "keep\n",
             "scripts/set-firebase-token.sh": "#!/bin/sh\n", "scripts/keep.py": "x = 1\n",
             ".superpowers/sdd/progress.md": "internal notes\n"}
    files.update(extra or {})
    for rel, text in files.items():
        put(src, rel, text)
    git(src, "init", "-q", "-b", "main")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "work\n\nCo-Authored-By: Someone <s@example.invalid>")
    return src


def test_exclude_list_is_final():
    assert set(ep.EXCLUDE) == {"docs/superpowers/", "docs/cli-alternatives.md", "install.sh",
                               "docs/how-it-works.png", "docs/how-it-works.excalidraw",
                               "scripts/set-firebase-token.sh", ".superpowers/"}


def test_build_notes_are_ignored_by_git_and_never_exported():
    assert ".superpowers/" in (ROOT / ".gitignore").read_text().splitlines()
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", ".superpowers/x/notes.md"])
    assert r.returncode == 0, ".superpowers/ must be ignored by git"


def test_no_shipped_script_writes_a_env_beside_the_code():
    """The member edition keeps keys in ~/.ghl-cli/keys.env. A tracked helper that appends to a
    .env next to the code (Jay's set-firebase-token.sh, his install.sh) is excluded from the export."""
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                             check=True).stdout.split()
    for rel in tracked:
        if not rel.endswith((".sh", ".ps1", ".py", ".cmd")) or rel.startswith("tests/"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        writes_env = (">> \"$ENV_FILE\"" in text or "cp .env.example .env" in text
                      or 'ENV_FILE=".env"' in text)
        if writes_env:
            assert ep._excluded(rel), f"{rel} writes a .env beside the code but would ship"


def test_export_drops_excluded_and_makes_one_clean_commit(tmp_path):
    src = make_src(tmp_path)
    dest = tmp_path / "out"
    assert ep.export(src, dest, denylist=DL) == []
    for rel in ep.EXCLUDE:
        assert not (dest / rel).exists()
    assert (dest / "docs/keep.md").exists() and (dest / "README.md").exists()
    assert git(dest, "log", "--format=%s").strip() == "ghl-cli member edition 9.9.9"
    assert len(git(dest, "rev-list", "--all").split()) == 1
    body = git(dest, "log", "--format=%an <%ae>%n%B")
    assert "Solnest AI" in body and "Co-Authored-By" not in body and "Someone" not in body
    assert git(dest, "branch", "--show-current").strip() == "main"


def test_export_refuses_and_cleans_up_on_findings(tmp_path):
    src = make_src(tmp_path, {"cli_anything/leak.py": f'K = "{PIT}"\n'})
    dest = tmp_path / "out"
    findings = ep.export(src, dest, denylist=DL)
    assert findings == ["cli_anything/leak.py:1: pit-token"]
    assert not dest.exists()


def test_export_ignores_findings_inside_excluded_paths(tmp_path):
    src = make_src(tmp_path, {"docs/superpowers/leak.md": f"{PIT}\n"})
    dest = tmp_path / "out"
    assert ep.export(src, dest, denylist=DL) == []
    assert dest.exists()


def test_export_release_flag_fails_on_markers(tmp_path):
    src = make_src(tmp_path, {"SETUP.md": f"step {MARKER}\n"})
    d1, d2 = tmp_path / "rc", tmp_path / "rel"
    assert ep.export(src, d1, denylist=DL) == []
    assert ep.export(src, d2, denylist=DL, release=True) == ["SETUP.md:1: unverified-marker"]
    assert not d2.exists()


def test_export_refuses_existing_nonempty_dest(tmp_path):
    src = make_src(tmp_path)
    dest = tmp_path / "out"
    put(dest, "precious.txt", "do not delete\n")
    with pytest.raises(SystemExit):
        ep.export(src, dest, denylist=DL)
    assert (dest / "precious.txt").exists()


def test_cli_missing_denylist_is_an_error_not_a_pass(tmp_path):
    put(tmp_path, "a.md", "clean\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sanitize_check.py"), str(tmp_path),
                        "--denylist", str(tmp_path / "none.txt")], capture_output=True, text=True)
    assert r.returncode == 2 and "denylist" in (r.stdout + r.stderr)


def test_export_cli_exit_codes(tmp_path):
    src = make_src(tmp_path)
    script = str(ROOT / "scripts" / "export_public.py")
    dl = tmp_path / "dl.txt"
    dl.write_text("nomatch-entry\n")
    ok = subprocess.run([sys.executable, script, str(src), str(tmp_path / "o1"), "--denylist", str(dl)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    dl.write_text("keep\n")
    bad = subprocess.run([sys.executable, script, str(src), str(tmp_path / "o2"), "--denylist", str(dl)],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "denylist entry #1" in bad.stdout + bad.stderr
    assert not (tmp_path / "o2").exists()


def test_export_scans_dirs_that_git_will_ship(tmp_path):
    src = make_src(tmp_path, {"docs/venv/x.md": f"{PIT}\n"})
    dest = tmp_path / "out"
    assert ep.export(src, dest, denylist=DL) == ["docs/venv/x.md:1: pit-token"]
    assert not dest.exists()


def test_export_flags_symlink_and_binary(tmp_path):
    src = make_src(tmp_path, {"bin.dat": "x"})
    (src / "bin.dat").write_bytes(b"\x00\x01")
    (src / "ln.md").symlink_to(src / "README.md")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "more")
    assert sorted(ep.export(src, tmp_path / "out", denylist=DL)) == [
        "bin.dat: binary file not scanned", "ln.md: symlink not allowed"]


def test_export_refuses_without_usable_denylist(tmp_path):
    src = make_src(tmp_path)
    for bad in ([], ["", ""]):
        with pytest.raises(SystemExit) as e:
            ep.export(src, tmp_path / "out", denylist=bad)
        assert e.value.code == 2
    assert not (tmp_path / "out").exists()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_public.py"), str(src),
                        str(tmp_path / "o3"), "--no-denylist"], capture_output=True, text=True)
    assert r.returncode == 2 and not (tmp_path / "o3").exists()


def test_export_dest_that_is_a_file_is_a_clean_error(tmp_path):
    src = make_src(tmp_path)
    f = put(tmp_path, "afile", "x")
    with pytest.raises(SystemExit):
        ep.export(src, f, denylist=DL)
    assert f.read_text() == "x"


def test_export_identity_cannot_be_overridden_and_is_unsigned(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "Mallory" if "NAME" in k else "mallory@example.invalid")
    cfg = put(tmp_path, "gitconfig", "[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /usr/bin/false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    dest = tmp_path / "out"
    assert ep.export(src, dest, denylist=DL) == []
    got = git(dest, "log", "-1", "--format=%an|%ae|%cn|%ce|%G?").strip()
    assert got == "Solnest AI|solnest-ai@users.noreply.github.com|Solnest AI|solnest-ai@users.noreply.github.com|N"
