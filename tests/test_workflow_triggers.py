"""Tests for compiling spec triggers into the triggers GHL stores.

Every expected condition below is copied from real triggers read off the
agency's sub-accounts on 2026-10-18 (278 triggers, 16 types), with ids replaced.
"""
import pytest

from cli_anything.gohighlevel.utils.workflow_spec import SpecError
from cli_anything.gohighlevel.utils.workflow_triggers import (
    check_trigger_refs,
    compile_trigger,
    compile_triggers,
)

FORM = "FormAAAAAAAAAAAAAAA1"
SURVEY = "SurveyBBBBBBBBBBBBB2"
CAL = "CalendarCCCCCCCCCCC3"
PIPE = "PipelineDDDDDDDDDDD4"
STAGE = "f282a03d-24ea-44f3-be94-2da804a21393"


def test_tag_trigger_matches_ghl():
    t = compile_trigger({"type": "tag", "value": "Booking-Confirmed"})
    assert t["type"] == "contact_tag"
    assert t["conditions"] == [{"operator": "index-of-true", "field": "tagsAdded",
                                "value": "booking-confirmed", "title": "Tag Added",
                                "type": "select", "id": "tag-added"}]


def test_form_trigger_matches_ghl():
    t = compile_trigger({"type": "form", "form_id": FORM})
    assert t["type"] == "form_submission"
    assert t["conditions"] == [{"field": "form.id", "title": "Form is", "type": "string",
                                "value": [FORM], "operator": "is-any-of"}]


def test_form_trigger_without_a_form_fires_on_any_form():
    assert compile_trigger({"type": "form"})["conditions"] == []


def test_survey_trigger_matches_ghl():
    t = compile_trigger({"type": "survey", "survey_id": SURVEY})
    assert t["type"] == "survey_submission"
    (c,) = t["conditions"]
    assert {k: c[k] for k in ("operator", "field", "value", "title", "type")} == {
        "operator": "is-any-of", "field": "survey.id", "value": [SURVEY],
        "title": "Survey is", "type": "string"}


def test_appointment_booked_matches_ghl():
    t = compile_trigger({"type": "appointment_booked", "calendar_id": CAL})
    assert t["type"] == "customer_appointment"
    assert t["conditions"] == [
        {"operator": "is-any-of", "field": "contactMode", "value": ["contact"],
         "title": "Who should be enrolled into the workflow?", "type": "select", "id": "contactMode"},
        {"operator": "==", "field": "calendar.id", "value": CAL, "title": "In calendar",
         "type": "select"}]


def test_appointment_status_matches_ghl():
    t = compile_trigger({"type": "appointment_status", "status": "showed", "calendar_id": CAL})
    assert t["type"] == "appointment"
    assert t["conditions"] == [
        {"operator": "==", "field": "appointment.eventType", "value": "normal",
         "title": "Event type", "type": "select"},
        {"operator": "is-any-of", "field": "contactMode", "value": ["contact"],
         "title": "Who should be enrolled into the workflow?", "type": "select", "id": "contactMode"},
        {"operator": "==", "field": "calendar.id", "value": CAL, "title": "In calendar",
         "type": "select"},
        {"operator": "==", "field": "appointment.status", "value": "showed",
         "title": "Appointment status is", "type": "select"}]


def test_appointment_status_refuses_an_unknown_status():
    with pytest.raises(SpecError, match="status"):
        compile_trigger({"type": "appointment_status", "status": "maybe"})


def test_pipeline_stage_matches_ghl():
    t = compile_trigger({"type": "pipeline_stage", "pipeline_id": PIPE, "stage_id": STAGE})
    assert t["type"] == "pipeline_stage_updated"
    assert t["conditions"] == [
        {"operator": "==", "field": "opportunity.pipelineId", "value": PIPE,
         "title": "In pipeline", "type": "select"},
        {"operator": "==", "field": "opportunity.pipelineStageId", "value": STAGE,
         "title": "Pipeline stage", "type": "select"}]


def test_pipeline_stage_needs_both_ids():
    with pytest.raises(SpecError, match="stage_id"):
        compile_trigger({"type": "pipeline_stage", "pipeline_id": PIPE})


def test_opportunity_status_matches_ghl():
    t = compile_trigger({"type": "opportunity_status", "status": "won", "pipeline_id": PIPE})
    assert t["type"] == "opportunity_status_changed"
    assert t["conditions"] == [
        {"field": "opportunity.pipelineId", "title": "In pipeline", "type": "select",
         "value": PIPE, "operator": "=="},
        {"field": "opportunity.status", "id": "moved-to-status", "title": "Moved to status",
         "type": "select", "value": "won", "operator": "=="}]


