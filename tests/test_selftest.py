"""`ghl selftest`: build, read back, fire (only when safe or after a yes), and
always clean up. No network: FakeGHL + FakeContacts, sleep is a no-op."""
import functools

import pytest

pytest.importorskip("click")

from click.testing import CliRunner  # noqa: E402

from cli_anything.gohighlevel import gohighlevel_cli as cli_mod
from cli_anything.gohighlevel.utils import ghl_client as api
from cli_anything.gohighlevel.utils import ghl_internal_client as internal
from cli_anything.gohighlevel.utils import selftest as st
from cli_anything.gohighlevel.utils.selftest import Risky, cleanup, run_selftest, scan_risky
from fake_contacts import FakeContacts
from fake_ghl import FakeGHL

LOC = "SelfTestLocAaaaaaaaa"
RUN = "a1b2c3"
NAME = f"ghl-cli setup check {RUN}"
START, FIRED = f"ghl-cli-check-{RUN}-start", f"ghl-cli-check-{RUN}-fired"
NOSLEEP = lambda s: None  # noqa: E731

SMS = {"id": "s-sms", "type": "sms", "name": "Text them", "attributes": {"body": "hi"}}
NOTIFY = {"id": "s-not", "type": "internal_notification", "name": "Tell the team", "attributes": {}}


@pytest.fixture
def server():
    return FakeGHL()


@pytest.fixture
def c(server):
    return server.client(LOC)


@pytest.fixture
def contacts(server):
    return FakeContacts(server, LOC)


def trigger(wf_id, ttype, conditions=(), tid=None):
    return {"id": tid or f"tr-{wf_id}-{ttype}", "workflow_id": wf_id, "type": ttype,
            "name": ttype, "deleted": False, "conditions": list(conditions)}


def tag_cond(tag, operator="index-of-true", field="tagsAdded"):
    return {"operator": operator, "field": field, "value": tag, "title": "Tag Added",
            "type": "select", "id": "tag-added"}


def member_wf(server, wf_id, name, ttype, *, conditions=(), steps=(SMS, NOTIFY),
              status="published", parent=None):
    server.add_workflow(LOC, wf_id, name, list(steps), status=status, parent_id=parent,
                        triggers=[trigger(wf_id, ttype, conditions)])


def folder(server, fid, name, parent=None):
    doc = server.add_workflow(LOC, fid, name, [], parent_id=parent)
    doc["type"] = "directory"
    return doc


def run_docs(server, run=RUN):
    return [d["name"] for (loc, _), d in server.docs.items() if run in d["name"]]


def run_tags(server, run=RUN):
    return sorted(t for t in server.tags.get(LOC, set()) if run in t)


def run_contacts(contacts, run=RUN):
    return [cid for cid, ct in contacts.contacts.items() if run in ct.get("lastName", "")]


def assert_nothing_left(server, contacts, result=None, run=RUN):
    assert run_docs(server, run) == []
    assert run_tags(server, run) == []
    assert run_contacts(contacts, run) == []
    if result is not None:
        assert result.leftovers == []


def published_calls(server):
    return [b for (_, m, p, b) in server.calls
            if m == "PUT" and "/change-status/" in p and (b or {}).get("status") == "published"]


# -- the brief's ten -------------------------------------------------------

def test_new_account_fires_and_leaves_nothing(server, c, contacts):
    sent = []
    real_post = contacts.post
    contacts.post = lambda p, data=None, version=None: (sent.append((p, data)), real_post(p, data, version))[1]

    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)

    assert r.status == "PASS", (r.reason, r.lines)
    assert r.risky == [] and r.reason == ""
    assert len(published_calls(server)) == 1
    assert published_calls(server)[0]["updatedBy"] == "UserBBBBBBBBBBBBBBBB"
    # one throwaway contact, no email and no phone, deleted again
    create = [d for p, d in sent if p in ("/contacts/", "/contacts")]
    assert len(create) == 1
    assert create[0]["firstName"] == "ghl-cli" and create[0]["lastName"] == f"setup check {RUN}"
    assert "email" not in create[0] and "phone" not in create[0]
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created
    assert ("/contacts/" + contacts.created[0] + "/tags", {"tags": [START]}) in sent
    assert_nothing_left(server, contacts, r)


