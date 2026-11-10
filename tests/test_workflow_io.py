"""Tests for reading, editing and cloning existing workflows (internal API).

Fixture shapes are copied from live GoHighLevel reads (2026-10-11): a reply
wait with two transitions, a step that points at ANOTHER workflow, a survey
trigger and a tag trigger. The fake server lives in tests/fake_ghl.py.
"""
import copy
import json
from pathlib import Path

import pytest

from cli_anything.gohighlevel.utils import workflow_io

from cli_anything.gohighlevel.utils.workflow_io import (
    CloneError,
    DriftError,
    ForeignRefsError,
    NeedsConfirm,
    VerifyError,
    WorkflowIOError,
    clone_workflow,
    diff_steps,
    edit_workflow,
    ensure_tags,
    find_location_refs,
    asset_lookups,
    list_assets,
    read_workflow,
    referenced_tags,
    remap_ids,
)
from fake_ghl import FakeClient, FakeGHL

SRC_LOC = "LocSourceAAAAAAAAAA1"
DST_LOC = "LocTargetBBBBBBBBBB2"
SRC_WF = "aaaaaaaa-0000-4000-8000-00000000000a"
OTHER_WF = "bbbbbbbb-0000-4000-8000-00000000000b"
FOLDER = "ffffffff-0000-4000-8000-00000000000f"
SURVEY = "4K7bjS5gfypQ8oyMeuSx"
COND_ID = "7949830c-d5ab-4d7a-838f-62380edad3c8"

WAIT = "00000000-0000-4000-8000-000000000001"
SMS = "00000000-0000-4000-8000-000000000002"
REPLY = "00000000-0000-4000-8000-000000000003"
T_REPLIED = "00000000-0000-4000-8000-000000000004"
T_TIMEOUT = "00000000-0000-4000-8000-000000000005"
ADD = "00000000-0000-4000-8000-000000000006"
STEP_IDS = {WAIT, SMS, REPLY, T_REPLIED, T_TIMEOUT, ADD}

STEPS = [
    {"id": WAIT, "order": 0, "type": "wait", "name": "Wait 1 day", "next": SMS,
     "attributes": {"type": "time",
                    "startAfter": {"type": "days", "when": "after", "value": 1, "action_in": 0}}},
    {"id": SMS, "order": 1, "type": "sms", "name": "Nudge", "parentKey": WAIT, "next": REPLY,
     "attributes": {"body": f"Hi {{{{contact.first_name}}}}, ref {SMS}"}},
    {"id": REPLY, "order": 2, "type": "wait", "cat": "multi-path", "name": "Wait for reply",
     "parentKey": SMS, "next": [T_REPLIED, T_TIMEOUT],
     "attributes": {"type": "reply", "reply": [SMS], "replyLabel": ["Nudge"],
                    "transitions": [{"id": T_REPLIED}, {"id": T_TIMEOUT}]}},
    {"id": T_REPLIED, "order": 3, "type": "transition", "cat": "transition", "name": "wait",
     "parentKey": REPLY, "parent": REPLY, "attributes": {"type": "wait_reply"}},
    {"id": T_TIMEOUT, "order": 3, "type": "transition", "cat": "transition", "name": "timeout",
     "parentKey": REPLY, "parent": REPLY, "next": ADD, "attributes": {"type": "wait_timeout"}},
    {"id": ADD, "order": 4, "type": "add_to_workflow", "name": "Move to nurture",
     "parentKey": T_TIMEOUT, "attributes": {"workflow_id": OTHER_WF}},
]

SURVEY_TRIGGER = {
    "id": "yJBZArmDoY3QqgGXyA8d", "date_added": "2026-07-16T23:13:18.332Z", "deleted": False,
    "actions": [{"workflow_id": SRC_WF, "type": "add_to_workflow"}],
    "type": "survey_submission", "masterType": "highlevel", "workflow_id": SRC_WF,
    "location_id": SRC_LOC, "name": "Application submitted", "belongs_to": "workflow",
    "schedule_config": {}, "active": True, "date_updated": "2026-07-18T16:46:52.485Z",
    "conditions": [{"operator": "is-any-of", "field": "survey.id", "value": [SURVEY],
                    "title": "Survey is", "type": "string", "id": COND_ID}],
    "last_conditions_change_at": "",
}

