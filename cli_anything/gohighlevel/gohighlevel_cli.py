"""GoHighLevel CLI — Agent-usable command-line interface to the GHL API."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from datetime import datetime

import click
import requests

from cli_anything.gohighlevel import __version__
from cli_anything.gohighlevel.utils import ghl_client as api
from cli_anything.gohighlevel.utils import settings
from cli_anything.gohighlevel.utils import skill_install
from cli_anything.gohighlevel.utils.skill_install import GENERATED_MARK, install_skill, skills_dir  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _output(ctx: click.Context, data, label: str = ""):
    """Print data respecting --json flag."""
    as_json = ctx.obj.get("json", False)
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        if label:
            click.echo(f"\n{label}")
            click.echo("-" * len(label))
        click.echo(api.format_output(data))


def _handle_error(e: Exception):
    """Handle API errors with clear messages."""
    if isinstance(e, requests.exceptions.HTTPError):
        resp = e.response
        try:
            body = resp.json()
            msg = body.get("message") or body.get("msg") or json.dumps(body)
        except Exception:
            msg = resp.text
        click.echo(f"API Error ({resp.status_code}): {msg}", err=True)
    else:
        click.echo(f"Error: {e}", err=True)
    sys.exit(1)


def _epoch_ms(value: str | None) -> int | None:
    """Coerce a date to epoch milliseconds.

    GHL's calendar endpoints reject date strings: they require a number
    (`startDate must be a number conforming to the specified constraints`).
    Accepts epoch ms passthrough, YYYY-MM-DD, or full ISO 8601.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        raise click.BadParameter(
            f"{value!r} is not a date. Use YYYY-MM-DD, ISO 8601, or epoch milliseconds."
        )


def _loc(ctx: click.Context) -> str:
    """Get location ID from context or env."""
    return ctx.obj.get("location_id") or api._get_location_id()


# ---------------------------------------------------------------------------
# Main CLI Group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.option("--location-id", envvar="GHL_LOCATION_ID", default=None, help="GHL Location/Sub-account ID")
@click.option("--experimental", is_flag=True, help="Enable experimental commands (internal GHL API)")
@click.version_option(__version__, prog_name="ghl")
@click.pass_context
def cli(ctx, use_json, location_id, experimental):
    """GoHighLevel CLI — manage contacts, workflows, calendars, and more."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    ctx.obj["location_id"] = location_id
    ctx.obj["experimental"] = experimental
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ---------------------------------------------------------------------------
# REPL Command
# ---------------------------------------------------------------------------

@cli.command(hidden=True)
@click.pass_context
def repl(ctx):
    """Interactive REPL mode."""
    try:
        from cli_anything.gohighlevel.utils.repl_skin import ReplSkin
        skin = ReplSkin("gohighlevel", version=__version__)
        skin.print_banner()
        pt_session = skin.create_prompt_session()

        commands = {
            "contacts": "Manage contacts (list, get, create, update, delete, search, tags)",
            "opportunities": "Manage pipeline opportunities (list, get, create, update, delete)",
            "calendars": "Manage calendars and appointments (list, slots, book)",
            "workflows": "List and manage workflows",
            "conversations": "Manage conversations and messages",
            "emails": "Email campaigns (list, get, send)",
            "payments": "Transactions, invoices, orders",
            "forms": "Forms and submissions",
            "social": "Social media posts and analytics",
            "locations": "Location/sub-account management",
            "help": "Show this help",
            "exit": "Exit REPL",
        }

        while True:
            try:
                line = skin.get_input(pt_session, project_name="ghl")
                if not line or not line.strip():
                    continue
                parts = line.strip().split()
                cmd = parts[0].lower()

                if cmd in ("exit", "quit", "q"):
                    skin.print_goodbye()
                    break
                elif cmd == "help":
                    skin.help(commands)
                else:
                    # Pass through to Click CLI
                    try:
                        cli.main(parts, standalone_mode=False, obj=ctx.obj)
                    except SystemExit:
                        pass
                    except click.exceptions.UsageError as e:
                        skin.error(str(e))
            except (EOFError, KeyboardInterrupt):
                skin.print_goodbye()
                break
    except ImportError:
        click.echo("REPL requires prompt-toolkit. Install with: pip install prompt-toolkit")
        click.echo("Use subcommands directly instead: ghl contacts list")


# ===========================================================================
# CONTACTS
# ===========================================================================

@cli.group()
@click.pass_context
def contacts(ctx):
    """Manage contacts — list, get, create, update, delete, search, tags."""
    pass


@contacts.command("list")
@click.option("--limit", default=20, help="Number of contacts to return")
@click.option("--offset", "skip", default=0, help="Number to skip (for pagination)")
@click.option("--query", default=None, help="Search query string")
@click.pass_context
def contacts_list(ctx, limit, skip, query):
    """List contacts in the location."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "startAfterId": skip if skip else None}
        if query:
            params["query"] = query
        data = api.get("/contacts/", params=params)
        contacts_data = data.get("contacts", data)
        _output(ctx, contacts_data if ctx.obj["json"] else data, "Contacts")
    except Exception as e:
        _handle_error(e)


@contacts.command("get")
@click.argument("contact_id")
@click.pass_context
def contacts_get(ctx, contact_id):
    """Get a single contact by ID."""
    try:
        data = api.get(f"/contacts/{contact_id}")
        _output(ctx, data, "Contact Details")
    except Exception as e:
        _handle_error(e)


@contacts.command("create")
@click.option("--email", default=None, help="Contact email")
@click.option("--phone", default=None, help="Contact phone")
@click.option("--first-name", default=None, help="First name")
@click.option("--last-name", default=None, help="Last name")
@click.option("--name", default=None, help="Full name")
@click.option("--company", "company_name", default=None, help="Company name")
@click.option("--tag", "tags", multiple=True, help="Tags to add (repeatable)")
@click.option("--source", default=None, help="Contact source")
@click.pass_context
def contacts_create(ctx, email, phone, first_name, last_name, name, company_name, tags, source):
    """Create a new contact."""
    try:
        body = {"locationId": _loc(ctx)}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        if first_name:
            body["firstName"] = first_name
        if last_name:
            body["lastName"] = last_name
        if name:
            body["name"] = name
        if company_name:
            body["companyName"] = company_name
        if tags:
            body["tags"] = list(tags)
        if source:
            body["source"] = source
        data = api.post("/contacts/", data=body)
        _output(ctx, data, "Contact Created")
    except Exception as e:
        _handle_error(e)


@contacts.command("update")
@click.argument("contact_id")
@click.option("--email", default=None)
@click.option("--phone", default=None)
@click.option("--first-name", default=None)
@click.option("--last-name", default=None)
@click.option("--company", "company_name", default=None)
@click.option("--tag", "tags", multiple=True, help="Replace all tags")
@click.pass_context
def contacts_update(ctx, contact_id, email, phone, first_name, last_name, company_name, tags):
    """Update a contact by ID."""
    try:
        body = {}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        if first_name:
            body["firstName"] = first_name
        if last_name:
            body["lastName"] = last_name
        if company_name:
            body["companyName"] = company_name
        if tags:
            body["tags"] = list(tags)
        data = api.put(f"/contacts/{contact_id}", data=body)
        _output(ctx, data, "Contact Updated")
    except Exception as e:
        _handle_error(e)


@contacts.command("delete")
@click.argument("contact_id")
@click.pass_context
def contacts_delete(ctx, contact_id):
    """Delete a contact by ID."""
    try:
        data = api.delete(f"/contacts/{contact_id}")
        _output(ctx, data, "Contact Deleted")
    except Exception as e:
        _handle_error(e)


@contacts.command("search")
@click.argument("query")
@click.option("--limit", default=20)
@click.pass_context
def contacts_search(ctx, query, limit):
    """Search contacts with advanced filters."""
    try:
        # /contacts/search wants pageLimit (pageSize is rejected 422). Free-text `query`
        # matches name, email and phone — the old firstNameLowerCase filter could never
        # match an email or a last name.
        body = {
            "locationId": _loc(ctx),
            "pageLimit": limit,
            "query": query,
        }
        data = api.post("/contacts/search", data=body)
        _output(ctx, data, f"Search: '{query}'")
    except Exception as e:
        _handle_error(e)


@contacts.command("add-tag")
@click.argument("contact_id")
@click.argument("tags", nargs=-1, required=True)
@click.pass_context
def contacts_add_tag(ctx, contact_id, tags):
    """Add tags to a contact."""
    try:
        data = api.post(f"/contacts/{contact_id}/tags", data={"tags": list(tags)})
        _output(ctx, data, "Tags Added")
    except Exception as e:
        _handle_error(e)


@contacts.command("remove-tag")
@click.argument("contact_id")
@click.argument("tags", nargs=-1, required=True)
@click.pass_context
def contacts_remove_tag(ctx, contact_id, tags):
    """Remove tags from a contact."""
    try:
        data = api.delete(f"/contacts/{contact_id}/tags", data={"tags": list(tags)})
        _output(ctx, data, "Tags Removed")
    except Exception as e:
        _handle_error(e)


@contacts.command("notes")
@click.argument("contact_id")
@click.pass_context
def contacts_notes(ctx, contact_id):
    """List notes on a contact."""
    try:
        data = api.get(f"/contacts/{contact_id}/notes")
        _output(ctx, data, "Notes")
    except Exception as e:
        _handle_error(e)


@contacts.command("add-note")
@click.argument("contact_id")
@click.argument("body")
@click.option("--user-id", default=None, help="Author user ID")
@click.pass_context
def contacts_add_note(ctx, contact_id, body, user_id):
    """Add a note to a contact."""
    try:
        payload = {"body": body}
        if user_id:
            payload["userId"] = user_id
        data = api.post(f"/contacts/{contact_id}/notes", data=payload)
        _output(ctx, data, "Note Added")
    except Exception as e:
        _handle_error(e)


