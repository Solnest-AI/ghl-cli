"""Tests for the fake GHL routes the selftest needs (Task 5): publish, delete,
folder listing, whoami and the contacts double. Test infrastructure only."""
import pytest

from cli_anything.gohighlevel.utils import workflow_builder as wb
from cli_anything.gohighlevel.utils.workflow_spec import note_step
from fake_contacts import FakeContacts
from fake_ghl import FakeGHL

LOC = "LocSourceAAAAAAAAAA1"
WF = "aaaaaaaa-0000-4000-8000-00000000000a"
WF2 = "bbbbbbbb-0000-4000-8000-00000000000b"
FOLDER = "ffffffff-0000-4000-8000-00000000000f"


def tag_trigger(tid, wf_id, tag, ttype="contact_tag"):
    return {"id": tid, "workflow_id": wf_id, "type": ttype, "name": "t", "deleted": False,
            "conditions": [{"operator": "index-of-true", "field": "tagsAdded", "value": tag,
                            "title": "Tag Added", "type": "select", "id": "tag-added"}]}


def fire_workflow(srv, wf_id="wf-fire", status="published", trig_tag="zz-fire", *, loc=LOC):
    steps = [wb.tag_step("Mark fired", ["zz-fired"]), note_step("Leave a note", "zz note body")]
    srv.add_workflow(loc, wf_id, "fire", steps, status=status,
                     triggers=[tag_trigger("trig-" + wf_id, wf_id, trig_tag)])


@pytest.fixture
def srv():
    return FakeGHL()


@pytest.fixture
def c(srv):
    return srv.client(LOC)


# -- change-status ---------------------------------------------------------

def test_change_status_publishes_and_unpublishes(srv, c):
    srv.add_workflow(LOC, WF, "w", [])
    r = c.request("PUT", f"/workflow/{LOC}/change-status/{WF}",
                  {"status": "published", "updatedBy": "UserBBBBBBBBBBBBBBBB"})
    assert not (r or {}).get("_error")
    assert c.request("GET", f"/workflow/{LOC}/{WF}")["status"] == "published"
    c.request("PUT", f"/workflow/{LOC}/change-status/{WF}", {"status": "draft", "updatedBy": "x"})
    assert srv.docs[(LOC, WF)]["status"] == "draft"


def test_change_status_unknown_workflow_is_404(srv, c):
    r = c.request("PUT", f"/workflow/{LOC}/change-status/nope", {"status": "published"})
    assert r["_error"] and r["code"] == 404


def test_fail_publish_knob_returns_500_and_leaves_draft(srv, c):
    srv.add_workflow(LOC, WF, "w", [])
    srv.fail_publish = True
    r = c.request("PUT", f"/workflow/{LOC}/change-status/{WF}", {"status": "published"})
    assert r["_error"] and r["code"] == 500
    assert srv.docs[(LOC, WF)]["status"] == "draft"


def test_change_status_validates_its_body(srv, c):
    srv.add_workflow(LOC, WF, "w", [])
    path = f"/workflow/{LOC}/change-status/{WF}"
    for bad in ({}, {"updatedBy": "u"}, {"status": "live", "updatedBy": "u"}, {"status": "published"}):
        r = c.request("PUT", path, bad)
        assert r["_error"] and r["code"] == 400, bad
    assert srv.docs[(LOC, WF)]["status"] == "draft"


# -- delete workflow / folder ---------------------------------------------

def test_delete_workflow_removes_doc_and_its_triggers_only(srv, c):
    srv.add_workflow(LOC, WF, "w", [], triggers=[tag_trigger("t1", WF, "a")])
    srv.add_workflow(LOC, WF2, "other", [], triggers=[tag_trigger("t2", WF2, "b")])
    r = c.request("DELETE", f"/workflow/{LOC}/{WF}")
    assert not (r or {}).get("_error")
    assert (LOC, WF) not in srv.docs and (LOC, WF2) in srv.docs
    assert srv.triggers_of(WF) == [] and len(srv.triggers_of(WF2)) == 1
    assert c.request("GET", f"/workflow/{LOC}/{WF}")["code"] == 404