TAG_TRIGGER = {
    "id": "TrigTagAAAAAAAAAAAA1", "date_added": "2026-08-26T00:06:20.213Z", "deleted": False,
    "actions": [{"workflow_id": SRC_WF, "type": "add_to_workflow"}],
    "type": "contact_tag", "masterType": "highlevel", "workflow_id": SRC_WF,
    "location_id": SRC_LOC, "name": "Proposal sent", "belongs_to": "workflow",
    "active": True, "date_updated": "2026-08-26T00:06:20.213Z",
    "conditions": [{"operator": "index-of-true", "field": "tagsAdded", "value": "proposal-sent",
                    "title": "Tag Added", "type": "select", "id": "tag-added"}],
    "last_conditions_change_at": "",
}


@pytest.fixture(autouse=True)
def _no_tag_wait(monkeypatch):
    """ensure_tags waits between tag-list re-reads; tests never need to."""
    monkeypatch.setattr(workflow_io, "_sleep", lambda s: None)


def _by_name(steps):
    return {s["name"]: s for s in steps}


def _export(templates, triggers):
    return {"workflow_id": SRC_WF, "location_id": SRC_LOC, "name": "Proposal follow-up",
            "templates": copy.deepcopy(templates), "triggers": copy.deepcopy(triggers)}


def _server(**kw):
    server = FakeGHL()
    triggers = kw.pop("triggers", (SURVEY_TRIGGER,))
    server.add_workflow(SRC_LOC, SRC_WF, "Proposal follow-up", STEPS, triggers=triggers, **kw)
    return server


def _edited_steps():
    steps = copy.deepcopy(STEPS)
    steps[1]["attributes"]["body"] = "Hi {{contact.first_name}}, any questions?"
    return steps


# ── remap_ids ─────────────────────────────────────────────────────────────

def test_remap_gives_every_step_a_fresh_unique_id():
    steps, _, id_map = remap_ids(STEPS, [])
    new_ids = [s["id"] for s in steps]
    assert len(set(new_ids)) == 6
    assert not set(new_ids) & STEP_IDS
    assert set(id_map) == STEP_IDS


def test_remap_keeps_the_step_graph_wired():
    by = _by_name(remap_ids(STEPS, [])[0])
    assert by["Wait 1 day"]["next"] == by["Nudge"]["id"]
    assert by["Nudge"]["parentKey"] == by["Wait 1 day"]["id"]
    assert by["Wait for reply"]["next"] == [by["wait"]["id"], by["timeout"]["id"]]
    assert by["timeout"]["parent"] == by["Wait for reply"]["id"]
    assert by["Move to nurture"]["parentKey"] == by["timeout"]["id"]


def test_remap_rewrites_step_ids_nested_in_attributes():
    by = _by_name(remap_ids(STEPS, [])[0])
    reply = by["Wait for reply"]["attributes"]
    assert reply["reply"] == [by["Nudge"]["id"]]
    assert reply["transitions"] == [{"id": by["wait"]["id"]}, {"id": by["timeout"]["id"]}]


def test_remap_rewrites_step_ids_inside_triggers():
    trigger = {**SURVEY_TRIGGER, "targetActionId": WAIT}
    steps, (new_trigger,), _ = remap_ids(STEPS, [trigger])
    assert new_trigger["targetActionId"] == _by_name(steps)["Wait 1 day"]["id"]


def test_remap_leaves_text_other_workflows_and_its_input_alone():
    original = copy.deepcopy(STEPS)
    by = _by_name(remap_ids(STEPS, [])[0])
    assert STEPS == original
    assert by["Nudge"]["attributes"]["body"] == f"Hi {{{{contact.first_name}}}}, ref {SMS}"
    assert by["Move to nurture"]["attributes"]["workflow_id"] == OTHER_WF