@contacts.command("tasks")
@click.argument("contact_id")
@click.pass_context
def contacts_tasks(ctx, contact_id):
    """List tasks on a contact."""
    try:
        data = api.get(f"/contacts/{contact_id}/tasks")
        _output(ctx, data, "Tasks")
    except Exception as e:
        _handle_error(e)


@contacts.command("add-task")
@click.argument("contact_id")
@click.argument("title")
@click.option("--due-date", required=True, help="Due date (ISO 8601 or YYYY-MM-DD)")
@click.option("--body", default=None, help="Task description")
@click.option("--assigned-to", default=None, help="User ID to assign to")
@click.pass_context
def contacts_add_task(ctx, contact_id, title, due_date, body, assigned_to):
    """Add a task to a contact."""
    try:
        payload = {"title": title, "dueDate": due_date, "completed": False}
        if body:
            payload["body"] = body
        if assigned_to:
            payload["assignedTo"] = assigned_to
        data = api.post(f"/contacts/{contact_id}/tasks", data=payload)
        _output(ctx, data, "Task Added")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# OPPORTUNITIES
# ===========================================================================

@cli.group()
@click.pass_context
def opportunities(ctx):
    """Manage pipeline opportunities — list, get, create, update, delete."""
    pass


@opportunities.command("list")
@click.option("--pipeline-id", default=None, help="Filter by pipeline ID")
@click.option("--limit", default=20)
@click.option("--status", default=None, type=click.Choice(["open", "won", "lost", "abandoned"]))
@click.pass_context
def opportunities_list(ctx, pipeline_id, limit, status):
    """List opportunities."""
    try:
        # /opportunities/search takes snake_case params — locationId/pipelineId are rejected (422)
        params = {"location_id": _loc(ctx), "limit": limit}
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        if status:
            params["status"] = status
        data = api.get("/opportunities/search", params=params)
        _output(ctx, data, "Opportunities")
    except Exception as e:
        _handle_error(e)


@opportunities.command("get")
@click.argument("opportunity_id")
@click.pass_context
def opportunities_get(ctx, opportunity_id):
    """Get opportunity details."""
    try:
        data = api.get(f"/opportunities/{opportunity_id}")
        _output(ctx, data, "Opportunity Details")
    except Exception as e:
        _handle_error(e)


@opportunities.command("create")
@click.option("--pipeline-id", required=True, help="Pipeline ID")
@click.option("--stage-id", required=True, help="Stage ID")
@click.option("--name", required=True, help="Opportunity name")
@click.option("--contact-id", required=True, help="Contact ID")
@click.option("--value", "monetary_value", default=None, type=float, help="Monetary value")
@click.option("--status", default="open", type=click.Choice(["open", "won", "lost", "abandoned"]))
@click.pass_context
def opportunities_create(ctx, pipeline_id, stage_id, name, contact_id, monetary_value, status):
    """Create a new opportunity."""
    try:
        body = {
            "locationId": _loc(ctx),
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "name": name,
            "contactId": contact_id,
            "status": status,
        }
        if monetary_value is not None:
            body["monetaryValue"] = monetary_value
        data = api.post("/opportunities/", data=body)
        _output(ctx, data, "Opportunity Created")
    except Exception as e:
        _handle_error(e)


@opportunities.command("update")
@click.argument("opportunity_id")
@click.option("--name", default=None)
@click.option("--stage-id", default=None, help="Move to stage")
@click.option("--status", default=None, type=click.Choice(["open", "won", "lost", "abandoned"]))
@click.option("--value", "monetary_value", default=None, type=float)
@click.pass_context
def opportunities_update(ctx, opportunity_id, name, stage_id, status, monetary_value):
    """Update an opportunity."""
    try:
        body = {}
        if name:
            body["name"] = name
        if stage_id:
            body["pipelineStageId"] = stage_id
        if status:
            body["status"] = status
        if monetary_value is not None:
            body["monetaryValue"] = monetary_value
        data = api.put(f"/opportunities/{opportunity_id}", data=body)
        _output(ctx, data, "Opportunity Updated")
    except Exception as e:
        _handle_error(e)


@opportunities.command("delete")
@click.argument("opportunity_id")
@click.pass_context
def opportunities_delete(ctx, opportunity_id):
    """Delete an opportunity."""
    try:
        data = api.delete(f"/opportunities/{opportunity_id}")
        _output(ctx, data, "Opportunity Deleted")
    except Exception as e:
        _handle_error(e)


@opportunities.command("pipelines")
@click.pass_context
def opportunities_pipelines(ctx):
    """List all pipelines."""
    try:
        data = api.get("/opportunities/pipelines", params={"locationId": _loc(ctx)})
        _output(ctx, data, "Pipelines")
    except Exception as e:
        _handle_error(e)


@opportunities.command("search")
@click.option("--query", "q", default=None, help="Free-text search")
@click.option("--pipeline-id", default=None, help="Filter by pipeline")
@click.option("--stage-id", "pipeline_stage_id", default=None, help="Filter by stage")
@click.option("--contact-id", default=None, help="Filter by contact")
@click.option("--assigned-to", default=None, help="Filter by assigned user ID")
@click.option("--status", type=click.Choice(["open", "won", "lost", "abandoned", "all"]), default=None)
@click.option("--limit", default=20)
@click.option("--page", default=1)
@click.pass_context
def opportunities_search(ctx, q, pipeline_id, pipeline_stage_id, contact_id, assigned_to, status, limit, page):
    """Search opportunities with filters.

    Note: unlike the rest of the GHL API, this endpoint takes snake_case
    parameters. Verified 2026-08-25: camelCase (pipelineId, contactId,
    assignedTo) is rejected with 422 "property ... should not exist".
    """
    try:
        params = {"location_id": _loc(ctx), "limit": limit, "page": page}
        for key, val in (
            ("q", q), ("pipeline_id", pipeline_id), ("pipeline_stage_id", pipeline_stage_id),
            ("contact_id", contact_id), ("assigned_to", assigned_to), ("status", status),
        ):
            if val:
                params[key] = val
        data = api.get("/opportunities/search", params=params)
        _output(ctx, data, "Opportunity Search")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# CALENDARS
# ===========================================================================

@cli.group()
@click.pass_context
def calendars(ctx):
    """Manage calendars, appointments, and availability slots."""
    pass


@calendars.command("list")
@click.pass_context
def calendars_list(ctx):
    """List all calendars."""
    try:
        data = api.get("/calendars/", params={"locationId": _loc(ctx)})
        _output(ctx, data, "Calendars")
    except Exception as e:
        _handle_error(e)


@calendars.command("get")
@click.argument("calendar_id")
@click.pass_context
def calendars_get(ctx, calendar_id):
    """Get calendar details."""
    try:
        data = api.get(f"/calendars/{calendar_id}")
        _output(ctx, data, "Calendar Details")
    except Exception as e:
        _handle_error(e)


@calendars.command("slots")
@click.argument("calendar_id")
@click.option("--start", required=True, help="Start date (YYYY-MM-DD or epoch ms)")
@click.option("--end", required=True, help="End date (YYYY-MM-DD or epoch ms)")
@click.option("--timezone", default="America/New_York")
@click.pass_context
def calendars_slots(ctx, calendar_id, start, end, timezone):
    """Get available appointment slots."""
    try:
        # calendarId belongs in the path only; sending it as a query param is a
        # 422 ("property calendarId should not exist"). Dates must be epoch ms.
        params = {"startDate": _epoch_ms(start), "endDate": _epoch_ms(end)}
        if timezone:
            params["timezone"] = timezone
        data = api.get(f"/calendars/{calendar_id}/free-slots", params=params)
        _output(ctx, data, "Available Slots")
    except Exception as e:
        _handle_error(e)


@calendars.command("appointments")
@click.option("--calendar-id", default=None, help="Filter by calendar ID")
@click.option("--contact-id", default=None, help="Filter by contact ID")
@click.option("--start", default=None, help="Start date filter")
@click.option("--end", default=None, help="End date filter")
@click.pass_context
def calendars_appointments(ctx, calendar_id, contact_id, start, end):
    """List appointments."""
    try:
        params = {"locationId": _loc(ctx)}
        if calendar_id:
            params["calendarId"] = calendar_id
        if contact_id:
            params["contactId"] = contact_id
        # /calendars/events/appointments 404s; the real endpoint is /calendars/events
        params["startTime"] = _epoch_ms(start) if start else _epoch_ms(
            datetime.now().strftime("%Y-%m-%d")
        )
        params["endTime"] = _epoch_ms(end) if end else params["startTime"] + 30 * 86400 * 1000
        data = api.get("/calendars/events", params=params)
        _output(ctx, data, "Appointments")
    except Exception as e:
        _handle_error(e)


@calendars.command("book")
@click.option("--calendar-id", required=True, help="Calendar ID")
@click.option("--contact-id", required=True, help="Contact ID")
@click.option("--slot-id", required=True, help="Slot ID from 'slots' command")
@click.option("--start", required=True, help="Start time (ISO 8601)")
@click.option("--end", required=True, help="End time (ISO 8601)")
@click.option("--title", default=None, help="Appointment title")
@click.pass_context
def calendars_book(ctx, calendar_id, contact_id, slot_id, start, end, title):
    """Book an appointment."""
    try:
        body = {
            "calendarId": calendar_id,
            "locationId": _loc(ctx),
            "contactId": contact_id,
            "selectedSlot": slot_id,
            "startTime": start,
            "endTime": end,
        }
        if title:
            body["title"] = title
        data = api.post("/calendars/events/appointments", data=body)
        _output(ctx, data, "Appointment Booked")
    except Exception as e:
        _handle_error(e)


@calendars.command("groups")
@click.pass_context
def calendars_groups(ctx):
    """List calendar groups."""
    try:
        data = api.get("/calendars/groups", params={"locationId": _loc(ctx)})
        _output(ctx, data, "Calendar Groups")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# WORKFLOWS
# ===========================================================================