def test_delete_works_on_a_folder_through_the_same_route(srv, c):
    fid = c.request("POST", f"/workflow/{LOC}", {"name": "F", "type": "directory"})["id"]
    c.request("DELETE", f"/workflow/{LOC}/{fid}")
    assert (LOC, fid) not in srv.docs


def test_delete_unknown_workflow_is_404(srv, c):
    assert c.request("DELETE", f"/workflow/{LOC}/nope")["code"] == 404


def test_refuse_workflow_delete_knob_returns_500_and_keeps_doc(srv, c):
    srv.add_workflow(LOC, WF, "w", [])
    srv.refuse_workflow_delete = True
    r = c.request("DELETE", f"/workflow/{LOC}/{WF}")
    assert r["_error"] and r["code"] == 500
    assert (LOC, WF) in srv.docs


# -- delete tag ------------------------------------------------------------

def test_delete_tag_removes_it_and_ids_stay_stable_across_deletes(srv, c):
    for name in ("alpha", "beta", "gamma"):
        c.request("POST", f"/workflow/{LOC}/tags/create", {"tag": name})
    rows = c.request("GET", f"/locations/{LOC}/tags")["tags"]
    ids = {r["name"]: r["id"] for r in rows}
    assert c.request("DELETE", f"/locations/{LOC}/tags/{ids['alpha']}") == {"succeeded": True}
    assert c.request("DELETE", f"/locations/{LOC}/tags/{ids['beta']}") == {"succeeded": True}
    left = [r["name"] for r in c.request("GET", f"/locations/{LOC}/tags")["tags"]]
    assert left == ["gamma"]


def test_delete_tag_refuses_unknown_id_and_bare_name(srv, c):
    c.request("POST", f"/workflow/{LOC}/tags/create", {"tag": "alpha"})
    for bad in ("alpha", "tag-nope", "nope"):
        r = c.request("DELETE", f"/locations/{LOC}/tags/{bad}")
        assert r["_error"] and r["code"] == 404, bad
    assert [t["name"] for t in c.request("GET", f"/locations/{LOC}/tags")["tags"]] == ["alpha"]
    # a second delete of an already-deleted id is also refused
    c.request("DELETE", f"/locations/{LOC}/tags/tag-alpha")
    assert c.request("DELETE", f"/locations/{LOC}/tags/tag-alpha")["code"] == 404


# -- list: parentId, folders_hide_children, count override ------------------

def _tree(srv):
    srv.add_workflow(LOC, FOLDER, "F", [], parent_id=None)
    srv.docs[(LOC, FOLDER)]["type"] = "directory"
    srv.add_workflow(LOC, WF, "inside", [], parent_id=FOLDER)
    srv.add_workflow(LOC, WF2, "top", [])


def test_list_without_parent_id_still_returns_every_row(srv, c):
    _tree(srv)
    rows = c.request("GET", f"/workflow/{LOC}/list?limit=100")["rows"]
    assert {r["id"] for r in rows} == {FOLDER, WF, WF2}


def test_list_with_parent_id_returns_only_that_folders_children(srv, c):
    _tree(srv)
    rows = c.request("GET", f"/workflow/{LOC}/list?limit=100&parentId={FOLDER}")["rows"]
    assert [r["id"] for r in rows] == [WF]


def test_folders_hide_children_returns_only_top_level_rows(srv, c):
    _tree(srv)
    srv.folders_hide_children = True
    rows = c.request("GET", f"/workflow/{LOC}/list?limit=100")["rows"]
    assert {r["id"] for r in rows} == {FOLDER, WF2}
    # opening the folder still shows what is inside
    kids = c.request("GET", f"/workflow/{LOC}/list?limit=100&parentId={FOLDER}")["rows"]
    assert [r["id"] for r in kids] == [WF]


def test_list_count_override_reports_a_larger_count_than_rows(srv, c):
    _tree(srv)
    srv.list_count_override = 250
    r = c.request("GET", f"/workflow/{LOC}/list?limit=100")
    assert r["count"] == 250 and len(r["rows"]) == 3