# ── find_location_refs ────────────────────────────────────────────────────

def test_location_refs_flag_ids_that_only_exist_in_the_source_account():
    refs = find_location_refs(_export(STEPS, [SURVEY_TRIGGER]))
    assert {r["value"] for r in refs} == {SURVEY, OTHER_WF}


def test_location_refs_ignore_plain_tag_names():
    tag = copy.deepcopy(TAG_TRIGGER)
    tag["conditions"][0]["value"] = "proposalsentfollowup"   # 20 chars, still a tag name
    assert find_location_refs(_export(STEPS[:2], [tag])) == []


# ── read_workflow ─────────────────────────────────────────────────────────

def test_read_workflow_returns_steps_triggers_and_version():
    server = _server(status="published", version=12, parent_id=FOLDER)
    exp = read_workflow(server.client(SRC_LOC), SRC_WF)
    assert exp["templates"] == STEPS
    assert exp["triggers"] == [SURVEY_TRIGGER]
    assert (exp["workflow_id"], exp["location_id"], exp["name"], exp["status"],
            exp["version"], exp["parent_id"]) == (
        SRC_WF, SRC_LOC, "Proposal follow-up", "published", 12, FOLDER)


def test_read_workflow_raises_when_the_workflow_does_not_exist():
    with pytest.raises(WorkflowIOError, match="404"):
        read_workflow(_server().client(SRC_LOC), "no-such-workflow")


def test_read_workflow_raises_when_ghl_refuses_the_session():
    class Refused:
        location_id = SRC_LOC

        def request(self, method, path, body=None):
            return None

    with pytest.raises(WorkflowIOError, match="refused"):
        read_workflow(Refused(), SRC_WF)


def test_read_workflow_raises_rather_than_returning_no_triggers():
    class TriggersDown(FakeClient):
        def request(self, method, path, body=None):
            if "/trigger?" in path:
                return {"_error": True, "code": 500, "message": "Internal Server Error"}
            return super().request(method, path, body)

    with pytest.raises(WorkflowIOError, match="trigger"):
        read_workflow(TriggersDown(_server(), SRC_LOC), SRC_WF)


# ── diff_steps ────────────────────────────────────────────────────────────

def test_diff_steps_names_added_removed_and_changed():
    old = copy.deepcopy(STEPS[:2])
    new = [copy.deepcopy(STEPS[0]),
           {"id": "00000000-0000-4000-8000-0000000000ff", "type": "add_contact_tag",
            "name": "Tag", "attributes": {"tags": ["x"]}}]
    new[0]["attributes"]["startAfter"]["value"] = 2
    assert diff_steps(old, new) == {"added": ["Tag"], "removed": ["Nudge"],
                                    "changed": ["Wait 1 day"]}


# ── edit_workflow ─────────────────────────────────────────────────────────

def test_edit_saves_the_new_steps(tmp_path):
    server = _server(version=3)
    result = edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    assert server.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] == _edited_steps()
    assert (result["version_before"], result["version_after"]) == (3, 4)
    assert result["diff"] == {"added": [], "removed": [], "changed": ["Nudge"]}


def test_edit_keeps_the_workflow_settings(tmp_path):
    settings = {"allowMultiple": True, "timezone": "America/Vancouver",
                "window": {"days": [1, 2, 3], "start": "09:00", "end": "17:00"},
                "meta": {"advanceCanvasMeta": {"enabled": True}}}
    server = _server(**settings)
    edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    doc = server.docs[(SRC_LOC, SRC_WF)]
    assert {k: doc[k] for k in settings} == settings


def test_edit_renames_when_asked(tmp_path):
    server = _server()
    edit_workflow(server.client(SRC_LOC), SRC_WF, STEPS, name="Proposal follow-up v2",
                  snapshot_dir=tmp_path)
    assert server.docs[(SRC_LOC, SRC_WF)]["name"] == "Proposal follow-up v2"