def _require_experimental(ctx: click.Context):
    """Guard: exit if --experimental flag not set."""
    if not ctx.obj.get("experimental"):
        click.echo(
            "Error: This command requires --experimental flag (uses internal GHL API).\n"
            "Usage: ghl --experimental workflows create ...",
            err=True,
        )
        sys.exit(1)


INTERNAL_AUTH_HELP = """
GoHighLevel's internal API refused this location (401).

The usual cause is NOT an expired token. It is that GHL_LOCATION_ID and
GHL_FIREBASE_REFRESH_TOKEN come from two DIFFERENT GoHighLevel accounts.

  * GHL_API_KEY (the PIT) is issued for one specific location and works on the
    public API no matter which agency you are logged into in the browser.
  * GHL_FIREBASE_REFRESH_TOKEN is a browser session for one agency, and can only
    reach locations inside THAT agency.

If the location is not in the agency you grabbed the token from, every
location-scoped internal call returns 401 forever, and re-grabbing the token
changes nothing. Two sub-accounts sharing a name makes this easy to miss.

Run `ghl --experimental workflows auth-check` - it lists the locations this
Firebase session CAN reach. Then either:
  a) set GHL_LOCATION_ID to one of those, or
  b) log into the agency that owns the location you want, in the browser, and
     re-grab the token (docs/get-firebase-token.md).
"""


def _internal_auth_ok(client) -> bool:
    """Cheap read against the internal API to prove the Firebase session is live.

    The refresh token minting a JWT is NOT proof of access: GHL expires the
    browser session server-side while Google keeps issuing valid tokens.
    `request()` returns None only on 401/403, which is exactly this case.
    """
    return client.request("GET", f"/workflow/{client.location_id}/list") is not None


def _get_internal_client(ctx: click.Context, preflight: bool = True,
                         location_id: str | None = None):
    """Get an InternalGHLClient (lazy import to avoid dep when not needed).

    `location_id` overrides the configured location, e.g. a clone's target.
    """
    from cli_anything.gohighlevel.utils.ghl_internal_client import TokenManager, InternalGHLClient
    token_mgr = TokenManager()
    client = InternalGHLClient(token_mgr, location_id or _loc(ctx))
    if preflight and not _internal_auth_ok(client):
        click.echo(INTERNAL_AUTH_HELP, err=True)
        sys.exit(4)
    return client


@cli.command("doctor")
@click.option("--only", type=click.Choice(["api-key", "workflow-token"]), default=None,
              help="Check just one key")
@click.pass_context
def doctor_cmd(ctx, only):
    """Check both keys and whether they reach your sub-account."""
    from cli_anything.gohighlevel.utils import doctor
    checks = doctor.run_doctor(only, location_id=ctx.obj.get("location_id"))
    _print_checks(checks)
    sys.exit(0 if all(c.ok for c in checks) else 1)


def _print_checks(checks):
    """The doctor lines, as `ghl doctor` shows them."""
    for c in checks:
        if c.ok:
            click.echo(f"  {c.name}: OK {c.detail}")
        else:
            click.echo(f"  {c.name}: PROBLEM {c.detail}")
            if c.fix:
                click.echo(f"     fix: {c.fix}")


NEXT_SKILL = "Next: ghl skill install, so your Claude Code skill matches this version."


@cli.command("update")
@click.option("--undo", "undo_", is_flag=True, help="Put back the version from before the last update")
@click.option("--force", is_flag=True, help="Download the latest release even if this one is current")
@click.pass_context
def update_cmd(ctx, undo_, force):
    """Update the GHL CLI to the latest release. Your keys and settings are kept."""
    from cli_anything.gohighlevel.utils import doctor, updater
    try:
        if undo_:
            r = updater.undo(updater.ROOT)
            click.echo(f"Put back version {r['to']} (was {r['from']}).")
            if r.get("setup_changed"):
                click.echo(updater.reinstall(updater.ROOT))
            click.echo(NEXT_SKILL)
            return
        r = updater.update(updater.ROOT, force=force)
    except updater.UpdateError as e:
        click.echo(f"update: {e}", err=True)
        sys.exit(1)
    if r["status"] == "current":
        click.echo(f"Already on the latest release ({r['to']}). Nothing to do.")
        return
    click.echo(f"Updated {r['from']} -> {r['to']}: {len(r['added'])} new file(s), "
               f"{len(r['removed'])} removed. Kept: your keys and settings, .env and your own files.")
    if r["edited"]:
        click.echo(f"You had edited {len(r['edited'])} file(s); your versions are saved in "
                   f"{r['backup']}: {', '.join(r['edited'][:10])}")
    if r.get("setup_changed"):
        click.echo(updater.reinstall(updater.ROOT))
    click.echo("Checking your setup with the new version:")
    rc = updater.run_doctor_subprocess(click.echo, ctx.obj.get("location_id"))
    if rc is None:
        click.echo("(The new version could not be started for the check, so this check used the "
                   "old code. Run `ghl doctor` yourself to be sure.)")
        try:
            checks = doctor.run_doctor(None, location_id=ctx.obj.get("location_id"))
        except Exception as e:
            click.echo(f"  could not run the check ({type(e).__name__}). Run `ghl doctor` yourself.")
            checks = []
        _print_checks(checks)
        rc = 0 if all(c.ok for c in checks) else 1
    if rc != 0:
        click.echo("The update itself worked. The check above found a problem; if it started with "
                   "this update, `ghl update --undo` puts the previous version back.")
    else:
        click.echo("To go back to the previous version: ghl update --undo")
    click.echo(NEXT_SKILL)


# ---------------------------------------------------------------------------
# keys / config (member setup)
# ---------------------------------------------------------------------------

# The api-key shape is deliberately loose. GHL has issued more than one Private
# Integration key format (pit-<uuid>, and newer keys with a short segment after
# pit-), and a strict shape rejected real keys. This check only catches a wrong
# clipboard (a URL, the workflow token, a cut-off copy). `ghl doctor`, which runs
# right after saving, is what proves the key works.
API_KEY_MIN = 30   # characters after "pit-"; the classic pit-<uuid> key has 36
KEY_SHAPES = {
    "api-key": ("GHL_API_KEY",
                r"pit-(?=[A-Za-z0-9_-]{%d,120}$)[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+" % API_KEY_MIN),
    "workflow-token": ("GHL_FIREBASE_REFRESH_TOKEN", r"[A-Za-z0-9_\-]{200,}"),
}


def _describe_wrong_input(kind: str, value: str) -> str:
    """Member-facing mismatch message. Only length and kind, never any content."""
    import re
    n = len(value)
    low = value.lower()
    if kind == "api-key":
        what, again = "a GHL Private Integration key", 'Copy the key again in GHL, then say "copied".'
        other = "the GHL login token" if re.fullmatch(KEY_SHAPES["workflow-token"][1], value) else None
    else:
        what = "the GHL login token"
        again = ("Run the snippet in docs/get-firebase-token.md again, copy what it gives you, "
                 'then say "copied".')
        other = ("a GHL Private Integration key"
                 if re.fullmatch(KEY_SHAPES["api-key"][1], value) else None)
    if not value:
        kind_txt = "it's empty"
    elif "://" in value or low.startswith("www."):
        kind_txt = f"it's {n} characters and looks like a web address"
    elif other:
        kind_txt = f"it's {n} characters and looks like {other}"
    elif n < (4 + API_KEY_MIN if kind == "api-key" else 200):
        kind_txt = f"it's {n} characters, which is too short"
    else:
        kind_txt = f"it's {n} characters and not the right shape"
    return f"That doesn't look like {what} ({kind_txt}). {again}"


@cli.group()
def keys():
    """Save your GHL keys from the clipboard (never printed)."""


@keys.command("paste")
@click.argument("kind", type=click.Choice(["api-key", "workflow-token"]))
@click.option("--from-stdin", is_flag=True, help="Read the key from stdin instead of the clipboard")
def keys_paste(kind, from_stdin):
    """Validate, save and check a key copied to the clipboard."""
    import re
    from cli_anything.gohighlevel.utils import clipboard, doctor
    name, pattern = KEY_SHAPES[kind]
    # No --from-stdin hint anywhere here: in Claude Code the only way to feed stdin is a key
    # pasted into the chat. The file route keeps the key between the member and their editor.
    keys_file = settings.settings_home() / "keys.env"
    file_route = (f"open {keys_file} in a plain text editor, add a line {name}= with the key "
                  f"right after the =, save it, then run ghl doctor. Never paste a key into a chat.")
    try:
        raw = sys.stdin.read() if from_stdin else clipboard.read_clipboard()
    except clipboard.ClipboardUnavailable:
        click.echo(f"The clipboard can't be read on this computer. Put the key in the file "
                   f"instead: {file_route}", err=True)
        sys.exit(2)
    value = (raw or "").strip()
    if not re.fullmatch(pattern, value):
        click.echo(_describe_wrong_input(kind, value), err=True)
        if not value and not from_stdin:
            # An empty read with exit 0 is also what a clipboard blocked by a privacy
            # setting or a remote session looks like, so say the other way in.
            click.echo(f"If you did copy it, this computer may be blocking the clipboard. Put "
                       f"the key in the file instead: {file_route}", err=True)
        sys.exit(2)
    del raw
    try:
        settings.save_key(name, value)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    os.environ[name] = value
    if not from_stdin:
        try:
            clipboard.clear_clipboard()
        except Exception:
            pass
    only = kind
    if kind == "api-key" and not os.environ.get("GHL_LOCATION_ID", "").strip():
        click.echo("saved. Next: set your sub-account.")
        return

    def scrub(text):
        return (text or "").replace(value, "<your key>")

    checks = doctor.run_doctor(only)
    bad = [c for c in checks if not c.ok]
    if not bad:
        click.echo("saved, works")
        return
    for c in bad:
        click.echo(f"saved, but: {scrub(c.detail)}")
        if c.fix:
            click.echo(f"     fix: {scrub(c.fix)}")
    sys.exit(1)


