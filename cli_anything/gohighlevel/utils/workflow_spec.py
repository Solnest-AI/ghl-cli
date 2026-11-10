"""Declarative workflow spec -> GoHighLevel's flat step graph.

The original builder could only emit a straight line: every step ran after the
one before it. GHL itself supports branching, and this module compiles a nested
spec into the flat, cross-linked node list GHL actually stores.

The linking model was reverse-engineered from real workflows (2026-08-25):

  next        id of the following step. On a condition node this is instead an
              ARRAY of that condition's branch ids, with the else path LAST.
  parentKey   id of the immediately preceding step. For the first step inside a
              branch it is the branch node's own id.
  parent      id of the branch node this step lives under. Absent at root level.
  order       index within the current level. Restarts at 0 inside each branch.
  sibling     on a branch node, the ids of the other branches.
  nodeType    "condition-node" on the if_else itself, "branch-yes" / "branch-no"
              on the branch entry nodes.
  cat         "conditions" on all of the above.

A branch is represented by TWO node kinds. The `if_else` condition node carries
`attributes.branches`, and then each branch gets its own entry node whose `id`
equals the branch id declared on the parent. Children of the branch hang off
that entry node.

Spec shape:

    {
      "name": "Welcome",
      "trigger": {"type": "tag", "value": "signed-up"},
      "steps": [
        {"type": "email", "name": "Hello", "subject": "Hi", "body": "..."},
        {"type": "wait",  "name": "Wait",  "value": 1, "unit": "days"},
        {"type": "if_else", "name": "Opened?",
         "condition": {"kind": "email_event", "value": "opened"},
         "yes": [ ...steps... ],
         "no":  [ ...steps... ]}
      ]
    }

`no` runs for every contact that does not match `condition`: it compiles to
GHL's else path, the way GHL's own builder stores a yes/no If/Else, so a
condition is never negated.

Condition kinds: email_event, tag, appointment_rescheduled, or a raw GHL
condition dict, which passes through untouched. tag takes ONE tag name,
{"kind": "tag", "value": "vip"}, plus "operator": "index-of-false" for "does not
include". custom_field and unknown kinds raise SpecError: their GHL shape is
unverified, so write the raw dict instead. appointment_rescheduled (like any raw
"appointment" condition) needs "trigger": {"type": "appointment"} or
"customer_appointment" (spec: appointment_status / appointment_booked), because
GHL disables that category otherwise. Triggers: "trigger" (one) or "triggers"
(list), any type in workflow_triggers.py; `workflows build` creates them all.

Branches are terminal: GHL does not rejoin them automatically. Use a `goto` step
to jump back into an earlier node if you need to converge.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from cli_anything.gohighlevel.utils import workflow_builder as wb


def _uid() -> str:
    return str(uuid.uuid4())


# ── Conditions ────────────────────────────────────────────────────────────

# kind -> (conditionType, conditionSubType, default operator, default value).
#
# appointment_rescheduled is exactly what GHL's builder stores for Appointment >
# Rescheduled (saved, read back and fired live 2026-10-05). Boolean conditions in
# the builder's BASE_ALLOW_IS_OPERATOR_TYPES categories use operator "is".
_CONDITIONS = {
    "email_event": ("events", "email_event", "==", "opened"),
    "appointment_rescheduled": ("appointment", "appointmentRescheduled", "is", "true"),
}

# Contact Details > Tags (builder bundle read 2026-10-05; saved and read back live
# verbatim the same day, not fired). GHL has no "contact" category: has-tag is
# contact_detail / tags, a multiselect whose options are
# {value: tag.name.toLowerCase()}, and a multiselect stores the LIST of chosen
# values. Its other operators, has_value / has_no_value, store no value and are
# unverified, so they are left to raw GHL dicts.
_TAG_OPERATORS = ("index-of-true", "index-of-false")   # includes / does not include


def _tag_condition(value: Any, operator: Optional[str]) -> dict:
    operator = operator or "index-of-true"
    if operator not in _TAG_OPERATORS:
        raise SpecError(
            f"tag conditions take operator 'index-of-true' (includes) or "
            f"'index-of-false' (does not include), not {operator!r}. "
            "Use a raw GHL condition dict for anything else."
        )
    if not isinstance(value, str) or not value.strip():
        raise SpecError("a tag condition needs 'value': one tag name, e.g. \"vip\"")
    return {
        "isWait": False,
        "conditionSubType": "tags",
        "conditionOperator": operator,
        "conditionValue": [value.strip().lower()],
        "conditionType": "contact_detail",
    }

# GHL disables a condition category unless the workflow has one of these
# triggers (builder: requiredTriggersForConditionType, read 2026-10-05). Only
# categories this compiler can emit by name are listed.
_REQUIRED_TRIGGERS = {"appointment": ("appointment", "customer_appointment")}


def _condition(kind: str, value: Any, operator: Optional[str] = None) -> dict:
    """One condition inside a branch segment, as GHL's builder stores it.

    A kind whose GHL shape is unverified raises SpecError instead of compiling a
    guess; a raw GHL condition dict covers anything else.
    """
    if kind == "tag":
        return _tag_condition(value, operator)
    if kind == "custom_field":
        raise SpecError(
            "custom_field conditions are not verified against GHL: its builder keys "
            "them by the custom field's id under contact_detail, with operators and "
            "values that depend on the field type. Use a raw GHL condition dict."
        )
    if kind not in _CONDITIONS:
        raise SpecError(
            f"unknown condition kind {kind!r}. Supported: "
            f"{', '.join(sorted([*_CONDITIONS, 'tag']))}, or a raw GHL condition dict."
        )
    ctype, subtype, default_op, default_val = _CONDITIONS[kind]
    if value is None:
        value = default_val
    elif isinstance(value, bool):
        # GHL's boolean condition values are the string keys "true"/"false".
        value = "true" if value else "false"
    return {
        "isWait": False,
        "conditionSubType": subtype,
        "conditionOperator": operator or default_op,
        "conditionValue": value,
        "conditionType": ctype,
    }


def _resolve_condition(condition: dict) -> dict:
    """A spec condition ({"kind": ...}) or a raw GHL condition dict, as GHL stores it."""
    if "conditionSubType" in condition:          # already a raw GHL condition
        return condition
    return _condition(condition.get("kind", "tag"),
                      condition.get("value"),
                      condition.get("operator"))


def _branch(name: str, conditions: list[dict], branch_id: Optional[str] = None) -> dict:
    return {
        "id": branch_id or _uid(),
        "name": name,
        "segments": [{"operator": "and", "conditions": conditions}],
        "operator": "and",
        "showErrors": False,
    }


# ── Step builders for types the original CLI never exposed ────────────────

def if_else_step(name: str, condition: dict, yes_id: str) -> dict:
    """A yes/no condition node, stored the way GHL's own builder stores one.

    Only Yes is declared. No is the else path, which GHL's builder labels from
    noneBranchName, so No catches every contact Yes does not, whatever the
    operator. The old negated "No" copy only knew == and !=: it turned "is" and
    "index-of-true" into "==", so No was not the opposite of Yes, and contacts
    matching neither fell into an empty else path and silently did nothing.
    `condition` is a spec dict or a raw GHL dict.
    """
    return {
        "id": _uid(),
        "type": "if_else",
        "name": name,
        "cat": "conditions",
        "nodeType": "condition-node",
        "attributes": {
            "branches": [_branch("Yes", [_resolve_condition(condition)], yes_id)],
            "operator": "and",
            "if": True,
            "conditionName": name,
            "version": 2,
            "noneBranchName": "No",
        },
    }


def branch_node(branch_id: str, name: str, parent_id: str, siblings: list[str],
                is_else: bool = False) -> dict:
    """Entry node for one branch.

    Its id must match the id declared in the parent's `attributes.branches`,
    except for the else node, which is NOT declared there.

    nodeType encodes the ROLE, not the yes/no label: every declared branch is
    "branch-yes"; the single fallback/else path is "branch-no". Verified against
    live workflows: N declared branches have N + 1 child nodes. A yes/no if_else
    declares only Yes, so its No is the branch-no node.
    """
    return {
        "id": branch_id,
        "type": "if_else",
        "name": name,
        "cat": "conditions",
        "nodeType": "branch-no" if is_else else "branch-yes",
        "parent": parent_id,
        "parentKey": parent_id,
        "sibling": siblings,
        # GHL validates these. A declared branch needs conditionName/operator/if;
        # the else node is exempt precisely because it carries {"else": true}
        # ("...required fields when else is not true").
        "attributes": {"else": True} if is_else else {
            "if": False,
            "conditionName": "Condition",
            "operator": "and",
            "branches": [],
        },
    }


def goto_step(name: str, target_id: str) -> dict:
    """Jump to an earlier node. The only way to rejoin after a branch."""
    return {"id": _uid(), "type": "goto", "name": name,
            "attributes": {"targetNodeId": target_id, "type": "goto"}}


def internal_notification_step(name: str, to_email: str, subject: str, body: str) -> dict:
    """Email the team, not the contact. Supports {{contact.*}} merge fields.

    userType "custom_email" is REQUIRED for a "to" address. Without it GHL accepts and
    publishes the step but silently sends nothing (verified live 2026-10-05: same step,
    no email; with custom_email, delivered in 5s). The UI's other modes are "all" users
    and the assigned user.
    """
    html = wb.dm_email(body)
    return {
        "id": _uid(), "type": "internal_notification", "name": name,
        "attributes": {
            "type": "email",
            "email": {"isCloned": False, "html": html, "subject": subject,
                      "to": to_email, "userType": "custom_email", "attachments": []},
        },
    }


def note_step(name: str, body: str) -> dict:
    """Add a note to the contact.

    The text goes in attributes.html; there is no "note" key. Without html GHL
    rejects the whole step save ("Html is required", HTTP 400, live 2026-10-11).
    Every Add Note step in a live sub-account (read 2026-10-17) stores it
    there, two of three as exactly this dm_email <p> markup, so an empty body is
    refused here instead of losing every step at save time.
    """
    html = wb.dm_email(body or "")
    if not html:
        raise SpecError("a note step needs a non-empty 'body'")
    return {"id": _uid(), "type": "add_notes", "name": name,
            "attributes": {"html": html}}


def update_field_step(name: str, field_id: str, value: Any) -> dict:
    return {"id": _uid(), "type": "update_contact_field", "name": name,
            "attributes": {"fields": [{"id": field_id, "value": value}]}}


def assign_user_step(name: str, user_id: str) -> dict:
    return {"id": _uid(), "type": "assign_user", "name": name,
            "attributes": {"userId": user_id}}


def remove_from_workflow_step(name: str, workflow_id: str) -> dict:
    return {"id": _uid(), "type": "remove_from_workflow", "name": name,
            "attributes": {"workflowId": workflow_id}}


BUILDERS = {
    "email": lambda s: wb.email_step(s["name"], s.get("subject", ""), s.get("body", ""),
                                     s.get("from_name", "")),
    "sms": lambda s: wb.sms_step(s["name"], s.get("body", "")),
    "wait": lambda s: wb.wait_step(s["name"], int(s.get("value", 1)), s.get("unit", "days")),
    "tag": lambda s: wb.tag_step(s["name"], _as_list(s.get("tags")), s.get("remove", False)),
    "webhook": lambda s: wb.webhook_step(s["name"], s["url"], s.get("method", "POST")),
    "ai": lambda s: wb.ai_step(s["name"], s.get("prompt", ""), s.get("model", "gpt-4o")),
    "goto": lambda s: goto_step(s["name"], s["target"]),
    "notify": lambda s: internal_notification_step(s["name"], s.get("to", ""),
                                                   s.get("subject", ""), s.get("body", "")),
    "note": lambda s: note_step(s["name"], s.get("body", "")),
    "update_field": lambda s: update_field_step(s["name"], s["field_id"], s.get("value")),
    "assign": lambda s: assign_user_step(s["name"], s["user_id"]),
    "remove_from_workflow": lambda s: remove_from_workflow_step(s["name"], s["workflow_id"]),
}


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    return list(v)


# ── Compiler ──────────────────────────────────────────────────────────────

class SpecError(ValueError):
    pass


def _check_trigger(condition: dict, trigger_types: tuple, here: str) -> None:
    ctype = condition.get("conditionType")
    allowed = _REQUIRED_TRIGGERS.get(ctype)
    if allowed and not any(t in allowed for t in trigger_types):
        raise SpecError(
            f"{here}: '{ctype}' conditions need an "
            f"{' or '.join(repr(t) for t in allowed)} trigger; GHL disables the "
            f"category otherwise. Declare it in the spec, e.g. "
            f"\"trigger\": {{\"type\": \"{allowed[0]}\"}}."
        )


def compile_spec(spec: dict) -> list[dict]:
    """Compile a nested spec into GHL's flat, cross-linked step list."""
    steps = spec.get("steps")
    if not steps:
        raise SpecError("spec has no 'steps'")
    # Lazy import: workflow_triggers imports SpecError from this module.
    from cli_anything.gohighlevel.utils.workflow_triggers import ghl_trigger_type, spec_triggers
    triggers = spec_triggers(spec)
    for trigger in triggers:
        if not isinstance(trigger, dict):
            raise SpecError("'trigger' must be an object, e.g. "
                            "{\"type\": \"tag\", \"value\": \"signed-up\"}")
    trigger_types = tuple(ghl_trigger_type(t) for t in triggers)
    out: list[dict] = []
    _emit(steps, out, parent=None, path="steps", trigger_type=trigger_types)
    return out