def test_edit_snapshots_the_live_workflow_before_writing(tmp_path):
    server = _server(version=3)
    result = edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    snap = json.loads(Path(result["snapshot"]).read_text())
    assert (snap["templates"], snap["triggers"], snap["version"]) == (STEPS, [SURVEY_TRIGGER], 3)


def test_edit_refuses_when_the_workflow_changed_since_export(tmp_path):
    server = _server(version=3)
    with pytest.raises(DriftError):
        edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), expected_version=2,
                      snapshot_dir=tmp_path)
    assert server.writes() == []


def test_edit_refuses_a_published_workflow_without_confirm(tmp_path):
    server = _server(status="published")
    with pytest.raises(NeedsConfirm) as exc:
        edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    assert exc.value.diff == {"added": [], "removed": [], "changed": ["Nudge"]}
    assert server.writes() == []


def test_edit_saves_a_published_workflow_with_confirm(tmp_path):
    server = _server(status="published")
    edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), confirm=True,
                  snapshot_dir=tmp_path)
    assert server.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] == _edited_steps()


NEW_ROOT = "00000000-0000-4000-8000-0000000000aa"


def test_edit_repoints_a_trigger_whose_first_step_was_replaced(tmp_path):
    # Seen live 2026-10-11: steps replaced wholesale left the trigger pointing
    # at a step id that no longer existed.
    server = _server(triggers=({**SURVEY_TRIGGER, "targetActionId": WAIT},))
    new_steps = [{"id": NEW_ROOT, "order": 0, "type": "add_contact_tag", "name": "Tag",
                  "attributes": {"tags": ["x"]}}]
    result = edit_workflow(server.client(SRC_LOC), SRC_WF, new_steps, snapshot_dir=tmp_path)
    (trigger,) = server.triggers_of(SRC_WF)
    assert trigger["targetActionId"] == NEW_ROOT
    assert trigger["conditions"] == SURVEY_TRIGGER["conditions"]
    assert result["relinked_triggers"] == 1


def test_edit_leaves_trigger_links_alone_when_the_first_step_is_kept(tmp_path):
    server = _server(triggers=({**SURVEY_TRIGGER, "targetActionId": WAIT},))
    result = edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    assert [c for c in server.writes() if "/trigger/" in c[2]] == []
    assert result["relinked_triggers"] == 0


def test_edit_fails_loudly_when_ghl_rejects_the_save(tmp_path):
    server = _server()
    server.fail_step_saves = True
    with pytest.raises(WorkflowIOError, match="500"):
        edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)


def test_edit_fails_loudly_when_ghl_silently_drops_the_save(tmp_path):
    server = _server()
    server.drop_step_saves = True
    with pytest.raises(VerifyError) as exc:
        edit_workflow(server.client(SRC_LOC), SRC_WF, _edited_steps(), snapshot_dir=tmp_path)
    assert Path(exc.value.snapshot).exists()


# ── clone_workflow ────────────────────────────────────────────────────────

def test_clone_in_the_same_account_makes_a_draft_copy_beside_the_source():
    server = _server(status="published", parent_id=FOLDER)
    client = server.client(SRC_LOC)
    result = clone_workflow(client, client, SRC_WF)
    new = server.docs[(SRC_LOC, result["workflow_id"])]
    new_steps = new["workflowData"]["templates"]
    assert (new["name"], new["status"], new["parentId"]) == (
        "Proposal follow-up (copy)", "draft", FOLDER)
    assert [s["name"] for s in new_steps] == [s["name"] for s in STEPS]
    assert not {s["id"] for s in new_steps} & STEP_IDS
    assert result["foreign_refs"] == []
    assert server.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] == STEPS
    assert [c for c in server.writes() if SRC_WF in c[2]] == []


