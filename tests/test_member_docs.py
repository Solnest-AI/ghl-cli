"""Member-facing docs and the installer: every command they name exists, and the rules hold.

No network. These read files and walk the click command tree only.
"""
import inspect
import re
from pathlib import Path

import pytest

pytest.importorskip("click")

import click  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

MEMBER_DOCS = ["SETUP.md", "README.md", "CLAUDE.md", "builders/README.md", "docs/get-firebase-token.md"]
SETUP_SCRIPTS = ["setup.sh", "setup.ps1", "setup.cmd"]
EM_DASH = "—"


def _text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# -- the brief's four tests --------------------------------------------------

def test_member_docs_have_no_em_dash_or_dead_ends():
    for f in MEMBER_DOCS:
        t = _text(f)
        assert EM_DASH not in t, f
        assert "contact the author" not in t.lower(), f
        assert "/Users/" + "ryan_" not in t, f
        # Public repo: Solnest AI wording, never a person's name or Ryan's ids.
        assert not re.search(r"\bRyan\b", t), f
        assert "ryan" + "lefebvre" not in t.lower(), f


# Global options the top-level group takes before a command name.
_GLOBAL_FLAGS = {"--json", "--experimental", "--version", "--help"}
_GLOBAL_WITH_VALUE = {"--location-id"}
_STOP = {"|", "||", "&&", ";", ">", ">>", "2>&1", "2>/dev/null"}
# A token that runs the CLI: ghl, ghl.exe, or a path ending in one (quotes and & allowed).
_GHL_TOKEN = re.compile(r"""^["'(&]*(?:\S*[/\\])?ghl(?:\.exe)?["')]?$""")
_NAME = re.compile(r"[a-z][a-z0-9-]*")


def _chunks(text):
    """Fenced code lines whole; elsewhere, each line split on backticks (a code span ends a run)."""
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield line
        else:
            yield from line.split("`")


def _invocations(text):
    """Yield the token list after each `ghl` invocation, up to a shell separator or comment."""
    for chunk in _chunks(text):
        tokens = chunk.split()
        for i, tok in enumerate(tokens):
            if not _GHL_TOKEN.match(tok):
                continue
            # A bare `ghl` runs only at the start of a command; mid-sentence it is quoted output
            # ("Could not find the ghl script"). A path to ghl always runs.
            if not re.search(r"[/\\]", tok) and i and tokens[i - 1] not in _STOP | {"&"}:
                continue
            rest = []
            for t in tokens[i + 1:]:
                if t in _STOP or t.startswith("#") or _GHL_TOKEN.match(t):
                    break
                if t.endswith(";"):          # `ghl --version; cat VERSION`
                    rest.append(t.rstrip(";"))
                    break
                rest.append(t)
            yield chunk.strip(), rest


def _option_names(cmd):
    names = {"--help"}
    for p in cmd.params:
        if isinstance(p, click.Option):
            names.update(o for o in p.opts + p.secondary_opts if o.startswith("--"))
    return names


def _needs_experimental(cmd):
    try:
        return "_require_experimental" in inspect.getsource(cmd.callback)
    except (OSError, TypeError):
        return False


def _check(rest):
    """None when the invocation resolves in the real CLI, else why it does not."""
    toks = list(rest)
    experimental = False
    while toks and toks[0].startswith("-"):
        flag = toks.pop(0).split("=", 1)[0]
        if flag in _GLOBAL_WITH_VALUE:
            if toks:
                toks.pop(0)
        elif flag == "--experimental":
            experimental = True
        elif flag not in _GLOBAL_FLAGS:
            return f"unknown global option {flag}"
    if not toks or not _NAME.fullmatch(toks[0]):
        return None                      # `ghl`, `ghl --version`, or a placeholder like <group>
    name = toks.pop(0)
    cmd = cli_mod.cli.commands.get(name)
    if cmd is None:
        return f"no command {name!r}"
    if isinstance(cmd, click.Group):
        if not toks or not _NAME.fullmatch(toks[0]):
            return None
        sub = toks.pop(0)
        if sub not in cmd.commands:
            return f"no command {name} {sub}"
        cmd = cmd.commands[sub]
    allowed = _option_names(cmd)
    for t in toks:
        if t.startswith("--"):
            flag = t.split("=", 1)[0].rstrip(",.;:)\"'")
            if flag not in allowed:
                return f"{cmd.name} has no option {flag}"
    if _needs_experimental(cmd) and not experimental:
        return f"{cmd.name} needs --experimental before the group name"
    return None


