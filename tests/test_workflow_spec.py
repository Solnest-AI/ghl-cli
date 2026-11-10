"""Tests for the branching workflow compiler.

The linking rules asserted here were read off real GoHighLevel workflows
(2026-08-25). If GHL changes its graph format these tests are the tripwire.
"""
import json

import pytest

from cli_anything.gohighlevel.utils.workflow_spec import (
    SpecError,
    compile_spec,
    describe,
)


def _by_id(steps):
    return {s["id"]: s for s in steps}


def _of_type(steps, t):
    return [s for s in steps if s["type"] == t]


def _condition_node(steps):
    return next(s for s in steps if s.get("nodeType") == "condition-node")


def _conditions(steps):
    """Every condition the compiled graph evaluates, across all declared branches."""
    return [c for s in steps if s.get("nodeType") == "condition-node"
            for b in s["attributes"]["branches"]
            for seg in b["segments"]
            for c in seg["conditions"]]


LINEAR = {
    "name": "Linear",
    "steps": [
        {"type": "email", "name": "E1", "subject": "Hi", "body": "hello"},
        {"type": "wait", "name": "W1", "value": 2, "unit": "days"},
        {"type": "tag", "name": "T1", "tags": "done"},
    ],
}

BRANCHED = {
    "name": "Branched",
    "steps": [
        {"type": "email", "name": "E1", "subject": "Hi", "body": "hello"},
        {"type": "wait", "name": "W1", "value": 1, "unit": "days"},
        {
            "type": "if_else",
            "name": "Opened?",
            "condition": {"kind": "email_event", "value": "opened"},
            "yes": [{"type": "tag", "name": "Hot", "tags": "engaged"}],
            "no": [
                {"type": "wait", "name": "W2", "value": 3, "unit": "days"},
                {"type": "email", "name": "E2", "subject": "Bump", "body": "again"},
            ],
        },
    ],
}


class TestLinear:
    def test_order_is_sequential(self):
        steps = compile_spec(LINEAR)
        assert [s["order"] for s in steps] == [0, 1, 2]

    def test_next_chains_forward(self):
        steps = compile_spec(LINEAR)
        assert steps[0]["next"] == steps[1]["id"]
        assert steps[1]["next"] == steps[2]["id"]

    def test_last_step_has_no_next(self):
        steps = compile_spec(LINEAR)
        assert "next" not in steps[-1]

    def test_parentkey_points_at_previous(self):
        steps = compile_spec(LINEAR)
        assert "parentKey" not in steps[0]
        assert steps[1]["parentKey"] == steps[0]["id"]
        assert steps[2]["parentKey"] == steps[1]["id"]

    def test_root_steps_have_no_parent(self):
        for s in compile_spec(LINEAR):
            assert "parent" not in s

    def test_ids_are_unique(self):
        steps = compile_spec(LINEAR)
        assert len({s["id"] for s in steps}) == len(steps)


