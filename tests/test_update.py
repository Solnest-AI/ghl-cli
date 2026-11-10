"""`ghl update` (download the latest release, swap the tool files, keep the member's files, one-step
undo) and scripts/detach_from_github.py (unlink a git clone). Ported from the Listing Optimizer's
tests. No network: the release lookup and download are faked, and every git repo here is local."""
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("click")

from click.testing import CliRunner  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import detach_from_github as dg  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod  # noqa: E402
from cli_anything.gohighlevel.utils import doctor  # noqa: E402
from cli_anything.gohighlevel.utils import updater as update  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://github.com/Solnest-AI/ghl-cli.git"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
ENV.pop("GHL_KEEP_GIT", None)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git needed")


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _member_clone(tmp_path, origin=PUBLIC):
    """What `git clone` of the public repo looks like: main, tracking origin/main."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "ghl-cli"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "scripts").mkdir()
    (work / "scripts" / "tool.py").write_text("print('v1')\n", encoding="utf-8")
    (work / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-url", "origin", origin)
    (work / ".env").write_text("SOME_SETTING=keep-me\n", encoding="utf-8")  # gitignored in real life
    return work


# -- Detach ----------------------------------------------------------------
@needs_git
def test_a_fresh_clone_is_detached_and_remembers_its_files(tmp_path):
    work = _member_clone(tmp_path)
    msg = dg.detach(work, env=ENV)
    assert "detached" in msg and not (work / ".git").exists()
    assert (work / "scripts" / "tool.py").exists() and (work / ".env").exists(), "only .git goes"
    files = json.loads((work / update.MANIFEST).read_text(encoding="utf-8"))["files"]
    assert set(files) == {"scripts/tool.py", "VERSION"}, "the manifest is git's tracked files, never .env"
    assert "not attached" in dg.detach(work, env=ENV), "running it again is a no-op"


@needs_git
@pytest.mark.parametrize("origin", ["git@github.com:solnest-ai/GHL-CLI",
                                    "https://github.com/Solnest-AI/ghl-cli/"])
def test_ssh_and_mixed_case_addresses_count_as_ours(tmp_path, origin):
    work = _member_clone(tmp_path, origin=origin)
    dg.detach(work, env=ENV)
    assert not (work / ".git").exists()


@needs_git
@pytest.mark.parametrize("change", ["edit", "branch", "commit", "stash", "other_origin"])
def test_a_working_copy_is_never_touched(tmp_path, change):
    work = _member_clone(tmp_path, origin="https://github.com/someone/fork.git" if change == "other_origin" else PUBLIC)
    if change == "edit":
        (work / "scripts" / "tool.py").write_text("print('mine')\n", encoding="utf-8")
    elif change == "branch":
        _git(work, "branch", "my-experiment")
    elif change == "commit":
        (work / "new.py").write_text("x = 1\n", encoding="utf-8")
        _git(work, "add", "new.py")
        _git(work, "commit", "-qm", "local work")
    elif change == "stash":
        (work / "scripts" / "tool.py").write_text("print('wip')\n", encoding="utf-8")
        _git(work, "stash", "-q")
    msg = dg.detach(work, env=ENV)
    assert (work / ".git").is_dir() and "kept attached" in msg
    assert _git(work, "remote").strip() == "origin"


@needs_git
def test_a_developer_can_opt_out(tmp_path):
    work = _member_clone(tmp_path)
    (work / ".git" / "ghl-keep-attached").touch()
    assert "still attached" in dg.detach(work, env=ENV) and (work / ".git").is_dir()
    (work / ".git" / "ghl-keep-attached").unlink()
    assert "still attached" in dg.detach(work, env={**ENV, "GHL_KEEP_GIT": "1"}) and (work / ".git").is_dir()


@needs_git
def test_a_linked_worktree_is_a_developer_copy_and_never_detached(tmp_path):
    """In a git worktree .git is a FILE pointing at the main repo. Removing it would break the
    developer's checkout, so it is never touched, whatever origin says."""
    main = _member_clone(tmp_path)
    tree = tmp_path / "linked-tree"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(tree))
    assert (tree / ".git").is_file()
    before = (tree / ".git").read_bytes()
    msg = dg.detach(tree, env=ENV)
    assert (tree / ".git").is_file() and (tree / ".git").read_bytes() == before
    assert "worktree" in msg and "kept attached" in msg
    assert not (tree / update.MANIFEST).exists(), "nothing is written into a developer copy"
    assert _git(tree, "rev-parse", "--git-dir").strip(), "git still works in the worktree"