def test_every_ghl_command_in_the_docs_exists():
    seen = {}
    for f in MEMBER_DOCS:
        for chunk, rest in _invocations(_text(f)):
            problem = _check(rest)
            assert problem is None, f"{f}: {problem}: {chunk!r}"
            seen.setdefault(f, []).append(" ".join(rest[:2]))
    setup = " | ".join(seen.get("SETUP.md", []))
    # The runbook names every member command, so a parser that matched nothing cannot pass.
    for must in ("doctor", "keys paste", "config set-location", "selftest", "skill install", "update"):
        assert must in setup, f"SETUP.md never runs `ghl {must}`"
    assert sum(len(v) for v in seen.values()) >= 25


def test_the_command_checker_catches_bad_commands():
    """The checker itself: a typo, a missing --experimental and an unknown flag all fail."""
    def bad(line):
        return [_check(rest) for _, rest in _invocations(line)]
    assert bad("`ghl doctr`") == ["no command 'doctr'"]
    assert bad("`ghl keys pasta api-key`") == ["no command keys pasta"]
    assert bad("`ghl workflows build --from-spec s.json`")[0].endswith("needs --experimental before the group name")
    assert bad("`ghl selftest --firee`") == ["selftest has no option --firee"]
    assert bad("```\n~/ghl-cli/.venv/bin/ghl update --undo\n```") == [None]
    assert bad('`& "$env:USERPROFILE\\ghl-cli\\.venv\\Scripts\\ghl.exe" doctor --only api-key`') == [None]
    assert list(_invocations("the gohighlevel_cli module, ~/.ghl-cli and skills/ghl-cli/")) == []
    assert list(_invocations("`Could not find the ghl script next to this Python`")) == []
    assert bad("```\nprintf x | ghl keys pasta api-key --from-stdin\n```") == ["no command keys pasta"]


def _sections(text):
    """Map '## ' heading -> its body (to the next '## ')."""
    parts = re.split(r"(?m)^## ", text)
    return {p.splitlines()[0].strip(): p for p in parts[1:]}


def test_setup_has_six_phases_each_with_a_gate():
    text = _text("SETUP.md")
    heads = re.findall(r"(?m)^## (Phase \d+)\.", text)
    assert heads == [f"Phase {n}" for n in range(6)], heads
    for head, body in _sections(text).items():
        if not head.startswith("Phase "):
            continue
        assert "**GATE" in body, head
        assert "If it fails" in body, head
        assert "Mac" in body and "Windows" in body, head
    hard = text.split("## Phase 0.")[0]
    assert "HARD RULES" in hard
    assert "keys.env" in hard and "plain yes" in hard


def test_readme_credits_jay_and_claims_no_license():
    t = _text("README.md")
    assert "Built on Lead Gen Jay's GoHighLevel CLI, shared with his permission." in t
    assert "MIT" not in t
    assert "licen" not in t.lower()


# -- what the brief and the rulings pin down ---------------------------------

REPO_URL = "https://github.com/Solnest-AI/ghl-cli"