class TestBranching:
    """GHL's branch model, verified against live workflows.

    A condition node declares its branches in attributes.branches, and its
    `next` lists one id per declared branch plus the else path LAST. GHL's
    builder reads next[-1] as the else path and labels it from
    attributes.noneBranchName (default "None"). Declared branches are
    nodeType "branch-yes"; the else path is "branch-no".

    A yes/no if_else is stored the way GHL's own builder stores a boolean
    If/Else (read back live 2026-10-05): ONE declared branch (Yes) plus the
    else path (No). No is then the exact complement of Yes whatever the
    operator, so nothing is negated and no contact can miss both branches.
    """

    @staticmethod
    def _branch_named(steps, name):
        return next(s for s in steps
                    if s.get("nodeType", "").startswith("branch-") and s["name"] == name)

    def test_condition_node_shape(self):
        cond = _condition_node(compile_spec(BRANCHED))
        assert cond["type"] == "if_else"
        assert cond["cat"] == "conditions"
        assert cond["attributes"]["conditionName"] == "Opened?"
        assert cond["attributes"]["version"] == 2

    def test_yes_is_the_only_declared_branch(self):
        """A declared "No" branch is a negated copy of Yes, which breaks on "is"."""
        cond = _condition_node(compile_spec(BRANCHED))
        assert [b["name"] for b in cond["attributes"]["branches"]] == ["Yes"]

    def test_next_lists_the_declared_branch_then_the_else_path(self):
        """GHL: "expected N next entries (branches + 1 for else path)", else last."""
        steps = compile_spec(BRANCHED)
        yes, no = self._branch_named(steps, "Yes"), self._branch_named(steps, "No")
        assert _condition_node(steps)["next"] == [yes["id"], no["id"]]

    def test_no_is_the_else_path(self):
        steps = compile_spec(BRANCHED)
        kinds = {s["name"]: s["nodeType"] for s in steps
                 if s.get("nodeType", "").startswith("branch-")}
        assert kinds == {"Yes": "branch-yes", "No": "branch-no"}

    def test_branch_node_attributes_match_ghl(self):
        """GHL: conditionName/operator/if required unless else is true."""
        steps = compile_spec(BRANCHED)
        assert self._branch_named(steps, "Yes")["attributes"] == {
            "if": False, "conditionName": "Condition", "operator": "and", "branches": []}
        assert self._branch_named(steps, "No")["attributes"] == {"else": True}

    def test_else_path_is_labelled_no(self):
        """Without noneBranchName GHL's builder relabels the No steps "None"."""
        steps = compile_spec(BRANCHED)
        assert _condition_node(steps)["attributes"].get("noneBranchName") == "No"

    def test_else_node_is_not_declared_in_branches(self):
        steps = compile_spec(BRANCHED)
        declared = {b["id"] for b in _condition_node(steps)["attributes"]["branches"]}
        assert self._branch_named(steps, "No")["id"] not in declared

    def test_declared_branch_ids_match_their_entry_nodes(self):
        steps = compile_spec(BRANCHED)
        cond = next(s for s in steps if s.get("nodeType") == "condition-node")
        for b in cond["attributes"]["branches"]:
            assert self._branch_named(steps, b["name"])["id"] == b["id"]

    def test_previous_step_points_at_the_condition(self):
        """GHL rejects a parentKey whose target's `next` omits the child."""
        steps = compile_spec(BRANCHED)
        cond = next(s for s in steps if s.get("nodeType") == "condition-node")
        prev = next(s for s in steps if s["id"] == cond["parentKey"])
        assert prev["next"] == cond["id"]

    def test_parentkey_is_reciprocal_everywhere(self):
        steps = compile_spec(BRANCHED)
        by_id = _by_id(steps)
        for s in steps:
            pk = s.get("parentKey")
            if not pk or pk == s.get("parent"):
                continue          # first-in-branch points at its branch node
            nxt = by_id[pk].get("next")
            targets = nxt if isinstance(nxt, list) else [nxt]
            assert s["id"] in targets, f"{s['name']} unreachable from {by_id[pk]['name']}"

    def test_branch_nodes_point_back_at_the_condition(self):
        steps = compile_spec(BRANCHED)
        cond = next(s for s in steps if s.get("nodeType") == "condition-node")
        for s in steps:
            if s.get("nodeType", "").startswith("branch-"):
                assert s["parent"] == cond["id"]
                assert s["parentKey"] == cond["id"]

    def test_each_branch_lists_the_other_as_its_sibling(self):
        steps = compile_spec(BRANCHED)
        yes, no = self._branch_named(steps, "Yes"), self._branch_named(steps, "No")
        assert yes["sibling"] == [no["id"]]
        assert no["sibling"] == [yes["id"]]

    def test_no_steps_hang_off_the_else_node(self):
        steps = compile_spec(BRANCHED)
        no_branch = self._branch_named(steps, "No")
        kids = sorted((s for s in steps if s.get("parent") == no_branch["id"]),
                      key=lambda s: s["order"])
        # The step builders rename as they go ("Wait 3 Days", "Email: E2"), so
        # assert on structure rather than on the spec's raw labels.
        assert [k["type"] for k in kids] == ["wait", "email"]
        assert kids[1]["attributes"]["subject"] == "Bump"

    def test_order_restarts_inside_each_branch(self):
        steps = compile_spec(BRANCHED)
        no_branch = self._branch_named(steps, "No")
        kids = sorted((s for s in steps if s.get("parent") == no_branch["id"]),
                      key=lambda s: s["order"])
        assert [k["order"] for k in kids] == [0, 1]

    def test_first_child_parentkey_is_the_branch_node(self):
        steps = compile_spec(BRANCHED)
        no_branch = self._branch_named(steps, "No")
        first = next(s for s in steps
                     if s.get("parent") == no_branch["id"] and s["order"] == 0)
        assert first["parentKey"] == no_branch["id"]

    def test_branch_node_next_points_at_its_first_child(self):
        steps = compile_spec(BRANCHED)
        no_branch = self._branch_named(steps, "No")
        first = next(s for s in steps
                     if s.get("parent") == no_branch["id"] and s["order"] == 0)
        assert no_branch["next"] == first["id"]

    # One condition per operator family. The old compiler built "No" by flipping
    # == and !=, which turned "is" (boolean categories) and "index-of-true"
    # (has tag) into "==": a No branch that was not the opposite of Yes.
    @pytest.mark.parametrize("condition", [
        {"conditionType": "events", "conditionSubType": "email_event",
         "conditionOperator": "==", "conditionValue": "opened", "isWait": False},
        {"conditionType": "contact_reply", "conditionSubType": "contact_replied",
         "conditionOperator": "is", "conditionValue": "true", "isWait": False},
        {"conditionType": "contact_detail", "conditionSubType": "tags",
         "conditionOperator": "index-of-true", "conditionValue": ["vip"], "isWait": False},
    ], ids=["==", "is", "index-of-true"])
    def test_no_is_never_a_negated_copy_of_yes(self, condition):
        steps = compile_spec({"name": "x", "steps": [{
            "type": "if_else", "name": "C", "condition": dict(condition),
            "yes": [{"type": "tag", "name": "Y", "tags": "y"}],
            "no": [{"type": "tag", "name": "N", "tags": "n"}],
        }]})
        assert _conditions(steps) == [condition]

    def test_empty_branch_is_allowed(self):
        steps = compile_spec({
            "name": "One-sided",
            "steps": [{
                "type": "if_else", "name": "Tagged?",
                "condition": {"kind": "tag", "value": "vip"},
                "yes": [{"type": "tag", "name": "Mark", "tags": "vip-handled"}],
            }],
        })
        assert "next" not in self._branch_named(steps, "No")

    def test_if_else_nests_under_the_else_path(self):
        steps = compile_spec({
            "name": "Nested",
            "steps": [{
                "type": "if_else", "name": "Opened?",
                "condition": {"kind": "email_event", "value": "opened"},
                "yes": [{"type": "tag", "name": "Hot", "tags": "engaged"}],
                "no": [{
                    "type": "if_else", "name": "Clicked?",
                    "condition": {"kind": "email_event", "value": "clicked"},
                    "yes": [{"type": "tag", "name": "Warm", "tags": "warm"}],
                    "no": [{"type": "tag", "name": "Cold", "tags": "cold"}],
                }],
            }],
        })
        outer, inner = [s for s in steps if s.get("nodeType") == "condition-node"]
        outer_else = next(s for s in steps
                          if s.get("parent") == outer["id"] and s["nodeType"] == "branch-no")
        assert inner["parent"] == inner["parentKey"] == outer_else["id"]
        assert outer_else["next"] == inner["id"]

    def test_all_ids_unique_including_branch_nodes(self):
        steps = compile_spec(BRANCHED)
        assert len({s["id"] for s in steps}) == len(steps)


