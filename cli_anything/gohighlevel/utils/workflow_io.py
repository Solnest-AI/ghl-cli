"""Read, edit and clone EXISTING GoHighLevel workflows (internal API).

The builder in workflow_builder.py only creates. This module works on workflows
that already exist. Routes, read off the live API 2026-10-11:

  GET /workflow/{loc}/{id}                  the doc; steps come back INLINE in
                                            workflowData.templates
  GET /workflow/{loc}/trigger?workflowId=X  that workflow's triggers, a list
                                            (without ?workflowId GHL answers 422)
  PUT /workflow/{loc}/{id}                  must carry the CURRENT version

drleadflow/ghl-automation-builder documents triggers as unreadable over REST and
reads them from Firebase Storage. Both halves are wrong here: the trigger route
above works, and the storage bucket answers 400 to direct reads.

Every write here re-reads afterwards and compares, because GHL can answer 200
and still not hold what was sent.

EXPERIMENTAL: Gated behind --experimental flag in CLI.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from cli_anything.gohighlevel.utils.ghl_internal_client import STRIP_KEYS

EXPORT_FORMAT = "ghl-cli-workflow/1"

# Pause between tag-list re-reads; module-level so tests can skip the wait.
_sleep = time.sleep

# Server bookkeeping on a trigger. Copying these onto a new trigger would point
# it at the source's history.
_TRIGGER_SERVER_KEYS = frozenset([
    "id", "date_added", "date_updated", "last_conditions_change_at",
    "company_id", "company_age",
])

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_FIRESTORE_ID = re.compile(r"[A-Za-z0-9]{20}")
_OBJECT_ID = re.compile(r"[0-9a-f]{24}")


class WorkflowIOError(RuntimeError):
    """GHL refused, rejected, or did not keep a workflow read or write."""


class DriftError(WorkflowIOError):
    """The live workflow moved on since the file being applied was exported."""


class NeedsConfirm(WorkflowIOError):
    """The edit would change a PUBLISHED workflow, which live contacts are in."""

    def __init__(self, message: str, diff: dict):
        super().__init__(message)
        self.diff = diff


class VerifyError(WorkflowIOError):
    """GHL accepted a save but does not hold what was sent."""

    def __init__(self, message: str, snapshot: str):
        super().__init__(message)
        self.snapshot = snapshot


class ForeignRefsError(WorkflowIOError):
    """A cross-account clone names ids that do not exist in the target.

    GHL rejects the save outright ("Referenced asset(s) missing from this
    location", HTTP 400, seen live 2026-10-18), so this is raised before any write.
    """

    def __init__(self, message: str, unresolved: list[dict]):
        super().__init__(message)
        self.unresolved = unresolved


class CloneError(WorkflowIOError):
    """A clone failed after the new workflow was created."""

    def __init__(self, message: str, workflow_id: str):
        super().__init__(message)
        self.workflow_id = workflow_id


def _check(result: Any, what: str) -> Any:
    if result is None:
        raise WorkflowIOError(
            f"GHL refused the session while {what} (401/403). "
            "Run `ghl --experimental workflows auth-check`."
        )
    if isinstance(result, dict) and result.get("_error"):
        raise WorkflowIOError(
            f"GHL rejected {what}: HTTP {result.get('code', '?')} {result.get('message', '')}".rstrip()
        )
    return result


def _get_doc(client, wf_id: str) -> dict:
    return _check(client.request("GET", f"/workflow/{client.location_id}/{wf_id}"),
                  f"reading workflow {wf_id}")


def _get_triggers(client, wf_id: str) -> list:
    triggers = _check(
        client.request("GET", f"/workflow/{client.location_id}/trigger?workflowId={wf_id}"),
        f"reading the triggers of workflow {wf_id}",
    )
    if not isinstance(triggers, list):
        raise WorkflowIOError(f"unexpected trigger response for {wf_id}: {str(triggers)[:200]}")
    return triggers


def _templates(doc: dict) -> list:
    return (doc.get("workflowData") or {}).get("templates") or []


def _export(doc: dict, triggers: list, loc: str, wf_id: str) -> dict:
    # fileUrl is left out on purpose: it carries a download token for the steps.
    return {
        "format": EXPORT_FORMAT,
        "workflow_id": wf_id,
        "location_id": loc,
        "name": doc.get("name"),
        "status": doc.get("status"),
        "version": doc.get("version"),
        "parent_id": doc.get("parentId"),
        "meta": doc.get("meta"),
        "templates": _templates(doc),
        "triggers": triggers,
    }


def read_workflow(client, wf_id: str) -> dict:
    """A workflow's steps, triggers and version as one portable export dict."""
    doc = _get_doc(client, wf_id)
    return _export(doc, _get_triggers(client, wf_id), client.location_id, wf_id)


def remap_ids(templates: list, triggers: list) -> tuple[list, list, dict]:
    """Give every step a fresh id and rewrite every reference to the old ones.

    Step ids turn up well beyond `id`/`next`/`parentKey`: a reply wait lists the
    steps it waits on in attributes.reply and its branches in
    attributes.transitions, and a trigger points at its first step. Any string
    EQUAL to an old step id is rewritten, wherever it sits. Inputs are untouched.
    """
    id_map = {t["id"]: str(uuid.uuid4()) for t in templates if t.get("id")}

    def sub(x: Any) -> Any:
        if isinstance(x, str):
            return id_map.get(x, x)
        if isinstance(x, list):
            return [sub(v) for v in x]
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        return x

    return sub(templates), sub(triggers), id_map


def _is_record_id(value: str) -> bool:
    """GHL record ids: 20-char Firestore ids (mixed case) or 24-hex ObjectIds.

    Mixed case is required so a 20-letter lowercase tag name is not mistaken for one.
    """
    if _OBJECT_ID.fullmatch(value):
        return True
    return bool(_FIRESTORE_ID.fullmatch(value)
                and any(c.isupper() for c in value) and any(c.islower() for c in value))


def find_location_refs(export: dict) -> list[dict]:
    """Ids in a workflow that only mean something inside its own account.

    A survey, form, calendar, pipeline, user, custom field or other workflow is
    addressed by id. Cloned into another account those ids point at nothing, and
    GHL does not say so. This lists them so they get re-pointed by hand. The
    workflow's own step ids and condition ids (key "id", a UUID) are not refs.
    """
    own = {t["id"] for t in export.get("templates", []) if t.get("id")}
    own.add(export.get("workflow_id"))
    refs: list[dict] = []

    def walk(x: Any, where: str, key: Optional[str] = None) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{where}.{k}", k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}]", key)
        elif isinstance(x, str):
            if _UUID.fullmatch(x):
                if x not in own and key != "id":
                    refs.append({"where": where, "value": x})
            elif _is_record_id(x):
                refs.append({"where": where, "value": x})

    for i, step in enumerate(export.get("templates", [])):
        walk(step.get("attributes"), f"step '{step.get('name', i)}' attributes")
    for i, trig in enumerate(export.get("triggers", [])):
        for k in ("conditions", "schedule_config"):
            walk(trig.get(k), f"trigger '{trig.get('name', i)}' {k}")
    return refs


