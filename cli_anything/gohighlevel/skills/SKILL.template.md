---
name: ghl-cli
description: Drive your own GoHighLevel sub-account from the terminal with the ghl CLI. Read contacts, opportunities, calendars, conversations, forms and workflows; build, edit and clone workflows as drafts. Use when the user says "ghl", "gohighlevel", "workflow", "fix my GHL keys", "update the GHL CLI", "remove the GHL CLI", or asks to read or change anything in their GoHighLevel account.
---

# GHL CLI

`ghl` is a command line for GoHighLevel. It reads and manages your CRM data (contacts, opportunities, calendars, conversations, emails, payments, forms, social, locations, documents) and builds workflows for you. It only ever talks to the one sub-account saved in your settings.

Always run it by this absolute path, from any folder. Quote it, because the path may contain spaces:

```bash
GHL="{{GHL}}"
"$GHL" doctor
```

Setup instructions for this install live in `{{SETUP}}`.

## Safety rules (not optional)

1. **Never ask for a key, and never print one.** Do not read or print `~/.ghl-cli/keys.env` or `~/.ghl-cli/workflow-pass.json` (the saved one-hour Google pass). Keys go in only through `ghl keys paste` (see "Fix my GHL keys"). If you see a key in a file or in output, do not repeat it.
2. **Reads are free.** Listing, getting, searching, `preview`, `workflows get`, `doctor` and `selftest --check-only` change nothing. Run them without asking.
3. **Any write needs the user's plain yes in chat first.** Creating or updating contacts, sending messages, building, editing or cloning a workflow, enrolling someone: say what will change, ask "apply this?", and wait for a plain yes. One yes can cover a batch you showed together.
4. **Workflows are built as drafts.** After you build, edit or clone one, show the user the editor link (format below) so they can look at it in GHL.
5. **No publish or delete commands exist for workflows, on purpose.** The user clicks Publish in GHL themselves, after they have looked at the draft.
6. **`--confirm` only after a plain yes.** It is needed to edit a published workflow and to run `payments send-invoice` or `payments void-invoice`. Sending an invoice emails a real customer and voiding one cannot be undone. Never add `--confirm` on your own.
7. **Check `--help` before guessing flags.** Many commands take positional arguments, not options. Run `"$GHL" <group> <command> --help` first. If this file and `--help` disagree, trust `--help`.

## Everyday commands

```bash
"$GHL" contacts list --limit 5
"$GHL" contacts search "name or email or phone"   # positional query
"$GHL" contacts get CONTACT_ID                    # positional
"$GHL" contacts add-tag CONTACT_ID TAG [TAG...]   # positional (write)
"$GHL" contacts notes CONTACT_ID                  # positional
"$GHL" contacts add-note CONTACT_ID "text"        # positional (write)
"$GHL" opportunities pipelines
"$GHL" opportunities search --status open --limit 50
"$GHL" conversations list --type Email --limit 20
"$GHL" conversations messages CONVERSATION_ID     # positional
"$GHL" calendars list
"$GHL" calendars slots CALENDAR_ID --start 2026-01-05 --end 2026-01-20   # max 31 days apart
"$GHL" forms list
"$GHL" emails templates
"$GHL" locations tags
"$GHL" workflows list
```

Groups: contacts, opportunities, calendars, conversations, workflows, emails, payments, forms, social, locations, documents. Run `"$GHL" --help` for the full list.

Add `--json` before the group name for machine-readable output, for example `"$GHL" --json workflows list`. Note that `contacts list` with `--json` returns the bare list of contacts and drops the paging info; every other command returns the whole response.

`--location-id ID` before the group name points one command at a different sub-account. Do that only if the user asked for it.

## Health checks and fixes

### "Fix my GHL keys"