class TestValidation:
    def test_rejects_steps_after_if_else(self):
        spec = {
            "name": "Bad",
            "steps": [
                {"type": "if_else", "name": "C",
                 "condition": {"kind": "tag", "value": "x"},
                 "yes": [{"type": "tag", "name": "T", "tags": "a"}]},
                {"type": "email", "name": "Unreachable", "subject": "s", "body": "b"},
            ],
        }
        with pytest.raises(SpecError, match="cannot follow an if_else"):
            compile_spec(spec)

    def test_rejects_unknown_type(self):
        with pytest.raises(SpecError, match="unsupported type"):
            compile_spec({"name": "x", "steps": [{"type": "carrier_pigeon", "name": "n"}]})

    def test_rejects_missing_name(self):
        with pytest.raises(SpecError, match="missing 'name'"):
            compile_spec({"name": "x", "steps": [{"type": "email"}]})

    def test_rejects_missing_type(self):
        with pytest.raises(SpecError, match="missing 'type'"):
            compile_spec({"name": "x", "steps": [{"name": "n"}]})

    def test_rejects_if_else_without_condition(self):
        with pytest.raises(SpecError, match="needs a 'condition'"):
            compile_spec({"name": "x", "steps": [
                {"type": "if_else", "name": "C",
                 "yes": [{"type": "tag", "name": "T", "tags": "a"}]}]})

    def test_rejects_if_else_without_branches(self):
        with pytest.raises(SpecError, match="needs a 'yes' and/or 'no'"):
            compile_spec({"name": "x", "steps": [
                {"type": "if_else", "name": "C",
                 "condition": {"kind": "tag", "value": "v"}}]})

    def test_reports_missing_required_field(self):
        with pytest.raises(SpecError, match="missing required field"):
            compile_spec({"name": "x", "steps": [{"type": "goto", "name": "g"}]})

    def test_rejects_empty_spec(self):
        with pytest.raises(SpecError, match="no 'steps'"):
            compile_spec({"name": "x"})

    def test_rejects_a_trigger_that_is_not_an_object(self):
        """Otherwise preview/build die on trigger.get() with a traceback."""
        with pytest.raises(SpecError, match="'trigger' must be an object"):
            compile_spec({**LINEAR, "trigger": "appointment"})