# -- whoami ----------------------------------------------------------------

def test_oauth_login_current_returns_a_user_id(srv, c):
    assert c.request("GET", "/oauth/2/login/current") == {"userId": "UserBBBBBBBBBBBBBBBB"}


def test_oauth_login_current_refused_under_refuse_auth(srv, c):
    srv.refuse_auth = True
    assert c.request("GET", "/oauth/2/login/current") is None


# -- FakeContacts ----------------------------------------------------------

def _contacts(srv, **kw):
    ct = FakeContacts(srv, LOC)
    for k, v in kw.items():
        setattr(ct, k, v)
    return ct


def _new(ct):
    return ct.post("/contacts/", data={"locationId": LOC, "firstName": "ZZ"})["contact"]["id"]


def test_contact_create_get_delete_and_bookkeeping(srv):
    ct = _contacts(srv)
    cid = _new(ct)
    assert ct.created == [cid] and ct.deleted == []
    assert ct.get(f"/contacts/{cid}")["contact"]["id"] == cid
    assert ct.get(f"/contacts/{cid}")["contact"]["tags"] == []
    ct.delete(f"/contacts/{cid}")
    assert ct.deleted == [cid]
    with pytest.raises(Exception):
        ct.get(f"/contacts/{cid}")


def test_contact_tags_add_and_notes_empty(srv):
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["x", "y"]})
    assert ct.get(f"/contacts/{cid}")["contact"]["tags"] == ["x", "y"]
    assert ct.get(f"/contacts/{cid}/notes")["notes"] == []


def test_published_tag_trigger_runs_tag_and_note_steps(srv):
    fire_workflow(srv)
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert "zz-fired" in ct.get(f"/contacts/{cid}")["contact"]["tags"]
    notes = ct.get(f"/contacts/{cid}/notes")["notes"]
    assert [n["body"] for n in notes] and "zz note body" in notes[0]["body"]


def test_draft_workflow_does_not_run(srv):
    fire_workflow(srv, status="draft")
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert ct.get(f"/contacts/{cid}")["contact"]["tags"] == ["zz-fire"]
    assert ct.get(f"/contacts/{cid}/notes")["notes"] == []


def test_workflow_published_after_creation_runs(srv):
    fire_workflow(srv, status="draft")
    srv.client(LOC).request("PUT", f"/workflow/{LOC}/change-status/wf-fire", {"status": "published", "updatedBy": "u"})
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert "zz-fired" in ct.get(f"/contacts/{cid}")["contact"]["tags"]


def test_non_matching_tag_and_other_location_do_not_fire(srv):
    fire_workflow(srv, trig_tag="zz-fire")
    fire_workflow(srv, wf_id="wf-other-loc", loc="LocOtherCCCCCCCCCC3")
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["something-else"]})
    assert ct.get(f"/contacts/{cid}")["contact"]["tags"] == ["something-else"]
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    # only this location's workflow ran: one note, not two
    assert len(ct.get(f"/contacts/{cid}/notes")["notes"]) == 1


def test_non_tag_trigger_types_do_not_fire(srv):
    fire_workflow(srv)
    srv.triggers["trig-wf-fire"]["type"] = "contact_created"
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert ct.get(f"/contacts/{cid}/notes")["notes"] == []


def test_executes_false_means_nothing_ever_fires(srv):
    fire_workflow(srv)
    ct = _contacts(srv, executes=False)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert ct.get(f"/contacts/{cid}")["contact"]["tags"] == ["zz-fire"]
    assert ct.get(f"/contacts/{cid}/notes")["notes"] == []


def test_deleted_workflow_trigger_no_longer_fires(srv):
    fire_workflow(srv)
    srv.client(LOC).request("DELETE", f"/workflow/{LOC}/wf-fire")
    ct = _contacts(srv)
    cid = _new(ct)
    ct.post(f"/contacts/{cid}/tags", data={"tags": ["zz-fire"]})
    assert ct.get(f"/contacts/{cid}/notes")["notes"] == []