def test_setup_and_readme_download_the_latest_release_instead_of_cloning():
    """R18: the latest RELEASE tag, resolved through releases/latest, never the main branch."""
    from cli_anything.gohighlevel.utils import updater
    assert REPO_URL == f"https://github.com/{updater.REPO}"     # docs and `ghl update` agree
    for f in ("SETUP.md", "README.md"):
        t = _text(f)
        assert "refs/heads/main" not in t, f
        assert "releases/latest" in t and "/archive/refs/tags/" in t, f
        assert REPO_URL in t and "~/ghl-cli" in t, f
        assert "NO RELEASE PUBLISHED YET" in t, f
        assert "do **not** `git clone`" in t or "never `git clone`" in t.lower(), f
    s = _text("SETUP.md")
    # both Windows routes resolve the tag too, and the zip route no longer names a branch folder
    assert s.count("releases/latest") >= 3 and "ghl-cli-main" not in s
    assert '$Repo/archive/refs/tags/$TAG.tar.gz' in s and '$Repo/archive/refs/tags/$TAG.zip' in s
    assert "curl.exe" in s and "Invoke-WebRequest" in s
    # a "no release" row exists in Phase 1's table
    assert re.search(r"(?m)^\| `NO RELEASE PUBLISHED YET` \|", s)


def _download_block(rel):
    """The Mac download block (fenced bash holding releases/latest and refs/tags) from a doc;
    a README blockquote's leading '> ' is stripped."""
    lines = [re.sub(r"^> ?", "", ln) for ln in _text(rel).splitlines()]
    blocks, cur = [], None
    for ln in lines:
        if ln.strip() == "```bash":
            cur = []
        elif ln.strip() == "```" and cur is not None:
            blocks.append("\n".join(cur))
            cur = None
        elif cur is not None:
            cur.append(ln)
    found = [b for b in blocks if "releases/latest" in b and "archive/refs/tags" in b and "mkdir" in b]
    assert len(found) == 1, rel
    return found[0]


FAKE_CURL = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG"
case " $* " in *" -fsSLI "*)
  [ -n "$FAKE_RESOLVE_RC" ] && exit "$FAKE_RESOLVE_RC"
  printf '%s' "$FAKE_EFFECTIVE"; exit 0 ;;
esac
out=; prev=
for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done
[ -n "$FAKE_DOWNLOAD_RC" ] && exit "$FAKE_DOWNLOAD_RC"
cp "$FAKE_TARBALL" "$out"
"""


def _run_download(tmp_path, rel, **fake):
    """Run a doc's download block with HOME in tmp_path and a fake curl first on the PATH."""
    import shutil
    import subprocess
    import sys
    import tarfile
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32" or not Path("/usr/bin/tar").exists():
        pytest.skip("needs bash and tar")
    home, fakebin = tmp_path / "home", tmp_path / "bin"
    home.mkdir(exist_ok=True)
    fakebin.mkdir(exist_ok=True)
    (fakebin / "curl").write_text(FAKE_CURL)
    (fakebin / "curl").chmod(0o755)
    src = tmp_path / "ghl-cli-9.9.9"
    src.mkdir(exist_ok=True)
    (src / "setup.sh").write_text("#!/bin/sh\n")
    (src / "VERSION").write_text("9.9.9\n")
    tarball = tmp_path / "release.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(src, arcname="ghl-cli-9.9.9")
    log = tmp_path / "curl.log"
    log.write_text("")
    env = {"HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin", "FAKE_LOG": str(log),
           "FAKE_TARBALL": str(tarball),
           "FAKE_EFFECTIVE": "https://github.com/Solnest-AI/ghl-cli/releases/tag/v9.9.9"}
    env.update(fake)
    r = subprocess.run([bash, "-c", _download_block(rel)], env=env, cwd=tmp_path,
                       capture_output=True, text=True)
    return r, home / "ghl-cli", log.read_text().splitlines()


@pytest.mark.parametrize("rel", ["SETUP.md", "README.md"])
def test_download_block_gets_the_latest_release_tag(tmp_path, rel):
    r, d, calls = _run_download(tmp_path, rel)
    assert r.returncode == 0, r.stderr
    assert "DOWNLOADED v9.9.9" in r.stdout
    assert (d / "setup.sh").is_file() and (d / "VERSION").read_text() == "9.9.9\n"
    assert not (d.parent / "ghl-cli.tar.gz").exists()
    assert calls[0].endswith("https://github.com/Solnest-AI/ghl-cli/releases/latest")
    assert calls[1].endswith("https://github.com/Solnest-AI/ghl-cli/archive/refs/tags/v9.9.9.tar.gz")
    assert len(calls) == 2 and not any("refs/heads" in c for c in calls)