def _rescheduled_spec(trigger=None, **condition):
    spec = {"name": "Booking changed", "steps": [{
        "type": "if_else", "name": "Was it rescheduled?",
        "condition": {"kind": "appointment_rescheduled", **condition},
        "yes": [{"type": "tag", "name": "Moved", "tags": "moved"}],
        "no": [{"type": "tag", "name": "New", "tags": "new-booking"}],
    }]}
    if trigger is not None:
        spec["trigger"] = trigger
    return spec


class TestAppointmentRescheduled:
    def test_compiles_to_the_condition_ghl_stores(self):
        """Saved by GHL's builder, read back, and fired correctly live 2026-10-05."""
        steps = compile_spec(_rescheduled_spec(trigger={"type": "appointment"}))
        assert _conditions(steps) == [{
            "conditionType": "appointment",
            "conditionSubType": "appointmentRescheduled",
            "conditionOperator": "is",
            "conditionValue": "true",
            "isWait": False,
        }]

    def test_customer_appointment_trigger_also_enables_it(self):
        steps = compile_spec(_rescheduled_spec(trigger={"type": "customer_appointment"}))
        assert _conditions(steps)[0]["conditionType"] == "appointment"

    @pytest.mark.parametrize("trigger", [None, {"type": "tag", "value": "booked"}],
                             ids=["no trigger", "tag trigger"])
    def test_rejected_without_an_appointment_trigger(self, trigger):
        """GHL disables the Appointment category unless one of these triggers exists."""
        with pytest.raises(SpecError, match="'appointment' or 'customer_appointment' trigger"):
            compile_spec(_rescheduled_spec(trigger=trigger))

    def test_raw_appointment_conditions_are_gated_too(self):
        raw = {"conditionType": "appointment", "conditionSubType": "isRunningAsGuest",
               "conditionOperator": "is", "conditionValue": "true", "isWait": False}
        with pytest.raises(SpecError, match="customer_appointment"):
            compile_spec({"name": "x", "trigger": {"type": "tag", "value": "t"}, "steps": [{
                "type": "if_else", "name": "Guest?", "condition": raw,
                "yes": [{"type": "tag", "name": "G", "tags": "guest"}]}]})

    def test_gate_reaches_nested_conditions(self):
        with pytest.raises(SpecError, match=r"steps\[0\]\.yes\[0\]"):
            compile_spec({"name": "x", "trigger": {"type": "tag", "value": "t"}, "steps": [{
                "type": "if_else", "name": "Opened?",
                "condition": {"kind": "email_event", "value": "opened"},
                "yes": [{"type": "if_else", "name": "Moved?",
                         "condition": {"kind": "appointment_rescheduled"},
                         "yes": [{"type": "tag", "name": "M", "tags": "moved"}]}]}]})

    def test_nested_condition_sees_the_spec_trigger(self):
        steps = compile_spec({"name": "x", "trigger": {"type": "appointment"}, "steps": [{
            "type": "if_else", "name": "Opened?",
            "condition": {"kind": "email_event", "value": "opened"},
            "no": [{"type": "if_else", "name": "Moved?",
                    "condition": {"kind": "appointment_rescheduled"},
                    "yes": [{"type": "tag", "name": "M", "tags": "moved"}]}]}]})
        assert [c["conditionType"] for c in _conditions(steps)] == ["events", "appointment"]

    def test_json_boolean_value_becomes_ghl_string_key(self):
        """GHL's boolean condition values are the strings "true"/"false", not JSON bools."""
        steps = compile_spec(_rescheduled_spec(trigger={"type": "appointment"}, value=False))
        assert _conditions(steps)[0]["conditionValue"] == "false"