def test_clone_copies_triggers_onto_the_new_workflow():
    server = _server()
    client = server.client(SRC_LOC)
    result = clone_workflow(client, client, SRC_WF)
    new_id = result["workflow_id"]
    (trigger,) = server.triggers_of(new_id)
    new_root = _by_name(server.docs[(SRC_LOC, new_id)]["workflowData"]["templates"])["Wait 1 day"]
    assert trigger["type"] == "survey_submission"
    assert trigger["conditions"] == SURVEY_TRIGGER["conditions"]
    assert trigger["actions"] == [{"workflow_id": new_id, "type": "add_to_workflow"}]
    assert trigger["location_id"] == SRC_LOC
    assert trigger["targetActionId"] == new_root["id"]
    assert trigger["id"] != SURVEY_TRIGGER["id"]
    assert server.triggers_of(SRC_WF) == [SURVEY_TRIGGER]
    # The step save also syncs the triggers onto the canvas, as the builder does.
    step_saves = [c for c in server.writes() if c[1] == "PUT" and c[2].endswith(new_id)]
    assert [t["id"] for t in step_saves[-1][3]["newTriggers"]] == [trigger["id"]]
    assert result["triggers"] == 1


def test_clone_to_another_account_creates_trigger_tags_and_flags_source_only_ids():
    server = _cross_server()
    server.docs[(SRC_LOC, SRC_WF)]["parentId"] = FOLDER
    server.triggers[TAG_TRIGGER["id"]] = copy.deepcopy(TAG_TRIGGER)
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF,
                            name="Proposal follow-up")
    new_id = result["workflow_id"]
    assert server.docs[(DST_LOC, new_id)]["name"] == "Proposal follow-up"
    assert server.docs[(DST_LOC, new_id)]["parentId"] is None
    assert server.tags[DST_LOC] == {"proposal-sent"}
    assert {t["location_id"] for t in server.triggers_of(new_id)} == {DST_LOC}
    assert {r["value"] for r in result["foreign_refs"]} == {SURVEY, OTHER_WF}


def test_clone_into_a_named_folder_creates_it_once():
    server = _cross_server()
    src, dst = server.client(SRC_LOC), server.client(DST_LOC)
    first = clone_workflow(src, dst, SRC_WF, folder="Client builds")
    second = clone_workflow(src, dst, SRC_WF, folder="Client builds")
    folders = [d for (loc, _), d in server.docs.items()
               if loc == DST_LOC and d["type"] == "directory" and d["name"] == "Client builds"]
    assert len(folders) == 1
    parents = {server.docs[(DST_LOC, r["workflow_id"])]["parentId"] for r in (first, second)}
    assert parents == {folders[0]["id"]}


def test_clone_names_the_half_built_copy_when_saving_steps_fails():
    server = _server()
    server.fail_step_saves = True
    client = server.client(SRC_LOC)
    with pytest.raises(CloneError) as exc:
        clone_workflow(client, client, SRC_WF)
    assert (SRC_LOC, exc.value.workflow_id) in server.docs


# ── tags the workflow uses must exist in the account ──────────────────────
# Seen live 2026-10-18 in GHL's editor: every add-tag step whose tag did not
# exist in the sub-account showed "Referenced Tag does not exist or does not
# belong to this location". Creating the tags cleared every error.

TAG_STEPS = [
    {"id": WAIT, "order": 0, "type": "add_contact_tag", "name": "Mark lead", "next": SMS,
     "attributes": {"tags": ["lead", "new"]}},
    {"id": SMS, "order": 1, "type": "remove_contact_tag", "name": "Unmark cold",
     "parentKey": WAIT, "next": REPLY, "attributes": {"tags": ["cold"]}},
    {"id": REPLY, "order": 2, "type": "if_else", "name": "VIP?", "parentKey": SMS,
     "attributes": {"branches": [{"segments": [{"conditions": [
         {"conditionType": "contact_detail", "conditionSubType": "tags",
          "conditionOperator": "index-of-true", "conditionValue": ["vip"], "isWait": False}]}]}]}},
]