@pytest.mark.parametrize("rel", ["SETUP.md", "README.md"])
def test_download_block_with_no_release_creates_nothing(tmp_path, rel):
    # GitHub sends /releases/latest to /releases when a repo has no release (checked live)
    r, d, calls = _run_download(tmp_path, rel,
                                FAKE_EFFECTIVE="https://github.com/Solnest-AI/ghl-cli/releases")
    assert r.stdout.strip() == "NO RELEASE PUBLISHED YET"
    assert not d.exists() and len(calls) == 1


@pytest.mark.parametrize("rel", ["SETUP.md", "README.md"])
def test_download_block_when_github_cannot_be_reached(tmp_path, rel):
    # a missing repo answers 404 and curl exits 56 (checked live); offline exits non-zero too
    r, d, calls = _run_download(tmp_path, rel, FAKE_RESOLVE_RC="56")
    assert r.stdout.strip() == "DOWNLOAD FAILED (could not reach GitHub)"
    assert not d.exists() and len(calls) == 1


@pytest.mark.parametrize("rel", ["SETUP.md", "README.md"])
def test_failed_archive_download_leaves_nothing_behind(tmp_path, rel):
    r, d, calls = _run_download(tmp_path, rel, FAKE_DOWNLOAD_RC="22")
    assert r.stdout.strip() == "DOWNLOAD FAILED"
    assert not d.exists() and not (d.parent / "ghl-cli.tar.gz").exists()
    assert len(calls) == 2


@pytest.mark.parametrize("rel", ["SETUP.md", "README.md"])
def test_download_block_never_touches_an_existing_folder(tmp_path, rel):
    (tmp_path / "home" / "ghl-cli").mkdir(parents=True)
    r, d, calls = _run_download(tmp_path, rel)
    assert "ALREADY THERE" in r.stdout and calls == []


def test_setup_states_the_rulings():
    t = _text("SETUP.md")
    assert "VERIFY IN BUILD (U5)" in t
    assert "600000" in t and "300000" not in t             # R9 + final review: selftest timeout
    assert "selftest --cleanup RUN" in t
    assert "selftest --check-only" in t
    assert "not yet tested" in t.lower()                   # U6, said once
    assert t.lower().count("not yet tested") == 1
    assert "no email or phone" in t                        # R8
    assert "Conversation AI" in t and "Triggers" in t      # R8
    assert "--from-stdin" in t and "read_env_file" in t    # Phase 0 key move
    assert "agency admin" in t                             # U2, never a dead end
    assert "docs/get-firebase-token.md" in t               # Phase 3 snippet source


def test_claude_md_points_at_setup_and_holds_the_hard_rules():
    t = _text("CLAUDE.md")
    assert "SETUP.md" in t
    low = t.lower()
    assert "never ask" in low and "key" in low
    assert "keys.env" in t
    assert "plain yes" in low
    assert "never commit, push, fork or open a pull request" in low
    # the one .env exception is named, and a development copy is told apart
    assert "1e" in t and "--from-stdin" in t
    assert ".git/ghl-keep-attached" in t
    assert "600000" in t and "300000" not in t


def test_builders_readme_says_what_the_examples_are():
    t = _text("builders/README.md")
    assert "Lead Gen Jay" in t and "permission" in t
    assert "workflows preview" in t and "workflows build" in t
    assert "run as-is" in t          # every builder reads email copy from files not shipped


@pytest.mark.parametrize("rel", ["README.md", "builders/README.md"])
def test_example_commands_use_the_full_ghl_path(rel):
    """ghl is not on the PATH, so a bare `ghl` in a copyable block would not run."""
    in_fence, bare = False, []
    for ln in _text(rel).splitlines():
        s = re.sub(r"^> ?", "", ln).strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and re.match(r"ghl\s", s):
            bare.append(s)
    assert bare == [], bare
    t = _text(rel)
    assert "~/ghl-cli/.venv/bin/ghl " in t
    assert '& "$env:USERPROFILE\\ghl-cli\\.venv\\Scripts\\ghl.exe" ' in t


