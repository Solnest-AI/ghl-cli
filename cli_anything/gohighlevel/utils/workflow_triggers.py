"""Compile spec triggers into the triggers GoHighLevel stores.

Every shape here is copied from real triggers read off the agency's 19
sub-accounts on 2026-10-18 (278 triggers): what GHL's own builder writes, not a
guess. A spec gives one trigger under "trigger" or several under "triggers":

    {"type": "tag", "value": "booking-confirmed"}
    {"type": "form", "form_id": "..."}                (no form_id: any form)
    {"type": "survey", "survey_id": "..."}
    {"type": "appointment_booked", "calendar_id": "..."}      (calendar optional)
    {"type": "appointment_status", "status": "showed", "calendar_id": "..."}
    {"type": "pipeline_stage", "pipeline_id": "...", "stage_id": "..."}
    {"type": "opportunity_status", "status": "won", "pipeline_id": "..."}
    {"type": "contact_created"}
    {"type": "customer_replied", "contains": "text" | ["a", "b"]}   (optional)
    {"type": "raw", "ghl": {"type": "...", "conditions": [...]}}    (anything else)

Any of them takes "name" to override the trigger's display name. GHL's own type
names (contact_tag, form_submission, customer_appointment, ...) work as aliases.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from cli_anything.gohighlevel.utils.workflow_spec import SpecError

_ALIASES = {
    "tag": "tag", "contact_tag": "tag",
    "form": "form", "form_submission": "form",
    "survey": "survey", "survey_submission": "survey",
    "appointment_booked": "appointment_booked", "customer_appointment": "appointment_booked",
    "appointment_status": "appointment_status", "appointment": "appointment_status",
    "pipeline_stage": "pipeline_stage", "pipeline_stage_updated": "pipeline_stage",
    "opportunity_status": "opportunity_status", "opportunity_status_changed": "opportunity_status",
    "contact_created": "contact_created",
    "customer_replied": "customer_replied", "customer_reply": "customer_replied",
    "raw": "raw",
}

_GHL_TYPE = {
    "tag": "contact_tag", "form": "form_submission", "survey": "survey_submission",
    "appointment_booked": "customer_appointment", "appointment_status": "appointment",
    "pipeline_stage": "pipeline_stage_updated", "opportunity_status": "opportunity_status_changed",
    "contact_created": "contact_created", "customer_replied": "customer_reply",
}

# Values GHL stores for these selects (2026-10-18). Appointment: the builder's
# LEGACY_STATUS_LABEL_KEYS, read from its bundle; GHL labels cancelled / invalid /
# new / confirmed in lowercase, so a lowercase label in the editor is normal.
# Opportunity: all four rendered as labels in GHL's editor.
APPOINTMENT_STATUSES = ("new", "confirmed", "cancelled", "showed", "noshow", "invalid")
OPPORTUNITY_STATUSES = ("open", "won", "lost", "abandoned")

_CONTACT_MODE = {"operator": "is-any-of", "field": "contactMode", "value": ["contact"],
                 "title": "Who should be enrolled into the workflow?", "type": "select",
                 "id": "contactMode"}


def _kind(trigger: dict) -> str:
    kind = _ALIASES.get(str(trigger.get("type", "")))
    if kind is None:
        raise SpecError(
            f"unknown trigger type {trigger.get('type')!r}. Supported: "
            f"{', '.join(sorted(set(_ALIASES.values())))}.")
    return kind


def ghl_trigger_type(trigger: dict) -> str:
    """The GHL type a spec trigger becomes (for condition checks)."""
    kind = _kind(trigger)
    if kind == "raw":
        return str((trigger.get("ghl") or {}).get("type", ""))
    return _GHL_TYPE[kind]


def _need(trigger: dict, key: str) -> str:
    value = trigger.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"a {trigger.get('type')} trigger needs '{key}'")
    return value.strip()


def _status(trigger: dict, allowed: tuple) -> str:
    status = str(trigger.get("status", "")).strip().lower()
    if status not in allowed:
        raise SpecError(f"a {trigger.get('type')} trigger needs 'status', one of "
                        f"{', '.join(allowed)}; got {trigger.get('status')!r}")
    return status


def _conditions(kind: str, t: dict) -> tuple[list, str]:
    """(conditions, default display name) for one spec trigger."""
    if kind == "tag":
        tag = _need(t, "value").lower()
        return ([{"operator": "index-of-true", "field": "tagsAdded", "value": tag,
                  "title": "Tag Added", "type": "select", "id": "tag-added"}],
                tag.replace("-", " ").title())
    if kind == "form":
        form = t.get("form_id") or t.get("value")
        conds = [{"field": "form.id", "title": "Form is", "type": "string",
                  "value": [form], "operator": "is-any-of"}] if form else []
        return conds, "Form Submitted"
    if kind == "survey":
        survey = t.get("survey_id") or t.get("value")
        conds = [{"operator": "is-any-of", "field": "survey.id", "value": [survey],
                  "title": "Survey is", "type": "string", "id": str(uuid.uuid4())}] if survey else []
        return conds, "Survey Submitted"
    if kind == "appointment_booked":
        conds = [dict(_CONTACT_MODE)]
        if t.get("calendar_id"):
            conds.append({"operator": "==", "field": "calendar.id", "value": t["calendar_id"],
                          "title": "In calendar", "type": "select"})
        return conds, "Appointment Booked"
    if kind == "appointment_status":
        status = _status(t, APPOINTMENT_STATUSES)
        conds = [{"operator": "==", "field": "appointment.eventType", "value": "normal",
                  "title": "Event type", "type": "select"}, dict(_CONTACT_MODE)]
        if t.get("calendar_id"):
            conds.append({"operator": "==", "field": "calendar.id", "value": t["calendar_id"],
                          "title": "In calendar", "type": "select"})
        conds.append({"operator": "==", "field": "appointment.status", "value": status,
                      "title": "Appointment status is", "type": "select"})
        return conds, f"Appointment Status: {status.title()}"
    if kind == "pipeline_stage":
        pipe, stage = _need(t, "pipeline_id"), _need(t, "stage_id")
        return ([{"operator": "==", "field": "opportunity.pipelineId", "value": pipe,
                  "title": "In pipeline", "type": "select"},
                 {"operator": "==", "field": "opportunity.pipelineStageId", "value": stage,
                  "title": "Pipeline stage", "type": "select"}], "Pipeline Stage Changed")
    if kind == "opportunity_status":
        status = _status(t, OPPORTUNITY_STATUSES)
        conds = []
        if t.get("pipeline_id"):
            conds.append({"field": "opportunity.pipelineId", "title": "In pipeline",
                          "type": "select", "value": t["pipeline_id"], "operator": "=="})
        conds.append({"field": "opportunity.status", "id": "moved-to-status",
                      "title": "Moved to status", "type": "select", "value": status,
                      "operator": "=="})
        return conds, f"Opportunity Status: {status.title()}"
    if kind == "contact_created":
        return [], "Contact Created"
    if kind == "customer_replied":
        contains = t.get("contains")
        words = [contains] if isinstance(contains, str) else list(contains or [])
        conds = [{"operator": "string-contains-any-of", "field": "message.body",
                  "value": words, "title": "Message body", "type": "string",
                  "id": "message-body"}] if words else []
        return conds, "Customer Replied"
    raise SpecError(f"no compiler for trigger kind {kind!r}")  # pragma: no cover


def compile_trigger(trigger: dict) -> dict:
    """One spec trigger -> {"type", "name", "conditions"} as GHL stores it."""
    if not isinstance(trigger, dict):
        raise SpecError(f"a trigger must be an object, got {trigger!r}")
    kind = _kind(trigger)
    if kind == "raw":
        ghl = trigger.get("ghl")
        if not isinstance(ghl, dict) or not ghl.get("type"):
            raise SpecError("a raw trigger needs 'ghl': {\"type\": ..., \"conditions\": [...]}")
        return {"type": ghl["type"], "name": trigger.get("name") or ghl.get("name") or ghl["type"],
                "conditions": list(ghl.get("conditions") or [])}
    conditions, default_name = _conditions(kind, trigger)
    return {"type": _GHL_TYPE[kind], "name": trigger.get("name") or default_name,
            "conditions": conditions}


def spec_triggers(spec: dict) -> list[dict]:
    """The spec's triggers as a list: "triggers" (list) or "trigger" (one)."""
    if spec.get("triggers") is not None:
        if not isinstance(spec["triggers"], list):
            raise SpecError("'triggers' must be a list")
        return list(spec["triggers"])
    return [spec["trigger"]] if spec.get("trigger") else []