def _emit(steps: list[dict], out: list[dict], parent: Optional[str], path: str,
          trigger_type: tuple) -> None:
    prev_id: Optional[str] = None
    for i, raw in enumerate(steps):
        here = f"{path}[{i}]"
        if "type" not in raw:
            raise SpecError(f"{here}: missing 'type'")
        if "name" not in raw:
            raise SpecError(f"{here}: missing 'name'")
        stype = raw["type"]

        if stype == "if_else":
            yes_children = raw.get("yes") or []
            no_children = raw.get("no") or []
            if not yes_children and not no_children:
                raise SpecError(f"{here}: if_else needs a 'yes' and/or 'no' branch")
            if "condition" not in raw:
                raise SpecError(f"{here}: if_else needs a 'condition'")
            if i < len(steps) - 1:
                raise SpecError(
                    f"{here}: steps cannot follow an if_else at the same level. "
                    "GHL does not rejoin branches. Move the following steps into "
                    "'yes'/'no', or use a 'goto' step to converge."
                )
            try:
                condition = _resolve_condition(raw["condition"])
            except SpecError as e:
                raise SpecError(f"{here}: {e}") from None
            _check_trigger(condition, trigger_type, here)
            yes_id, else_id = _uid(), _uid()
            node = if_else_step(raw["name"], condition, yes_id)
            _place(node, out, parent, prev_id, i)
            # The preceding step must point at us, or GHL rejects the graph with
            # "parentKey points to X but that node's next does not contain Y".
            _link_prev(out, prev_id, node["id"])
            # A condition needs one `next` per declared branch PLUS one for the
            # else path, and GHL's builder reads the LAST entry as the else.
            node["next"] = [yes_id, else_id]

            all_ids = (yes_id, else_id)
            for bid, bname, children, is_else in (
                (yes_id, "Yes", yes_children, False),
                (else_id, node["attributes"]["noneBranchName"], no_children, True),
            ):
                bnode = branch_node(bid, bname, node["id"],
                                    [x for x in all_ids if x != bid], is_else=is_else)
                bnode["order"] = 0
                out.append(bnode)
                if children:
                    _emit(children, out, parent=bid, path=f"{here}.{bname.lower()}",
                          trigger_type=trigger_type)
                    first = next(s for s in out
                                 if s.get("parent") == bid and s.get("order") == 0)
                    bnode["next"] = first["id"]
            prev_id = node["id"]
            continue

        builder = BUILDERS.get(stype)
        if builder is None:
            raise SpecError(
                f"{here}: unsupported type '{stype}'. "
                f"Supported: {', '.join(sorted(BUILDERS))}, if_else"
            )
        try:
            node = builder(raw)
        except KeyError as e:
            raise SpecError(f"{here}: type '{stype}' is missing required field {e}") from None
        except SpecError as e:
            raise SpecError(f"{here}: {e}") from None
        _place(node, out, parent, prev_id, i)
        _link_prev(out, prev_id, node["id"])
        prev_id = node["id"]


