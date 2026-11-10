"""`ghl selftest`: prove a workflow trigger really fires in the member's own
GoHighLevel account, then remove everything the test made.

It runs on a REAL account, so two things must never happen:

1. Setting off the member's own automations without their yes. Before anything
   is written, every published workflow is read (the top-level list and every
   folder, recursively). A trigger that our test contact, our start tag, our
   fired tag or our note could set off makes the default a draft-only check
   (build, read back, delete). So does any list or workflow we could not read in
   full. Firing then needs fire=True, which the CLI passes only as --fire after
   the member says yes.
2. Leaving junk behind. Everything carries the run id in its name. Cleanup runs
   in `finally`, tries every item even when one delete fails, then reads GHL
   again to prove what is gone. Anything still there is reported with its id and
   the command that removes it.

Request shapes are the ones scripts/live_workflow_check.py used live on
2026-10-18: build via CampaignBuilder, publish via change-status with
updatedBy, a contact with no email and no phone, tag it, poll, check the note.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import requests

from cli_anything.gohighlevel.utils import ghl_client as api
from cli_anything.gohighlevel.utils.workflow_builder import CampaignBuilder
from cli_anything.gohighlevel.utils.workflow_io import read_workflow
from cli_anything.gohighlevel.utils.workflow_spec import compile_spec
from cli_anything.gohighlevel.utils.workflow_triggers import compile_triggers

# Triggers nothing in the test can set off: our contact never submits a form or
# survey, books, gets an opportunity, replies or pays.
SAFE_TRIGGER_TYPES = frozenset({
    "form_submission", "survey_submission", "customer_appointment", "appointment",
    "pipeline_stage_updated", "opportunity_status_changed", "customer_reply",
    "payment_received",
})

ACTION_WORDS = {
    "sms": "texts the contact", "email": "emails the contact",
    "internal_notification": "notifies your team", "webhook": "sends a webhook",
    "custom_webhook": "sends a webhook", "create_opportunity": "creates an opportunity",
    "assign_user": "assigns a user", "add_to_workflow": "adds the contact to another workflow",
    "slack_message": "posts to Slack", "google_sheets": "writes to Google Sheets",
    "task-notification": "creates a task",
}

RUN_RE = re.compile(r"[0-9a-f]{6}")
LIST_LIMIT = 100
# A contact_tag trigger is limited to named tags only by an "includes" operator.
# "index-of-false" (does not include X) fires on every OTHER tag, ours included.
_TAG_FIELDS = ("tagsAdded", "tagsRemoved", "contact.tags")
_TAG_INCLUDE_OPERATORS = ("index-of-true", "==", "is-any-of")
_GONE_CODES = (400, 404, 410)


@dataclass
class Risky:
    workflow_id: str
    name: str
    trigger_type: str
    actions: list[str]


@dataclass
class Result:
    status: Literal["PASS", "PASS_NO_FIRE", "FAIL"]
    lines: list[str]
    leftovers: list[str]
    risky: list[Risky]
    reason: str = ""
    contact_made: bool = False   # a test contact may have existed, so risky workflows may have run


def run_names(run: str) -> tuple[str, str, str]:
    """(workflow and folder name, start tag, fired tag) for one run."""
    return f"ghl-cli setup check {run}", f"ghl-cli-check-{run}-start", f"ghl-cli-check-{run}-fired"


# ── scan ──────────────────────────────────────────────────────────────────

def _ok(r: Any) -> bool:
    return isinstance(r, dict) and not r.get("_error")


def _guard(fn: Callable[[], Any], default: Any = None) -> Any:
    """Run one cleanup or proof step; a failure never stops the next one."""
    try:
        return fn()
    except (Exception, SystemExit):
        return default


def _list(client, parent: Optional[str] = None) -> Optional[dict]:
    """One answer from the workflow list route, or None when GHL gave no usable list."""
    path = f"/workflow/{client.location_id}/list?limit={LIST_LIMIT}"
    r = client.request("GET", path + (f"&parentId={parent}" if parent else ""))
    if not _ok(r) or not isinstance(r.get("rows"), list):
        return None
    return r


def _full(page: dict) -> bool:
    """False when the list says (or hints) it holds more rows than it returned."""
    rows, count = page["rows"], page.get("count")
    if page.get("isLocationRateLimited"):
        return False
    if any(not (isinstance(r, dict) and r.get("id")) for r in rows):
        return False
    if isinstance(count, int) and not isinstance(count, bool):
        return count <= len(rows)
    return len(rows) < LIST_LIMIT


def _all_rows(client) -> tuple[list[dict], bool]:
    """Every row: the top-level list, then every folder opened, recursively.

    The top-level list does not always include a folder's contents (a nested
    draft survived a cleanup live on 2026-10-18), so folders are always opened.
    """
    page = _list(client)
    if page is None:
        return [], False
    complete = _full(page)
    seen = {r["id"]: r for r in page["rows"] if isinstance(r, dict) and r.get("id")}
    queue, opened = list(seen.values()), set()
    while queue:
        row = queue.pop(0)
        if row.get("type") != "directory" or row["id"] in opened:
            continue
        opened.add(row["id"])
        kids = _list(client, row["id"])
        if kids is None:
            complete = False
            continue
        complete = complete and _full(kids)
        for k in kids["rows"]:
            if isinstance(k, dict) and k.get("id") and k["id"] not in seen:
                seen[k["id"]] = k
                queue.append(k)
    return list(seen.values()), complete


def _names_a_tag(trigger: dict) -> bool:
    """True when the trigger only fires for tags it names ("tag vip added").

    Without that, a contact_tag trigger fires on ANY tag, and our start and fired
    tags would set it off. Other conditions are filters that only narrow it.
    """
    tag_conds = [c for c in trigger.get("conditions") or []
                 if isinstance(c, dict) and c.get("field") in _TAG_FIELDS]
    if not tag_conds:
        return False
    for c in tag_conds:
        values = c.get("value") if isinstance(c.get("value"), list) else [c.get("value")]
        if c.get("operator") not in _TAG_INCLUDE_OPERATORS or not values or \
                not all(isinstance(v, str) and v.strip() for v in values):
            return False
    return True


def _is_safe(trigger: dict) -> bool:
    ttype = trigger.get("type")
    if ttype in SAFE_TRIGGER_TYPES:
        return True
    return ttype == "contact_tag" and _names_a_tag(trigger)


def _actions(templates: list) -> list[str]:
    out: list[str] = []
    for step in templates or []:
        if isinstance(step, dict) and step.get("type"):
            word = ACTION_WORDS.get(step["type"], str(step["type"]))
            if word not in out:
                out.append(word)
    return out


def scan_risky(client, emit: Optional[Callable[[str], Any]] = None) -> tuple[list[Risky], bool]:
    """Published workflows our test could set off, and whether every one was read.

    Only GETs. Drafts are skipped; a status that is not "draft" counts as live.
    `emit` gets one progress line before the per-workflow reads, which take a
    while on a big account, so a caller watching the output sees it working.
    """
    rows, complete = _all_rows(client)
    live = [row for row in rows if row.get("type") != "directory" and row.get("status") != "draft"]
    if live and emit is not None:
        n = len(live)
        emit(f"Reading {n} published workflow{'s' if n != 1 else ''}...")
    risky: list[Risky] = []
    for row in live:
        try:
            wf = read_workflow(client, row["id"])
        except Exception:
            complete = False
            continue
        if (wf.get("status") or row.get("status")) == "draft":
            continue
        triggers = wf.get("triggers") or []
        if any(not isinstance(t, dict) for t in triggers):
            complete = False
        bad = [t for t in triggers
               if isinstance(t, dict) and t.get("deleted") is not True and not _is_safe(t)]
        if bad:
            types = dict.fromkeys(str(t.get("type") or "unknown") for t in bad)
            risky.append(Risky(row["id"], str(wf.get("name") or row.get("name") or row["id"]),
                               ", ".join(types), _actions(wf.get("templates"))))
    return risky, complete


# ── run ───────────────────────────────────────────────────────────────────

class _Fail(Exception):
    """A step failed; the message is the member-facing reason."""


@dataclass
class _Made:
    built: bool = False            # a build was sent, so run-named things may exist
    workflow_id: Optional[str] = None
    folder_id: Optional[str] = None
    published: bool = False        # a publish was sent
    contact_ids: list[str] = field(default_factory=list)
    contact_unknown: bool = False  # a contact create was sent and no id came back


def _span(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        n = int(seconds // 60)
        return f"{n} minute{'s' if n != 1 else ''}"
    n = int(seconds)
    return f"{n} second{'s' if n != 1 else ''}"


def _why(r: Any) -> str:
    if r is None:
        return "GHL refused the session"
    if isinstance(r, dict) and r.get("_error"):
        return f"HTTP {r.get('code', '?')}"
    return "unexpected answer"


def _user_id(client) -> str:
    me = client.whoami()
    return str(me.get("userId") or "") if _ok(me) else ""


def _tags(client) -> Optional[dict[str, Any]]:
    """{tag name (lowercase): id} at the location, or None if it could not be read."""
    r = client.request("GET", f"/locations/{client.location_id}/tags")
    if not _ok(r) or not isinstance(r.get("tags"), list):
        return None
    return {str(t.get("name") or "").lower(): t.get("id") for t in r["tags"] if isinstance(t, dict)}


def _tag_value_has(trigger: dict, tag: str) -> bool:
    for c in trigger.get("conditions") or []:
        if isinstance(c, dict) and c.get("field") == "tagsAdded":
            values = c.get("value") if isinstance(c.get("value"), list) else [c.get("value")]
            if any(str(v).lower() == tag for v in values):
                return True
    return False


def _build(client, run: str, made: _Made, say: Callable[[str], None]) -> str:
    name, start, fired = run_names(run)
    spec = {"name": name, "trigger": {"type": "tag", "value": start}, "steps": [
        {"type": "tag", "name": "Mark fired", "tags": [fired]},
        {"type": "note", "name": "Leave a note", "body": f"ghl-cli setup check {run}"}]}
    wf_def = {"name": name, "templates": compile_spec(spec), "triggers": compile_triggers(spec)}
    made.built = True
    stats = CampaignBuilder(client).build({"workflow": wf_def}, name)
    made.folder_id = stats.get("folder_id")
    made.workflow_id = (stats.get("workflow_ids") or {}).get("workflow")
    if stats.get("errors") or not made.workflow_id:
        first = (stats.get("errors") or ["no workflow id came back"])[0]
        raise _Fail(f"GHL did not build the test workflow: {first}")
    say(f"Built a draft test workflow: {name}")
    return made.workflow_id


def _verify(client, run: str, wf_id: str, say: Callable[[str], None]) -> None:
    _, start, fired = run_names(run)
    try:
        wf = read_workflow(client, wf_id)
    except Exception as e:
        raise _Fail(f"could not read the test workflow back: {e}")
    steps = [s for s in wf.get("templates") or [] if isinstance(s, dict)]
    if sorted(str(s.get("type")) for s in steps) != ["add_contact_tag", "add_notes"]:
        raise _Fail(f"the test workflow did not save as built: 2 steps sent, GHL kept {len(steps)}")
    trig = wf.get("triggers") or []
    if len(trig) != 1 or trig[0].get("type") != "contact_tag" or not _tag_value_has(trig[0], start):
        raise _Fail("the test workflow's tag trigger did not save as built")
    have = _tags(client)
    if have is None:
        raise _Fail("could not read your tag list back")
    missing = [t for t in (start, fired) if t not in have]
    if missing:
        raise _Fail(f"GHL did not create the test tag(s): {', '.join(missing)}")
    say("Read it back: 2 steps, 1 tag trigger, both tags exist")


def _fire(client, contacts, run: str, wf_id: str, made: _Made, say: Callable[[str], None],
          sleep: Callable[[float], Any], poll_seconds: float, poll_tries: int) -> None:
    name, start, fired = run_names(run)
    loc = client.location_id
    me = _user_id(client)
    if not me:
        raise _Fail("could not read your GHL user id, so the test workflow was not turned on")
    made.published = True
    r = client.request("PUT", f"/workflow/{loc}/change-status/{wf_id}",
                       {"status": "published", "updatedBy": me})
    if not _ok(r):
        raise _Fail(f"GHL would not turn the test workflow on ({_why(r)})")
    doc = client.request("GET", f"/workflow/{loc}/{wf_id}")
    if not _ok(doc) or doc.get("status") != "published":
        raise _Fail("GHL did not turn the test workflow on")
    say("Turned the test workflow on")

    made.contact_unknown = True
    try:
        made_contact = contacts.post("/contacts/", data={
            "locationId": loc, "firstName": "ghl-cli", "lastName": f"setup check {run}"})
    except Exception as e:
        # api.post retries timeouts, 5xx and 401 "Command timed out", any of which
        # may have created the contact (maybe more than once). Only a clear refusal
        # means nothing was made; otherwise cleanup searches for it by name.
        if _nothing_made(e):
            made.contact_unknown = False
        raise _Fail(f"could not make the test contact: {e}")
    cid = (made_contact.get("contact") or {}).get("id") if isinstance(made_contact, dict) else None
    if not cid:
        raise _Fail("could not make the test contact: GHL sent no contact id back")
    made.contact_ids.append(cid)
    made.contact_unknown = False
    say("Made a test contact with no email and no phone")

    sleep(poll_seconds)
    try:
        contacts.post(f"/contacts/{cid}/tags", data={"tags": [start]})
    except Exception as e:
        raise _Fail(f"could not add the start tag to the test contact: {e}")
    say(f"Added the start tag {start}")

    fired_seen = False
    for _ in range(poll_tries):
        sleep(poll_seconds)
        try:
            tags = (contacts.get(f"/contacts/{cid}").get("contact") or {}).get("tags") or []
        except Exception:
            continue
        if fired not in {str(t).lower() for t in tags}:
            continue
        fired_seen = True
        try:
            notes = contacts.get(f"/contacts/{cid}/notes").get("notes") or []
        except Exception:
            continue
        # GHL stores the note as html, so look for the text inside it.
        if any(name in str(n.get("body") or "") for n in notes if isinstance(n, dict)):
            say("The trigger fired: the workflow added its tag and wrote its note")
            return
    if fired_seen:
        raise _Fail(f"the test workflow fired but its note step did not run within "
                    f"{_span(poll_seconds * poll_tries)}")
    raise _Fail(f"the test workflow did not fire within {_span(poll_seconds * poll_tries)}")


def _no_fire_reason(fire: Optional[bool], risky: list[Risky], complete: bool) -> str:
    if fire is False:
        return "you asked for a draft-only check"
    if not complete:
        return "couldn't read all your workflows" + (
            ", and some you have could be set off by the test contact" if risky else "")
    return "some of your published workflows could be set off by the test contact"


def run_selftest(client, contacts=api, *, fire: Optional[bool] = None,
                 run_id: Optional[str] = None, sleep=time.sleep,
                 poll_seconds: float = 5, poll_tries: int = 24,
                 emit: Optional[Callable[[str], Any]] = None) -> Result:
    """Scan, build, read back, fire (when safe or fire=True), and always clean up.

    fire=None fires only when the scan read everything and found nothing risky;
    fire=True means the member said yes; fire=False is draft-only. `emit` gets
    each progress line as it happens, so a run that is killed has already shown
    its run id and the command that cleans it up.
    """
    run = run_id or secrets.token_hex(3)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        if emit is not None:
            try:
                emit(line)
            except Exception:
                pass   # a broken output stream must never stop the cleanup

    risky: list[Risky] = []
    made = _Made()
    status, reason = "FAIL", ""
    leftovers: list[str] = []
    try:
        say(f"Run id {run}. If this stops early, run: ghl selftest --cleanup {run}")
        risky, complete = scan_risky(client, emit=say)
        if not complete:
            say("Could not read all your workflows")
        else:
            say(f"Checked your published workflows: "
                f"{len(risky) or 'none'} could be set off by the test")
        firing = (complete and not risky) if fire is None else bool(fire)
        wf_id = _build(client, run, made, say)
        _verify(client, run, wf_id, say)
        if firing:
            _fire(client, contacts, run, wf_id, made, say, sleep, poll_seconds, poll_tries)
            status = "PASS"
        else:
            status, reason = "PASS_NO_FIRE", _no_fire_reason(fire, risky, complete)
    except _Fail as e:
        reason = str(e)
    except KeyboardInterrupt:
        reason = "the selftest was stopped before it finished"
    except SystemExit:
        reason = "a GHL key stopped working partway through. Run: ghl doctor"
    except Exception as e:
        reason = f"something unexpected went wrong: {type(e).__name__}: {e}"
    finally:
        if made.built:
            try:
                say("Removing what the test made")
                leftovers = _cleanup(client, contacts, run, made)
            except KeyboardInterrupt:
                leftovers = [f"cleanup was stopped before it finished: "
                             f"remove with: ghl selftest --cleanup {run}"]
    if leftovers:
        if status != "FAIL":
            status, reason = "FAIL", "it could not remove everything it made"
    elif made.built:
        say("Removed everything it made")
    return Result(status, lines, leftovers, risky, reason,
                  contact_made=bool(made.contact_ids or made.contact_unknown))


# ── cleanup ───────────────────────────────────────────────────────────────

def _run_rows(client, name: str) -> Optional[tuple[list[dict], bool]]:
    """(rows named `name`, including inside the run's folders, top list full?), or None."""
    page = _list(client)
    if page is None:
        return None
    found = {r["id"]: r for r in page["rows"]
             if isinstance(r, dict) and r.get("id") and r.get("name") == name}
    for row in list(found.values()):
        if row.get("type") == "directory":
            for k in (_list(client, row["id"]) or {}).get("rows", []):
                if isinstance(k, dict) and k.get("id") and k.get("name") == name:
                    found.setdefault(k["id"], k)
    return list(found.values()), _full(page)


def _doc_maybe_present(client, wf_id: str) -> bool:
    """False only when GHL clearly says the workflow or folder is gone."""
    r = _guard(lambda: client.request("GET", f"/workflow/{client.location_id}/{wf_id}"))
    if isinstance(r, dict) and r.get("_error"):
        return r.get("code") not in _GONE_CODES
    if isinstance(r, dict):
        return r.get("deleted") is not True
    return True


def _http_status(e: BaseException) -> Optional[int]:
    """The HTTP status of a requests.HTTPError, or None for any other failure."""
    if not isinstance(e, requests.HTTPError):
        return None
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code is None:
        m = re.match(r"\s*(\d{3})\b", str(e))
        code = int(m.group(1)) if m else None
    return code


def _not_found(e: BaseException) -> bool:
    return _http_status(e) in _GONE_CODES


def _nothing_made(e: BaseException) -> bool:
    """True only when GHL clearly refused a create: a 4xx that is not a timeout or rate limit."""
    code = _http_status(e)
    if code is None or not 400 <= code < 500 or code in (408, 429):
        return False
    if code == 401:   # GHL mislabels a backend timeout as 401 "Command timed out"
        text = (getattr(getattr(e, "response", None), "text", None) or str(e)).lower()
        return not any(m in text for m in api.RETRY_401_MESSAGES)
    return True


def _find_run_contacts(contacts, loc: str, run: str, name: str) -> Optional[list[str]]:
    """Ids of contacts named exactly for this run, or None if the search failed."""
    found = _guard(lambda: contacts.post("/contacts/search", data={
        "locationId": loc, "query": f"setup check {run}", "pageLimit": 20}))
    if not (isinstance(found, dict) and isinstance(found.get("contacts"), list)):
        return None
    return [ct["id"] for ct in found["contacts"] if _is_run_contact(ct, name)]


def _contact_maybe_present(contacts, cid: str) -> bool:
    try:
        r = contacts.get(f"/contacts/{cid}")
    except Exception as e:
        return not _not_found(e)
    except SystemExit:
        return True
    return not (isinstance(r, dict) and (r.get("contact") or {}).get("deleted") is True)


def _is_run_contact(ct: Any, name: str) -> bool:
    if not isinstance(ct, dict) or not ct.get("id"):
        return False
    full = f"{ct.get('firstName') or ''} {ct.get('lastName') or ''}".strip().lower()
    return full == name


def _cleanup(client, contacts, run: str, made: Optional[_Made]) -> list[str]:
    """Delete everything named for `run` plus anything the run recorded by id.

    Every step is tried even when an earlier one failed, then GHL is read again:
    what is still there comes back as one leftover line each.
    """
    by_run = made is None
    made = made or _Made(built=True)
    name, start, fired = run_names(run)
    loc = client.location_id
    removal = f"remove with: ghl selftest --cleanup {run}"

    # 1. workflows (turned off first), then folders
    listed = _guard(lambda: _run_rows(client, name))
    items: dict[str, dict] = {r["id"]: r for r in (listed or ([], False))[0]}
    if made.workflow_id:
        items.setdefault(made.workflow_id, {"id": made.workflow_id, "type": "workflow"})
    if made.folder_id:
        items.setdefault(made.folder_id, {"id": made.folder_id, "type": "directory"})
    me: Optional[str] = None
    for row in [r for r in items.values() if r.get("type") != "directory"]:
        if row.get("status") != "draft" or made.published:
            if me is None:
                me = _guard(lambda: _user_id(client), "") or ""
            if me:
                _guard(lambda: client.request("PUT", f"/workflow/{loc}/change-status/{row['id']}",
                                              {"status": "draft", "updatedBy": me}))
        _guard(lambda: client.request("DELETE", f"/workflow/{loc}/{row['id']}"))
    held: set[str] = set()   # run folders the member put their own workflows in
    for row in [r for r in items.values() if r.get("type") == "directory"]:
        # Never delete a run folder that holds anything not named for this run:
        # GHL may take a folder's contents with it.
        kids = _guard(lambda: _list(client, row["id"]))
        if kids is None or len(kids["rows"]) >= LIST_LIMIT:
            continue
        if any(not isinstance(k, dict) or k.get("name") != name for k in kids["rows"]):
            held.add(row["id"])
            continue
        _guard(lambda: client.request("DELETE", f"/workflow/{loc}/{row['id']}"))

    # 2. the contact: by id, plus a search by name (finds a crashed run's contact;
    #    search can lag a brand-new contact by minutes, so ids come first)
    ids = list(made.contact_ids)
    found = _find_run_contacts(contacts, loc, run, name)
    ids += [cid for cid in dict.fromkeys(found or []) if cid not in ids]
    for cid in ids:
        _guard(lambda: contacts.delete(f"/contacts/{cid}"))

    # 3. tags, by the ids GHL lists for them
    before = _guard(lambda: _tags(client))
    for tag in (start, fired):
        tid = (before or {}).get(tag)
        if tid:
            _guard(lambda: client.request("DELETE", f"/locations/{loc}/tags/{tid}"))

    # 4. prove it
    left: list[str] = []
    after = _guard(lambda: _run_rows(client, name))
    still = {r["id"]: r for r in (after or ([], False))[0]}
    for wid, row in items.items():
        if wid not in still and _doc_maybe_present(client, wid):
            still[wid] = row
    for wid, row in sorted(still.items(), key=lambda kv: kv[1].get("type") == "directory"):
        kind = "folder" if row.get("type") == "directory" else "workflow"
        if wid in held:
            left.append(f"folder {row.get('name') or name} ({wid}): holds workflows the selftest "
                        f"did not make. Move them out in GHL, then run: ghl selftest --cleanup {run}")
        else:
            left.append(f"{kind} {row.get('name') or name} ({wid}): {removal}")
    if not items and not still and (after is None or not after[1]):
        left.append(f"workflow {name} (id unknown): could not check every workflow in "
                    f"your account, look for {name} in GHL")
    # Search again: a create that timed out and was retried can leave a second
    # contact with the same name, and search can miss a brand-new one the first time.
    again = _find_run_contacts(contacts, loc, run, name)
    extra = [cid for cid in dict.fromkeys(again or []) if cid not in ids]
    for cid in extra:
        _guard(lambda: contacts.delete(f"/contacts/{cid}"))
    ids += extra
    searched = found is not None or again is not None
    for cid in ids:
        if _contact_maybe_present(contacts, cid):
            left.append(f"contact {name} ({cid}): {removal}")
    if not ids and (made.contact_unknown or (by_run and not searched)):
        left.append(f"contact {name} (id unknown): {removal}")
    now = _guard(lambda: _tags(client))
    for tag in (start, fired):
        if now is not None:
            if tag in now:
                left.append(f"tag {tag} ({now[tag]}): {removal}")
        elif before is None or tag in before:
            left.append(f"tag {tag} ({(before or {}).get(tag) or 'id unknown'}): {removal}")
    return left


def cleanup(client, contacts, run_id: str) -> list[str]:
    """Delete everything named for one run (a kept or crashed one); return leftovers."""
    if not RUN_RE.fullmatch(run_id or ""):
        raise ValueError(f"not a selftest run id: {run_id!r}")
    return _cleanup(client, contacts, run_id, None)