def compile_triggers(spec: dict) -> list[dict]:
    return [compile_trigger(t) for t in spec_triggers(spec)]


def check_trigger_refs(triggers: list[dict], lookups: dict) -> list[str]:
    """Ids a spec's triggers name that do not exist in the sub-account.

    GHL saves a trigger pointing at a form, survey, calendar or stage that does
    not exist, and the workflow then never fires. `lookups` maps "form",
    "survey", "calendar" to a set of ids, and "pipeline" to {pipeline_id: set of
    stage ids}. A lookup that is None could not be read, so its ids are unknown,
    not missing.
    """
    problems: list[str] = []

    def check(kind: str, value: Optional[str], label: str) -> None:
        known = lookups.get(kind)
        if value and known is not None and value not in known:
            problems.append(f"{label} {value!r} does not exist in this sub-account")

    for t in triggers:
        kind = _kind(t)
        if kind == "form":
            check("form", t.get("form_id") or t.get("value"), "form")
        elif kind == "survey":
            check("survey", t.get("survey_id") or t.get("value"), "survey")
        elif kind in ("appointment_booked", "appointment_status"):
            check("calendar", t.get("calendar_id"), "calendar")
        elif kind in ("pipeline_stage", "opportunity_status"):
            pipes = lookups.get("pipeline")
            pipe = t.get("pipeline_id")
            if pipes is not None and pipe:
                if pipe not in pipes:
                    problems.append(f"pipeline {pipe!r} does not exist in this sub-account")
                elif kind == "pipeline_stage" and t.get("stage_id") not in pipes[pipe]:
                    problems.append(f"stage {t.get('stage_id')!r} is not in pipeline {pipe!r}")
    return problems


def ref_kinds(triggers: list[dict]) -> set[str]:
    """Which id lookups check_trigger_refs needs for these spec triggers."""
    kinds: set[str] = set()
    for t in triggers:
        kind = _kind(t)
        if kind == "form" and (t.get("form_id") or t.get("value")):
            kinds.add("form")
        elif kind == "survey" and (t.get("survey_id") or t.get("value")):
            kinds.add("survey")
        elif kind in ("appointment_booked", "appointment_status") and t.get("calendar_id"):
            kinds.add("calendar")
        elif kind in ("pipeline_stage", "opportunity_status") and t.get("pipeline_id"):
            kinds.add("pipeline")
    return kinds