def test_no_working_git_leaves_the_folder_alone(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    def broken(*a, **k):
        raise OSError("xcrun: error: invalid active developer path")
    monkeypatch.setattr(dg.subprocess, "run", broken)
    assert "left as is" in dg.detach(tmp_path, env={}) and (tmp_path / ".git").is_dir()


def test_detach_always_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dg, "detach", lambda *a, **k: "GitHub: kept attached. test")
    assert dg.main() == 0 and "kept attached" in capsys.readouterr().out


# -- Update ----------------------------------------------------------------
def _tarball(files: dict, top="ghl-cli-2.2.0", extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        d = tarfile.TarInfo(top)
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        for rel, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return buf.getvalue()


V1 = {"VERSION": "2.1.0\n", "setup.py": "# setup v1\n", "cli_anything/tool.py": "print('v1')\n",
      "cli_anything/old.py": "print('old')\n", "tests/test_old.py": "def test_old(): pass\n"}
V2 = {"VERSION": "2.2.0\n", "setup.py": "# setup v1\n", "cli_anything/tool.py": "print('v2')\n",
      "cli_anything/new.py": "print('new')\n"}
USER = {".env": "SOME_SETTING=keep-me\n", ".venv/bin/python": "py", ".pytest_cache/v/cache": "c",
        "my-notes.md": "mine, not in any release\n", "builders/mine/spec.json": "{}"}


def _plain_copy(tmp_path, files=V1, manifest=True):
    root = tmp_path / "ghl-cli"
    for rel, body in {**files, **USER}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    if manifest:
        update.write_manifest(root, list(files))
    return root


def _fake_release(monkeypatch, files=V2, tag="v2.2.0", blob=None):
    calls = []
    monkeypatch.setattr(update, "latest_tag", lambda: tag)
    def download(t):
        calls.append(t)
        return blob if blob is not None else _tarball(files)
    monkeypatch.setattr(update, "download", download)
    return calls


def _snapshot(root):
    return {str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_constants_are_ghl_cli(tmp_path):
    assert update.REPO == "Solnest-AI/ghl-cli" and update.MANIFEST == ".ghl-files.json"
    assert update.BACKUP_DIR == ".update-backup" and update.UA == {"User-Agent": "ghl-cli-updater"}
    assert update.PROTECTED_FILES >= {".env"}
    assert set(update.PROTECTED_DIRS) == {".venv/", ".git/", ".update-backup/", ".pytest_cache/"}
    assert update.ROOT == ROOT, "the install root is the folder holding VERSION, never the cwd"
    assert (update.ROOT / "VERSION").is_file()


def test_an_update_replaces_the_tool_and_keeps_every_member_file(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    _fake_release(monkeypatch)
    report = update.update(root)
    assert report["from"] == "2.1.0" and report["to"] == "2.2.0"
    assert (root / "cli_anything/tool.py").read_text() == "print('v2')\n"
    assert (root / "cli_anything/new.py").exists()
    assert not (root / "cli_anything/old.py").exists() and not (root / "tests/test_old.py").exists(), \
        "a file the release dropped must not be left behind"
    for rel, body in USER.items():
        assert (root / rel).read_text(encoding="utf-8") == body, f"{rel} must survive an update"
    assert (root / "VERSION").read_text().strip() == "2.2.0"
    assert set(json.loads((root / update.MANIFEST).read_text())["files"]) == set(V2)


def test_an_edited_file_is_backed_up_and_named(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    (root / "cli_anything/tool.py").write_text("print('my tweak')\n", encoding="utf-8")
    _fake_release(monkeypatch)
    report = update.update(root)
    assert report["edited"] == ["cli_anything/tool.py"]
    backup = Path(report["backup"])
    assert (backup / "cli_anything/tool.py").read_text() == "print('my tweak')\n"


def test_undo_puts_back_exactly_the_previous_version(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch)
    update.update(root)
    update.undo(root)
    after = {k: v for k, v in _snapshot(root).items() if not k.startswith(update.BACKUP_DIR)}
    assert after == before


def test_already_current_downloads_nothing(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path, files={**V1, "VERSION": "2.2.0\n"})
    calls = _fake_release(monkeypatch)
    assert update.update(root)["status"] == "current" and calls == []


def test_force_downloads_even_when_current(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path, files={**V1, "VERSION": "2.2.0\n"})
    calls = _fake_release(monkeypatch)
    assert update.update(root, force=True)["status"] == "updated" and calls == ["v2.2.0"]


def test_a_folder_still_linked_to_git_is_not_overwritten(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    (root / ".git").mkdir()
    calls = _fake_release(monkeypatch)
    with pytest.raises(update.UpdateError, match="git pull"):
        update.update(root)
    assert calls == [] and (root / "cli_anything/tool.py").read_text() == "print('v1')\n"


def test_a_linked_worktree_is_not_overwritten_either(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    (root / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/x\n", encoding="utf-8")
    calls = _fake_release(monkeypatch)
    with pytest.raises(update.UpdateError, match="git pull"):
        update.update(root)
    assert calls == [] and (root / "cli_anything/tool.py").read_text() == "print('v1')\n"


@pytest.mark.parametrize("evil", ["../escape.py", "/etc/passwd", "link"])
def test_a_tarball_that_reaches_outside_the_folder_is_refused(tmp_path, monkeypatch, evil):
    info = tarfile.TarInfo(f"ghl-cli-2.2.0/{evil}" if not evil.startswith("/") else evil)
    if evil == "link":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    else:
        info.size = 1
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch, blob=_tarball(V2, extra=[info]))
    with pytest.raises(update.UpdateError):
        update.update(root)
    assert _snapshot(root) == before and not (tmp_path / "escape.py").exists()


def test_a_failure_halfway_rolls_back(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch)
    real = update._write
    def flaky(path, data):
        if path.name == "new.py":
            raise OSError("disk full")
        real(path, data)
    monkeypatch.setattr(update, "_write", flaky)
    with pytest.raises(update.UpdateError, match="put back"):
        update.update(root)
    assert {k: v for k, v in _snapshot(root).items() if not k.startswith(update.BACKUP_DIR)} == before


def test_no_network_is_a_clear_message_and_changes_nothing(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    def offline():
        raise update.UpdateError("could not reach GitHub (URLError)")
    monkeypatch.setattr(update, "latest_tag", offline)
    with pytest.raises(update.UpdateError, match="could not reach GitHub"):
        update.update(root)
    assert _snapshot(root) == before


def test_the_shipped_env_example_is_updated_but_real_env_files_never_are(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path, files={**V1, ".env.example": "GHL_API_KEY=\n"})
    (root / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    _fake_release(monkeypatch, files={**V2, ".env.example": "GHL_API_KEY=\n# GHL_LOCATION_ID=\n",
                                      ".env.local": "overwritten\n"})
    update.update(root)
    assert "GHL_LOCATION_ID" in (root / ".env.example").read_text()
    assert (root / ".env.local").read_text() == "SECRET=1\n" and (root / ".env").read_text() == USER[".env"]


def test_a_release_whose_version_does_not_match_its_tag_is_refused(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch, files={**V2, "VERSION": "2.1.5\n"}, tag="v2.2.0")
    with pytest.raises(update.UpdateError, match="2.1.5"):
        update.update(root)
    assert _snapshot(root) == before


def test_settings_home_is_never_touched(tmp_path, monkeypatch):
    home = tmp_path / "ghl-home"
    tree = {"keys.env": "GHL_API_KEY=not-a-real-value\n", "config.json": '{"location_id": "x"}\n',
            "snapshots/2026-10-01/wf.json": '{"a": 1}\n', "snapshots/readme.txt": "kept\n"}
    for rel, body in tree.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(body, encoding="utf-8")
    monkeypatch.setenv("GHL_CLI_HOME", str(home))
    before = _snapshot(home)
    root = _plain_copy(tmp_path)
    _fake_release(monkeypatch)
    update.update(root)
    assert _snapshot(home) == before, "same files, same bytes, nothing added or removed"
    update.undo(root)
    assert _snapshot(home) == before


def test_a_release_cannot_write_protected_paths(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    _fake_release(monkeypatch, files={**V2, ".git/config": "x", ".venv/bin/python": "evil",
                                      ".update-backup/z": "z", ".pytest_cache/q": "q", ".env": "evil"})
    update.update(root)
    assert (root / ".env").read_text() == USER[".env"] and (root / ".venv/bin/python").read_text() == "py"
    assert not (root / ".git").exists()


def test_the_repo_ships_a_version_and_ignores_local_update_files():
    assert update.parse_version((ROOT / "VERSION").read_text()) is not None
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert update.MANIFEST in ignored and update.BACKUP_DIR in ignored


# -- After the swap: editable reinstall --------------------------------------
def test_a_changed_setup_py_is_reported_and_unchanged_is_not(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    _fake_release(monkeypatch)
    assert update.update(root)["setup_changed"] is False
    root2 = _plain_copy(tmp_path / "b")
    _fake_release(monkeypatch, files={**V2, "setup.py": "# setup v2\n"})
    assert update.update(root2)["setup_changed"] is True


def test_reinstall_runs_uv_pip_install_editable_with_this_python(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(update.shutil, "which", lambda name: "/opt/bin/uv")
    monkeypatch.setattr(update.subprocess, "run",
                        lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    msg = update.reinstall(tmp_path)
    assert ran == [["/opt/bin/uv", "pip", "install", "-e", str(tmp_path), "--python", sys.executable]]
    assert "-e" in update.reinstall_command(tmp_path) and "--non-editable" not in " ".join(ran[0])
    assert "—" not in msg


def test_reinstall_without_uv_anywhere_points_at_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr(update.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    called = []
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: called.append(a))
    msg = update.reinstall(tmp_path)
    assert called == []
    assert "Rerun setup (SETUP.md Phase 1, step 1c)" in msg
    # never a bare `uv ...` line for a member who has no uv on the PATH
    assert "uv pip install" not in msg and "—" not in msg


@pytest.mark.parametrize("platform,exe", [("darwin", "uv"), ("win32", "uv.exe")])
def test_reinstall_finds_uv_where_its_installer_puts_it(tmp_path, monkeypatch, platform, exe):
    """uv's installer writes ~/.local/bin/uv (Windows: %USERPROFILE%\\.local\\bin\\uv.exe), which an
    already-open shell's PATH may not have yet."""
    home = tmp_path / "home"
    uv = home / ".local" / "bin" / exe
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr(update.sys, "platform", platform)
    monkeypatch.setattr(update.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERPROFILE", str(home))
    ran = []
    monkeypatch.setattr(update.subprocess, "run",
                        lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert update.reinstall(tmp_path) == "packages: reinstalled"
    assert ran == [[str(uv), "pip", "install", "-e", str(tmp_path), "--python", sys.executable]]


def test_a_failed_reinstall_says_the_command_to_run_by_hand(tmp_path, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: "/opt/bin/uv")
    monkeypatch.setattr(update.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    msg = update.reinstall(tmp_path)
    assert shlex.join(["uv", "pip", "install", "-e", str(tmp_path), "--python", sys.executable]) in msg and "did not finish" in msg


# -- The command ---------------------------------------------------------------
@pytest.fixture
def cmd_env(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "reinstall", lambda r: "packages: reinstalled")
    monkeypatch.setattr(update, "run_doctor_subprocess", lambda emit, loc=None, root=None: None)
    return root


def _checks(ok=True):
    if ok:
        return [doctor.Check("GHL key", True, "works")]
    return [doctor.Check("GHL key", False, "GHL would not accept the key", "paste a new key")]


def test_ghl_update_prints_what_changed_and_the_doctor_lines(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks())
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0, res.output
    assert "2.1.0" in res.output and "2.2.0" in res.output
    assert "  GHL key: OK works" in res.output
    assert "ghl update --undo" in res.output and "—" not in res.output
    assert (cmd_env / "cli_anything/tool.py").read_text() == "print('v2')\n"


def test_a_doctor_problem_after_an_update_does_not_fail_the_update(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks(ok=False))
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0, res.output
    assert "GHL key: PROBLEM GHL would not accept the key" in res.output
    assert "fix: paste a new key" in res.output
    assert "ghl update --undo" in res.output and "—" not in res.output


def test_ghl_update_reinstalls_only_when_setup_py_changed(cmd_env, monkeypatch):
    _fake_release(monkeypatch, files={**V2, "setup.py": "# setup v2\n"})
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks())
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert "packages: reinstalled" in res.output
    root2 = _plain_copy(cmd_env.parent / "again")
    monkeypatch.setattr(update, "ROOT", root2)
    _fake_release(monkeypatch)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert "packages: reinstalled" not in res.output


def test_ghl_update_already_current(cmd_env, monkeypatch):
    (cmd_env / "VERSION").write_text("2.2.0\n")
    calls = _fake_release(monkeypatch)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0 and "Already on the latest" in res.output and calls == []


def test_ghl_update_offline_is_a_plain_message_and_exit_1(cmd_env, monkeypatch):
    def offline():
        raise update.UpdateError("could not reach GitHub (URLError)")
    monkeypatch.setattr(update, "latest_tag", offline)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 1 and "could not reach GitHub" in res.output
    assert "contact" not in res.output.lower() and "—" not in res.output
    assert "Traceback" not in res.output


def test_ghl_update_undo_puts_back_the_old_version(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks())
    CliRunner().invoke(cli_mod.cli, ["update"])
    res = CliRunner().invoke(cli_mod.cli, ["update", "--undo"])
    assert res.exit_code == 0 and "2.1.0" in res.output and "—" not in res.output
    assert (cmd_env / "cli_anything/tool.py").read_text() == "print('v1')\n"
    res = CliRunner().invoke(cli_mod.cli, ["update", "--undo"])
    assert res.exit_code == 1 and "no earlier version" in res.output


def test_ghl_update_does_not_need_experimental(cmd_env):
    assert "update" in cli_mod.cli.commands and not cli_mod.cli.commands["update"].hidden


# -- The post-update check runs the NEW code in a subprocess -------------------------
def test_the_post_update_check_is_a_subprocess_of_the_new_code(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    seen = {}
    def fake(emit, location_id=None, root=None):
        seen["called"] = True
        emit("  GHL key: OK from the new process")
        return 0
    monkeypatch.setattr(update, "run_doctor_subprocess", fake)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: pytest.fail("old in-process doctor ran"))
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0 and seen["called"]
    assert "GHL key: OK from the new process" in res.output and "old code" not in res.output


def test_a_subprocess_doctor_problem_keeps_the_update_and_mentions_undo(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    def fake(emit, location_id=None, root=None):
        emit("  GHL key: PROBLEM nope")
        return 1
    monkeypatch.setattr(update, "run_doctor_subprocess", fake)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0 and "PROBLEM nope" in res.output
    assert "ghl update --undo" in res.output and (cmd_env / "VERSION").read_text().strip() == "2.2.0"


def test_if_the_subprocess_cannot_start_it_falls_back_and_says_so(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    monkeypatch.setattr(update, "run_doctor_subprocess", lambda *a, **k: None)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks(ok=False))
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0 and "used the old code" in res.output
    assert "GHL key: PROBLEM" in res.output and "ghl update --undo" in res.output and "—" not in res.output


def test_run_doctor_subprocess_command_streams_and_returns_the_exit_code(tmp_path, monkeypatch):
    class FakeProc:
        stdout = iter(["  GHL key: OK\n", "  Workflow token: OK\n"])
        def wait(self):
            return 0
    launched = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **k: launched.append((cmd, k)) or FakeProc())
    lines = []
    assert update.run_doctor_subprocess(lines.append, "LOCID", tmp_path) == 0
    assert launched[0][0] == [sys.executable, "-m", "cli_anything.gohighlevel.gohighlevel_cli",
                              "--location-id", "LOCID", "doctor"]
    assert launched[0][1]["cwd"] == tmp_path and lines == ["  GHL key: OK", "  Workflow token: OK"]


def test_run_doctor_subprocess_returns_none_when_it_cannot_start(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("no such file")
    monkeypatch.setattr(update.subprocess, "Popen", boom)
    assert update.run_doctor_subprocess(lambda l: None, None, tmp_path) is None


# -- final review: the skill follows the code ------------------------------------
@pytest.mark.parametrize("doctor_ok", [True, False])
def test_a_successful_update_ends_with_next_skill_install(cmd_env, monkeypatch, doctor_ok):
    _fake_release(monkeypatch)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks(ok=doctor_ok))
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0, res.output
    assert res.output.rstrip().splitlines()[-1].startswith("Next: ghl skill install")
    assert "—" not in res.output


def test_undo_also_says_next_skill_install(cmd_env, monkeypatch):
    _fake_release(monkeypatch)
    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: _checks())
    CliRunner().invoke(cli_mod.cli, ["update"])
    res = CliRunner().invoke(cli_mod.cli, ["update", "--undo"])
    assert res.exit_code == 0 and "Next: ghl skill install" in res.output


def test_no_next_skill_line_when_nothing_was_swapped(cmd_env, monkeypatch):
    (cmd_env / "VERSION").write_text("2.2.0\n")
    _fake_release(monkeypatch)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 0 and "Next: ghl skill install" not in res.output

    def offline():
        raise update.UpdateError("could not reach GitHub (URLError)")
    monkeypatch.setattr(update, "latest_tag", offline)
    res = CliRunner().invoke(cli_mod.cli, ["update"])
    assert res.exit_code == 1 and "Next: ghl skill install" not in res.output
