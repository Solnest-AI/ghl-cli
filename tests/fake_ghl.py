"""In-memory stand-in for GoHighLevel's internal workflow API. Test utility only.

Routes and shapes mirror what the live API returned on 2026-10-11:
  GET  /workflow/{loc}/{id}                  -> doc, steps inline in workflowData.templates
  GET  /workflow/{loc}/trigger?workflowId=X  -> list of that workflow's triggers
  PUT  /workflow/{loc}/{id}                  -> rejected unless body.version == current
  POST /workflow/{loc}                       -> {"id": ...}
  POST /workflow/{loc}/trigger               -> {"id": ...}
  PUT  /workflow/{loc}/trigger/{id}
  POST /workflow/{loc}/tags/create
  PUT  /workflow/{loc}/change-status/{id}    -> publish / unpublish (knob fail_publish)
  DELETE /workflow/{loc}/{id}                -> workflow or folder, plus its triggers
  DELETE /locations/{loc}/tags/{id}          -> {"succeeded": True}
  GET  /workflow/{loc}/list?parentId=X       -> only X's children
  GET  /oauth/2/login/current                -> {"userId": ...}
The client contract matches InternalGHLClient.request(): None means GHL refused
auth (401/403), a dict with "_error" means any other HTTP failure.

PUT is deliberately pessimistic: settings missing from the body are reset to
GHL's defaults. Nobody has proven GHL keeps them, so code must send them back.
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Optional

SETTING_DEFAULTS = {
    "allowMultiple": False,
    "allowMultipleOpportunity": False,
    "autoMarkAsRead": False,
    "removeContactFromLastStep": True,
    "stopOnResponse": False,
    "timezone": "account",
    "window": None,
    "meta": None,
}


def make_doc(loc: str, wf_id: str, name: str, templates: list, *, status: str = "draft",
             version: int = 3, parent_id: Optional[str] = None, **settings: Any) -> dict:
    """A workflow document with every key the live GET returned."""
    doc = {
        "__v": 0, "_id": wf_id, "id": wf_id,
        "companyAge": 48, "companyId": "CompanyAAAAAAAAAAAAAA",
        "createdAt": "2026-08-26T00:06:19.000Z", "updatedAt": "2026-08-26T00:06:20.000Z",
        "creationSource": "builder", "originType": "user", "dataVersion": 7, "deleted": False,
        "eventStartDate": "", "isTriggerBucketMigrated": True, "locationId": loc,
        "filePath": f"location/{loc}/workflows/{wf_id}/{version}",
        "fileUrl": f"https://firebasestorage.googleapis.com/v0/b/x/o/{wf_id}?alt=media",
        "triggersFilePath": f"location/{loc}/workflow-triggers/{wf_id}/{version}",
        "name": name, "parentId": parent_id, "permission": 380,
        "permissionMeta": {"canRead": True, "canWrite": True},
        "status": status, "type": "workflow", "updatedBy": "UserBBBBBBBBBBBBBBBB",
        "version": version, "workflowNote": "",
        "workflowData": {"templates": copy.deepcopy(templates)},
    }
    doc.update(SETTING_DEFAULTS)
    doc.update(settings)
    return doc


class FakeGHL:
    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.triggers: dict[str, dict] = {}
        self.tags: dict[str, set] = {}
        self.calls: list[tuple[str, str, str, Any]] = []
        self.drop_step_saves = False      # accept PUTs but keep the old steps
        self.fail_step_saves = False      # answer PUTs with a 500
        self.refuse_tags = False          # answer tags/create with a 500
        self.refuse_auth = False          # every request except the agency search returns None (GHL refused auth)
        self.fail_publish = False         # answer change-status PUTs with a 500
        self.refuse_workflow_delete = False  # answer workflow/folder DELETEs with a 500
        # List-route knobs. With folders_hide_children the unfiltered list returns
        # only rows with no parentId (nested drafts hide, as seen live 2026-10-18);
        # list_count_override reports a larger "count" than the rows returned.
        self.folders_hide_children = False
        self.list_count_override: Optional[int] = None
        # Locations the agency search route returns: [{"id", "name"}].
        self.locations: list[dict] = []
        # Replaces the /workflow/{loc}/list answer when set (API drift tests).
        self.list_override: Any = None
        # Live GHL lists a just-created tag only a moment later (2026-10-18):
        # a new tag stays out of the tag list for this many list reads.
        self.tag_list_lag = 0
        self._pending_tags: dict[tuple[str, str], int] = {}
        # Per-location forms / surveys / calendars / pipelines, as the internal
        # list routes return them: {"forms": [{"id", "name"}], ...,
        # "pipelines": [{"id", "name", "stages": [{"id", "name"}]}]}.
        self.assets: dict[str, dict[str, list]] = {}

    # -- setup -------------------------------------------------------------
    def add_workflow(self, loc: str, wf_id: str, name: str, templates: list,
                     triggers: tuple = (), **kw: Any) -> dict:
        doc = make_doc(loc, wf_id, name, templates, **kw)
        self.docs[(loc, wf_id)] = doc
        for t in triggers:
            self.triggers[t["id"]] = copy.deepcopy(t)
        return doc

    def client(self, loc: str) -> "FakeClient":
        return FakeClient(self, loc)

    def writes(self) -> list[tuple[str, str, str, Any]]:
        return [c for c in self.calls if c[1] != "GET"]

    def triggers_of(self, wf_id: str) -> list[dict]:
        return [t for t in self.triggers.values()
                if t.get("workflow_id") == wf_id and not t.get("deleted")]

    # -- routing -----------------------------------------------------------
    def handle(self, loc: str, method: str, path: str, body: Any) -> Any:
        self.calls.append((loc, method, path, copy.deepcopy(body)))
        m = re.fullmatch(r"/locations/search\?.*", path)
        if m and method == "GET":
            # Agency search stays open under refuse_auth: an agency owner whose
            # location is not in their session can still list the ones that are.
            return {"locations": copy.deepcopy(self.locations)}
        if self.refuse_auth:
            return None
        if self.list_override is not None and re.fullmatch(r"/workflow/([^/?]+)/list(\?.*)?", path):
            return copy.deepcopy(self.list_override)
        if path == "/oauth/2/login/current" and method == "GET":
            return {"userId": "UserBBBBBBBBBBBBBBBB"}
        m = re.fullmatch(r"/workflow/([^/?]+)/change-status/([^/?]+)", path)
        if m and method == "PUT":
            doc = self.docs.get((m.group(1), m.group(2)))
            if doc is None:
                return {"_error": True, "code": 404, "message": '{"msg":"Not found"}'}
            if self.fail_publish:
                return {"_error": True, "code": 500, "message": "Internal Server Error"}
            body = body or {}
            if body.get("status") not in ("draft", "published") or not body.get("updatedBy"):
                return {"_error": True, "code": 400, "message": "fake: status and updatedBy required"}
            doc["status"] = body["status"]
            return {}
        m = re.fullmatch(r"/workflow/([^/?]+)/trigger\?workflowId=(.+)", path)
        if m and method == "GET":
            return copy.deepcopy(self.triggers_of(m.group(2)))
        m = re.fullmatch(r"/workflow/([^/?]+)/trigger", path)
        if m and method == "POST":
            tid = uuid.uuid4().hex[:20]
            stored = {**copy.deepcopy(body), "id": tid}
            # GHL takes workflowId on create and stores it as workflow_id (live read-back).
            stored.setdefault("workflow_id", body.get("workflowId"))
            self.triggers[tid] = stored
            return {"id": tid}
        m = re.fullmatch(r"/workflow/([^/?]+)/trigger/([^/?]+)", path)
        if m and method == "PUT":
            self.triggers[m.group(2)].update(copy.deepcopy(body))
            return {}
        m = re.fullmatch(r"/workflow/([^/?]+)/tags/create", path)
        if m and method == "POST":
            if self.refuse_tags:
                return {"_error": True, "code": 500, "message": "Internal Server Error"}
            if self.tag_list_lag:
                self._pending_tags.setdefault((m.group(1), body["tag"]), self.tag_list_lag)
            else:
                self.tags.setdefault(m.group(1), set()).add(body["tag"])
            # Live GHL answers HTTP 200 text/plain "OK" (2026-10-18); the client
            # hands a non-JSON success back as {"_text": ...}.
            return {"_text": "OK"}
        m = re.fullmatch(r"/locations/([^/?]+)/tags", path)
        if m and method == "GET":
            for key in list(self._pending_tags):
                if key[0] == m.group(1):
                    self._pending_tags[key] -= 1
                    if self._pending_tags[key] < 0:
                        self.tags.setdefault(key[0], set()).add(key[1])
                        del self._pending_tags[key]
            return {"tags": [{"id": f"tag-{t}", "name": t, "locationId": m.group(1)}
                             for t in sorted(self.tags.get(m.group(1), set()))]}
        m = re.fullmatch(r"/locations/([^/?]+)/tags/([^/?]+)", path)
        if m and method == "DELETE":
            # Only ids from the tag list are accepted; a bare name or an unknown
            # id is refused, as the real API would.
            tid = m.group(2)
            name = tid[len("tag-"):] if tid.startswith("tag-") else None
            if name is None or name not in self.tags.get(m.group(1), set()):
                return {"_error": True, "code": 404, "message": '{"msg":"Not found"}'}
            self.tags[m.group(1)].discard(name)
            return {"succeeded": True}
        m = re.fullmatch(r"/(forms|surveys|calendars|opportunities/pipelines)/?\?locationId=([^&]+)(.*)", path)
        if m and method == "GET":
            key = "pipelines" if m.group(1).endswith("pipelines") else m.group(1)
            rows = copy.deepcopy(self.assets.get(m.group(2), {}).get(key, []))
            if key in ("forms", "surveys"):
                # Live 2026-10-18: both page with skip/limit and report total;
                # surveys answer 422 above limit=50; forms key rows by "_id".
                q = dict(re.findall(r"[?&](\w+)=([^&]*)", "?x=1" + m.group(3)))
                limit, skip = int(q.get("limit", 10)), int(q.get("skip", 0))
                if key == "surveys" and limit > 50:
                    return {"_error": True, "code": 422,
                            "message": '{"message":["limit must not be greater than 50"]}'}
                page = rows[skip:skip + limit]
                if key == "forms":
                    page = [{"_id": r.pop("id"), **r} for r in page]
                return {key: page, "total": len(rows), "traceId": "t"}
            return {key: rows, "traceId": "t"}
        m = re.fullmatch(r"/workflow/([^/?]+)/list(\?.*)?", path)
        if m and method == "GET":
            rows = [{"id": d["id"], "name": d["name"], "type": d["type"],
                     "status": d.get("status"), "parentId": d.get("parentId")}
                    for (loc_, _), d in self.docs.items() if loc_ == m.group(1)]
            parent = dict(re.findall(r"[?&](\w+)=([^&]*)", m.group(2) or "")).get("parentId")
            if parent:
                rows = [r for r in rows if r["parentId"] == parent]
            elif self.folders_hide_children:
                rows = [r for r in rows if not r["parentId"]]
            count = self.list_count_override if self.list_count_override is not None else len(rows)
            return {"rows": rows, "count": count, "isLocationRateLimited": False}
        m = re.fullmatch(r"/workflow/([^/?]+)", path)
        if m and method == "POST":
            wf_id = str(uuid.uuid4())
            doc = make_doc(m.group(1), wf_id, body["name"], [], version=1,
                           parent_id=body.get("parentId"))
            if body.get("type") == "directory":
                doc["type"] = "directory"
            self.docs[(m.group(1), wf_id)] = doc
            return {"id": wf_id}
        m = re.fullmatch(r"/workflow/([^/?]+)/([^/?]+)", path)
        if m:
            doc = self.docs.get((m.group(1), m.group(2)))
            if doc is None:
                return {"_error": True, "code": 404, "message": '{"msg":"Not found"}'}
            if method == "GET":
                return copy.deepcopy(doc)
            if method == "DELETE":
                if self.refuse_workflow_delete:
                    return {"_error": True, "code": 500, "message": "Internal Server Error"}
                del self.docs[(m.group(1), m.group(2))]
                for tid in [t["id"] for t in self.triggers_of(m.group(2))]:
                    del self.triggers[tid]
                return {}
            if method == "PUT":
                return self._put(doc, body)
        return {"_error": True, "code": 404, "message": f"fake: no route {method} {path}"}

    def _put(self, doc: dict, body: dict) -> Any:
        if self.fail_step_saves:
            return {"_error": True, "code": 500, "message": "Internal Server Error"}
        if body.get("version") != doc["version"]:
            return {"_error": True, "code": 400, "message": "version mismatch"}
        if not self.drop_step_saves and "workflowData" in body:
            doc["workflowData"] = {"templates": copy.deepcopy(body["workflowData"]["templates"])}
        doc["name"] = body.get("name", doc["name"])
        for key, default in SETTING_DEFAULTS.items():
            doc[key] = copy.deepcopy(body.get(key, default))
        doc["version"] += 1
        return copy.deepcopy(doc)


class FakeClient:
    """Same surface as InternalGHLClient that workflow code may use."""

    def __init__(self, server: FakeGHL, location_id: str):
        self.server = server
        self.location_id = location_id
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def request(self, method: str, path: str, body: Any = None) -> Any:
        self._call_count += 1
        return self.server.handle(self.location_id, method, path, body)

    def whoami(self) -> Any:
        """Same as InternalGHLClient.whoami: the session's identity, None if refused."""
        return self.request("GET", "/oauth/2/login/current")

    def list_accessible_locations(self, company_id: str) -> list:
        r = self.request("GET", f"/locations/search?companyId={company_id}&limit=100")
        if isinstance(r, dict) and not r.get("_error"):
            return r.get("locations") or r.get("data") or []
        return []