def test_contact_created_has_no_filter():
    t = compile_trigger({"type": "contact_created"})
    assert (t["type"], t["conditions"]) == ("contact_created", [])


def test_customer_replied_can_filter_on_message_text():
    t = compile_trigger({"type": "customer_replied", "contains": "laundry offer"})
    assert t["type"] == "customer_reply"
    assert t["conditions"] == [{"operator": "string-contains-any-of", "field": "message.body",
                                "value": ["laundry offer"], "title": "Message body",
                                "type": "string", "id": "message-body"}]


def test_raw_trigger_passes_through():
    raw = {"type": "payment_received", "conditions": [{"field": "payment.payment_status",
                                                       "operator": "==", "value": "succeeded"}]}
    t = compile_trigger({"type": "raw", "ghl": raw})
    assert (t["type"], t["conditions"]) == ("payment_received", raw["conditions"])


def test_unknown_trigger_type_is_refused():
    with pytest.raises(SpecError, match="unknown trigger"):
        compile_trigger({"type": "telepathy"})


def test_spec_name_overrides_the_default_name():
    assert compile_trigger({"type": "contact_created", "name": "New contact"})["name"] == "New contact"


def test_a_spec_can_have_several_triggers():
    spec = {"triggers": [{"type": "tag", "value": "a"}, {"type": "form", "form_id": FORM}]}
    assert [t["type"] for t in compile_triggers(spec)] == ["contact_tag", "form_submission"]


def test_a_spec_with_one_trigger_still_works():
    assert [t["type"] for t in compile_triggers({"trigger": {"type": "tag", "value": "a"}})] == [
        "contact_tag"]


# ── id checks: a wrong id saves fine in GHL and then never fires ──────────

LOOKUPS = {"form": {FORM}, "survey": {SURVEY}, "calendar": {CAL}, "pipeline": {PIPE: {STAGE}}}


def test_refs_that_exist_pass():
    triggers = [{"type": "form", "form_id": FORM}, {"type": "survey", "survey_id": SURVEY},
                {"type": "appointment_booked", "calendar_id": CAL},
                {"type": "pipeline_stage", "pipeline_id": PIPE, "stage_id": STAGE}]
    assert check_trigger_refs(triggers, LOOKUPS) == []


def test_refs_that_do_not_exist_are_named():
    triggers = [{"type": "form", "form_id": "NopeAAAAAAAAAAAAAAAA"},
                {"type": "pipeline_stage", "pipeline_id": PIPE, "stage_id": "no-such-stage"}]
    problems = check_trigger_refs(triggers, LOOKUPS)
    assert len(problems) == 2
    assert "NopeAAAAAAAAAAAAAAAA" in problems[0] and "no-such-stage" in problems[1]


def test_refs_that_cannot_be_checked_are_not_called_missing():
    # A lookup GHL would not answer is None: unknown, not empty.
    triggers = [{"type": "form", "form_id": FORM}]
    assert check_trigger_refs(triggers, {"form": None}) == []


# ── the step compiler sees every trigger ──────────────────────────────────

from cli_anything.gohighlevel.utils.workflow_spec import compile_spec  # noqa: E402

RESCHEDULED_IF = [{"type": "if_else", "name": "Moved?",
                   "condition": {"kind": "appointment_rescheduled"},
                   "yes": [{"type": "tag", "name": "Moved", "tags": "moved"}]}]


def test_any_trigger_in_the_list_can_enable_an_appointment_condition():
    spec = {"triggers": [{"type": "tag", "value": "a"},
                         {"type": "appointment_booked", "calendar_id": CAL}],
            "steps": RESCHEDULED_IF}
    assert compile_spec(spec)


def test_an_unknown_trigger_fails_at_compile_time_not_at_deploy():
    with pytest.raises(SpecError, match="unknown trigger"):
        compile_spec({"trigger": {"type": "telepathy"},
                      "steps": [{"type": "wait", "name": "W", "value": 1}]})


@pytest.mark.parametrize("status", ["new", "confirmed", "cancelled", "showed", "noshow", "invalid"])
def test_appointment_status_accepts_every_status_ghl_offers(status):
    # GHL's builder: LEGACY_STATUS_LABEL_KEYS (read from its bundle 2026-10-18).
    t = compile_trigger({"type": "appointment_status", "status": status})
    assert t["conditions"][-1]["value"] == status


@pytest.mark.parametrize("status", ["open", "won", "lost", "abandoned"])
def test_opportunity_status_accepts_every_status_ghl_offers(status):
    # All four rendered as GHL labels in its editor (2026-10-18).
    assert compile_trigger({"type": "opportunity_status", "status": status})["conditions"][-1]["value"] == status