def _if_else_spec(condition):
    return {"name": "x", "steps": [{
        "type": "if_else", "name": "Tagged?", "condition": condition,
        "yes": [{"type": "wait", "name": "Y", "value": 1, "unit": "days"}],
        "no": [{"type": "wait", "name": "N", "value": 2, "unit": "days"}],
    }]}


class TestTagCondition:
    """Contact Details > Tags, as GHL's builder stores it.

    Read from the builder bundle 2026-10-05: its "Has Tag" If/Else recipe is
    contact_detail / tags / index-of-true / isWait false, Tags is a multiselect
    whose options are {value: tag.name.toLowerCase()}, and the multiselect
    stores options.map(o => o.value), so the value is a LIST of lowercase names.
    The old {"conditionType": "contact", "conditionSubType": "tag"} names a
    category the builder does not have.
    """

    def test_compiles_to_the_condition_ghl_stores(self):
        steps = compile_spec(_if_else_spec({"kind": "tag", "value": "vip"}))
        assert _conditions(steps) == [{
            "conditionType": "contact_detail",
            "conditionSubType": "tags",
            "conditionOperator": "index-of-true",
            "conditionValue": ["vip"],
            "isWait": False,
        }]

    def test_tag_name_is_lowercased_like_the_builder_options(self):
        """The builder can only ever store lowercase names; "VIP" would be a shape it never makes."""
        steps = compile_spec(_if_else_spec({"kind": "tag", "value": "  VIP "}))
        assert _conditions(steps)[0]["conditionValue"] == ["vip"]

    def test_does_not_include(self):
        steps = compile_spec(_if_else_spec(
            {"kind": "tag", "value": "vip", "operator": "index-of-false"}))
        assert _conditions(steps) == [{
            "conditionType": "contact_detail",
            "conditionSubType": "tags",
            "conditionOperator": "index-of-false",
            "conditionValue": ["vip"],
            "isWait": False,
        }]

    @pytest.mark.parametrize("operator", ["==", "!=", "is", "has_value"])
    def test_refuses_operators_it_cannot_emit_as_ghl_does(self, operator):
        """== / != / is are not multiselect operators; has_value's stored shape is unverified."""
        with pytest.raises(SpecError, match=r"steps\[0\]: .*index-of-true"):
            compile_spec(_if_else_spec({"kind": "tag", "value": "vip", "operator": operator}))

    @pytest.mark.parametrize("value", [None, "", "   ", ["vip", "hot"], 7],
                             ids=["missing", "empty", "blank", "several", "not text"])
    def test_needs_one_tag_name(self, value):
        """GHL's builder marks an empty value invalid; several tags have unverified semantics."""
        with pytest.raises(SpecError, match=r"steps\[0\]: .*one tag name"):
            compile_spec(_if_else_spec({"kind": "tag", "value": value}))

    def test_raw_tag_condition_passes_through_untouched(self):
        """Raw GHL dicts are the escape hatch for anything the tag kind refuses."""
        raw = {"conditionType": "contact_detail", "conditionSubType": "tags",
               "conditionOperator": "has_value", "isWait": False}
        steps = compile_spec(_if_else_spec(dict(raw)))
        assert _conditions(steps) == [raw]


