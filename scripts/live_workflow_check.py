#!/usr/bin/env python3
"""Live end-to-end test of the workflow commands against the configured sub-account.

It runs the real CLI (auth-check, preview, build, get, edit, clone, enroll, remove),
publishes ONE workflow whose only trigger is a tag unique to this run, fires it on a
throwaway contact that has no email and no phone (so nothing can be sent), checks the
steps ran, and then deletes everything it made: workflows, folder, tags, contact.

Only things named for this run are ever touched: workflows "ZZ TEST ghl-cli live <run>",
folder "ZZ ghl-cli live <run>", tags "zz-live-<run>-*", contact "ZZ ghl-cli live <run>".

    .venv/bin/python scripts/live_workflow_check.py               # test, then clean up
    .venv/bin/python scripts/live_workflow_check.py --keep        # leave it all to inspect
    .venv/bin/python scripts/live_workflow_check.py --cleanup RUN # delete a kept run
    .venv/bin/python scripts/live_workflow_check.py --clone-to LOC  # also clone across accounts

Exit 0 = every check passed. Writes to the LIVE sub-account in GHL_LOCATION_ID.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli_anything.gohighlevel.utils import ghl_client as api  # noqa: E402
from cli_anything.gohighlevel.utils.ghl_internal_client import (  # noqa: E402
    InternalGHLClient, TokenManager,
)
from cli_anything.gohighlevel.utils.workflow_io import list_assets  # noqa: E402
from cli_anything.gohighlevel.utils.workflow_triggers import compile_triggers  # noqa: E402

GHL = str(ROOT / "ghl")
LOC = os.environ["GHL_LOCATION_ID"]
TOKENS = TokenManager()
PASS, FAIL = [], []
CREATED_CONTACTS: list[str] = []   # deleted by id: contact search lags new contacts


def check(ok: bool, what: str, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(what)
    print(f"  {'ok  ' if ok else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def ghl(*args: str, loc: str | None = None) -> subprocess.CompletedProcess:
    cmd = [GHL, "--experimental", *(["--location-id", loc] if loc else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def ghl_json(*args: str, loc: str | None = None):
    r = ghl("--json", *args, loc=loc)
    try:
        return r, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r, None


def client(loc: str = LOC) -> InternalGHLClient:
    return InternalGHLClient(TOKENS, loc)


# ── cleanup: only this run's names ────────────────────────────────────────

def cleanup(run: str, locs: list[str]) -> None:
    prefix, folder, tag_prefix = f"ZZ TEST ghl-cli live {run}", f"ZZ ghl-cli live {run}", f"zz-live-{run}-"
    print(f"\n== cleanup {run}")
    for loc in locs:
        c = client(loc)
        rows = _all_rows(c, folder)
        for r in rows:
            if r.get("type") == "workflow" and str(r.get("name", "")).startswith(prefix):
                if r.get("status") == "published":
                    c.request("PUT", f"/workflow/{loc}/change-status/{r['id']}",
                              {"status": "draft", "updatedBy": _user_id(c)})
                c.request("DELETE", f"/workflow/{loc}/{r['id']}")
        for r in (c.request("GET", f"/workflow/{loc}/list?limit=100") or {}).get("rows", []):
            if r.get("type") == "directory" and r.get("name") == folder:
                c.request("DELETE", f"/workflow/{loc}/{r['id']}")
        for t in (c.request("GET", f"/locations/{loc}/tags") or {}).get("tags", []):
            if str(t.get("name", "")).startswith(tag_prefix):
                c.request("DELETE", f"/locations/{loc}/tags/{t['id']}")
    ids = list(CREATED_CONTACTS)
    try:  # a kept run's contact is found by name; search lags brand-new contacts
        found = api.post("/contacts/search", data={"locationId": LOC, "query": f"ghl-cli live {run}",
                                                   "pageLimit": 20}).get("contacts", [])
        ids += [ct["id"] for ct in found
                if f"{ct.get('firstName', '')} {ct.get('lastName', '')}".strip() == f"ZZ ghl-cli live {run}"]
    except Exception as e:
        print(f"  contact search error: {e}")
    for cid in dict.fromkeys(ids):
        try:
            api.delete(f"/contacts/{cid}")
        except Exception as e:
            print(f"  contact delete error: {e}")
        try:
            api.get(f"/contacts/{cid}")
            gone = False
        except Exception:
            gone = True
        check(gone, f"test contact {cid} deleted")
    time.sleep(3)
    for loc in locs:
        c = client(loc)
        left = [r["name"] for r in _all_rows(c, folder) if str(r.get("name", "")).startswith((prefix, folder))]
        tags = [t["name"] for t in (c.request("GET", f"/locations/{loc}/tags") or {}).get("tags", [])
                if str(t.get("name", "")).startswith(tag_prefix)]
        check(not left and not tags, f"cleanup {loc}: nothing left", f"{left} {tags}")


def _all_rows(c: InternalGHLClient, folder: str) -> list[dict]:
    """Top-level rows plus everything inside folders named `folder`.

    The top-level list does not always include a folder's contents (a nested
    draft survived a cleanup on 2026-10-18), so the run's folders are opened too.
    """
    loc = c.location_id
    rows = (c.request("GET", f"/workflow/{loc}/list?limit=100") or {}).get("rows", [])
    seen = {r["id"] for r in rows}
    for r in list(rows):
        if r.get("type") == "directory" and r.get("name") == folder:
            for k in (c.request("GET", f"/workflow/{loc}/list?limit=100&parentId={r['id']}") or {}).get("rows", []):
                if k["id"] not in seen:
                    rows.append(k)
                    seen.add(k["id"])
    return rows


def _user_id(c: InternalGHLClient) -> str:
    return (c.whoami() or {}).get("userId", "")


def _status(c: InternalGHLClient, wf: str, status: str) -> None:
    c.request("PUT", f"/workflow/{c.location_id}/change-status/{wf}",
              {"status": status, "updatedBy": _user_id(c)})


# ── the test ──────────────────────────────────────────────────────────────

def first_assets(loc: str) -> dict:
    """One real form, survey, calendar and pipeline-with-stages from a sub-account."""
    assets = list_assets(client(loc))
    pick = lambda kind: next(iter(assets.get(kind) or {}), None)  # noqa: E731
    pipe = next(((pid, p) for pid, p in (assets.get("pipeline") or {}).items() if p["stages"]), None)
    return {"form": pick("form"), "survey": pick("survey"), "calendar": pick("calendar"),
            "pipeline": pipe[0] if pipe else None,
            "stage": next(iter(pipe[1]["stages"])) if pipe else None}


def run_test(run: str, work: Path, clone_to: str | None) -> None:
    prefix, folder = f"ZZ TEST ghl-cli live {run}", f"ZZ ghl-cli live {run}"
    tag = lambda name: f"zz-live-{run}-{name}"  # noqa: E731

    print(f"== run {run} in {LOC}")
    check(ghl("workflows", "auth-check").returncode == 0, "auth-check")

    # Real ids from this sub-account, so every trigger can point at something.
    a = first_assets(LOC)
    form, survey, cal = a["form"], a["survey"], a["calendar"]
    print(f"  assets found: form={bool(form)} survey={bool(survey)} calendar={bool(cal)} pipeline={bool(a['pipeline'])}")
    triggers = [{"type": "tag", "value": tag("start")}, {"type": "contact_created"},
                {"type": "customer_replied", "contains": "zz live"}]
    if form:
        triggers.append({"type": "form", "form_id": form})
    if survey:
        triggers.append({"type": "survey", "survey_id": survey})
    if cal:
        triggers += [{"type": "appointment_booked", "calendar_id": cal},
                     {"type": "appointment_status", "status": "showed", "calendar_id": cal}]
    if a["pipeline"]:
        triggers += [{"type": "pipeline_stage", "pipeline_id": a["pipeline"], "stage_id": a["stage"]},
                     {"type": "opportunity_status", "status": "won", "pipeline_id": a["pipeline"]}]
    spec = {"name": f"{prefix} all triggers", "triggers": triggers, "steps": [
        {"type": "wait", "name": "Wait 1 day", "value": 1, "unit": "days"},
        {"type": "tag", "name": "Mark tagged", "tags": [tag("tagged")]},
        {"type": "note", "name": "Leave a note", "body": "ghl-cli live test note"},
        {"type": "if_else", "name": "VIP?", "condition": {"kind": "tag", "value": tag("vip")},
         "yes": [{"type": "tag", "name": "Mark VIP", "tags": [tag("yes")]}],
         "no": [{"type": "tag", "name": "Mark not VIP", "tags": [tag("no")]}]}]}
    spec_path = work / "all.json"
    spec_path.write_text(json.dumps(spec))

    check(ghl("workflows", "preview", "--from-spec", str(spec_path)).returncode == 0, "preview")
    r, stats = ghl_json("workflows", "build", "--from-spec", str(spec_path), "--folder", folder)
    ok = check(r.returncode == 0 and stats and not stats["errors"], "build with every trigger type",
               (r.stderr or r.stdout)[-300:])
    if not ok:
        return
    check(stats["triggers_created"] == len(triggers), f"build created {len(triggers)} triggers",
          str(stats["triggers_created"]))
    wf = stats["workflow_ids"]["workflow"]

    # get: every trigger present, as compiled, wired to the first step
    r, exp = ghl_json("workflows", "get", wf)
    check(r.returncode == 0 and exp is not None, "get")
    want = compile_triggers(spec)
    got = {t["type"]: t for t in exp["triggers"]}
    root = next(s["id"] for s in exp["templates"] if not s.get("parentKey") and not s.get("parent"))
    for w in want:
        g = got.get(w["type"])
        same = g is not None and [{k: v for k, v in c.items() if k != "id"} for c in g["conditions"]] == \
            [{k: v for k, v in c.items() if k != "id"} for c in w["conditions"]]
        check(same, f"trigger {w['type']} saved as compiled")
        check(g is not None and g.get("targetActionId") == root, f"trigger {w['type']} wired to first step")
    c = client()
    tags_now = {t["name"] for t in c.request("GET", f"/locations/{LOC}/tags").get("tags", [])}
    check({tag(n) for n in ("start", "tagged", "vip", "yes", "no")} <= tags_now, "every tag created")

    # bad id: refused before anything is deployed
    bad = copy.deepcopy(spec)
    bad["name"], bad["triggers"] = f"{prefix} bad id", [{"type": "form", "form_id": "ZZnotAformAAAAAAAAAA"}]
    (work / "bad.json").write_text(json.dumps(bad))
    before = len(c.request("GET", f"/workflow/{LOC}/list?limit=100").get("rows", []))
    r = ghl("workflows", "build", "--from-spec", str(work / "bad.json"), "--folder", folder)
    after = len(c.request("GET", f"/workflow/{LOC}/list?limit=100").get("rows", []))
    check(r.returncode == 2 and before == after, "build refuses a form id that does not exist",
          f"exit {r.returncode}, rows {before}->{after}")

    # edit, drift refusal, undo
    exp_path = work / "export.json"
    r = ghl("workflows", "get", wf, "--output-file", str(exp_path))
    data = json.loads(exp_path.read_text())
    note = next(s for s in data["templates"] if s["type"] == "add_notes")
    note["attributes"]["html"] = "<p>edited by live test</p>"
    exp_path.write_text(json.dumps(data))
    r, done = ghl_json("workflows", "edit", wf, "--from-json", str(exp_path))
    check(r.returncode == 0 and done and done["diff"]["changed"] == ["Leave a note"], "edit", (r.stderr or "")[-200:])
    r2, exp2 = ghl_json("workflows", "get", wf)
    check(exp2 and len(exp2["triggers"]) == len(triggers), "edit leaves every trigger in place")
    check(ghl("workflows", "edit", wf, "--from-json", str(exp_path)).returncode == 2, "edit refuses a stale export")
    if done:
        r = ghl("workflows", "edit", wf, "--from-json", done["snapshot"], "--expect-version", str(done["version_after"]))
        r3, exp3 = ghl_json("workflows", "get", wf)
        back = exp3 and next(s for s in exp3["templates"] if s["type"] == "add_notes")["attributes"]["html"]
        check(r.returncode == 0 and back and "edited by live test" not in back, "undo restores the old step")

    # clone in the same account
    r, cl = ghl_json("workflows", "clone", wf, "--name", f"{prefix} clone", "--folder", folder)
    ok = check(r.returncode == 0 and cl and not cl["missing_tags"], "clone", (r.stderr or "")[-200:])
    if ok:
        r, cexp = ghl_json("workflows", "get", cl["workflow_id"])
        check(cexp and len(cexp["triggers"]) == len(triggers) and
              [s["name"] for s in cexp["templates"]] == [s["name"] for s in exp["templates"]] and
              not {s["id"] for s in cexp["templates"]} & {s["id"] for s in exp["templates"]},
              "clone has every step and trigger, with new ids")

    if clone_to:
        cross_test(run, work, folder, a, clone_to)

    fire_test(run, work, folder, c)


def cross_test(run: str, work: Path, folder: str, a: dict, dst: str) -> None:
    """Cross-account clone: refused with the ids listed, then accepted once mapped."""
    prefix = f"ZZ TEST ghl-cli live {run}"
    if not (a["form"] and a["pipeline"]):
        print("  SKIP cross-account test: source has no form or pipeline")
        return
    spec = {"name": f"{prefix} cross source", "triggers": [
        {"type": "form", "form_id": a["form"]},
        {"type": "pipeline_stage", "pipeline_id": a["pipeline"], "stage_id": a["stage"]}],
        "steps": [{"type": "wait", "name": "Wait 1 day", "value": 1, "unit": "days"}]}
    (work / "cross.json").write_text(json.dumps(spec))
    r, stats = ghl_json("workflows", "build", "--from-spec", str(work / "cross.json"), "--folder", folder)
    if not check(r.returncode == 0 and stats and not stats["errors"], "build cross-account source"):
        return
    wf = stats["workflow_ids"]["workflow"]
    d = client(dst)
    before = len(d.request("GET", f"/workflow/{dst}/list?limit=100").get("rows", []))
    r = ghl("workflows", "clone", wf, "--to-location", dst, "--name", f"{prefix} cross")
    after = len(d.request("GET", f"/workflow/{dst}/list?limit=100").get("rows", []))
    check(r.returncode == 2 and a["form"] in r.stdout and a["pipeline"] in r.stdout and before == after,
          "cross clone refuses unmatched ids, lists them, writes nothing",
          f"exit {r.returncode}, rows {before}->{after}")
    t = first_assets(dst)
    if not (t["form"] and t["pipeline"]):
        print("  SKIP mapped cross clone: target has no form or pipeline")
        return
    r, xc = ghl_json("workflows", "clone", wf, "--to-location", dst, "--name", f"{prefix} cross",
                     "--folder", folder, "--map", f"{a['form']}={t['form']}",
                     "--map", f"{a['pipeline']}={t['pipeline']}", "--map", f"{a['stage']}={t['stage']}")
    ok = check(r.returncode == 0 and xc is not None, "cross clone with --map is accepted by GHL",
               (r.stderr or "")[-300:])
    if ok:
        r, got = ghl_json("workflows", "get", xc["workflow_id"], loc=dst)
        values = {json.dumps(c.get("value")) for tr in (got or {}).get("triggers", []) for c in tr["conditions"]}
        check({json.dumps([t["form"]]), json.dumps(t["pipeline"]), json.dumps(t["stage"])} <= values,
              "cross clone triggers point at the target's assets")


def fire_test(run: str, work: Path, folder: str, c: InternalGHLClient) -> None:
    """Publish a workflow whose only trigger is this run's tag, fire it, check it ran."""
    prefix, tag = f"ZZ TEST ghl-cli live {run}", (lambda n: f"zz-live-{run}-{n}")
    # Guard: a new contact must not set off any real published workflow.
    rows = c.request("GET", f"/workflow/{LOC}/list?limit=100").get("rows", [])
    risky = []
    for r in rows:
        if r.get("type") == "workflow" and r.get("status") == "published":
            for t in c.request("GET", f"/workflow/{LOC}/trigger?workflowId={r['id']}") or []:
                if t.get("type") == "contact_created" or (t.get("type") == "contact_tag" and not t.get("conditions")):
                    risky.append(r["name"])
    if not check(not risky, "no published workflow fires on a new contact", f"{risky}"):
        print("  SKIP fire test")
        return
    spec = {"name": f"{prefix} fire", "trigger": {"type": "tag", "value": tag("fire")}, "steps": [
        {"type": "tag", "name": "Mark fired", "tags": [tag("fired")]},
        {"type": "note", "name": "Leave a note", "body": f"ghl-cli live fire test {run}"}]}
    (work / "fire.json").write_text(json.dumps(spec))
    r, stats = ghl_json("workflows", "build", "--from-spec", str(work / "fire.json"), "--folder", folder)
    if not check(r.returncode == 0 and stats and not stats["errors"], "build fire workflow"):
        return
    wf = stats["workflow_ids"]["workflow"]
    _status(c, wf, "published")
    check(c.request("GET", f"/workflow/{LOC}/{wf}").get("status") == "published", "publish fire workflow")

    # published guard
    exp_path = work / "fire-export.json"
    ghl("workflows", "get", wf, "--output-file", str(exp_path))
    data = json.loads(exp_path.read_text())
    data["templates"][1]["attributes"]["html"] = f"<p>ghl-cli live fire test {run}</p>"
    exp_path.write_text(json.dumps(data))
    check(ghl("workflows", "edit", wf, "--from-json", str(exp_path)).returncode == 2,
          "edit refuses a published workflow without --confirm")
    r = ghl("workflows", "edit", wf, "--from-json", str(exp_path), "--confirm")
    check(r.returncode == 0 and c.request("GET", f"/workflow/{LOC}/{wf}").get("status") == "published",
          "edit --confirm saves and it stays published", (r.stderr or "")[-200:])

    contact = api.post("/contacts/", data={"locationId": LOC, "firstName": "ZZ",
                                           "lastName": f"ghl-cli live {run}"}).get("contact", {})
    cid = contact.get("id")
    if not check(bool(cid), "create throwaway contact (no email, no phone)"):
        return
    CREATED_CONTACTS.append(cid)
    time.sleep(5)
    api.post(f"/contacts/{cid}/tags", data={"tags": [tag("fire")]})
    fired = False
    for _ in range(24):
        time.sleep(5)
        tags = api.get(f"/contacts/{cid}").get("contact", {}).get("tags", [])
        if tag("fired") in tags:
            fired = True
            break
    check(fired, "published tag trigger FIRED and ran its tag step")
    notes = api.get(f"/contacts/{cid}/notes").get("notes", [])
    check(any(f"live fire test {run}" in (n.get("body") or "") for n in notes),
          "note step wrote the note on the contact", json.dumps([n.get("body") for n in notes])[:200])

    r = ghl("workflows", "enroll", "--contact-id", cid, "--workflow-id", wf)
    check(r.returncode == 0, "enroll", r.stderr[-200:])
    r = ghl("workflows", "remove", "--contact-id", cid, "--workflow-id", wf)
    check(r.returncode == 0, "remove", r.stderr[-200:])
    _status(c, wf, "draft")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="leave this run's artifacts in GHL")
    ap.add_argument("--cleanup", metavar="RUN", help="only delete a kept run's artifacts")
    ap.add_argument("--clone-to", metavar="LOC", help="also clone across to this sub-account")
    args = ap.parse_args()
    locs = [LOC] + ([args.clone_to] if args.clone_to else [])
    if args.cleanup:
        cleanup(args.cleanup, locs)
    else:
        run = time.strftime("%m%d%H%M")
        work = Path(os.environ.get("TMPDIR", "/tmp")) / f"ghl-live-{run}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            run_test(run, work, args.clone_to)
        finally:
            if args.keep:
                print(f"\nKept. Clean up later with: --cleanup {run}" +
                      (f" --clone-to {args.clone_to}" if args.clone_to else ""))
            else:
                cleanup(run, locs)
    print(f"\nPASS={len(PASS)}  FAIL={len(FAIL)}")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