@cli.group("config")
def config_group():
    """Member settings."""


@config_group.command("set-location")
@click.argument("url_or_id")
def config_set_location(url_or_id):
    """Save your sub-account from the GHL address bar (or its id)."""
    try:
        loc, domain = settings.parse_location_url(url_or_id)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    settings.save_config(location_id=loc, app_domain=domain)
    os.environ["GHL_LOCATION_ID"] = loc
    if domain:
        os.environ["GHL_APP_DOMAIN"] = domain
    click.echo(f"Sub-account set: {loc}" + (f" (domain {domain})" if domain else ""))


@cli.group("skill")
def skill_group():
    """Your Claude Code skill for this CLI."""


@skill_group.command("install")
@click.option("--force", is_flag=True,
              help="Overwrite an existing ghl-cli skill that this command did not write.")
def skill_install_cmd(force):
    """Write ~/.claude/skills/ghl-cli/SKILL.md with this install's own paths."""
    ghl = skill_install.find_ghl()
    if ghl is None:
        click.echo("Could not find the ghl script next to this Python, so the skill "
                   "would point nowhere. Fix: re-run the install phase in SETUP.md, "
                   "then run ghl skill install again.", err=True)
        sys.exit(2)
    try:
        path = install_skill(ghl, skill_install.setup_md_path(), force=force)
    except skill_install.SkillExists as exc:
        click.echo(f"{exc} already exists and ghl did not write it, so it was left "
                   "alone. To replace it with the generated skill, run: "
                   "ghl skill install --force", err=True)
        sys.exit(2)
    click.echo(f"Skill written: {path}")
    backup = getattr(install_skill, "last_backup", None)
    if backup:
        click.echo(f"Your old file was saved as {backup}")
    click.echo("Open a new Claude Code chat and it will know how to drive ghl.")


# ---------------------------------------------------------------------------
# selftest (member setup)
# ---------------------------------------------------------------------------

def _print_risky(risky) -> None:
    """One line per published workflow the test could set off: name, trigger, actions."""
    for r in risky:
        does = ", ".join(r.actions) if r.actions else "no steps it could read"
        click.echo(f"  - {r.name} (trigger: {r.trigger_type}): {does}")


@contextlib.contextmanager
def _stops_raise_interrupt():
    """SIGTERM and SIGHUP raise KeyboardInterrupt while inside, so the selftest's
    cleanup in `finally` still runs when a caller times out or a terminal closes.
    The previous handlers come back afterwards. SIGKILL cannot be caught, which
    is why the run id and its cleanup command are printed before the first write.
    """
    import signal

    def _raise(signum, frame):
        raise KeyboardInterrupt

    previous = {}
    for name in ("SIGTERM", "SIGHUP"):   # Windows has no SIGHUP
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, _raise)
        except (ValueError, OSError):    # not the main thread
            pass
    try:
        yield
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)


@cli.command("selftest")
@click.option("--fire", is_flag=True,
              help="Fire the test trigger even if published workflows could be set off. "
                   "Only after the member says yes.")
@click.option("--draft-only", is_flag=True,
              help="Build, read back and delete the test workflow without firing it.")
@click.option("--check-only", is_flag=True,
              help="Only list the published workflows the test could set off. Writes nothing.")
@click.option("--cleanup", "cleanup_run", metavar="RUN", default=None,
              help="Remove what an earlier selftest run left behind.")
@click.pass_context
def selftest_cmd(ctx, fire, draft_only, check_only, cleanup_run):
    """Prove a workflow trigger fires in your account, then remove everything it made."""
    from cli_anything.gohighlevel.utils import selftest as st
    if fire and draft_only:
        raise click.UsageError("Use --fire or --draft-only, not both.")
    if sum((fire or draft_only, check_only, cleanup_run is not None)) > 1:
        raise click.UsageError("--check-only and --cleanup each run on their own.")
    if cleanup_run is not None and not st.RUN_RE.fullmatch(cleanup_run):
        raise click.UsageError(f"{cleanup_run!r} is not a selftest run id. "
                               "A run id is 6 characters, like a1b2c3.")
    client = _get_internal_client(ctx)

    if check_only:
        risky, complete = st.scan_risky(client, emit=click.echo)
        if not complete:
            click.echo("Could not read all your workflows, so some that could be set off "
                       "may not be listed.")
        if risky:
            click.echo(f"{len(risky)} published workflow(s) could be set off by the selftest:")
            _print_risky(risky)
        elif complete:
            click.echo("No published workflow would be set off by the selftest.")
        if not complete:
            click.echo("The selftest will not fire unless you pass --fire.")
        sys.exit(0)

    if cleanup_run is not None:
        left = st.cleanup(client, api, cleanup_run)
        if left:
            click.echo(f"FAIL: part of selftest run {cleanup_run} is still in your account:")
            for line in left:
                click.echo(f"  {line}")
            sys.exit(1)
        click.echo(f"Removed everything from selftest run {cleanup_run}. Nothing is left.")
        sys.exit(0)

    # Lines stream as they happen: the first one names the run and its cleanup
    # command before anything is written, in case this process is killed.
    with _stops_raise_interrupt():
        result = st.run_selftest(client, api, fire=True if fire else (False if draft_only else None),
                                 emit=lambda line: click.echo(f"  {line}"))
    if result.status == "PASS":
        click.echo("PASS")
    elif result.status == "PASS_NO_FIRE":
        click.echo("PASS, firing not tested")
        click.echo(f"Why: {result.reason}")
        if result.risky:
            click.echo("These published workflows could be set off by the test contact:")
            _print_risky(result.risky)
        click.echo("To test firing later: ghl selftest --fire")
        sys.exit(0)
    else:
        click.echo(f"FAIL: {result.reason}")
        if result.leftovers:
            click.echo("Still in your account:")
            for line in result.leftovers:
                click.echo(f"  {line}")
    if result.risky and result.contact_made:
        # Fired with --fire: the selftest removes only what it made.
        click.echo("The test contact may have set off these published workflows. "
                   "Anything they did was not undone:")
        _print_risky(result.risky)
    sys.exit(0 if result.status == "PASS" else 1)


@cli.group()
@click.pass_context
def workflows(ctx):
    """List and manage workflows. Create commands require --experimental."""
    pass


@workflows.command("list")
@click.pass_context
def workflows_list(ctx):
    """List all workflows."""
    try:
        data = api.get("/workflows/", params={"locationId": _loc(ctx)})
        _output(ctx, data, "Workflows")
    except Exception as e:
        _handle_error(e)


@workflows.command("enroll")
@click.option("--contact-id", required=True, help="Contact ID to enroll")
@click.option("--workflow-id", required=True, help="Workflow ID")
@click.pass_context
def workflows_enroll(ctx, contact_id, workflow_id):
    """Enroll a contact in a workflow (public API)."""
    try:
        data = api.post(f"/contacts/{contact_id}/workflow/{workflow_id}")
        _output(ctx, data, "Contact Enrolled")
    except Exception as e:
        _handle_error(e)


@workflows.command("remove")
@click.option("--contact-id", required=True, help="Contact ID to remove")
@click.option("--workflow-id", required=True, help="Workflow ID")
@click.pass_context
def workflows_remove(ctx, contact_id, workflow_id):
    """Remove a contact from a workflow (public API)."""
    try:
        data = api.delete(f"/contacts/{contact_id}/workflow/{workflow_id}")
        _output(ctx, data, "Contact Removed from Workflow")
    except Exception as e:
        _handle_error(e)


@workflows.command("create")
@click.option("--name", required=True, help="Workflow name")
@click.option("--folder", default=None, help="Folder name (created if needed)")
@click.option("--from-json", "json_file", required=True, type=click.Path(exists=True),
              help="Campaign JSON file path")
@click.pass_context
def workflows_create(ctx, name, folder, json_file):
    """Create workflows from a campaign JSON file (experimental, internal API).

    The JSON file should contain a campaign dict where each key is a workflow
    with 'name', 'templates' (linked steps), and optional 'tag' (trigger).
    """
    _require_experimental(ctx)
    try:
        from cli_anything.gohighlevel.utils.workflow_builder import CampaignBuilder

        with open(json_file) as f:
            campaign = json.load(f)

        # `workflows create-step` emits a bare LIST of steps, but build() wants a
        # campaign dict. Accept both so the documented create-step -> create flow
        # works without hand-editing the file in between.
        if isinstance(campaign, list):
            campaign = {"workflow": {"name": name, "templates": campaign}}

        client = _get_internal_client(ctx)
        builder = CampaignBuilder(client)
        stats = builder.build(campaign, folder or name)

        if ctx.obj["json"]:
            click.echo(json.dumps(stats, indent=2, default=str))
        else:
            click.echo(builder.format_summary())
        # Previously this exited 0 even when nothing was created.
        if stats.get("errors"):
            sys.exit(1)
    except Exception as e:
        _handle_error(e)