def test_referenced_tags_collects_step_condition_and_trigger_tags():
    tags = referenced_tags(TAG_STEPS + [copy.deepcopy(TAG_STEPS[0])], [TAG_TRIGGER])
    assert sorted(tags) == ["cold", "lead", "new", "proposal-sent", "vip"]


def test_clone_creates_every_tag_the_workflow_uses():
    server = _server(triggers=(TAG_TRIGGER,))
    server.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] = copy.deepcopy(TAG_STEPS)
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF)
    assert server.tags[DST_LOC] == {"lead", "new", "cold", "vip", "proposal-sent"}
    assert result["missing_tags"] == []


def test_clone_reports_tags_ghl_would_not_create():
    server = _server(triggers=(TAG_TRIGGER,))
    server.docs[(SRC_LOC, SRC_WF)]["workflowData"]["templates"] = copy.deepcopy(TAG_STEPS)
    server.refuse_tags = True
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF)
    assert sorted(result["missing_tags"]) == ["cold", "lead", "new", "proposal-sent", "vip"]


def test_edit_creates_the_tags_its_new_steps_use(tmp_path):
    server = _server()
    result = edit_workflow(server.client(SRC_LOC), SRC_WF, copy.deepcopy(TAG_STEPS),
                           snapshot_dir=tmp_path)
    assert server.tags[SRC_LOC] == {"lead", "new", "cold", "vip"}
    assert result["missing_tags"] == []


def test_a_tag_that_shows_up_late_in_the_list_is_not_reported_missing():
    # Seen live 2026-10-18: the last tag created was missing from a list read
    # made right after, then present a moment later.
    server = FakeGHL()
    server.tag_list_lag = 1
    assert ensure_tags(server.client(SRC_LOC), ["late-tag"]) == []


# ── cross-account clone: GHL refuses ids that do not exist in the target ──
# Seen live 2026-10-18: saving a cloned workflow whose trigger named a form and
# a pipeline from the source account failed with HTTP 400 "Referenced asset(s)
# missing from this location: Form '...', Pipeline '...'". So source-only ids
# are mapped to the target's same-named assets, or the clone is refused before
# anything is written.

DST_SURVEY = "SurveyDstZZZZZZZZZZ9"
DST_NURTURE = "dddddddd-0000-4000-8000-00000000000d"
SRC_PIPE, DST_PIPE = "PipeSrcAAAAAAAAAAAA1", "PipeDstBBBBBBBBBBBB2"
SRC_STAGE = "11111111-1111-4111-8111-111111111111"
DST_STAGE = "22222222-2222-4222-8222-222222222222"


def _cross_server(dst_survey=True):
    server = _server(triggers=(SURVEY_TRIGGER,))
    server.add_workflow(SRC_LOC, OTHER_WF, "Nurture", [])
    server.add_workflow(DST_LOC, DST_NURTURE, "Nurture", [])
    server.assets[SRC_LOC] = {"surveys": [{"id": SURVEY, "name": "Application"}]}
    server.assets[DST_LOC] = {"surveys": [{"id": DST_SURVEY, "name": "Application"}] if dst_survey else []}
    return server


def test_list_assets_reads_names_by_id():
    server = _cross_server()
    server.assets[SRC_LOC]["pipelines"] = [{"id": SRC_PIPE, "name": "Sales",
                                            "stages": [{"id": SRC_STAGE, "name": "Proposal"}]}]
    assets = list_assets(server.client(SRC_LOC))
    assert assets["survey"] == {SURVEY: "Application"}
    assert assets["pipeline"] == {SRC_PIPE: {"name": "Sales", "stages": {SRC_STAGE: "Proposal"}}}
    assert assets["workflow"][OTHER_WF] == "Nurture"