def _link_prev(out: list[dict], prev_id: Optional[str], node_id: str) -> None:
    """Point the previous step at this one.

    GHL validates `parentKey` reciprocally: if B.parentKey == A, then A.next must
    contain B. Skipping this produces a 400 on save.
    """
    if prev_id is None:
        return
    for s in out:
        if s["id"] == prev_id and "next" not in s:
            s["next"] = node_id
            return


def _place(node: dict, out: list[dict], parent: Optional[str],
           prev_id: Optional[str], index: int) -> None:
    node["order"] = index
    if parent is not None:
        node["parent"] = parent
        node["parentKey"] = prev_id if prev_id else parent
    elif prev_id:
        node["parentKey"] = prev_id
    out.append(node)


def describe(steps: list[dict]) -> str:
    """Render the compiled graph as an indented tree, for eyeballing."""
    by_parent: dict[Optional[str], list[dict]] = {}
    for s in steps:
        by_parent.setdefault(s.get("parent"), []).append(s)
    for v in by_parent.values():
        v.sort(key=lambda s: s.get("order", 0))
    lines: list[str] = []

    def walk(parent: Optional[str], depth: int) -> None:
        for s in by_parent.get(parent, []):
            a = s.get("attributes") or {}
            if s.get("nodeType", "").startswith("branch-"):
                label = f"[{s['name']}]"
            elif s["type"] == "if_else":
                label = f"if_else: {a.get('conditionName')}"
            else:
                detail = (a.get("subject") or ", ".join(a.get("tags", []))
                          or (f"{a.get('startAfter', {}).get('value')} "
                              f"{a.get('startAfter', {}).get('type')}"
                              if a.get("startAfter") else ""))
                label = f"{s['type']}: {s['name']}" + (f"  ({detail})" if detail else "")
            lines.append("    " * depth + "- " + label)
            walk(s["id"], depth + 1)

    walk(None, 0)
    return "\n".join(lines)