1. Run `"$GHL" doctor`. It checks both keys and whether they reach the sub-account, and prints a fix for each problem. Use `--only api-key` or `--only workflow-token` to check one.
2. If a key is missing or rejected, go back to the SETUP phase that failed (in `{{SETUP}}`) and redo that phase. A new key goes in with `"$GHL" keys paste api-key` or `"$GHL" keys paste workflow-token`, which reads it from the clipboard and never shows it. If the clipboard can't be read, open the key file in a plain text editor for the user instead (the commands are in SETUP's Phase 2, "When the clipboard can't be read"): they add the line, save and close, and you never read the file back. Don't reach for `--from-stdin`: this shell can't take typed input, and the only way to feed it would be a key pasted into the chat. Never ask the user to paste a key into the chat.
3. If the sub-account is wrong, the user copies the address bar URL from inside their GHL sub-account and you run `"$GHL" config set-location "URL_OR_ID"`.
4. Run `"$GHL" doctor` again and show the result.

The workflow token (the browser session key) expires from time to time. When workflow commands start failing with an auth error, that is the first thing to refresh. A message that Google is rate limiting is not an expired token: the CLI already waited and retried, so wait a minute and run the command again. Do not replace the token for that.

### "Update the GHL CLI"

Run `ghl update` (as `"$GHL" update`). It installs the newest release and keeps the old one so it can be reverted. `"$GHL" update --undo` goes back to the previous version. Add `--force` only if the user says the update is stuck and gives a plain yes. After an update, run `"$GHL" doctor`, then `"$GHL" skill install` to refresh this skill file.

### "Remove the GHL CLI"

Deleting can't be undone and takes the saved keys with it, so first say exactly what goes and get a plain yes. Three folders go: the install folder `{{ROOT}}`, `~/.ghl-cli` (keys and settings, and also the snapshots `workflows edit` kept for its undo) and `~/.claude/skills/ghl-cli` (this skill, plus a `SKILL.md.bak` if `ghl skill install --force` once set an older hand-copied skill aside). The first command deletes the install folder only if it still holds `VERSION` and `SETUP.md`, so a wrong path deletes nothing.

```bash
R="{{ROOT}}"
if [ -f "$R/VERSION" ] && [ -f "$R/SETUP.md" ]; then rm -rf "$R" && echo "REMOVED $R"; else echo "NOT REMOVED: $R does not hold VERSION and SETUP.md"; fi
rm -rf ~/.ghl-cli ~/.claude/skills/ghl-cli && echo "REMOVED keys, settings and the skill"
```

On Windows (PowerShell):

```powershell
$R = "{{ROOT}}"
if ((Test-Path -LiteralPath "$R\VERSION") -and (Test-Path -LiteralPath "$R\SETUP.md")) { Remove-Item -LiteralPath $R -Recurse -Force; "REMOVED $R" } else { "NOT REMOVED: $R does not hold VERSION and SETUP.md" }
Remove-Item -Recurse -Force "$env:USERPROFILE\.ghl-cli", "$env:USERPROFILE\.claude\skills\ghl-cli" -ErrorAction SilentlyContinue; "REMOVED keys, settings and the skill"
```

`NOT REMOVED` means the folder moved or was already deleted: tell the user, and don't delete anything else by guesswork.