class TestUnverifiedConditionKinds:
    """A kind whose GHL shape is unverified fails loudly instead of compiling a guess."""

    def test_custom_field_is_refused(self):
        """GHL keys custom-field conditions by field id; operators and values vary by field type."""
        with pytest.raises(SpecError, match=r"steps\[0\]: custom_field .*raw GHL condition"):
            compile_spec(_if_else_spec({"kind": "custom_field", "value": "x"}))

    def test_unknown_kind_is_refused(self):
        """It used to compile to conditionType "contact", a category GHL's builder lacks."""
        with pytest.raises(SpecError, match=r"steps\[0\]: unknown condition kind 'email'"):
            compile_spec(_if_else_spec({"kind": "email", "value": "a@b.com"}))


class TestBuildCommandTrigger:
    """`workflows build` creates the spec's triggers (any type in workflow_triggers)."""

    @staticmethod
    def _build(tmp_path, monkeypatch, spec):
        pytest.importorskip("click")  # bare system pythons lack it; the venv has it
        from click.testing import CliRunner
        from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
        from cli_anything.gohighlevel.utils import workflow_builder

        built = {}

        class OfflineBuilder:
            """Stands in for the network-bound CampaignBuilder; records what it was asked to build."""
            def __init__(self, client):
                pass

            def build(self, campaign, folder_name=None, folder_id=None):
                built.update(campaign)
                return {"errors": []}

            def format_summary(self):
                return "built"

        monkeypatch.setattr(workflow_builder, "CampaignBuilder", OfflineBuilder)
        monkeypatch.setattr(cli_mod, "_get_internal_client", lambda ctx: object())
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec))
        result = CliRunner().invoke(
            cli_mod.cli, ["--experimental", "workflows", "build", "--from-spec", str(path)])
        assert result.exit_code == 0, result.output
        return built["workflow"], result

    def test_an_appointment_trigger_is_built_too(self, tmp_path, monkeypatch):
        """It used to be skipped with a "never runs" note: only tag triggers were built."""
        wf, result = self._build(tmp_path, monkeypatch, _rescheduled_spec(
            trigger={"type": "appointment_status", "status": "confirmed"}))
        assert [t["type"] for t in wf["triggers"]] == ["appointment"]
        assert "never runs" not in result.stderr

    def test_tag_trigger_is_created_without_a_note(self, tmp_path, monkeypatch):
        wf, result = self._build(tmp_path, monkeypatch, {**LINEAR,
                                 "trigger": {"type": "tag", "value": "signed-up"}})
        assert [(t["type"], t["conditions"][0]["value"]) for t in wf["triggers"]] == [
            ("contact_tag", "signed-up")]
        assert result.stderr == ""


