"""CampaignBuilder must not report success when GHL rejects the steps.

Seen live 2026-10-11: `workflows build` printed 0 errors and exited 0 while GHL
answered the step save with HTTP 400 ("Html is required" on an add_notes step).
The workflow existed with its trigger and NO steps.
"""
from cli_anything.gohighlevel.utils.workflow_builder import CampaignBuilder
from fake_ghl import FakeGHL

LOC = "LocSourceAAAAAAAAAA1"
STEP = {"id": "00000000-0000-4000-8000-000000000001", "order": 0, "type": "add_contact_tag",
        "name": "Tag", "attributes": {"tags": ["x"]}}


def _build(server):
    builder = CampaignBuilder(server.client(LOC))
    return builder.build({"workflow": {"name": "WF", "templates": [STEP]}}, folder_name="F")


def test_build_reports_a_rejected_step_save_as_an_error():
    server = FakeGHL()
    server.fail_step_saves = True
    stats = _build(server)
    assert stats["steps_saved"] == 0
    assert any("500" in e for e in stats["errors"])


def test_build_reports_no_error_when_the_steps_save():
    stats = _build(FakeGHL())
    assert (stats["steps_saved"], stats["errors"]) == (1, [])


def test_build_creates_the_tags_its_steps_add():
    # Seen live 2026-10-18: GHL's editor flags add-tag steps whose tag does not
    # exist in the sub-account. Only the trigger's tag used to be created.
    server = FakeGHL()
    _build(server)
    assert "x" in server.tags[LOC]


def test_build_links_to_the_workflow_editor():
    # GHL's editor route is /automation/workflow/<id>; the plural route the
    # summary used to print opens a blank page (checked live 2026-10-18).
    server = FakeGHL()
    builder = CampaignBuilder(server.client(LOC))
    stats = builder.build({"workflow": {"name": "WF", "templates": [STEP]}}, folder_name="F")
    wf_id = stats["workflow_ids"]["workflow"]
    assert f"/v2/location/{LOC}/automation/workflow/{wf_id}" in builder.format_summary()


def test_build_leaves_the_standard_builder_on():
    # With meta.advanceCanvasMeta enabled GHL opens the Advanced builder, which
    # draws every step in one row, so the No branch looks chained after Yes
    # (seen live 2026-10-18). GHL's Standard builder lays the tree out itself.
    server = FakeGHL()
    stats = _build(server)
    doc = server.docs[(LOC, stats["workflow_ids"]["workflow"])]
    assert not ((doc.get("meta") or {}).get("advanceCanvasMeta") or {}).get("enabled")


from cli_anything.gohighlevel.utils.workflow_triggers import compile_trigger  # noqa: E402


def test_build_creates_every_trigger_wired_to_the_first_step():
    server = FakeGHL()
    triggers = [compile_trigger({"type": "form", "form_id": "FormAAAAAAAAAAAAAAA1"}),
                compile_trigger({"type": "tag", "value": "hot-lead"})]
    stats = CampaignBuilder(server.client(LOC)).build(
        {"workflow": {"name": "WF", "templates": [STEP], "triggers": triggers}}, folder_name="F")
    wf = stats["workflow_ids"]["workflow"]
    made = server.triggers_of(wf)
    assert sorted(t["type"] for t in made) == ["contact_tag", "form_submission"]
    assert {t["targetActionId"] for t in made} == {STEP["id"]}
    assert all(t["actions"] == [{"workflow_id": wf, "type": "add_to_workflow"}] for t in made)
    assert next(t for t in made if t["type"] == "form_submission")["conditions"] == \
        triggers[0]["conditions"]
    assert stats["triggers_created"] == 2
    assert "hot-lead" in server.tags[LOC]


def test_build_still_accepts_the_old_tag_key():
    server = FakeGHL()
    stats = CampaignBuilder(server.client(LOC)).build(
        {"workflow": {"name": "WF", "templates": [STEP], "tag": "legacy-tag"}}, folder_name="F")
    (t,) = server.triggers_of(stats["workflow_ids"]["workflow"])
    assert (t["type"], t["conditions"][0]["value"]) == ("contact_tag", "legacy-tag")