Then tell the user the two steps left for them in GoHighLevel: delete the Private Integration they made for the CLI (look in the sub-account's settings, under Private Integrations), and change their GoHighLevel password, which should end the login token setup copied from their browser.

### Proving workflows fire: `ghl selftest`

`ghl selftest` builds a throwaway workflow in the user's sub-account, fires its trigger on a throwaway contact, checks the result, and deletes everything it made. It writes to the live account, so get a plain yes before running it.

- `"$GHL" selftest --check-only` writes nothing. It lists published workflows that the test could accidentally set off. On a big account it prints `Reading N published workflows...` and then takes a while.
- `"$GHL" selftest --draft-only` builds, reads back and deletes the test workflow without firing it.
- `"$GHL" selftest --fire` fires the trigger even when published workflows could be set off. Before using it, run `--check-only`, show the user that list, and say plainly that those workflows' actions will really happen (emails, texts, tags) and cannot be undone. Only add `--fire` after a plain yes to that.
- **Timeouts:** run every selftest mode (plain, `--fire`, `--draft-only`, `--check-only` and `--cleanup`) with a Bash timeout of 600000 ms, or in the background and check on it. A run that does not fire waits over two minutes, and an account with many workflows takes minutes just to read them. A short timeout kills it halfway.
- It prints a run id and a cleanup command before it writes anything. If a run is interrupted or times out, run `"$GHL" selftest --cleanup RUN` with the id it printed, before doing anything else.

## Building workflows

Workflow commands use GHL's internal API and need the workflow token, so they sit under `--experimental`. Run this first, every time:

```bash
"$GHL" --experimental workflows auth-check
```

If it fails, follow "Fix my GHL keys". Workflow creation uses an undocumented GHL API, so GHL can change it without notice. If a build fails in a way that makes no sense, run `auth-check` and `doctor` before anything else.

### The flow

1. Write a spec (JSON, shape below).
2. `"$GHL" --experimental workflows preview --from-spec spec.json` prints the step tree. Read-only. Show it to the user.
3. After a plain yes, `"$GHL" --experimental workflows build --from-spec spec.json` creates the workflow as a DRAFT. Add `--name "..."` to override the name and `--folder "..."` to file it in a folder (created if needed).
4. Show the editor link: `https://app.gohighlevel.com/v2/location/LOCATION_ID/automation/workflow/WORKFLOW_ID`. It is `location` and `workflow`, both singular. A plural `workflows` link opens a blank page. If the user's account uses another app domain, use the one their GHL address bar shows.
5. Tell the user to open the draft, look it over, and click Publish themselves.

`build` creates every tag the workflow uses (in steps, conditions and triggers). GHL's editor flags a tag step with "Referenced Tag does not exist" otherwise.

### Spec format

```json
{
  "name": "Welcome",
  "trigger": {"type": "tag", "value": "signed-up"},
  "steps": [
    {"type": "email", "name": "Hello", "subject": "Hi", "body": "text"},
    {"type": "wait", "name": "Wait", "value": 2, "unit": "days"},
    {"type": "if_else", "name": "Opened?",
     "condition": {"kind": "email_event", "value": "opened"},
     "yes": [{"type": "tag", "name": "Warm", "tags": ["warm"]}],
     "no": [{"type": "sms", "name": "Nudge", "body": "text"}]}
  ]
}
```

- Step types that work, with their keys (every step also needs `type` and `name`):
  - `email`: `subject`, `body`, optional `from_name`
  - `sms`: `body`
  - `wait`: `value` and `unit` (for example `2` and `days`)
  - `tag`: `tags` (a list); `"remove": true` removes them instead
  - `note`: `body` (must not be empty)
  - `notify`: `to` (an email address on your team), `subject`, `body`. It emails your team, not the contact
  - `update_field`: `field_id` (a custom field id from `"$GHL" locations custom-fields`) and `value`
  - `assign`: `user_id` (from `"$GHL" locations users`)
  - `remove_from_workflow`: `workflow_id`
  - `webhook`: `url`, optional `method` (default `POST`)
  - `ai`: `prompt`, optional `model`
  - `goto`: `target` (the id of an earlier step)
  - `if_else`: `condition`, with `yes` and `no` branches
- `no` is the else path. Branches are terminal; use a `goto` step (with `target`, the id of an earlier step) to jump back.
- Condition kinds: `email_event`, `tag` (one tag name; add `"operator": "index-of-false"` for "does not include"), `appointment_rescheduled`. An appointment condition needs an appointment trigger on the workflow, because GHL disables it otherwise.
- Always run `preview` first. It reports spec mistakes with the exact place.

### Triggers

Use `"trigger": {...}` for one or `"triggers": [...]` for several. Every trigger takes an optional `"name"`.

| type | fields |
|---|---|
| `tag` | `value` (the tag) |
| `form` | `form_id` (optional) |
| `survey` | `survey_id` |
| `appointment_booked` | `calendar_id` (optional) |
| `appointment_status` | `status`: new, confirmed, cancelled, showed, noshow, invalid; `calendar_id` optional |
| `pipeline_stage` | `pipeline_id` and `stage_id` |
| `opportunity_status` | `status`: open, won, lost, abandoned; `pipeline_id` optional |
| `contact_created` | none |
| `customer_replied` | `contains` (optional) |
| `raw` | `{"ghl": {type, conditions}}`, a raw GHL trigger for anything else |

**Check the ids before you build.** A wrong form, survey, calendar, pipeline or stage id saves without any error and then never fires. `build` checks these ids against the account and exits 2 if one does not exist, but find the right ones up front: `"$GHL" forms list`, `"$GHL" calendars list`, `"$GHL" opportunities pipelines` (stages are listed under each pipeline). In GHL's editor, cancelled/invalid/new/confirmed show in lowercase and a pipeline stage can show as a raw id. Both are normal.

### Reading, editing and cloning existing workflows

```bash
"$GHL" --experimental workflows get WORKFLOW_ID                        # read only
"$GHL" --experimental workflows get WORKFLOW_ID --output-file wf.json  # export steps, triggers, version
# edit wf.json (keep the step ids), then, after a plain yes:
"$GHL" --experimental workflows edit WORKFLOW_ID --from-json wf.json
"$GHL" --experimental workflows clone WORKFLOW_ID
"$GHL" --experimental workflows clone WORKFLOW_ID --to-location LOCATION_ID --folder "Name"
```

- **edit** is safe by design. It re-reads the workflow first and refuses (exit 2) if it changed since your export. It saves a snapshot under `~/.ghl-cli/snapshots/`, keeps the workflow's settings, re-reads afterwards to prove GHL kept the change, and prints a one-line `Undo:` command. Show the user that Undo line after every edit and keep it. Triggers are not edited.
- **A published workflow:** edit exits 2 and prints the step diff instead of changing anything. Show the user the diff, get a plain yes, then run it again with `--confirm`.
- **clone** always makes a new DRAFT and never writes to the source. Into another sub-account it matches every form, survey, calendar, pipeline, stage and workflow the source names to the same-named item in the target. If any cannot be matched, it stops before writing anything (exit 2) and lists each id. Point them with `--map SOURCE_ID=TARGET_ID` (repeatable). Do not write into another sub-account unless the user asked for it by name.
- Other workflow commands: `list`, `enroll` and `remove` (public API, writes, need a plain yes), `create-step`, `create` and `create-n8n` (older helpers that `build` replaces for most jobs).

## Example workflows in `builders/`

The folder `{{BUILDERS}}` (next to SETUP.md) holds reference examples of real workflows from Lead Gen Jay's account, shared with his permission. They are for learning how a multi-step workflow is put together. Some of them read their inputs from files that are not included, so they may not run as-is, and some write to an existing workflow, so do not run one without reading it first. To build your own, read one for ideas, then write a spec and use `workflows preview` and `workflows build` as above.

## When something fails

| What you see | Do this |
|---|---|
| 401 or 403, "token", "unauthorized" | "Fix my GHL keys" above (`ghl doctor`, then the failed SETUP phase) |
| `auth-check` fails | Run the full `"$GHL" doctor` (not `--only`: only the full check tests whether the token reaches this sub-account), then follow the fix it prints, usually a fresh workflow token through `ghl keys paste workflow-token` |
| 422 on a search | Run the command's `--help`; the flag names or positional arguments are wrong |
| `calendars slots` 400 | Range is wider than 31 days; shorten it |
| Build exits 2 on an id | The id does not exist in this sub-account; find the right one with the list commands above |
| A selftest was interrupted | `"$GHL" selftest --cleanup RUN` with the id it printed |
| The command is not found | Re-run the install phase in `{{SETUP}}`, then `ghl skill install` |

Never leave the user with just an error. Say what failed, which command fixes it, and run the check again after.
