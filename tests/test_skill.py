"""ghl skill install: fills the member's own paths and never clobbers a hand-written skill."""
import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from cli_anything.gohighlevel.utils import skill_install as si

TEMPLATE = Path(si.__file__).resolve().parents[1] / "skills" / "SKILL.template.md"
HAND_WRITTEN = "---\nname: ghl-cli\n---\nmy own notes, not generated\n"


@pytest.fixture(autouse=True)
def tmp_skills(tmp_path, monkeypatch):
    """Every test writes to a scratch dir, never the real ~/.claude/skills."""
    d = tmp_path / "skills"
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(d))
    return d


@pytest.fixture
def fake_venv(tmp_path, monkeypatch):
    """A venv-like bin dir with a ghl script next to a fake python."""
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    exe = "ghl.exe" if sys.platform == "win32" else "ghl"
    (bin_dir / exe).write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    return bin_dir / exe


def _run(*args):
    return CliRunner().invoke(cli_mod.cli, list(args))


def _path(tmp_skills):
    return tmp_skills / "ghl-cli" / "SKILL.md"


def test_skills_dir_follows_env(tmp_skills):
    assert si.skills_dir() == tmp_skills


def test_skills_dir_defaults_to_home(monkeypatch):
    monkeypatch.delenv("CLAUDE_SKILLS_DIR")
    assert si.skills_dir() == Path.home() / ".claude" / "skills"


def test_install_fills_the_paths(tmp_skills, fake_venv):
    r = _run("skill", "install")
    assert r.exit_code == 0, r.output
    text = _path(tmp_skills).read_text()
    assert "{{" not in text
    assert str(fake_venv) in text
    assert si.GENERATED_MARK in text
    assert str(si.setup_md_path()) in text
    assert text.startswith("---\nname: ghl-cli\n")


def test_never_overwrites_a_hand_written_skill(tmp_skills, fake_venv):
    p = _path(tmp_skills)
    p.parent.mkdir(parents=True)
    p.write_text(HAND_WRITTEN)
    r = _run("skill", "install")
    assert r.exit_code == 2
    assert p.read_text() == HAND_WRITTEN
    assert "--force" in r.output
    assert "—" not in r.output


def test_regenerates_its_own_file(tmp_skills, fake_venv):
    assert _run("skill", "install").exit_code == 0
    p = _path(tmp_skills)
    p.write_text(p.read_text() + "\nstale tail\n")
    assert _run("skill", "install").exit_code == 0
    assert "stale tail" not in p.read_text()


def test_force_overwrites(tmp_skills, fake_venv):
    p = _path(tmp_skills)
    p.parent.mkdir(parents=True)
    p.write_text(HAND_WRITTEN)
    r = _run("skill", "install", "--force")
    assert r.exit_code == 0, r.output
    assert si.GENERATED_MARK in p.read_text()
    assert "not generated" not in p.read_text()


def test_exit_2_when_ghl_script_missing(tmp_skills, tmp_path, monkeypatch):
    bin_dir = tmp_path / "emptyvenv" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    r = _run("skill", "install")
    assert r.exit_code == 2
    assert not _path(tmp_skills).exists()
    assert "—" not in r.output
    assert "ghl" in r.output


def test_install_skill_returns_the_written_path(tmp_skills, tmp_path):
    ghl = tmp_path / "ghl"
    ghl.write_text("x")
    setup = tmp_path / "SETUP.md"
    setup.write_text("x")
    out = si.install_skill(ghl, setup)
    assert out == _path(tmp_skills)
    assert str(ghl) in out.read_text() and str(setup) in out.read_text()


def test_template_is_member_clean():
    t = TEMPLATE.read_text()
    assert "—" not in t
    assert "/Users/" + "ryan_" not in t
    assert "Ryan" not in t
    assert "contact the author" not in t.lower()
    assert set(re.findall(r"\{\{(\w+)\}\}", t)) == {"GHL", "SETUP", "ROOT", "BUILDERS"}


def test_template_has_frontmatter_and_triggers():
    t = TEMPLATE.read_text()
    assert t.startswith("---\nname: ghl-cli\n")
    head = t.split("---", 2)[1]
    for trig in ("ghl", "gohighlevel", "workflow", "fix my GHL keys", "update the GHL CLI"):
        assert trig in head


@pytest.mark.parametrize("needle", [
    "ghl doctor", "ghl update", "ghl keys paste", "ghl selftest", "--check-only",
    "--cleanup", "--confirm", "Undo", "builders/", "--help", "--json",
    "600000", "plain yes", "draft", "/v2/location/", "/automation/workflow/",
    "keys.env", "Publish",
    "tag", "form", "survey", "appointment_booked", "appointment_status",
    "pipeline_stage", "opportunity_status", "contact_created", "customer_replied", "raw",
    "send-invoice", "void-invoice",
    "Remove the GHL CLI", "text editor",
])
def test_template_covers(needle):
    assert needle in TEMPLATE.read_text()