@workflows.command("auth-check")
@click.pass_context
def workflows_auth_check(ctx):
    """Check whether workflow CREATION is currently possible.

    Workflow creation needs GHL's internal API, which uses a Firebase session
    token separate from GHL_API_KEY. This reports exactly which of the three
    stages is failing.
    """
    _require_experimental(ctx)
    import time as _time
    from cli_anything.gohighlevel.utils.ghl_internal_client import TokenManager, InternalGHLClient

    ok = True

    # Stage 1 - is a refresh token present?
    raw = os.environ.get("GHL_FIREBASE_REFRESH_TOKEN", "").strip()
    if raw:
        click.echo(f"  [1/3] workflow token saved               OK ({len(raw)} chars)")
    else:
        click.echo("  [1/3] workflow token saved               MISSING")
        click.echo("\n  No workflow token is saved yet. Run: ghl keys paste workflow-token")
        click.echo("  (copy it first with the snippet in docs/get-firebase-token.md; "
                   "never paste it into a chat)")
        sys.exit(4)

    # Stage 2 - will Google mint a JWT from it?
    token_mgr = TokenManager()
    kind, token = token_mgr.verify()
    if kind != "ok":
        click.echo("  [2/3] Google mints a JWT                 FAILED")
        if kind == "rate_limited":
            click.echo("\n  Google is rate limiting token checks right now. The token is "
                       "fine. Wait a minute, then run this again.")
        elif kind == "unreachable":
            click.echo("\n  Could not reach Google. Check the internet connection, then "
                       "run this again.")
        else:
            click.echo("\n  The refresh token itself is revoked. Get a new one:")
            click.echo("  docs/get-firebase-token.md")
        sys.exit(4)
    click.echo("  [2/3] Google mints a JWT                 OK")
    from cli_anything.gohighlevel.utils import doctor
    claims = doctor.decode_claims(token)
    if claims:
        if claims.get("auth_time"):
            age_days = int((_time.time() - claims["auth_time"]) / 86400)
            click.echo(f"        session age: {age_days} days  "
                       f"(user {claims.get('user_id', '?')})")
        # Scope is the real diagnostic. An agency token with locations=[] cannot
        # do location-scoped work, and a fresh token does not change that.
        locs = claims.get("locations")
        n_locs = len(locs) if isinstance(locs, list) else "?"
        click.echo(f"        scope: type={claims.get('type')} "
                   f"role={claims.get('role')} locations={n_locs}")
        perms = claims.get("permissions") or {}
        if perms:
            click.echo(f"        workflows_enabled={perms.get('workflows_enabled')} "
                       f"read_only={perms.get('workflows_read_only')}")
        # NOTE: locations=[] is NORMAL for an agency owner and is NOT the
        # failure cause. Access is decided by which company the location is in.
        if claims.get("company_id"):
            click.echo(f"        company: {claims['company_id']}")

    # Stage 3 - can this session reach the CONFIGURED location?
    target = _loc(ctx)
    client = InternalGHLClient(token_mgr, target)
    if _internal_auth_ok(client):
        click.echo(f"  [3/3] location {target}   ACCESSIBLE")
        click.echo("\n  Workflow creation is available.")
        sys.exit(0)

    click.echo(f"  [3/3] location {target}   REFUSED (401)")

    # Explain WHY: list what this session can actually reach.
    company = claims.get("company_id")
    locs = doctor.reachable_locations(client, claims)
    if locs:
        whose = f"company {company}" if company else "this login"
        click.echo(f"\n  This Firebase session belongs to {whose}")
        click.echo(f"  and can reach {len(locs)} location(s). "
                   f"{target} is NOT among them:\n")
        try:
            # No GHL key saved: _get_token() prints its own advice and exits. The
            # name is only a nicety here, so keep listing the locations.
            with contextlib.redirect_stderr(io.StringIO()):
                target_name = (api.get(f"/locations/{target}")["location"]["name"] or "").strip().lower()
        except (Exception, SystemExit):
            target_name = None
        for lid, lname in locs[:15]:
            marker = "  <-- name matches, DIFFERENT id" if (
                target_name and lid != target
                and (lname or "").strip().lower() == target_name) else ""
            click.echo(f"      {lid}  {lname or ''}".rstrip() + marker)
        if len(locs) > 15:
            click.echo(f"      ... and {len(locs) - 15} more")
        click.echo("\n  Set GHL_LOCATION_ID to one of the IDs above to use workflow")
        click.echo("  creation, or grab the token from the agency that owns "
                   f"{target}.")
    click.echo(INTERNAL_AUTH_HELP, err=True)
    sys.exit(4)


@workflows.command("preview")
@click.option("--from-spec", "spec_file", required=True, type=click.Path(exists=True),
              help="Workflow spec JSON")
@click.pass_context
def workflows_preview(ctx, spec_file):
    """Compile a workflow spec and print the step tree. Deploys NOTHING.

    Use this before 'workflows build' to check branching is shaped the way you
    expect. It makes no API calls, so it works without any credentials.
    """
    from cli_anything.gohighlevel.utils.workflow_spec import compile_spec, describe, SpecError
    try:
        with open(spec_file) as f:
            spec = json.load(f)
        steps = compile_spec(spec)
    except SpecError as e:
        click.echo(f"Spec error: {e}", err=True)
        sys.exit(2)
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON in {spec_file}: {e}", err=True)
        sys.exit(2)
    if ctx.obj["json"]:
        click.echo(json.dumps(steps, indent=2, default=str))
        return
    click.echo(f"\n{spec.get('name', '(unnamed)')}")
    from cli_anything.gohighlevel.utils.workflow_triggers import compile_triggers
    for trig in compile_triggers(spec):
        click.echo(f"  trigger: {trig['type']} ({trig['name']})")
    click.echo()
    click.echo(describe(steps))
    branches = len([s for s in steps if s.get("nodeType", "").startswith("branch-")])
    click.echo(f"\n{len(steps)} nodes, {branches} branch(es). Nothing deployed.")


@workflows.command("build")
@click.option("--from-spec", "spec_file", required=True, type=click.Path(exists=True),
              help="Workflow spec JSON")
@click.option("--name", default=None, help="Override the name in the spec")
@click.option("--folder", default=None, help="Folder name (created if needed)")
@click.pass_context
def workflows_build(ctx, spec_file, name, folder):
    """Compile a spec and deploy it, branching and triggers included (experimental).

    Unlike 'create', this accepts nested if_else branches and any trigger in
    workflow_triggers (tag, form, survey, appointment booked or status, pipeline
    stage, opportunity status, contact created, customer replied, raw). Form,
    survey, calendar and pipeline ids are checked before anything is deployed.
    Preview it first with 'workflows preview'. Workflows are created as DRAFT.
    """
    _require_experimental(ctx)
    from cli_anything.gohighlevel.utils.workflow_spec import compile_spec, SpecError
    from cli_anything.gohighlevel.utils.workflow_builder import CampaignBuilder
    from cli_anything.gohighlevel.utils.workflow_triggers import (
        check_trigger_refs, compile_triggers, ref_kinds, spec_triggers,
    )
    try:
        with open(spec_file) as f:
            spec = json.load(f)
        steps = compile_spec(spec)
        triggers = compile_triggers(spec)
    except SpecError as e:
        click.echo(f"Spec error: {e}", err=True)
        sys.exit(2)
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON in {spec_file}: {e}", err=True)
        sys.exit(2)

    wf_name = name or spec.get("name")
    if not wf_name:
        click.echo("No workflow name. Set 'name' in the spec or pass --name.", err=True)
        sys.exit(2)

    # A trigger pointing at a form, survey, calendar or stage that does not
    # exist saves fine in GHL and then never fires. Check before deploying.
    raw = spec_triggers(spec)
    kinds = ref_kinds(raw)
    if kinds:
        lookups = _trigger_lookups(ctx, kinds)
        problems = check_trigger_refs(raw, lookups)
        if problems:
            for p in problems:
                click.echo(f"Trigger error: {p}", err=True)
            click.echo("Nothing deployed.", err=True)
            sys.exit(2)
        unknown = sorted(k for k in kinds if lookups.get(k) is None)
        if unknown:
            click.echo(f"Note: could not check the {', '.join(unknown)} id(s): GHL did not "
                       "return the full list. A wrong id saves fine but the trigger never "
                       "fires.", err=True)
    if not triggers:
        click.echo("Note: no trigger in the spec. Contacts only enter if added directly.",
                   err=True)

    wf_def = {"name": wf_name, "templates": steps, "triggers": triggers}
    try:
        client = _get_internal_client(ctx)
        builder = CampaignBuilder(client)
        stats = builder.build({"workflow": wf_def}, folder or wf_name)
        if ctx.obj["json"]:
            click.echo(json.dumps(stats, indent=2, default=str))
        else:
            click.echo(builder.format_summary())
        if stats.get("errors"):
            sys.exit(1)
    except Exception as e:
        _handle_error(e)


def _trigger_lookups(ctx: click.Context, kinds: set) -> dict:
    """Ids that exist in the sub-account, per kind, for the trigger id check.

    Read through the internal API, which reaches any sub-account the Firebase
    session can (the public API key reaches only its own). A kind that cannot be
    read in full is None: unknown, never assumed missing.
    """
    from cli_anything.gohighlevel.utils.workflow_io import asset_lookups, list_assets
    lookups = asset_lookups(list_assets(_get_internal_client(ctx)))
    return {k: lookups.get(k) for k in kinds}


def _snapshot_dir() -> str:
    """Where `workflows edit` keeps the pre-edit copy that its undo restores."""
    return os.environ.get("GHL_SNAPSHOT_DIR") or os.path.expanduser("~/.ghl-cli/snapshots")


def _print_diff(diff: dict) -> None:
    for key in ("added", "removed", "changed"):
        for step_name in diff[key]:
            click.echo(f"    {key:8} {step_name}")
    if not any(diff.values()):
        click.echo("    (no step changes)")


@workflows.command("get")
@click.argument("workflow_id")
@click.option("--output-file", type=click.Path(dir_okay=False), default=None,
              help="Save the export (steps, triggers, version) to this file")
@click.pass_context
def workflows_get(ctx, workflow_id, output_file):
    """Read a workflow's steps and triggers (experimental, internal API).

    Read only. The export file is what 'workflows edit' applies back.
    """
    _require_experimental(ctx)
    from cli_anything.gohighlevel.utils.workflow_io import WorkflowIOError, read_workflow
    from cli_anything.gohighlevel.utils.workflow_spec import describe
    try:
        export = read_workflow(_get_internal_client(ctx), workflow_id)
    except WorkflowIOError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if output_file:
        with open(output_file, "w") as f:
            json.dump(export, f, indent=2)
    if ctx.obj["json"]:
        click.echo(json.dumps(export, indent=2, default=str))
        return
    click.echo(f"\n{export['name']}  [status: {export['status'] or 'unset'}, "
               f"version {export['version']}]")
    triggers = export["triggers"]
    click.echo(f"\nTriggers ({len(triggers)}):")
    for t in triggers:
        state = "active" if t.get("active") else "inactive"
        click.echo(f"  - {t.get('type')}: {t.get('name')} ({state})")
    if not triggers:
        click.echo("  (none: contacts only enter if added to it directly)")
    click.echo(f"\nSteps ({len(export['templates'])}):")
    click.echo(describe(export["templates"]) or "  (none)")
    if output_file:
        click.echo(f"\nSaved to {output_file}")