def test_risky_workflow_means_draft_only_by_default(server, c, contacts):
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS_NO_FIRE" and r.risky[0].actions == ["texts the contact", "notifies your team"]
    assert r.risky == [Risky("wf-welcome", "New lead welcome", "contact_created",
                             ["texts the contact", "notifies your team"])]
    assert contacts.created == []
    assert published_calls(server) == []
    # the draft was still built and read back before it was removed
    assert any(m == "POST" and p == f"/workflow/{LOC}" and (b or {}).get("name") == NAME
               and (b or {}).get("type") != "directory" for (_, m, p, b) in server.calls)
    assert_nothing_left(server, contacts, r)
    assert ("wf-welcome" in {wid for (_, wid) in server.docs})  # the member's own is untouched


def test_fire_flag_fires_even_with_risky(server, c, contacts):
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    r = run_selftest(c, contacts, fire=True, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS", (r.reason, r.lines)
    assert r.risky and r.risky[0].name == "New lead welcome"
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created
    assert_nothing_left(server, contacts, r)


def test_workflows_inside_folders_are_scanned(server, c, contacts):
    server.folders_hide_children = True
    folder(server, "fold-1", "Lead stuff")
    folder(server, "fold-2", "Inner", parent="fold-1")
    member_wf(server, "wf-deep", "Deep nurture", "contact_created", parent="fold-2")
    member_wf(server, "wf-mid", "Any tag", "contact_tag", parent="fold-1")
    risky, complete = scan_risky(c)
    assert complete is True
    assert sorted(x.name for x in risky) == ["Any tag", "Deep nurture"]
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS_NO_FIRE"
    assert contacts.created == []
    assert_nothing_left(server, contacts, r)


def test_incomplete_list_means_draft_only(server, c, contacts):
    server.list_count_override = 150
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS_NO_FIRE"
    assert "couldn't read all" in r.reason
    assert contacts.created == [] and published_calls(server) == []
    assert_nothing_left(server, contacts, r)


def test_specific_tag_trigger_is_not_risky(server, c, contacts):
    member_wf(server, "wf-vip", "VIP texts", "contact_tag", conditions=[tag_cond("vip")])
    assert scan_risky(c) == ([], True)
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS", (r.reason, r.lines)
    assert_nothing_left(server, contacts, r)


def test_failure_halfway_still_cleans_up(server, c, contacts, monkeypatch):
    server.fail_publish = True
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    assert r.reason
    assert contacts.created == []
    assert_nothing_left(server, contacts, r)

    # the CLI says FAIL and exits 1
    wire_cli(monkeypatch, server, contacts)
    res = CliRunner().invoke(cli_mod.cli, ["selftest"], obj={})
    assert res.exit_code == 1, res.output
    assert "FAIL" in res.output
    assert run_docs(server, "ghl-cli setup check") == []
    assert run_tags(server, "ghl-cli-check-") == [] and contacts.contacts == {}


def test_leftover_is_named_with_its_removal_command(server, c, contacts):
    server.refuse_workflow_delete = True
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    wf_id = next(wid for (_, wid), d in server.docs.items() if d["name"] == NAME and d["type"] == "workflow")
    fold_id = next(wid for (_, wid), d in server.docs.items() if d["name"] == NAME and d["type"] == "directory")
    assert f"workflow {NAME} ({wf_id}): remove with: ghl selftest --cleanup {RUN}" in r.leftovers
    assert f"folder {NAME} ({fold_id}): remove with: ghl selftest --cleanup {RUN}" in r.leftovers
    # one refused delete does not stop the rest
    assert run_tags(server) == [] and run_contacts(contacts) == []
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created


def test_no_fire_is_a_fail_with_a_reason(server, c, contacts):
    contacts.executes = False
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    assert r.reason == "the test workflow did not fire within 2 minutes"
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created
    assert_nothing_left(server, contacts, r)


def test_cleanup_by_run_id_removes_a_kept_run(server, c, contacts):
    server.folders_hide_children = True
    kept = make_kept_run(server, contacts, RUN)
    other = make_kept_run(server, contacts, "ffffff")
    member_wf(server, "wf-mine", "My own", "contact_tag", conditions=[tag_cond("vip")])
    server.tags.setdefault(LOC, set()).add("vip")
    mine = contacts.post("/contacts/", data={"locationId": LOC, "firstName": "Jo", "lastName": "Real"})

    assert cleanup(c, contacts, RUN) == []
    assert_nothing_left(server, contacts)
    assert kept["contact"] in contacts.deleted
    # another run, the member's workflow, tag and contact are all untouched
    assert run_docs(server, "ffffff") and run_tags(server, "ffffff") and other["contact"] in contacts.contacts
    assert (LOC, "wf-mine") in server.docs and "vip" in server.tags[LOC]
    assert mine["contact"]["id"] in contacts.contacts


# -- more failure modes ----------------------------------------------------

def make_kept_run(server, contacts, run):
    """What a crashed or kept run leaves: published workflow in its folder, both tags, contact."""
    name = f"ghl-cli setup check {run}"
    fid = f"fold-{run}"
    folder(server, fid, name)
    wid = f"wf-{run}"
    server.add_workflow(LOC, wid, name, [SMS], status="published", parent_id=fid,
                        triggers=[trigger(wid, "contact_tag", [tag_cond(f"ghl-cli-check-{run}-start")])])
    server.tags.setdefault(LOC, set()).update({f"ghl-cli-check-{run}-start", f"ghl-cli-check-{run}-fired"})
    ct = contacts.post("/contacts/", data={"locationId": LOC, "firstName": "ghl-cli",
                                           "lastName": f"setup check {run}"})["contact"]
    return {"folder": fid, "workflow": wid, "contact": ct["id"]}


@pytest.mark.parametrize("ttype,conditions", [
    ("contact_created", []),
    ("contact_tag", []),                                     # any tag added
    ("contact_tag", [tag_cond("")]),                         # tag left blank
    ("contact_tag", [tag_cond("vip", operator="index-of-false")]),  # any tag except vip
    ("note_added", []),
    ("contact_changed", []),
    ("brand_new_ghl_trigger", []),                           # unknown types are risky
    (None, []),
])
def test_risky_trigger_shapes(server, c, ttype, conditions):
    member_wf(server, "wf-x", "Something", ttype, conditions=conditions)
    risky, complete = scan_risky(c)
    assert complete and [x.workflow_id for x in risky] == ["wf-x"]


@pytest.mark.parametrize("ttype", sorted(st.SAFE_TRIGGER_TYPES))
def test_safe_trigger_types(server, c, ttype):
    member_wf(server, "wf-x", "Something", ttype)
    assert scan_risky(c) == ([], True)


def test_draft_workflows_are_ignored(server, c):
    member_wf(server, "wf-x", "Draft welcome", "contact_created", status="draft")
    assert scan_risky(c) == ([], True)


def test_unknown_actions_shown_by_raw_name_once_each(server, c):
    steps = [SMS, {"id": "a", "type": "wait", "name": "w"}, dict(SMS, id="s2"),
             {"id": "b", "type": "custom_webhook", "name": "hook"}]
    member_wf(server, "wf-x", "Mixed", "contact_created", steps=steps)
    (r,), _ = scan_risky(c)
    assert r.actions == ["texts the contact", "wait", "sends a webhook"]


def test_unreadable_triggers_mean_incomplete_scan(server, c, contacts):
    member_wf(server, "wf-x", "VIP", "contact_tag", conditions=[tag_cond("vip")])
    real = c.request

    def request(method, path, body=None):
        if "/trigger?workflowId=wf-x" in path:
            return {"_error": True, "code": 500, "message": "boom"}
        return real(method, path, body)
    c.request = request
    assert scan_risky(c) == ([], False)
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS_NO_FIRE" and "couldn't read all" in r.reason
    assert contacts.created == []


def test_refused_list_means_incomplete_scan(server, c):
    server.list_override = {"_error": True, "code": 503, "message": "x"}
    assert scan_risky(c) == ([], False)


def test_draft_only_flag_never_fires(server, c, contacts):
    r = run_selftest(c, contacts, fire=False, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS_NO_FIRE" and r.reason
    assert contacts.created == [] and published_calls(server) == []
    assert_nothing_left(server, contacts, r)


def test_whoami_failure_fails_before_publishing(server, c, contacts):
    c.whoami = lambda: None
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "user" in r.reason
    assert published_calls(server) == [] and contacts.created == []
    assert_nothing_left(server, contacts, r)


def test_exception_while_firing_still_cleans_up(server, c, contacts):
    real_post = contacts.post

    def post(path, data=None, version=None):
        if path.endswith("/tags"):
            raise RuntimeError("network fell over")
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "start tag" in r.reason
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created
    assert_nothing_left(server, contacts, r)


def test_interrupted_while_polling_still_cleans_up(server, c, contacts):
    contacts.executes = False
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 3:
            raise KeyboardInterrupt
    r = run_selftest(c, contacts, run_id=RUN, sleep=sleep)
    assert r.status == "FAIL" and "stopped" in r.reason
    assert_nothing_left(server, contacts, r)


def test_contact_that_will_not_delete_is_a_named_leftover(server, c, contacts):
    contacts.refuse_delete = True
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    cid = contacts.created[0]
    assert r.leftovers == [f"contact {NAME} ({cid}): remove with: ghl selftest --cleanup {RUN}"]
    assert run_docs(server) == [] and run_tags(server) == []


def test_note_missing_is_a_fail(server, c, contacts, monkeypatch):
    real_get = contacts.get
    contacts.get = lambda p, params=None, version=None: (
        {"notes": []} if p.endswith("/notes") else real_get(p, params, version))
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "note" in r.reason
    assert_nothing_left(server, contacts, r)


@pytest.mark.parametrize("knob,expect", [
    ("refuse_tags", "did not build the test workflow"),     # tags/create refused
    ("fail_step_saves", "did not build the test workflow"),  # step save refused
    ("drop_step_saves", "did not save as built"),            # saved, steps silently dropped
])
def test_build_or_read_back_failure_cleans_up(server, c, contacts, monkeypatch, knob, expect):
    from cli_anything.gohighlevel.utils import workflow_io
    monkeypatch.setattr(workflow_io, "_sleep", NOSLEEP)
    setattr(server, knob, True)
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and expect in r.reason, r.reason
    assert published_calls(server) == [] and contacts.created == []
    assert_nothing_left(server, contacts, r)


def test_contact_create_refused_by_ghl_leaves_nothing(server, c, contacts):
    import requests
    real_post = contacts.post

    def post(path, data=None, version=None):
        if path == "/contacts/":
            raise requests.HTTPError("422 locationId is invalid")
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "test contact" in r.reason
    assert_nothing_left(server, contacts, r)


def test_contact_create_answer_lost_is_found_by_name_and_removed(server, c, contacts):
    import requests
    real_post = contacts.post

    def post(path, data=None, version=None):
        out = real_post(path, data, version)
        if path == "/contacts/":
            raise requests.Timeout("read timed out")   # GHL made it, the answer never came
        return out
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "test contact" in r.reason
    assert len(contacts.created) == 1 and contacts.deleted == contacts.created
    assert_nothing_left(server, contacts, r)


def test_contact_create_answer_lost_and_not_found_is_reported(server, c, contacts):
    import requests
    real_post = contacts.post

    def post(path, data=None, version=None):
        if path == "/contacts/":
            raise requests.Timeout("read timed out")
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    assert r.leftovers == [f"contact {NAME} (id unknown): remove with: ghl selftest --cleanup {RUN}"]


def test_tag_delete_refused_is_a_named_leftover(server, c, contacts):
    real = c.request

    def request(method, path, body=None):
        if method == "DELETE" and "/tags/" in path:
            return {"_error": True, "code": 500, "message": "boom"}
        return real(method, path, body)
    c.request = request
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and r.reason == "it could not remove everything it made"
    assert r.leftovers == [f"tag {START} (tag-{START}): remove with: ghl selftest --cleanup {RUN}",
                           f"tag {FIRED} (tag-{FIRED}): remove with: ghl selftest --cleanup {RUN}"]
    assert run_docs(server) == [] and run_contacts(contacts) == []


def test_cleanup_keeps_a_run_folder_that_holds_a_member_workflow(server, c, contacts):
    kept = make_kept_run(server, contacts, RUN)
    member_wf(server, "wf-moved", "Moved in by hand", "form_submission", parent=kept["folder"])
    left = cleanup(c, contacts, RUN)
    assert (LOC, "wf-moved") in server.docs
    assert (LOC, kept["folder"]) in server.docs
    assert left == [f"folder {NAME} ({kept['folder']}): holds workflows the selftest did not make. "
                    f"Move them out in GHL, then run: ghl selftest --cleanup {RUN}"]
    assert (LOC, kept["workflow"]) not in server.docs


def test_run_id_is_six_lowercase_hex(server, c, contacts):
    import re
    r = run_selftest(c, contacts, fire=False, sleep=NOSLEEP)
    names = {(b or {}).get("name") for (_, m, p, b) in server.calls if m == "POST"}
    assert any(n and re.fullmatch(r"ghl-cli setup check [0-9a-f]{6}", n) for n in names)
    assert r.status == "PASS_NO_FIRE"


# -- CLI ---------------------------------------------------------------------

def wire_cli(monkeypatch, server, contacts):
    monkeypatch.setattr(cli_mod, "_get_internal_client",
                        lambda ctx, preflight=True, location_id=None: server.client(LOC))
    for verb in ("get", "post", "delete"):
        monkeypatch.setattr(api, verb, getattr(contacts, verb))
    monkeypatch.setattr(st, "run_selftest", functools.partial(st.run_selftest, sleep=NOSLEEP))


def invoke(*args):
    return CliRunner().invoke(cli_mod.cli, list(args), obj={})


def test_cli_pass_needs_no_experimental_flag(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    res = invoke("selftest")
    assert res.exit_code == 0, res.output
    assert res.output.rstrip().splitlines()[-1] == "PASS"


def test_cli_pass_no_fire_names_the_risky_workflows(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    res = invoke("selftest")
    assert res.exit_code == 0, res.output
    assert "PASS, firing not tested" in res.output
    line = next(ln for ln in res.output.splitlines() if "New lead welcome" in ln)
    assert "contact_created" in line and "texts the contact" in line and "notifies your team" in line
    assert res.output.rstrip().splitlines()[-1] == "To test firing later: ghl selftest --fire"
    assert contacts.created == []


def test_cli_incomplete_scan_reason(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    server.list_count_override = 150
    res = invoke("selftest")
    assert res.exit_code == 0 and "couldn't read all your workflows" in res.output
    assert res.output.rstrip().splitlines()[-1] == "To test firing later: ghl selftest --fire"


def test_cli_fire_fires_with_risky(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    res = invoke("selftest", "--fire")
    assert res.exit_code == 0, res.output
    assert len(contacts.created) == 1
    # their side effects are outside cleanup, so the member is told plainly
    out = res.output.splitlines()
    assert "PASS" in out
    warn = out.index("The test contact may have set off these published workflows. "
                     "Anything they did was not undone:")
    assert "New lead welcome" in out[warn + 1] and "texts the contact" in out[warn + 1]


def test_cli_fire_failing_before_any_contact_has_no_side_effect_warning(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    server.fail_publish = True
    res = invoke("selftest", "--fire")
    assert res.exit_code == 1 and contacts.created == []
    assert "may have set off" not in res.output


def test_cli_check_only_lists_and_writes_nothing(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    member_wf(server, "wf-welcome", "New lead welcome", "contact_created")
    res = invoke("selftest", "--check-only")
    assert res.exit_code == 0, res.output
    line = next(ln for ln in res.output.splitlines() if "New lead welcome" in ln)
    assert "contact_created" in line and "texts the contact" in line
    assert server.writes() == [] and contacts.created == []


@pytest.mark.parametrize("knob,value", [
    ("list_count_override", 150),                                   # more rows than returned
    ("list_override", {"_error": True, "code": 503, "message": "x"}),  # list refused outright
])
def test_cli_check_only_incomplete_scan_is_not_reassuring(server, contacts, monkeypatch, knob, value):
    wire_cli(monkeypatch, server, contacts)
    setattr(server, knob, value)
    res = invoke("selftest", "--check-only")
    assert res.exit_code == 0, res.output
    assert "No published workflow would be set off" not in res.output
    assert res.output.splitlines()[0] == ("Could not read all your workflows, so some that "
                                          "could be set off may not be listed.")
    assert server.writes() == []


def test_cli_check_only_with_nothing_risky(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    res = invoke("selftest", "--check-only")
    assert res.exit_code == 0 and res.output.strip()
    assert server.writes() == []


def test_cli_fail_prints_cause_and_leftovers(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    server.refuse_workflow_delete = True
    res = invoke("selftest")
    assert res.exit_code == 1
    assert "FAIL" in res.output
    assert "remove with: ghl selftest --cleanup " in res.output


@pytest.mark.parametrize("args", [
    ["selftest", "--fire", "--draft-only"],
    ["selftest", "--check-only", "--fire"],
    ["selftest", "--cleanup", "not-a-run"],
])
def test_cli_rejects_bad_combinations(server, contacts, monkeypatch, args):
    wire_cli(monkeypatch, server, contacts)
    res = invoke(*args)
    assert res.exit_code == 2
    assert server.writes() == [] and contacts.created == []


def test_cli_cleanup_removes_a_kept_run(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    make_kept_run(server, contacts, RUN)
    res = invoke("selftest", "--cleanup", RUN)
    assert res.exit_code == 0, res.output
    assert_nothing_left(server, contacts)


def test_cli_cleanup_exit_1_when_something_stays(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    make_kept_run(server, contacts, RUN)
    server.refuse_workflow_delete = True
    res = invoke("selftest", "--cleanup", RUN)
    assert res.exit_code == 1
    assert f"remove with: ghl selftest --cleanup {RUN}" in res.output


# -- R5: keys never printed ---------------------------------------------------

@pytest.fixture
def sentinel_wired(server, contacts, monkeypatch):
    """The real internal-client factory runs; only the network edges are faked."""
    monkeypatch.setenv("GHL_API_KEY", "key-SENTINEL-api")
    monkeypatch.setenv("GHL_FIREBASE_REFRESH_TOKEN", "tok-SENTINEL-refresh")
    monkeypatch.setenv("GHL_LOCATION_ID", LOC)
    monkeypatch.setattr(internal.TokenManager, "get_token", lambda self: "jwt-SENTINEL-session")
    monkeypatch.setattr(internal, "InternalGHLClient", lambda tm, loc: server.client(loc))
    for verb in ("get", "post", "delete"):
        monkeypatch.setattr(api, verb, getattr(contacts, verb))
    monkeypatch.setattr(st, "run_selftest", functools.partial(st.run_selftest, sleep=NOSLEEP))
    return server


@pytest.mark.parametrize("args,setup,code,expect", [
    (["selftest", "--check-only"], None, 0, "selftest"),
    (["selftest", "--fire"], "no_fire", 1, "did not fire within 2 minutes"),
    (["selftest"], "refuse_delete", 1, "remove with: ghl selftest --cleanup"),
])
def test_selftest_never_prints_key_values(sentinel_wired, contacts, args, setup, code, expect):
    if setup == "no_fire":
        contacts.executes = False
    elif setup == "refuse_delete":
        sentinel_wired.refuse_workflow_delete = True
    res = CliRunner().invoke(cli_mod.cli, args, obj={})
    assert res.exit_code == code, res.output
    assert expect in res.output
    assert "SENTINEL" not in res.output + res.stderr


def test_member_text_has_no_em_dash_or_contact_author():
    import inspect
    for src in (inspect.getsource(st), inspect.getsource(cli_mod.selftest_cmd.callback),
                inspect.getsource(cli_mod._print_risky)):
        assert "—" not in src
        assert "contact the author" not in src.lower()


# -- fix round 1 ----------------------------------------------------------------

def test_run_id_line_is_emitted_before_the_first_write_and_lines_stream(server, c, contacts):
    seen = []   # (line, writes so far, run docs so far) at the moment each line is emitted
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP,
                     emit=lambda line: seen.append((line, len(server.writes()), len(run_docs(server)))))
    assert r.status == "PASS"
    first, writes_then, _ = seen[0]
    assert first == f"Run id {RUN}. If this stops early, run: ghl selftest --cleanup {RUN}"
    assert writes_then == 0
    built = next(x for x in seen if x[0].startswith("Built a draft"))
    assert built[2] == 2            # emitted while the workflow and folder exist, not at the end
    assert [x[0] for x in seen] == r.lines


def test_emit_that_raises_never_stops_cleanup(server, c, contacts):
    def emit(line):
        raise BrokenPipeError("stdout closed")
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP, emit=emit)
    assert r.status == "PASS"
    assert_nothing_left(server, contacts, r)


def test_cli_prints_run_id_and_cleanup_command_first(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    res = invoke("selftest")
    assert res.exit_code == 0
    assert "If this stops early, run: ghl selftest --cleanup " in res.output.splitlines()[0]


def _stop_on_third_sleep(stop):
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 3:
            stop()
    return sleep


def test_cli_interrupt_mid_poll_cleans_up_and_prints_leftovers(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    contacts.executes = False
    server.refuse_workflow_delete = True

    def stop():
        raise KeyboardInterrupt
    monkeypatch.setattr(st, "run_selftest",
                        functools.partial(st.run_selftest.func, sleep=_stop_on_third_sleep(stop)))
    res = invoke("selftest")
    assert res.exit_code == 1, res.output
    assert "FAIL: the selftest was stopped before it finished" in res.output
    assert "Still in your account:" in res.output
    run = res.output.splitlines()[0].split("--cleanup ")[1].strip()
    assert f"  workflow ghl-cli setup check {run} (" in res.output
    assert f"): remove with: ghl selftest --cleanup {run}" in res.output
    assert contacts.contacts == {} and run_tags(server, run) == []


def test_cli_sigterm_mid_poll_runs_cleanup_and_restores_handlers(server, contacts, monkeypatch):
    import signal
    wire_cli(monkeypatch, server, contacts)
    contacts.executes = False
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM))}

    def stop():
        # what the OS does on SIGTERM: call the installed handler. If none were
        # installed this is SIG_DFL (not callable) and the run fails loudly.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    monkeypatch.setattr(st, "run_selftest",
                        functools.partial(st.run_selftest.func, sleep=_stop_on_third_sleep(stop)))
    res = invoke("selftest")
    assert res.exit_code == 1, res.output
    assert "FAIL: the selftest was stopped before it finished" in res.output
    assert_nothing_left(server, contacts, run=res.output.splitlines()[0].split("--cleanup ")[1].strip())
    assert {sig: signal.getsignal(sig) for sig in before} == before


@pytest.mark.parametrize("exc,nothing", [
    ("400 bad request", True), ("404 not found", True), ("422 invalid", True),
    ("408 request timeout", False), ("429 too many", False),
    ("500 server error", False), ("502 bad gateway", False), ("504 gateway timeout", False),
    ("401 Command timed out", False), ("401 Invalid JWT", True),
])
def test_only_a_clear_refusal_means_no_contact_was_made(exc, nothing):
    import requests
    assert st._nothing_made(requests.HTTPError(exc)) is nothing


def test_timeout_and_connection_errors_mean_a_contact_may_exist():
    import requests
    assert st._nothing_made(requests.Timeout("read timed out")) is False
    assert st._nothing_made(requests.ConnectionError("reset")) is False


def test_contact_made_then_502_is_found_and_deleted(server, c, contacts):
    """api.post retries a 5xx: the create can land up to 3 times and still raise."""
    import requests
    real_post = contacts.post

    def post(path, data=None, version=None):
        if path == "/contacts/":
            for _ in range(3):
                real_post(path, data, version)
            raise requests.HTTPError("502 Server Error: Bad Gateway")
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL" and "test contact" in r.reason
    assert r.contact_made is True
    assert len(contacts.created) == 3 and sorted(contacts.deleted) == sorted(contacts.created)
    assert_nothing_left(server, contacts, r)


def test_contact_502_with_nothing_found_is_reported(server, c, contacts):
    import requests
    real_post = contacts.post

    def post(path, data=None, version=None):
        if path == "/contacts/":
            raise requests.HTTPError("502 Server Error: Bad Gateway")
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    assert r.leftovers == [f"contact {NAME} (id unknown): remove with: ghl selftest --cleanup {RUN}"]


def test_retried_create_duplicate_is_found_by_the_proof_search(server, c, contacts):
    """First attempt lands but times out, the retry lands too: only the second id comes back,
    and the first search lags. The proof step's second search finds and deletes the first."""
    real_post = contacts.post
    searches = []

    def post(path, data=None, version=None):
        if path == "/contacts/":
            real_post(path, data, version)          # lost answer, contact exists
            return real_post(path, data, version)   # the retry's answer
        if path == "/contacts/search":
            searches.append(1)
            if len(searches) == 1:
                return {"contacts": [], "total": 0}  # search lags the brand-new contact
        return real_post(path, data, version)
    contacts.post = post
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "PASS", (r.reason, r.leftovers)
    assert len(contacts.created) == 2 and sorted(contacts.deleted) == sorted(contacts.created)
    assert len(searches) == 2
    assert_nothing_left(server, contacts, r)


def test_duplicate_that_will_not_delete_is_reported(server, c, contacts):
    real_post, real_delete = contacts.post, contacts.delete
    ids = []

    def post(path, data=None, version=None):
        if path == "/contacts/":
            ids.append(real_post(path, data, version)["contact"]["id"])
        return real_post(path, data, version)

    def delete(path, data=None, version=None):
        if path == f"/contacts/{ids[0]}":
            raise RuntimeError("refused")
        return real_delete(path, data, version)
    contacts.post, contacts.delete = post, delete
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.status == "FAIL"
    assert r.leftovers == [f"contact {NAME} ({ids[0]}): remove with: ghl selftest --cleanup {RUN}"]


# -- final review: a big account shows it is working --------------------------------

def test_scan_says_how_many_published_workflows_it_will_read(server, c):
    member_wf(server, "wf-a", "Alpha", "contact_created")
    member_wf(server, "wf-b", "Beta", "form_submission")
    member_wf(server, "wf-d", "Draft one", "contact_created", status="draft")
    folder(server, "fold-1", "A folder")
    member_wf(server, "wf-c", "In a folder", "contact_created", parent="fold-1")
    seen = []
    risky, complete = scan_risky(c, emit=seen.append)
    assert seen == ["Reading 3 published workflows..."]     # drafts and folders not counted
    assert {r.name for r in risky} == {"Alpha", "In a folder"} and complete   # form_submission is safe


def test_scan_singular_and_silent_when_there_is_nothing_to_read(server, c):
    seen = []
    scan_risky(c, emit=seen.append)
    assert seen == []                                     # nothing published: no line
    member_wf(server, "wf-a", "Alpha", "contact_created")
    scan_risky(c, emit=seen.append)
    assert seen == ["Reading 1 published workflow..."]


def test_progress_line_comes_before_the_per_workflow_reads(server, c):
    member_wf(server, "wf-a", "Alpha", "contact_created")
    member_wf(server, "wf-b", "Beta", "contact_created")
    seen = []
    scan_risky(c, emit=lambda line: seen.append((line, len(server.calls))))
    reads_before = sum(1 for (_, m, p, _) in server.calls[:seen[0][1]]
                       if m == "GET" and ("/wf-a" in p or "/wf-b" in p))
    assert reads_before == 0


def test_selftest_streams_the_progress_line_after_the_run_id(server, c, contacts):
    member_wf(server, "wf-a", "Alpha", "form_submission")
    r = run_selftest(c, contacts, run_id=RUN, sleep=NOSLEEP)
    assert r.lines[0].startswith(f"Run id {RUN}.")
    assert r.lines[1] == "Reading 1 published workflow..."


def test_cli_check_only_prints_the_progress_line_first(server, contacts, monkeypatch):
    wire_cli(monkeypatch, server, contacts)
    for i in range(3):
        member_wf(server, f"wf-{i}", f"Flow {i}", "form_submission")
    res = invoke("selftest", "--check-only")
    assert res.exit_code == 0, res.output
    assert res.output.splitlines()[0] == "Reading 3 published workflows..."
    assert server.writes() == []
