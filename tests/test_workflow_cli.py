"""CLI tests for `workflows get`, `workflows edit` and `workflows clone`.

They drive the real click commands against the in-memory fake GHL, so file
handling, exit codes and the printed undo are covered without the network.
"""
import copy
import json

import pytest

pytest.importorskip("click")  # bare system pythons lack it; the venv has it

from click.testing import CliRunner  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from fake_ghl import FakeGHL
from test_workflow_io import (
    DST_LOC,
    OTHER_WF,
    SRC_LOC,
    SRC_WF,
    STEPS,
    SURVEY,
    SURVEY_TRIGGER,
)


@pytest.fixture
def ghl(monkeypatch, tmp_path):
    server = FakeGHL()
    monkeypatch.setattr(
        cli_mod, "_get_internal_client",
        lambda ctx, preflight=True, location_id=None: server.client(location_id or SRC_LOC))
    monkeypatch.setenv("GHL_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    return server


def _run(*args):
    result = CliRunner().invoke(cli_mod.cli, ["--experimental", *args], obj={})
    return result


def _add(server, **kw):
    server.add_workflow(SRC_LOC, SRC_WF, "Proposal follow-up", STEPS,
                        triggers=(SURVEY_TRIGGER,), **kw)


def test_get_export_edited_and_applied_with_edit(ghl, tmp_path):
    _add(ghl, version=5)
    export_path = tmp_path / "wf.json"
    assert _run("workflows", "get", SRC_WF, "--output-file", str(export_path)).exit_code == 0

    export = json.loads(export_path.read_text())
    assert (export["version"], len(export["templates"]), len(export["triggers"])) == (5, 6, 1)
    export["templates"][1]["attributes"]["body"] = "Any questions?"
    export_path.write_text(json.dumps(export))

    result = _run("workflows", "edit", SRC_WF, "--from-json", str(export_path))
    assert result.exit_code == 0, result.output
    saved = ghl.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"]
    assert saved[1]["attributes"]["body"] == "Any questions?"


def test_edit_refuses_a_file_exported_from_another_workflow(ghl, tmp_path):
    _add(ghl)
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"workflow_id": OTHER_WF, "version": 3, "templates": STEPS}))
    result = _run("workflows", "edit", SRC_WF, "--from-json", str(path))
    assert result.exit_code == 2
    assert OTHER_WF in result.output
    assert ghl.writes() == []


def test_edit_of_a_published_workflow_shows_the_change_and_needs_confirm(ghl, tmp_path):
    _add(ghl, status="published")
    steps = copy.deepcopy(STEPS)
    steps[1]["attributes"]["body"] = "Any questions?"
    path = tmp_path / "steps.json"
    path.write_text(json.dumps(steps))

    refused = _run("workflows", "edit", SRC_WF, "--from-json", str(path))
    assert refused.exit_code == 2
    assert "Nudge" in refused.output
    assert ghl.writes() == []

    assert _run("workflows", "edit", SRC_WF, "--from-json", str(path), "--confirm").exit_code == 0
    assert ghl.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] == steps


def test_edit_undo_restores_the_original_steps(ghl, tmp_path):
    _add(ghl)
    steps = copy.deepcopy(STEPS)
    steps[1]["attributes"]["body"] = "Any questions?"
    path = tmp_path / "steps.json"
    path.write_text(json.dumps(steps))
    done = json.loads(_run("--json", "workflows", "edit", SRC_WF, "--from-json", str(path)).output)

    undo = _run("workflows", "edit", SRC_WF, "--from-json", done["snapshot"],
                "--expect-version", str(done["version_after"]))
    assert undo.exit_code == 0, undo.output
    assert ghl.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] == STEPS


def test_clone_to_another_account_refuses_and_lists_ids_to_map(ghl):
    # GHL rejects a save naming assets the target lacks (live, 2026-10-18), so
    # the CLI stops first and says exactly what to map.
    _add(ghl)
    result = _run("workflows", "clone", SRC_WF, "--to-location", DST_LOC)
    assert result.exit_code == 2
    assert SURVEY in result.output and OTHER_WF in result.output and "--map" in result.output
    assert [d for (loc, _), d in ghl.docs.items() if loc == DST_LOC] == []


def test_clone_to_another_account_with_map(ghl):
    _add(ghl)
    target_survey, target_wf = "SurveyTargetAAAAAAA1", "eeeeeeee-0000-4000-8000-00000000000e"
    result = _run("--json", "workflows", "clone", SRC_WF, "--to-location", DST_LOC,
                  "--map", f"{SURVEY}={target_survey}", "--map", f"{OTHER_WF}={target_wf}")
    assert result.exit_code == 0, result.output
    new_id = json.loads(result.stdout)["workflow_id"]
    (trigger,) = ghl.triggers_of(new_id)
    assert trigger["conditions"][0]["value"] == [target_survey]


def test_clone_prints_a_link_that_opens_the_workflow_editor(ghl):
    # /automation/workflows/<id> (plural) opens a blank page; GHL's editor is
    # /automation/workflow/<id> (checked live 2026-10-18).
    _add(ghl)
    result = _run("--json", "workflows", "clone", SRC_WF)
    new_id = json.loads(result.output)["workflow_id"]
    text = _run("workflows", "clone", SRC_WF).output
    assert f"/v2/location/{SRC_LOC}/automation/workflow/" in text
    assert "/automation/workflows/" not in text
    assert new_id


# ── build with non-tag triggers ───────────────────────────────────────────

REAL_FORM = "RealFormAAAAAAAAAAA1"


def _spec(tmp_path, triggers):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"name": "WF", "triggers": triggers,
                                "steps": [{"type": "wait", "name": "Wait", "value": 1}]}))
    return str(path)


@pytest.fixture
def lookups(monkeypatch):
    found = {"form": {REAL_FORM}, "survey": set(), "calendar": set(), "pipeline": {}}
    monkeypatch.setattr(cli_mod, "_trigger_lookups", lambda ctx, kinds: found)
    return found


def test_build_creates_a_form_trigger(ghl, lookups, tmp_path):
    result = _run("--json", "workflows", "build", "--from-spec",
                  _spec(tmp_path, [{"type": "form", "form_id": REAL_FORM}]))
    assert result.exit_code == 0, result.output
    wf = json.loads(result.stdout)["workflow_ids"]["workflow"]
    (t,) = ghl.triggers_of(wf)
    assert (t["type"], t["conditions"][0]["value"]) == ("form_submission", [REAL_FORM])


def test_build_refuses_a_trigger_pointing_at_a_form_that_does_not_exist(ghl, lookups, tmp_path):
    result = _run("workflows", "build", "--from-spec",
                  _spec(tmp_path, [{"type": "form", "form_id": "NopeAAAAAAAAAAAAAAAA"}]))
    assert result.exit_code == 2
    assert "NopeAAAAAAAAAAAAAAAA" in result.output
    assert ghl.writes() == []


def test_build_warns_when_ids_cannot_be_checked(ghl, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "_trigger_lookups", lambda ctx, kinds: {"form": None})
    result = _run("workflows", "build", "--from-spec",
                  _spec(tmp_path, [{"type": "form", "form_id": "UncheckedAAAAAAAAAA1"}]))
    assert result.exit_code == 0, result.output
    assert "could not check" in result.output.lower()