def diff_steps(old: list, new: list) -> dict:
    """Step names added, removed and changed, matched by step id."""
    o = {s.get("id"): s for s in old}
    n = {s.get("id"): s for s in new}
    return {
        "added": [s.get("name", i) for i, s in n.items() if i not in o],
        "removed": [s.get("name", i) for i, s in o.items() if i not in n],
        "changed": [s.get("name", i) for i, s in n.items() if i in o and s != o[i]],
    }


def _save_problems(sent: list, saved: list) -> list[str]:
    """What GHL does not hold of what was sent. GHL may ADD keys; that is fine."""
    problems = []
    if len(saved) != len(sent):
        problems.append(f"sent {len(sent)} steps, GHL holds {len(saved)}")
    saved_by_id = {s.get("id"): s for s in saved}
    for s in sent:
        label = s.get("name") or s.get("id")
        got = saved_by_id.get(s.get("id"))
        if got is None:
            problems.append(f"step '{label}' is missing")
            continue
        for key in ("type", "name", "next", "parentKey", "parent"):
            if s.get(key) != got.get(key):
                problems.append(f"step '{label}' {key} is {got.get(key)!r}, sent {s.get(key)!r}")
        got_attrs = got.get("attributes") or {}
        for key, value in (s.get("attributes") or {}).items():
            if got_attrs.get(key) != value:
                problems.append(f"step '{label}' attribute '{key}' was not kept")
    return problems