class TestNewStepTypes:
    @pytest.mark.parametrize("step,expected", [
        ({"type": "notify", "name": "n", "to": "a@b.com", "subject": "s", "body": "b"},
         "internal_notification"),
        ({"type": "note", "name": "n", "body": "b"}, "add_notes"),
        ({"type": "assign", "name": "n", "user_id": "u1"}, "assign_user"),
        ({"type": "update_field", "name": "n", "field_id": "f1", "value": "v"},
         "update_contact_field"),
        ({"type": "goto", "name": "n", "target": "t1"}, "goto"),
        ({"type": "remove_from_workflow", "name": "n", "workflow_id": "w1"},
         "remove_from_workflow"),
    ])
    def test_maps_to_ghl_action_type(self, step, expected):
        steps = compile_spec({"name": "x", "steps": [step]})
        assert steps[0]["type"] == expected

    def test_notify_targets_the_custom_address(self):
        # Without userType "custom_email" GHL publishes the step but never sends it.
        steps = compile_spec({"name": "x", "steps": [
            {"type": "notify", "name": "n", "to": "a@b.com", "subject": "s", "body": "b"}]})
        email = steps[0]["attributes"]["email"]
        assert email["userType"] == "custom_email"
        assert email["to"] == "a@b.com"

    def test_note_text_goes_in_html(self):
        # GHL rejects add_notes without attributes.html ("Html is required", HTTP 400,
        # whole step save rejected; live 2026-10-11). Every Add Note step in
        # a live sub-account (read 2026-10-17) stores the text there and has no
        # "note" key; two of three are exactly dm_email's <p> wrapper.
        steps = compile_spec({"name": "x", "steps": [
            {"type": "note", "name": "n", "body": "Line one\nLine **two**"}]})
        attrs = steps[0]["attributes"]
        assert attrs == {"html": (
            '<p style="margin:0 0 12px 0;line-height:1.75;font-size:16px;'
            'font-family:arial,helvetica,sans-serif;color:#000;">Line one</p>'
            '<p style="margin:0 0 12px 0;line-height:1.75;font-size:16px;'
            'font-family:arial,helvetica,sans-serif;color:#000;">'
            'Line <strong>two</strong></p>')}

    @pytest.mark.parametrize("body", [None, "", "   \n  "])
    def test_note_without_text_is_refused(self, body):
        # An empty html is a missing html to GHL, and one bad step loses them all.
        step = {"type": "note", "name": "n"}
        if body is not None:
            step["body"] = body
        with pytest.raises(SpecError, match="body"):
            compile_spec({"name": "x", "steps": [step]})

    def test_every_emitted_type_is_verified_by_the_builder(self):
        from cli_anything.gohighlevel.utils.workflow_builder import VERIFIED_ACTIONS
        steps = compile_spec(BRANCHED)
        for s in steps:
            assert s["type"] in VERIFIED_ACTIONS, f"{s['type']} would fail validation"

    def test_tags_accept_string_or_list(self):
        a = compile_spec({"name": "x", "steps": [
            {"type": "tag", "name": "t", "tags": "one, two"}]})
        b = compile_spec({"name": "x", "steps": [
            {"type": "tag", "name": "t", "tags": ["one", "two"]}]})
        assert a[0]["attributes"]["tags"] == b[0]["attributes"]["tags"] == ["one", "two"]


class TestDescribe:
    def test_renders_nested_tree(self):
        out = describe(compile_spec(BRANCHED))
        assert "if_else: Opened?" in out
        assert "[Yes]" in out and "[No]" in out
        # branch children are indented deeper than the branch label
        lines = out.splitlines()
        no_i = next(i for i, l in enumerate(lines) if "[No]" in l)
        assert lines[no_i + 1].index("-") > lines[no_i].index("-")
