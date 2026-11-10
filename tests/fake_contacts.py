"""In-memory contacts double for tests that exercise `ghl selftest`. Test utility only.

Matches the signatures of cli_anything.gohighlevel.utils.ghl_client get/post/delete
for /contacts/, /contacts/search, /contacts/{id}, /contacts/{id}/tags and
/contacts/{id}/notes, and
fires workflows the way live GHL did on 2026-10-18: when a tag is added and a
PUBLISHED workflow at this location has a contact_tag trigger on that tag, the
workflow's add_contact_tag and add_notes steps run on the contact. Execution is
synchronous, so a selftest poll finds the result on its first read.

    server = FakeGHL()
    contacts = FakeContacts(server, LOC)
    monkeypatch.setattr(api, "get", contacts.get)   # post / delete the same way
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

import requests

from fake_ghl import FakeGHL

_MAX_DEPTH = 5   # a workflow that tags a contact into another workflow's trigger


class FakeContacts:
    def __init__(self, server: FakeGHL, loc: str):
        self.server = server
        self.loc = loc
        self.executes = True                 # False: no workflow ever fires
        self.refuse_delete = False           # True: DELETE /contacts/{id} answers 500
        self.contacts: dict[str, dict] = {}
        self.notes: dict[str, list[dict]] = {}
        self.created: list[str] = []         # contact ids, in creation order
        self.deleted: list[str] = []         # contact ids, in deletion order
        self._n = 0

    # -- api.get / api.post / api.delete ------------------------------------
    def get(self, path: str, params: Optional[dict] = None, version: Optional[str] = None) -> dict:
        m = re.fullmatch(r"/contacts/([^/?]+)/notes", path)
        if m:
            self._need(m.group(1))
            return {"notes": copy.deepcopy(self.notes.get(m.group(1), []))}
        m = re.fullmatch(r"/contacts/([^/?]+)", path)
        if m:
            return {"contact": copy.deepcopy(self._need(m.group(1)))}
        raise self._http(404, f"fake: no route GET {path}")

    def post(self, path: str, data: Optional[dict] = None, version: Optional[str] = None) -> dict:
        data = data or {}
        if path in ("/contacts/", "/contacts"):
            self._n += 1
            cid = f"FakeContact{self._n:09d}"
            self.contacts[cid] = {
                "id": cid, "locationId": data.get("locationId", self.loc),
                "firstName": data.get("firstName", ""), "lastName": data.get("lastName", ""),
                "tags": [],
            }
            self.created.append(cid)
            return {"contact": copy.deepcopy(self.contacts[cid])}
        if path == "/contacts/search":
            # Free-text query over the name, as the CLI's `contacts search` sends it
            # ({"locationId", "query", "pageLimit"}); answers {"contacts": [...]}.
            q = str(data.get("query") or "").lower()
            rows = [copy.deepcopy(c) for c in self.contacts.values()
                    if q in f"{c['firstName']} {c['lastName']}".lower()]
            return {"contacts": rows[:int(data.get("pageLimit") or 20)], "total": len(rows)}
        m = re.fullmatch(r"/contacts/([^/?]+)/tags", path)
        if m:
            contact = self._need(m.group(1))
            self._add_tags(contact, list(data.get("tags") or []), 0)
            return {"tags": list(contact["tags"])}
        raise self._http(404, f"fake: no route POST {path}")

    def delete(self, path: str, data: Optional[dict] = None, version: Optional[str] = None) -> dict:
        m = re.fullmatch(r"/contacts/([^/?]+)", path)
        if m:
            self._need(m.group(1))
            if self.refuse_delete:
                raise self._http(500, "Internal Server Error")
            del self.contacts[m.group(1)]
            self.notes.pop(m.group(1), None)
            self.deleted.append(m.group(1))
            return {"succeeded": True}
        raise self._http(404, f"fake: no route DELETE {path}")

    # -- workflow execution --------------------------------------------------
    def _add_tags(self, contact: dict, tags: list[str], depth: int) -> None:
        fresh = [t for t in tags if t not in contact["tags"]]
        contact["tags"].extend(fresh)
        # Like live GHL, tagging a contact makes the tag exist at the location.
        self.server.tags.setdefault(self.loc, set()).update(fresh)
        if not self.executes or depth >= _MAX_DEPTH:
            return
        for tag in fresh:
            for doc in self._published_for(tag):
                self._run(doc, contact, depth)

    def _published_for(self, tag: str) -> list[dict]:
        out = []
        for (loc, wf_id), doc in sorted(self.server.docs.items()):
            if loc != self.loc or doc.get("status") != "published" or doc.get("type") != "workflow":
                continue
            for t in self.server.triggers_of(wf_id):
                if t.get("type") != "contact_tag":
                    continue
                if any(c.get("field") == "tagsAdded" and str(c.get("value", "")).lower() == tag.lower()
                       for c in t.get("conditions") or []):
                    out.append(doc)
                    break
        return out

    def _run(self, doc: dict, contact: dict, depth: int) -> None:
        for step in doc["workflowData"]["templates"]:
            attrs = step.get("attributes") or {}
            if step.get("type") == "add_contact_tag":
                self._add_tags(contact, list(attrs.get("tags") or []), depth + 1)
            elif step.get("type") == "add_notes":
                # The note text lives in attributes.html (live reads, 2026-10-17).
                notes = self.notes.setdefault(contact["id"], [])
                notes.append({"id": f"FakeNote{len(notes) + 1:09d}", "body": attrs.get("html", ""),
                              "contactId": contact["id"]})

    # -- helpers -------------------------------------------------------------
    def _need(self, cid: str) -> dict:
        if cid not in self.contacts:
            raise self._http(404, "contact not found")
        return self.contacts[cid]

    @staticmethod
    def _http(code: int, msg: str) -> requests.HTTPError:
        return requests.HTTPError(f"{code} {msg}")