def _write_snapshot(snapshot_dir, export: dict) -> Path:
    folder = Path(snapshot_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{export['workflow_id']}-v{export['version']}-{stamp}-{uuid.uuid4().hex[:6]}.json"
    path.write_text(json.dumps(export, indent=2))
    return path


def edit_workflow(client, wf_id: str, templates: list, *, name: Optional[str] = None,
                  expected_version: Optional[int] = None, confirm: bool = False,
                  snapshot_dir) -> dict:
    """Replace a workflow's steps (and optionally its name), safely.

    Fresh read -> drift check -> published guard -> snapshot -> save -> re-read.
    The save sends back every setting GHL returned (GET-merge-PUT), so allow
    re-entry, time windows, timezone and canvas meta are not reset. Triggers are
    not part of the save and are left as they are.
    """
    loc = client.location_id
    doc = _get_doc(client, wf_id)
    triggers = _get_triggers(client, wf_id)
    version = doc.get("version")
    if expected_version is not None and version != expected_version:
        raise DriftError(
            f"'{doc.get('name')}' is at version {version}, but your file was exported at "
            f"version {expected_version}. It changed since. Export it again and redo the edit."
        )
    diff = diff_steps(_templates(doc), templates)
    if doc.get("status") == "published" and not confirm:
        raise NeedsConfirm(
            f"'{doc.get('name')}' is PUBLISHED. Saving changes what live contacts get.", diff)

    snapshot = _write_snapshot(snapshot_dir, _export(doc, triggers, loc, wf_id))
    missing_tags = ensure_tags(client, referenced_tags(templates, triggers))
    body = {k: v for k, v in doc.items() if k not in STRIP_KEYS}
    body.update({"version": version, "workflowData": {"templates": templates}})
    if name:
        body["name"] = name
    _check(client.request("PUT", f"/workflow/{loc}/{wf_id}", body), f"saving workflow {wf_id}")

    after = _get_doc(client, wf_id)
    problems = _save_problems(templates, _templates(after))
    if after.get("status") != doc.get("status"):
        problems.append(f"status went from {doc.get('status')!r} to {after.get('status')!r}")
    if name and after.get("name") != name:
        problems.append(f"name is {after.get('name')!r}, sent {name!r}")
    if problems:
        raise VerifyError(
            "GHL accepted the save but does not hold it: " + "; ".join(problems)
            + f". The pre-edit workflow is in {snapshot}.", str(snapshot))

    # A trigger's targetActionId names the first step. Replacing that step left
    # the trigger pointing at nothing (seen live 2026-10-11); re-point it the way
    # GHL's own editor does. Triggers that never had a target are left alone.
    step_ids = {s.get("id") for s in templates}
    root = _root_step_id(templates)
    relinked = 0
    for t in triggers:
        target = t.get("targetActionId")
        if target and target not in step_ids and root:
            body = {k: v for k, v in t.items() if k not in _TRIGGER_SERVER_KEYS}
            body["targetActionId"] = root
            _check(client.request("PUT", f"/workflow/{loc}/trigger/{t['id']}", body),
                   f"re-pointing trigger '{t.get('name')}' at the new first step "
                   f"(steps ARE saved; pre-edit copy in {snapshot})")
            relinked += 1
    return {
        "workflow_id": wf_id,
        "name": after.get("name"),
        "status": after.get("status"),
        "version_before": version,
        "version_after": after.get("version"),
        "diff": diff,
        "relinked_triggers": relinked,
        "missing_tags": missing_tags,
        "snapshot": str(snapshot),
    }


_PAGE = 50   # surveys answer 422 above limit=50 (live, 2026-10-18)
_MAX_PAGES = 40


def _rid(row: dict) -> str:
    # The internal forms list keys rows by "_id"; the others use "id" (live, 2026-10-18).
    return row.get("id") or row.get("_id")


def _rows(result: Any, key: str) -> Optional[list]:
    if not isinstance(result, dict) or result.get("_error"):
        return None
    rows = result.get(key)
    return rows if isinstance(rows, list) else None


def _paged(client, path: str, key: str) -> Optional[list]:
    """Every row of a skip/limit list, or None if any page fails or it never ends."""
    found: list = []
    for page in range(_MAX_PAGES):
        result = client.request("GET", f"{path}&limit={_PAGE}&skip={page * _PAGE}")
        rows = _rows(result, key)
        if rows is None:
            return None
        found.extend(rows)
        total = result.get("total")
        if not rows or len(rows) < _PAGE or (isinstance(total, int) and len(found) >= total):
            return found
    return None


def list_assets(client) -> dict:
    """Forms, surveys, calendars, pipelines (with stages) and workflows, id -> name.

    Read through the internal API, which reaches every sub-account the session can
    (the public API key reaches only its own; verified live 2026-10-18). A kind
    that cannot be read in full is None: unknown, never assumed empty.
    """
    loc = client.location_id
    out: dict = {}
    for kind, key in (("form", "forms"), ("survey", "surveys")):
        rows = _paged(client, f"/{key}/?locationId={loc}", key)
        out[kind] = None if rows is None else {_rid(r): r.get("name") for r in rows}
    cals = _rows(client.request("GET", f"/calendars/?locationId={loc}"), "calendars")
    out["calendar"] = None if cals is None else {_rid(r): r.get("name") for r in cals}
    pipes = _rows(client.request("GET", f"/opportunities/pipelines?locationId={loc}"), "pipelines")
    out["pipeline"] = None if pipes is None else {
        p["id"]: {"name": p.get("name"),
                  "stages": {st["id"]: st.get("name") for st in p.get("stages") or []}}
        for p in pipes}
    listing = client.request("GET", f"/workflow/{loc}/list?limit=100")
    rows = listing.get("rows") if isinstance(listing, dict) and not listing.get("_error") else None
    out["workflow"] = None if rows is None else {
        r["id"]: r.get("name") for r in rows if r.get("type") == "workflow"}
    return out


def asset_lookups(assets: dict) -> dict:
    """list_assets() in the shape workflow_triggers.check_trigger_refs wants."""
    lookups = {k: (None if assets.get(k) is None else set(assets[k]))
               for k in ("form", "survey", "calendar")}
    pipes = assets.get("pipeline")
    lookups["pipeline"] = None if pipes is None else {
        pid: set(p["stages"]) for pid, p in pipes.items()}
    return lookups


def _name_index(assets: dict) -> dict:
    """id -> (kind, name, parent name) across every asset kind."""
    index: dict = {}
    for kind in ("form", "survey", "calendar", "workflow"):
        for asset_id, name in (assets.get(kind) or {}).items():
            index[asset_id] = (kind, name, None)
    for pid, pipe in (assets.get("pipeline") or {}).items():
        index[pid] = ("pipeline", pipe["name"], None)
        for sid, stage_name in pipe["stages"].items():
            index[sid] = ("stage", stage_name, pipe["name"])
    return index


def resolve_refs(refs: list[dict], src_assets: dict, dst_assets: dict,
                 id_map: Optional[dict] = None) -> tuple[dict, list, list]:
    """Map each source-only id to the target asset with the same kind and name.

    A stage matches on its own name AND its pipeline's name. `id_map` wins over
    any match. Returns (mapping, mapped, unresolved).
    """
    src_index, dst_index = _name_index(src_assets), _name_index(dst_assets)
    by_key: dict = {}
    for asset_id, key in dst_index.items():
        by_key.setdefault(key, []).append(asset_id)
    mapping = dict(id_map or {})
    mapped, unresolved, seen = [], [], set()
    for ref in refs:
        value = ref["value"]
        if value in seen:
            continue
        seen.add(value)
        key = src_index.get(value)
        kind, name = (key[0], key[1]) if key else (None, None)
        if value in mapping:
            mapped.append({"from": value, "to": mapping[value], "kind": kind, "name": name,
                           "how": "--map"})
            continue
        matches = by_key.get(key, []) if key else []
        if len(matches) == 1:
            mapping[value] = matches[0]
            mapped.append({"from": value, "to": matches[0], "kind": kind, "name": name,
                           "how": "same name"})
            continue
        reason = ("not one of the source's forms, surveys, calendars, pipelines or workflows"
                  if not key else
                  f"no {kind} named {name!r} in the target" if not matches else
                  f"{len(matches)} {kind}s named {name!r} in the target")
        unresolved.append({**ref, "kind": kind, "name": name, "reason": reason})
    return mapping, mapped, unresolved


def _substitute(x: Any, mapping: dict) -> Any:
    if isinstance(x, str):
        return mapping.get(x, x)
    if isinstance(x, list):
        return [_substitute(v, mapping) for v in x]
    if isinstance(x, dict):
        return {k: _substitute(v, mapping) for k, v in x.items()}
    return x


def find_or_create_folder(client, name: str) -> str:
    loc = client.location_id
    listing = _check(client.request("GET", f"/workflow/{loc}/list?limit=100"),
                     "listing workflow folders")
    for row in listing.get("rows", []):
        if row.get("type") == "directory" and row.get("name") == name:
            return row["id"]
    made = _check(client.request("POST", f"/workflow/{loc}", {"name": name, "type": "directory"}),
                  f"creating folder '{name}'")
    if not made.get("id"):
        raise WorkflowIOError(f"GHL created folder '{name}' but returned no id")
    return made["id"]


def _root_step_id(templates: list) -> Optional[str]:
    roots = [t for t in templates if not t.get("parentKey") and not t.get("parent")]
    roots.sort(key=lambda t: t.get("order", 0))
    return roots[0]["id"] if roots else None


def _trigger_tags(triggers: list) -> list[str]:
    tags: list[str] = []
    for t in triggers:
        if t.get("type") != "contact_tag":
            continue
        for c in t.get("conditions") or []:
            if c.get("field") in ("tagsAdded", "tagsRemoved", "contact.tags"):
                values = c.get("value") if isinstance(c.get("value"), list) else [c.get("value")]
                tags.extend(v for v in values if isinstance(v, str) and v and v not in tags)
    return tags


def referenced_tags(templates: list, triggers: list) -> list[str]:
    """Every tag name a workflow uses: add/remove-tag steps, tag conditions, tag triggers.

    GHL's editor flags any step whose tag does not exist in the sub-account
    ("Referenced Tag does not exist or does not belong to this location", seen
    live 2026-10-18), so all of these have to exist before the workflow is clean.
    """
    found: list[str] = []

    def add(value: Any) -> None:
        for tag in value if isinstance(value, list) else [value]:
            if isinstance(tag, str) and tag.strip() and tag not in found:
                found.append(tag)

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if x.get("conditionSubType") == "tags":
                add(x.get("conditionValue"))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for step in templates:
        if step.get("type") in ("add_contact_tag", "remove_contact_tag"):
            add((step.get("attributes") or {}).get("tags"))
        walk(step.get("attributes"))
    for tag in _trigger_tags(triggers):
        add(tag)
    return found


def ensure_tags(client, tags: list[str]) -> list[str]:
    """Create each tag in the client's sub-account; return the ones still missing.

    tags/create answers "OK" whether the tag is new or already there, so the
    tag list is read back to confirm. If it cannot be read, every tag is
    returned as unconfirmed rather than assumed fine.
    """
    if not tags:
        return []
    loc = client.location_id
    for tag in tags:
        client.request("POST", f"/workflow/{loc}/tags/create", {"tag": tag})
    # GHL lists a just-created tag only a moment later (seen live 2026-10-18),
    # so a tag is reported missing only after a few reads a second apart.
    missing = list(tags)
    for attempt in range(4):
        if attempt:
            _sleep(1)
        listing = client.request("GET", f"/locations/{loc}/tags")
        if not isinstance(listing, dict) or listing.get("_error"):
            continue
        have = {(t.get("name") or "").lower() for t in listing.get("tags") or []}
        missing = [t for t in tags if t.lower() not in have]
        if not missing:
            break
    return missing


def _trigger_body(trigger: dict, wf_id: str, loc: str) -> dict:
    body = {k: v for k, v in trigger.items() if k not in _TRIGGER_SERVER_KEYS}
    body.update({
        "workflowId": wf_id, "workflow_id": wf_id, "location_id": loc,
        "actions": [{"workflow_id": wf_id, "type": "add_to_workflow"}],
        "status": "draft", "deleted": False, "belongs_to": "workflow",
    })
    return body


def clone_workflow(src_client, dst_client, wf_id: str, *, name: Optional[str] = None,
                   folder: Optional[str] = None, id_map: Optional[dict] = None) -> dict:
    """Copy a workflow, steps and triggers, as a new DRAFT. The source is only read.

    Same account: lands beside the source unless `folder` is given. Another
    account: lands at the root (or `folder`), trigger tags are created there, and
    `foreign_refs` lists ids that only exist in the source account.
    """
    export = read_workflow(src_client, wf_id)
    same = src_client.location_id == dst_client.location_id
    templates, triggers, _ = remap_ids(export["templates"], export["triggers"])
    refs = [] if same else find_location_refs(export)
    mapping, mapped = dict(id_map or {}), []
    if refs:
        # Resolve every source-only id BEFORE writing: GHL rejects a save that
        # names assets missing from the target, which used to leave a half-built
        # draft behind.
        mapping, mapped, unresolved = resolve_refs(
            refs, list_assets(src_client), list_assets(dst_client), id_map)
        if unresolved:
            raise ForeignRefsError(
                f"{len(unresolved)} id(s) in this workflow only exist in the source "
                "sub-account and have no same-named match in the target. Nothing was "
                "written.", unresolved)
    if mapping:
        templates, triggers = _substitute(templates, mapping), _substitute(triggers, mapping)
    new_name = name or f"{export['name']} (copy)"
    loc = dst_client.location_id

    if folder:
        parent = find_or_create_folder(dst_client, folder)
    else:
        parent = export["parent_id"] if same else None
    create = {"name": new_name}
    if parent:
        create["parentId"] = parent
    made = _check(dst_client.request("POST", f"/workflow/{loc}", create), "creating the copy")
    new_id = made.get("id")
    if not new_id:
        raise WorkflowIOError("GHL created the copy but returned no id")

    try:
        # Before the triggers: a tag trigger renders blank, and a tag step shows
        # an error, unless the tag exists in the target account. Done in the same
        # account too, because a CLI-built source may never have created its tags.
        missing_tags = ensure_tags(dst_client, referenced_tags(templates, triggers))
        root = _root_step_id(templates)
        created_triggers = []
        for t in triggers:
            body = _trigger_body(t, new_id, loc)
            r = _check(dst_client.request("POST", f"/workflow/{loc}/trigger", body),
                       f"creating trigger '{t.get('name')}'")
            if root:
                # Without targetActionId the trigger floats unconnected on the canvas.
                body = {**body, "targetActionId": root}
                _check(dst_client.request("PUT", f"/workflow/{loc}/trigger/{r['id']}", body),
                       f"linking trigger '{t.get('name')}'")
            created_triggers.append({**body, "id": r["id"]})

        fresh = _get_doc(dst_client, new_id)
        put = {
            "name": new_name,
            "version": fresh.get("version"),
            "workflowData": {"templates": templates},
            "triggersChanged": bool(created_triggers),
            "oldTriggers": created_triggers,
            "newTriggers": created_triggers,
        }
        if export.get("meta"):
            put["meta"] = export["meta"]
        _check(dst_client.request("PUT", f"/workflow/{loc}/{new_id}", put),
               "saving the copied steps")

        saved = read_workflow(dst_client, new_id)
        problems = _save_problems(templates, saved["templates"])
        if len(saved["triggers"]) != len(triggers):
            problems.append(f"copied {len(triggers)} triggers, GHL holds {len(saved['triggers'])}")
        if problems:
            raise WorkflowIOError("the copy did not save cleanly: " + "; ".join(problems))
    except WorkflowIOError as e:
        raise CloneError(
            f"{e}. The half-built draft is {new_id} in location {loc}; delete it in GHL.",
            new_id) from e

    return {
        "workflow_id": new_id,
        "location_id": loc,
        "name": new_name,
        "steps": len(templates),
        "triggers": len(created_triggers),
        "foreign_refs": refs,
        "mapped": mapped,
        "missing_tags": missing_tags,
        "source_workflow_id": wf_id,
        "source_location_id": src_client.location_id,
    }