def test_cross_clone_maps_source_ids_to_same_named_target_assets():
    server = _cross_server()
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF)
    new_id = result["workflow_id"]
    (trigger,) = server.triggers_of(new_id)
    steps = _by_name(server.docs[(DST_LOC, new_id)]["workflowData"]["templates"])
    assert trigger["conditions"][0]["value"] == [DST_SURVEY]
    assert steps["Move to nurture"]["attributes"]["workflow_id"] == DST_NURTURE
    assert {(m["from"], m["to"]) for m in result["mapped"]} == {(SURVEY, DST_SURVEY),
                                                               (OTHER_WF, DST_NURTURE)}


def test_cross_clone_maps_a_pipeline_stage_by_pipeline_and_stage_name():
    stage_trigger = {**SURVEY_TRIGGER, "type": "pipeline_stage_updated", "conditions": [
        {"operator": "==", "field": "opportunity.pipelineId", "value": SRC_PIPE},
        {"operator": "==", "field": "opportunity.pipelineStageId", "value": SRC_STAGE}]}
    server = _cross_server()
    server.triggers.clear()
    server.triggers[stage_trigger["id"]] = copy.deepcopy(stage_trigger)
    server.assets[SRC_LOC]["pipelines"] = [{"id": SRC_PIPE, "name": "Sales",
                                            "stages": [{"id": SRC_STAGE, "name": "Proposal"}]}]
    server.assets[DST_LOC]["pipelines"] = [{"id": DST_PIPE, "name": "Sales",
                                            "stages": [{"id": DST_STAGE, "name": "Proposal"}]}]
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF)
    (trigger,) = server.triggers_of(result["workflow_id"])
    assert [c["value"] for c in trigger["conditions"]] == [DST_PIPE, DST_STAGE]


def test_cross_clone_refuses_unresolved_ids_before_writing_anything():
    server = _cross_server(dst_survey=False)
    with pytest.raises(ForeignRefsError) as exc:
        clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF)
    assert {r["value"] for r in exc.value.unresolved} == {SURVEY}
    assert [c for c in server.writes() if c[0] == DST_LOC] == []


def test_cross_clone_takes_a_manual_map_for_what_it_cannot_match():
    server = _cross_server(dst_survey=False)
    result = clone_workflow(server.client(SRC_LOC), server.client(DST_LOC), SRC_WF,
                            id_map={SURVEY: "SurveyManualAAAAAAA1"})
    (trigger,) = server.triggers_of(result["workflow_id"])
    assert trigger["conditions"][0]["value"] == ["SurveyManualAAAAAAA1"]


def test_asset_lookups_feed_the_trigger_id_check():
    from cli_anything.gohighlevel.utils.workflow_triggers import check_trigger_refs
    server = _cross_server()
    server.assets[SRC_LOC]["forms"] = [{"id": "FormRealAAAAAAAAAAA1", "name": "Contact"}]
    lookups = asset_lookups(list_assets(server.client(SRC_LOC)))
    ok = [{"type": "form", "form_id": "FormRealAAAAAAAAAAA1"}, {"type": "survey", "survey_id": SURVEY}]
    bad = [{"type": "form", "form_id": "FormNopeAAAAAAAAAAA1"}]
    assert check_trigger_refs(ok, lookups) == []
    assert "FormNopeAAAAAAAAAAA1" in check_trigger_refs(bad, lookups)[0]


def test_list_assets_reads_forms_keyed_by_underscore_id_and_every_survey_page():
    # Live 2026-10-18: forms rows carry "_id"; surveys page at most 50 at a time.
    server = _cross_server()
    server.assets[SRC_LOC]["forms"] = [{"id": "FormRealAAAAAAAAAAA1", "name": "Contact"}]
    server.assets[SRC_LOC]["surveys"] = [{"id": f"Survey{i:014d}", "name": f"S{i}"} for i in range(60)]
    assets = list_assets(server.client(SRC_LOC))
    assert assets["form"] == {"FormRealAAAAAAAAAAA1": "Contact"}
    assert len(assets["survey"]) == 60