@workflows.command("edit")
@click.argument("workflow_id")
@click.option("--from-json", "json_file", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="An export from 'workflows get', a snapshot, or a bare list of steps")
@click.option("--name", default=None, help="Rename the workflow too")
@click.option("--expect-version", type=int, default=None,
              help="Refuse unless GHL is at this version (default: the export's version)")
@click.option("--confirm", is_flag=True, help="Required to change a PUBLISHED workflow")
@click.pass_context
def workflows_edit(ctx, workflow_id, json_file, name, expect_version, confirm):
    """Replace a workflow's steps, safely (experimental, internal API).

    Snapshots the live workflow first, refuses if it changed since your export,
    keeps its settings and triggers, re-reads to prove GHL kept the change, and
    prints a one-line undo. A PUBLISHED workflow needs --confirm.
    """
    _require_experimental(ctx)
    from cli_anything.gohighlevel.utils.workflow_io import (
        DriftError, NeedsConfirm, WorkflowIOError, edit_workflow,
    )
    try:
        with open(json_file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON in {json_file}: {e}", err=True)
        sys.exit(2)
    if isinstance(data, list):
        templates, file_version = data, None
    elif isinstance(data, dict) and isinstance(data.get("templates"), list):
        source = data.get("workflow_id")
        if source and source != workflow_id:
            click.echo(f"{json_file} was exported from workflow {source}, not {workflow_id}. "
                       "To copy a workflow use 'workflows clone'.", err=True)
            sys.exit(2)
        templates, file_version = data["templates"], data.get("version")
        name = name or data.get("name")
    else:
        click.echo(f"{json_file} is neither an export from 'workflows get' nor a list of steps.",
                   err=True)
        sys.exit(2)

    expected = expect_version if expect_version is not None else file_version
    client = _get_internal_client(ctx)
    try:
        result = edit_workflow(client, workflow_id, templates, name=name,
                               expected_version=expected, confirm=confirm,
                               snapshot_dir=_snapshot_dir())
    except NeedsConfirm as e:
        click.echo(str(e), err=True)
        click.echo("  This edit would change:")
        _print_diff(e.diff)
        click.echo("Nothing saved. Re-run with --confirm to save it.", err=True)
        sys.exit(2)
    except DriftError as e:
        click.echo(f"Refused: {e}", err=True)
        sys.exit(2)
    except WorkflowIOError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if ctx.obj["json"]:
        click.echo(json.dumps(result, indent=2))
        return
    click.echo(f"\nSaved '{result['name']}' [status: {result['status'] or 'unset'}]: "
               f"version {result['version_before']} -> {result['version_after']}")
    _print_diff(result["diff"])
    if result["missing_tags"]:
        click.echo(f"  WARNING: these tags could not be created, so the steps using them "
                   f"will show errors in GHL: {', '.join(result['missing_tags'])}")
    if result["relinked_triggers"]:
        click.echo(f"  Re-pointed {result['relinked_triggers']} trigger(s) at the new first step.")
    undo = (f"ghl --experimental --location-id {client.location_id} workflows edit {workflow_id} "
            f"--from-json '{result['snapshot']}' --expect-version {result['version_after']}")
    if result["status"] == "published":
        undo += " --confirm"
    click.echo(f"\nUndo: {undo}")


@workflows.command("clone")
@click.argument("workflow_id")
@click.option("--to-location", default=None, help="Sub-account to copy into (default: same one)")
@click.option("--name", default=None, help="Name for the copy (default: '<name> (copy)')")
@click.option("--folder", default=None, help="Folder in the target (created if needed)")
@click.option("--map", "maps", multiple=True, metavar="SOURCE_ID=TARGET_ID",
              help="Point a source-only id at a target id (repeatable)")
@click.pass_context
def workflows_clone(ctx, workflow_id, to_location, name, folder, maps):
    """Copy a workflow, steps and triggers, as a new DRAFT (experimental).

    The source is only read. Across sub-accounts, every form, survey, calendar,
    pipeline, stage and workflow the source names is matched to the target's
    asset with the same name. Anything that cannot be matched stops the clone
    before it writes; point it with --map SOURCE_ID=TARGET_ID.
    """
    _require_experimental(ctx)
    from cli_anything.gohighlevel.utils.workflow_builder import _app_domain
    from cli_anything.gohighlevel.utils.workflow_io import (
        ForeignRefsError, WorkflowIOError, clone_workflow,
    )
    id_map = {}
    for item in maps:
        source, sep, target = item.partition("=")
        if not sep or not source.strip() or not target.strip():
            click.echo(f"--map takes SOURCE_ID=TARGET_ID, got {item!r}", err=True)
            sys.exit(2)
        id_map[source.strip()] = target.strip()
    src = _get_internal_client(ctx)
    dst = src
    if to_location and to_location != src.location_id:
        dst = _get_internal_client(ctx, location_id=to_location)
    try:
        result = clone_workflow(src, dst, workflow_id, name=name, folder=folder, id_map=id_map)
    except ForeignRefsError as e:
        click.echo(f"Refused: {e}", err=True)
        for u in e.unresolved:
            label = f" ({u['kind']} {u['name']!r})" if u.get("kind") else ""
            click.echo(f"  - {u['value']}{label} at {u['where']}: {u['reason']}")
        click.echo("Map each one and re-run:  --map SOURCE_ID=TARGET_ID")
        sys.exit(2)
    except WorkflowIOError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if ctx.obj["json"]:
        click.echo(json.dumps(result, indent=2))
        return
    click.echo(f"\nCloned as DRAFT: {result['name']}")
    click.echo(f"  {result['steps']} steps, {result['triggers']} trigger(s)")
    click.echo(f"  {_app_domain()}/v2/location/{result['location_id']}"
               f"/automation/workflow/{result['workflow_id']}")
    if result["missing_tags"]:
        click.echo(f"\nWARNING: these tags could not be created in the target, so the steps "
                   f"using them will show errors in GHL: {', '.join(result['missing_tags'])}")
    if result["mapped"]:
        click.echo(f"\nRe-pointed {len(result['mapped'])} source-only id(s):")
        for m in result["mapped"]:
            label = f"{m['kind']} {m['name']!r}" if m.get("kind") else "id"
            click.echo(f"  - {label}: {m['from']} -> {m['to']} ({m['how']})")


@workflows.command("create-step")
@click.option("--type", "step_type", required=True,
              type=click.Choice(["email", "sms", "wait", "tag", "webhook", "ai"]))
@click.option("--name", required=True, help="Step name")
@click.option("--output-file", "out_file", required=True, type=click.Path(),
              help="JSON file to append step to")
@click.option("--subject", default=None, help="Email subject (email type)")
@click.option("--body", default=None, help="Message body (email/sms type)")
@click.option("--from-name", default="", help="Sender name (email type)")
@click.option("--value", default=None, type=int, help="Wait value (wait type)")
@click.option("--unit", default="days", type=click.Choice(["minutes", "hours", "days"]),
              help="Wait unit (wait type)")
@click.option("--tags", default=None, help="Comma-separated tags (tag type)")
@click.option("--remove-tags", is_flag=True, help="Remove tags instead of add (tag type)")
@click.option("--url", default=None, help="Webhook URL (webhook type)")
@click.option("--method", default="POST", help="HTTP method (webhook type)")
@click.option("--prompt", default=None, help="AI prompt (ai type)")
@click.option("--model", default="gpt-4o", help="AI model (ai type)")
@click.pass_context
def workflows_create_step(ctx, step_type, name, out_file, subject, body, from_name,
                          value, unit, tags, remove_tags, url, method, prompt, model):
    """Build a workflow step and append to a JSON file (experimental).

    Use repeatedly to build up a workflow step-by-step, then pass the
    file to 'workflows create --from-json'.
    """
    _require_experimental(ctx)
    try:
        from cli_anything.gohighlevel.utils import workflow_builder as wb

        if step_type == "email":
            if not subject or not body:
                click.echo("Error: --subject and --body required for email step", err=True)
                sys.exit(1)
            step = wb.email_step(name, subject, body, from_name)
        elif step_type == "sms":
            if not body:
                click.echo("Error: --body required for sms step", err=True)
                sys.exit(1)
            step = wb.sms_step(name, body)
        elif step_type == "wait":
            if value is None:
                click.echo("Error: --value required for wait step", err=True)
                sys.exit(1)
            step = wb.wait_step(name, value, unit)
        elif step_type == "tag":
            if not tags:
                click.echo("Error: --tags required for tag step", err=True)
                sys.exit(1)
            step = wb.tag_step(name, [t.strip() for t in tags.split(",")], remove=remove_tags)
        elif step_type == "webhook":
            if not url:
                click.echo("Error: --url required for webhook step", err=True)
                sys.exit(1)
            step = wb.webhook_step(name, url, method)
        elif step_type == "ai":
            if not prompt:
                click.echo("Error: --prompt required for ai step", err=True)
                sys.exit(1)
            step = wb.ai_step(name, prompt, model)
        else:
            click.echo(f"Error: Unknown step type: {step_type}", err=True)
            sys.exit(1)

        # Load existing or start fresh
        import os
        if os.path.exists(out_file):
            with open(out_file) as f:
                steps = json.load(f)
        else:
            steps = []

        steps.append(step)

        # Auto-link all steps
        linked = wb.link_steps(steps)
        with open(out_file, "w") as f:
            json.dump(linked, f, indent=2)

        if ctx.obj["json"]:
            click.echo(json.dumps(step, indent=2))
        else:
            click.echo(f"Step added: {step['name']} ({step['type']})")
            click.echo(f"Total steps in {out_file}: {len(linked)}")
    except Exception as e:
        _handle_error(e)


@workflows.command("create-n8n")
@click.option("--name", required=True, help="Workflow name")
@click.option("--webhook-url", required=True, help="n8n webhook URL")
@click.option("--tag", default=None, help="Trigger tag (creates tag trigger)")
@click.option("--folder", default=None, help="Folder name")
@click.pass_context
def workflows_create_n8n(ctx, name, webhook_url, tag, folder):
    """Create a minimal GHL workflow that triggers an n8n webhook (experimental).

    Creates: [tag trigger] → [webhook POST to n8n URL]
    """
    _require_experimental(ctx)
    try:
        from cli_anything.gohighlevel.utils import workflow_builder as wb

        steps = [wb.webhook_step(f"n8n: {name}", webhook_url, "POST")]
        if tag:
            steps.insert(0, wb.tag_step(f"Tag: {tag}", [tag]))

        linked = wb.link_steps(steps)
        campaign = {
            "n8n_bridge": {
                "name": name,
                "templates": linked,
                "tag": tag,
            }
        }

        from cli_anything.gohighlevel.utils.workflow_builder import CampaignBuilder
        client = _get_internal_client(ctx)
        builder = CampaignBuilder(client)
        stats = builder.build(campaign, folder or f"n8n-{name}")

        if ctx.obj["json"]:
            click.echo(json.dumps(stats, indent=2, default=str))
        else:
            click.echo(builder.format_summary())
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# DOCUMENTS / CONTRACTS
# ===========================================================================

@cli.group()
@click.pass_context
def documents(ctx):
    """Documents, contracts, and proposals — list, send, templates."""
    pass


@documents.command("list")
@click.option("--status", default=None, type=click.Choice(["draft", "sent", "viewed", "completed", "accepted"]))
@click.option("--payment-status", default=None, type=click.Choice(["waiting_for_payment", "paid", "no_payment"]))
@click.option("--limit", default=20)
@click.option("--offset", "skip", default=0)
@click.option("--query", default=None, help="Search by name")
@click.pass_context
def documents_list(ctx, status, payment_status, limit, skip, query):
    """List documents/contracts."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "skip": skip}
        if status:
            params["status"] = status
        if payment_status:
            params["paymentStatus"] = payment_status
        if query:
            params["query"] = query
        data = api.get("/proposals/document", params=params)
        _output(ctx, data, "Documents")
    except Exception as e:
        _handle_error(e)


@documents.command("templates")
@click.option("--type", "template_type", default=None, type=click.Choice(["proposal", "estimate", "contentLibrary"]))
@click.option("--name", default=None, help="Filter by template name")
@click.option("--limit", default=20)
@click.pass_context
def documents_templates(ctx, template_type, name, limit):
    """List document/contract templates."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit}
        if template_type:
            params["type"] = template_type
        if name:
            params["name"] = name
        data = api.get("/proposals/templates", params=params)
        _output(ctx, data, "Document Templates")
    except Exception as e:
        _handle_error(e)


@documents.command("send")
@click.option("--document-id", required=True, help="Document ID to send")
@click.option("--sent-by", required=True, help="User ID of sender")
@click.option("--medium", default="email", type=click.Choice(["email", "link"]), help="Delivery method")
@click.option("--name", "document_name", default=None, help="Document name override")
@click.pass_context
def documents_send(ctx, document_id, sent_by, medium, document_name):
    """Send an existing document to its recipients."""
    try:
        body = {
            "locationId": _loc(ctx),
            "documentId": document_id,
            "sentBy": sent_by,
            "medium": medium,
        }
        if document_name:
            body["documentName"] = document_name
        data = api.post("/proposals/document/send", data=body)
        _output(ctx, data, "Document Sent")
    except Exception as e:
        _handle_error(e)


@documents.command("send-template")
@click.option("--template-id", required=True, help="Template ID")
@click.option("--contact-id", required=True, help="Contact ID to send to")
@click.option("--user-id", required=True, help="User ID (sender)")
@click.option("--opportunity-id", default=None, help="Link to opportunity")
@click.option("--send/--no-send", "send_document", default=True, help="Send immediately or just create")
@click.pass_context
def documents_send_template(ctx, template_id, contact_id, user_id, opportunity_id, send_document):
    """Create and send a contract from a template."""
    try:
        body = {
            "templateId": template_id,
            "contactId": contact_id,
            "userId": user_id,
            "locationId": _loc(ctx),
            "sendDocument": send_document,
        }
        if opportunity_id:
            body["opportunityId"] = opportunity_id
        data = api.post("/proposals/templates/send", data=body)
        _output(ctx, data, "Template Sent")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# CONVERSATIONS
# ===========================================================================

@cli.group()
@click.pass_context
def conversations(ctx):
    """Manage conversations and messages."""
    pass


@conversations.command("list")
@click.option("--limit", default=20)
@click.option("--status", default=None, type=click.Choice(["all", "read", "unread", "starred"]))
@click.option("--type", "msg_type", default=None,
              type=click.Choice(["Email", "SMS", "WhatsApp", "GMB", "IG", "FB", "Live_Chat", "Custom"]),
              help="Filter by last message type")
@click.pass_context
def conversations_list(ctx, limit, status, msg_type):
    """List conversations. Use --type Email to see email conversations."""
    try:
        # API uses TYPE_EMAIL format for lastMessageType filter
        TYPE_MAP = {
            "Email": "TYPE_EMAIL", "SMS": "TYPE_SMS", "WhatsApp": "TYPE_WHATSAPP",
            "GMB": "TYPE_GMB", "IG": "TYPE_INSTAGRAM", "FB": "TYPE_FACEBOOK",
            "Live_Chat": "TYPE_LIVE_CHAT", "Custom": "TYPE_CUSTOM",
        }
        params = {"locationId": _loc(ctx), "limit": limit}
        if status:
            params["status"] = status
        if msg_type:
            params["lastMessageType"] = TYPE_MAP.get(msg_type, f"TYPE_{msg_type.upper()}")
        data = api.get("/conversations/search", params=params)
        _output(ctx, data, "Conversations")
    except Exception as e:
        _handle_error(e)


@conversations.command("get")
@click.argument("conversation_id")
@click.pass_context
def conversations_get(ctx, conversation_id):
    """Get conversation details."""
    try:
        data = api.get(f"/conversations/{conversation_id}")
        _output(ctx, data, "Conversation Details")
    except Exception as e:
        _handle_error(e)


@conversations.command("messages")
@click.argument("conversation_id")
@click.option("--limit", default=20)
@click.option("--type", "msg_type", default=None,
              type=click.Choice(["Email", "SMS", "WhatsApp", "GMB", "IG", "FB", "Live_Chat", "Custom"]),
              help="Filter messages by type")
@click.pass_context
def conversations_messages(ctx, conversation_id, limit, msg_type):
    """Get messages in a conversation. Use --type Email for email messages only."""
    try:
        TYPE_MAP = {
            "Email": "TYPE_EMAIL", "SMS": "TYPE_SMS", "WhatsApp": "TYPE_WHATSAPP",
            "GMB": "TYPE_GMB", "IG": "TYPE_INSTAGRAM", "FB": "TYPE_FACEBOOK",
            "Live_Chat": "TYPE_LIVE_CHAT", "Custom": "TYPE_CUSTOM",
        }
        params = {"limit": limit}
        if msg_type:
            params["type"] = TYPE_MAP.get(msg_type, f"TYPE_{msg_type.upper()}")
        data = api.get(f"/conversations/{conversation_id}/messages", params=params)
        _output(ctx, data, "Messages")
    except Exception as e:
        _handle_error(e)


@conversations.command("get-email")
@click.argument("email_message_id")
@click.pass_context
def conversations_get_email(ctx, email_message_id):
    """Get full email details (subject, body, headers, attachments).

    Two-step workflow: list conversations --type Email → get message IDs → get-email <id>
    """
    try:
        data = api.get(f"/conversations/messages/email/{email_message_id}")
        _output(ctx, data, "Email Details")
    except Exception as e:
        _handle_error(e)


@conversations.command("send")
@click.argument("conversation_id")
@click.option("--type", "msg_type", default="SMS", type=click.Choice(["SMS", "Email", "WhatsApp", "GMB", "IG", "FB", "Live_Chat"]))
@click.option("--message", required=True, help="Message text")
@click.pass_context
def conversations_send(ctx, conversation_id, msg_type, message):
    """Send a message in a conversation."""
    try:
        body = {
            "type": msg_type,
            "message": message,
            "conversationId": conversation_id,
        }
        data = api.post(f"/conversations/messages", data=body)
        _output(ctx, data, "Message Sent")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# EMAILS
# ===========================================================================

@cli.group()
@click.pass_context
def emails(ctx):
    """Email campaigns and templates."""
    pass


@emails.command("list-campaigns")
@click.option("--status", default=None, help="Filter by status")
@click.pass_context
def emails_list_campaigns(ctx, status):
    """List email campaigns. Note: Uses the campaigns API."""
    try:
        params = {"locationId": _loc(ctx)}
        if status:
            params["status"] = status
        data = api.get("/campaigns/", params=params)
        _output(ctx, data, "Email Campaigns")
    except Exception as e:
        _handle_error(e)


@emails.command("templates")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.pass_context
def emails_templates(ctx, limit, offset):
    """List email templates (builder templates)."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "offset": offset}
        data = api.get("/emails/builder", params=params)
        _output(ctx, data, "Email Templates")
    except Exception as e:
        _handle_error(e)


@emails.command("scheduled")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.pass_context
def emails_scheduled(ctx, limit, offset):
    """List scheduled email sends."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "offset": offset}
        data = api.get("/emails/schedule", params=params)
        _output(ctx, data, "Scheduled Emails")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# PAYMENTS
# ===========================================================================

@cli.group()
@click.pass_context
def payments(ctx):
    """Payments, invoices, transactions, and orders."""
    pass


@payments.command("transactions")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.option("--contact-id", default=None)
@click.pass_context
def payments_transactions(ctx, limit, offset, contact_id):
    """List transactions."""
    try:
        params = {"altId": _loc(ctx), "altType": "location", "limit": limit, "offset": offset}
        if contact_id:
            params["contactId"] = contact_id
        data = api.get("/payments/transactions", params=params)
        _output(ctx, data, "Transactions")
    except Exception as e:
        _handle_error(e)


@payments.command("orders")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.pass_context
def payments_orders(ctx, limit, offset):
    """List orders."""
    try:
        params = {"altId": _loc(ctx), "altType": "location", "limit": limit, "offset": offset}
        data = api.get("/payments/orders", params=params)
        _output(ctx, data, "Orders")
    except Exception as e:
        _handle_error(e)


@payments.command("invoices")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.option("--status", default=None, type=click.Choice(["draft", "sent", "paid", "void"]))
@click.option("--contact-id", default=None)
@click.pass_context
def payments_invoices(ctx, limit, offset, status, contact_id):
    """List invoices."""
    try:
        params = {"altId": _loc(ctx), "altType": "location", "limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if contact_id:
            params["contactId"] = contact_id
        data = api.get("/invoices/", params=params)
        _output(ctx, data, "Invoices")
    except Exception as e:
        _handle_error(e)


@payments.command("create-invoice")
@click.option("--contact-id", required=True, help="Contact ID")
@click.option("--name", required=True, help="Invoice name/title")
@click.option("--amount", required=True, type=float, help="Total amount in cents")
@click.option("--due-date", required=True, help="Due date (YYYY-MM-DD)")
@click.pass_context
def payments_create_invoice(ctx, contact_id, name, amount, due_date):
    """Create a new invoice."""
    try:
        body = {
            "altId": _loc(ctx),
            "altType": "location",
            "contactId": contact_id,
            "name": name,
            "total": amount,
            "dueDate": due_date,
        }
        data = api.post("/invoices/", data=body)
        _output(ctx, data, "Invoice Created")
    except Exception as e:
        _handle_error(e)


@payments.command("products")
@click.option("--limit", default=20)
@click.option("--offset", default=0)
@click.pass_context
def payments_products(ctx, limit, offset):
    """List products."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "offset": offset}
        data = api.get("/products/", params=params)
        _output(ctx, data, "Products")
    except Exception as e:
        _handle_error(e)


@payments.command("send-invoice")
@click.argument("invoice_id")
@click.option("--user-id", required=True, help="User ID of the sender")
@click.option("--confirm", is_flag=True, help="Required. Sending an invoice emails a real customer.")
@click.pass_context
def payments_send_invoice(ctx, invoice_id, user_id, confirm):
    """Send an invoice to its contact. DELIVERS A REAL EMAIL."""
    if not confirm:
        click.echo(
            f"Refusing to send invoice {invoice_id} without --confirm.\n"
            "This delivers a real email (and a pay link) to the customer.",
            err=True,
        )
        sys.exit(2)
    try:
        payload = {"altId": _loc(ctx), "altType": "location", "userId": user_id,
                   "action": "sms_and_email", "liveMode": True}
        data = api.post(f"/invoices/{invoice_id}/send", data=payload)
        _output(ctx, data, "Invoice Sent")
    except Exception as e:
        _handle_error(e)


@payments.command("void-invoice")
@click.argument("invoice_id")
@click.option("--confirm", is_flag=True, help="Required. Voiding is irreversible.")
@click.pass_context
def payments_void_invoice(ctx, invoice_id, confirm):
    """Void an invoice. IRREVERSIBLE."""
    if not confirm:
        click.echo(f"Refusing to void invoice {invoice_id} without --confirm.", err=True)
        sys.exit(2)
    try:
        payload = {"altId": _loc(ctx), "altType": "location"}
        data = api.post(f"/invoices/{invoice_id}/void", data=payload)
        _output(ctx, data, "Invoice Voided")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# FORMS
# ===========================================================================

@cli.group()
@click.pass_context
def forms(ctx):
    """Forms and form submissions."""
    pass


@forms.command("list")
@click.option("--limit", default=20)
@click.option("--offset", "skip", default=0)
@click.option("--type", "form_type", default=None, help="Form type filter")
@click.pass_context
def forms_list(ctx, limit, skip, form_type):
    """List forms."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "skip": skip}
        if form_type:
            params["type"] = form_type
        data = api.get("/forms/", params=params)
        _output(ctx, data, "Forms")
    except Exception as e:
        _handle_error(e)


@forms.command("submissions")
@click.argument("form_id")
@click.option("--limit", default=20)
@click.option("--page", default=1)
@click.pass_context
def forms_submissions(ctx, form_id, limit, page):
    """Get form submissions."""
    try:
        params = {"locationId": _loc(ctx), "limit": limit, "page": page}
        data = api.get(f"/forms/submissions", params={"formId": form_id, **params})
        _output(ctx, data, f"Submissions for form {form_id}")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# SOCIAL MEDIA
# ===========================================================================

@cli.group()
@click.pass_context
def social(ctx):
    """Social media posts and analytics."""
    pass


@social.command("accounts")
@click.pass_context
def social_accounts(ctx):
    """List connected social media accounts."""
    try:
        data = api.get(f"/social-media-posting/{_loc(ctx)}/accounts")
        _output(ctx, data, "Social Accounts")
    except Exception as e:
        _handle_error(e)


@social.command("posts")
@click.option("--limit", default=20)
@click.option("--offset", "skip", default=0)
@click.option("--type", "post_type", default=None, help="Filter by post type")
@click.pass_context
def social_posts(ctx, limit, skip, post_type):
    """List social media posts."""
    try:
        # locationId lives in the path, not the body; skip/limit must be number *strings*
        params = {"limit": str(limit), "skip": str(skip)}
        if post_type:
            params["type"] = post_type
        data = api.post(f"/social-media-posting/{_loc(ctx)}/posts/list", data=params)
        _output(ctx, data, "Social Posts")
    except Exception as e:
        _handle_error(e)


@social.command("create-post")
@click.option("--account-id", required=True, multiple=True, help="Social account IDs (repeatable)")
@click.option("--text", required=True, help="Post text content")
@click.option("--media-url", default=None, multiple=True, help="Media URLs (repeatable)")
@click.option("--schedule", default=None, help="Schedule time (ISO 8601)")
@click.pass_context
def social_create_post(ctx, account_id, text, media_url, schedule):
    """Create a social media post."""
    try:
        body = {
            "locationId": _loc(ctx),
            "accountIds": list(account_id),
            "summary": text,
        }
        if media_url:
            body["media"] = [{"url": u, "type": "image"} for u in media_url]
        if schedule:
            body["scheduledAt"] = schedule
        data = api.post(f"/social-media-posting/{_loc(ctx)}/posts", data=body)
        _output(ctx, data, "Post Created")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# LOCATIONS
# ===========================================================================

@cli.group()
@click.pass_context
def locations(ctx):
    """Location/sub-account management."""
    pass


@locations.command("get")
@click.pass_context
def locations_get(ctx):
    """Get current location details."""
    try:
        data = api.get(f"/locations/{_loc(ctx)}")
        _output(ctx, data, "Location Details")
    except Exception as e:
        _handle_error(e)


@locations.command("search")
@click.option("--company-id", required=True, help="Company/Agency ID")
@click.option("--limit", default=20)
@click.option("--offset", "skip", default=0)
@click.option("--query", default=None, help="Search query")
@click.pass_context
def locations_search(ctx, company_id, limit, skip, query):
    """Search locations (requires company-level access)."""
    try:
        params = {"companyId": company_id, "limit": limit, "skip": skip}
        if query:
            params["query"] = query
        data = api.get("/locations/search", params=params)
        _output(ctx, data, "Locations")
    except Exception as e:
        _handle_error(e)


@locations.command("tags")
@click.pass_context
def locations_tags(ctx):
    """List tags for current location."""
    try:
        data = api.get(f"/locations/{_loc(ctx)}/tags")
        _output(ctx, data, "Location Tags")
    except Exception as e:
        _handle_error(e)


@locations.command("custom-fields")
@click.pass_context
def locations_custom_fields(ctx):
    """List custom fields for current location."""
    try:
        data = api.get(f"/locations/{_loc(ctx)}/customFields")
        _output(ctx, data, "Custom Fields")
    except Exception as e:
        _handle_error(e)


@locations.command("custom-values")
@click.pass_context
def locations_custom_values(ctx):
    """List custom values for current location."""
    try:
        data = api.get(f"/locations/{_loc(ctx)}/customValues")
        _output(ctx, data, "Custom Values")
    except Exception as e:
        _handle_error(e)


@locations.command("users")
@click.pass_context
def locations_users(ctx):
    """List users/team members on this location."""
    try:
        data = api.get("/users/", params={"locationId": _loc(ctx)})
        _output(ctx, data, "Users")
    except Exception as e:
        _handle_error(e)


# ===========================================================================
# Entry point
# ===========================================================================


def _tolerant_windows_output() -> None:
    """On Windows, a piped stdout (Claude Code's shell tool) is a cp1252 stream with
    strict errors, so one emoji or arrow in a contact name or workflow step would crash
    plain-text output with UnicodeEncodeError. Replace what cp1252 can't show instead."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError, OSError, io.UnsupportedOperation):
                pass


def main():
    _tolerant_windows_output()
    # Click reads GHL_LOCATION_ID via envvar at parse time, so settings load first.
    settings.load_settings()
    cli(obj={})


if __name__ == "__main__":
    main()