def test_template_ships_in_package_data():
    setup = (Path(si.__file__).resolve().parents[3] / "setup.py").read_text()
    assert "skills/*.md" in setup
    assert TEMPLATE.exists()


def test_old_ryan_skill_is_gone():
    assert not (TEMPLATE.parent / "SKILL.md").exists()


def test_builders_path_is_filled(tmp_skills, tmp_path):
    ghl = tmp_path / "ghl"
    ghl.write_text("x")
    setup = tmp_path / "SETUP.md"
    out = si.install_skill(ghl, setup)
    assert str(tmp_path / "builders") in out.read_text()


def test_body_that_merely_quotes_the_mark_is_refused(tmp_skills, fake_venv):
    p = _path(tmp_skills)
    p.parent.mkdir(parents=True)
    original = f"---\nname: ghl-cli\n---\nmy notes\nthe mark is {si.GENERATED_MARK}\n"
    p.write_text(original)
    r = _run("skill", "install")
    assert r.exit_code == 2
    assert p.read_text() == original


def test_force_backs_up_a_hand_written_file(tmp_skills, fake_venv):
    p = _path(tmp_skills)
    p.parent.mkdir(parents=True)
    p.with_name("SKILL.md.bak").write_text("older backup")
    p.write_text(HAND_WRITTEN)
    r = _run("skill", "install", "--force")
    assert r.exit_code == 0, r.output
    assert p.with_name("SKILL.md.bak").read_text() == HAND_WRITTEN
    assert "SKILL.md.bak" in r.output
    assert "\u2014" not in r.output


# -- final review ---------------------------------------------------------------------

def test_selftest_timeout_covers_every_mode_and_the_old_figure_is_gone():
    t = TEMPLATE.read_text()
    assert "300000" not in t
    line = next(ln for ln in t.splitlines() if "600000" in ln)
    for mode in ("--fire", "--draft-only", "--check-only", "--cleanup"):
        assert mode in line, mode


def test_every_spec_step_type_is_listed_with_its_keys():
    """The skill lists every step type workflow_spec.BUILDERS compiles, plus if_else."""
    from cli_anything.gohighlevel.utils import workflow_spec as ws
    t = TEMPLATE.read_text()
    listed = set(re.findall(r"(?m)^  - `(\w+)`:", t))
    assert listed == set(ws.BUILDERS) | {"if_else"}
    for key in ("`to`", "`field_id`", "`user_id`", "`workflow_id`", "`target`", "`url`"):
        assert key in t, key


def test_auth_check_failure_sends_to_the_full_doctor():
    t = TEMPLATE.read_text()
    row = next(ln for ln in t.splitlines() if ln.startswith("| `auth-check` fails"))
    assert "full" in row and "--only workflow-token" not in row


def _removal_block(text, lang):
    """The fenced block under "Remove the GHL CLI" in the given language."""
    section = text.split('### "Remove the GHL CLI"', 1)[1].split("\n### ", 1)[0]
    return section.split(f"```{lang}\n", 1)[1].split("```", 1)[0]


def test_every_placeholder_is_filled_and_root_is_the_install_folder(tmp_skills, tmp_path):
    root = tmp_path / "my install"           # a space, like many real home folders
    root.mkdir()
    out = si.install_skill(tmp_path / "ghl", root / "SETUP.md").read_text()
    assert "{{" not in out and not re.search(r"\{\{\w+\}\}", out)
    assert f'R="{root}"' in _removal_block(out, "bash")
    assert f'$R = "{root}"' in _removal_block(out, "powershell")
    assert str(root / "builders") in out
    # The old command worked the folder out from a path string, and dirname of a Windows
    # path in bash is ".". Nothing in the skill derives a folder that way any more.
    assert "dirname" not in out and "Split-Path" not in out


def test_removal_command_is_guarded_on_version_and_setup(tmp_path):
    """Runs only the install-folder lines of the rendered bash block, never the ~ line."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("needs bash")
    real = tmp_path / "real install"
    real.mkdir()
    (real / "VERSION").write_text("2.1.0\n")
    (real / "SETUP.md").write_text("x")
    decoy = tmp_path / "not the tool"
    (decoy / "keep").mkdir(parents=True)
    (decoy / "SETUP.md").write_text("no VERSION beside it")
    for root, should_go in ((real, True), (decoy, False)):
        block = _removal_block(si.render(tmp_path / "ghl", root / "SETUP.md"), "bash")
        lines = [ln for ln in block.splitlines() if ln.strip() and "~" not in ln]
        assert len(lines) == 2 and lines[0] == f'R="{root}"'
        r = subprocess.run([bash, "-c", "\n".join(lines)], cwd=tmp_path, capture_output=True,
                           text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "nohome")})
        assert r.returncode == 0, r.stderr
        assert root.exists() is not should_go, r.stdout
        assert r.stdout.startswith("REMOVED" if should_go else "NOT REMOVED"), r.stdout
    assert (decoy / "keep").is_dir() and tmp_path.is_dir()