def test_selftest_timeout_covers_every_mode_in_setup():
    t = _text("SETUP.md")
    phase4 = _sections(t)["Phase 4. The live test"]
    assert phase4.count("600000") >= 2                    # 4a (check-only) and 4c (the rest)
    run = phase4.split("**4c. Run it.**", 1)[1].split("\n\n", 1)[0]
    for mode in ("--fire", "--draft-only", "--check-only", "--cleanup"):
        assert mode in run, mode
    assert "Reading N published workflows..." in phase4


def test_phase4_gate_matches_what_selftest_prints():
    """PASS_NO_FIRE ends with 'To test firing later', and a fired PASS can end with a list,
    so the gate is a line that reads exactly PASS or PASS, firing not tested, plus exit 0."""
    gate = _sections(_text("SETUP.md"))["Phase 4. The live test"].split("**GATE:**", 1)[1]
    gate = gate.split("**If it fails:**", 1)[0]
    assert "reads exactly `PASS`" in gate and "exactly `PASS, firing not tested`" in gate
    assert "exit code is 0" in gate
    assert "the last line is" not in gate.lower()


def test_removal_names_the_snapshots_and_the_skill_backup():
    for f in ("SETUP.md", "README.md"):
        t = _text(f).split("emoving it", 1)[1]
        assert "snapshots" in t and "SKILL.md.bak" in t, f


def test_firebase_snippet_sends_the_member_back_to_claude():
    t = _text("docs/get-firebase-token.md")
    assert "set-firebase-token" not in t
    block = t.split("```js", 1)[1].split("```", 1)[0]
    assert "Now go back to Claude and say copied." in block
    assert "set-firebase-token" not in _text("SETUP.md")


def test_firebase_doc_keeps_the_snippet_and_uses_keys_paste():
    t = _text("docs/get-firebase-token.md")
    block = t.split("```js", 1)[1].split("```", 1)[0]
    first = block.strip().splitlines()[0].strip()
    assert first == "var _copy = copy;   // capture BEFORE the first await - see note below"
    assert "_copy(stm.refreshToken);" in block
    assert "ghl keys paste workflow-token" in t
    assert "GHL_FIREBASE_REFRESH_TOKEN=<paste here>" not in t   # no hand-editing a file


def test_env_example_points_at_the_commands_and_keeps_the_names():
    t = _text(".env.example")
    assert "ghl keys paste" in t and "ghl config set-location" in t
    for name in ("GHL_API_KEY=", "GHL_LOCATION_ID=", "GHL_FIREBASE_REFRESH_TOKEN=", "GHL_APP_DOMAIN="):
        assert name in t, name


def test_setup_scripts_build_editable_and_detach():
    sh, ps1, cmd = (_text(f) for f in SETUP_SCRIPTS)
    for script in (sh, ps1):
        assert "detach_from_github.py" in script
        assert "pip install" in script and "-e" in script
        assert "3.12" in script
    assert "--no-prompt" in sh and "--auto-install" in sh
    assert "-NoPrompt" in ps1 and "-AutoInstall" in ps1
    assert "setup.ps1" in cmd and "setup.ps1" in sh       # Git Bash on Windows hands off
    # The scripts set up code only: keys are Phases 2-3, the skill is Phase 5.
    for script in (sh, ps1):
        assert "skill install" not in script
        assert "keys.env" not in script and "cp .env.example" not in script


def test_windows_scripts_are_ascii():
    """Windows PowerShell 5.1 misreads UTF-8 without a BOM, and cmd.exe needs ASCII."""
    for f in ("setup.ps1", "setup.cmd"):
        (ROOT / f).read_bytes().decode("ascii")
    cmd = (ROOT / "setup.cmd").read_bytes()
    assert cmd.count(b"\n") == cmd.count(b"\r\n") > 0     # cmd.exe can misparse LF-only batch files
