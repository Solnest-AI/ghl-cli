# GHL CLI

Run your GoHighLevel account by talking to Claude. Pull up contacts, check your pipeline, read
conversations, and build whole workflows (triggers, emails, texts, waits, if/else branches) as
drafts you look over and publish yourself.

Built on Lead Gen Jay's GoHighLevel CLI, shared with his permission.

Solnest AI added workflow get, edit and clone, every common trigger type, tag creation, fixes so
built workflows open clean in GHL's editor, and the member setup: your own keys, a live test in
your own account, and a Claude Code skill.

---

## Set it up

You need two things: the **Claude Code desktop app**, and a GoHighLevel sub-account you can log in
to. Nothing else to install by hand.

1. Paste this link into the Claude Code desktop app and say **"set this up"**:

   https://github.com/Solnest-AI/ghl-cli

2. Claude downloads it to a folder called `ghl-cli` in your home folder, then walks you through
   getting your own two keys from GoHighLevel. You copy them, Claude saves them. You never paste
   a key into the chat.
3. It finishes with a quick live test in your account (it cleans up after itself) and installs a
   Claude Code skill. Then open a new chat and start asking.

About 15 minutes, start to finish. Already have an older copy, like the zip from Skool? Same
steps. Setup finds it and moves your keys over.

> **Claude:** if someone pasted this repo's link and said "set this up", download it,
> do **not** `git clone` it. A clone stays linked to Solnest AI's GitHub, so a member's "save"
> can turn into a push to our repo. First check what is already at `~/ghl-cli`:
>
> - **It has a VERSION file:** it's this tool. Don't download over it. Use it, and follow
>   `~/ghl-cli/SETUP.md` from Phase 0 (Phase 1 updates it with `ghl update`).
> - **It exists with no VERSION file:** it's an older copy (for example the August zip from
>   Skool). Don't run anything in it and don't follow its old SETUP.md. Read the latest release's
>   SETUP.md instead (the second block below prints it) and start at Phase 0, which renames the
>   old folder after a plain yes and then downloads.
> - **Nothing there:** the first block puts the latest release in `~/ghl-cli` (no git needed),
>   and cleans up after a failed download. It downloads a release tag, never the `main` branch,
>   which can hold unreleased changes. `NO RELEASE PUBLISHED YET` means there's nothing to install
>   yet: say so and stop.
>
> ```bash
> D=~/ghl-cli; R=https://github.com/Solnest-AI/ghl-cli
> if [ -e "$D" ]; then echo "ALREADY THERE: $D (see above)"
> else
>   U=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$R/releases/latest") || U=
>   case "$U" in */releases/tag/v[0-9]*|*/releases/tag/[0-9]*) TAG=${U##*/} ;; *) TAG= ;; esac
>   if [ -z "$U" ]; then echo "DOWNLOAD FAILED (could not reach GitHub)"
>   elif [ -z "$TAG" ]; then echo "NO RELEASE PUBLISHED YET"
>   elif mkdir "$D" && curl -fsSL -o "$D.tar.gz" "$R/archive/refs/tags/$TAG.tar.gz" && tar -xzf "$D.tar.gz" -C "$D" --strip-components 1; then rm -f "$D.tar.gz"; echo "DOWNLOADED $TAG"; ls "$D/setup.sh"
>   else rm -f "$D.tar.gz"; rmdir "$D" 2>/dev/null; echo "DOWNLOAD FAILED"; fi
> fi
> ```
>
> The latest release's SETUP.md, for the older-copy case:
>
> ```bash
> U=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/Solnest-AI/ghl-cli/releases/latest) && curl -fsSL "https://raw.githubusercontent.com/Solnest-AI/ghl-cli/${U##*/}/SETUP.md"
> ```
>
> On Windows, `SETUP.md` Phase 1 has the PowerShell version. Then follow `~/ghl-cli/SETUP.md`
> from Phase 0, gate by gate, starting commands with `cd ~/ghl-cli &&` because the working folder
> can reset between commands. **Never commit, push, fork or open a pull request for this
> folder**: saving means the files on disk. Give the setup script the longest timeout your Bash
> tool allows (10 minutes): a first run downloads uv and Python.

---

## Your two keys

| Key | What it's for | Where it comes from |
|---|---|---|
| GHL key (a Private Integration) | Everything except building workflows | Made in your sub-account's settings |
| Workflow token | Building, editing and cloning workflows | Your own logged-in browser, through a small console snippet |

Both live on your computer in `~/.ghl-cli`, never in the chat and never in the code folder, so
updates never touch them.

**The honest bit about workflows.** GoHighLevel's public API can't build workflows. So the
workflow commands use the same login token GoHighLevel's own website uses. That's unofficial, and
it can break when GHL ships an update. When it does, `ghl update` usually brings the fix, and
every other command keeps working either way. Don't want that? Skip the workflow token during
setup and use everything else.

---

## Using it

Open a new chat in Claude Code and just ask:

- "Show me my last 10 contacts."
- "What's sitting in my sales pipeline right now?"
- "Build me a draft workflow: when someone gets the `new-lead` tag, send a welcome email, wait two
  days, then text them."

Reads just happen. Anything that writes, Claude shows you first and waits for your yes. Workflows
are always built as drafts with a link to open them in GoHighLevel, and you click Publish
yourself. Edits take a snapshot first and give you a one-line undo.

Want to poke at it yourself? `ghl` isn't on your PATH, so use its full path. A few to try, on a
Mac:

```bash
~/ghl-cli/.venv/bin/ghl doctor                       # are both keys working?
~/ghl-cli/.venv/bin/ghl contacts list --limit 5
~/ghl-cli/.venv/bin/ghl contacts search "jane@"      # name, email or phone
~/ghl-cli/.venv/bin/ghl opportunities pipelines
~/ghl-cli/.venv/bin/ghl workflows list
~/ghl-cli/.venv/bin/ghl --experimental workflows preview --from-spec spec.json   # look before you build
~/ghl-cli/.venv/bin/ghl --experimental workflows build --from-spec spec.json     # builds a draft
~/ghl-cli/.venv/bin/ghl selftest --check-only        # what a live test could set off
```

On Windows (PowerShell), the same commands start with the Windows path instead:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" doctor
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" contacts list --limit 5
```

Add `--help` to any command to see its options.

---

## When something breaks

- Say **"fix my GHL keys"**. Claude runs `ghl doctor`, which names the key that stopped working and
  why, and redoes just that step with you.
- Say **"update the GHL CLI"**. Claude runs `ghl update`, which grabs the latest release and keeps
  your keys and settings. Didn't like it? `ghl update --undo` goes back.

---

## Removing it

1. Delete three folders: `~/ghl-cli` (the code), `~/.ghl-cli` (your saved keys and settings) and
   `~/.claude/skills/ghl-cli` (the skill). Or ask Claude to "remove the GHL CLI". Heads up:
   `~/.ghl-cli` also holds the snapshots behind each workflow edit's undo line, so those undos
   go with it. The skill folder may also hold a `SKILL.md.bak`, an older skill setup set aside;
   it goes too.
2. In GoHighLevel, delete the Private Integration you made for it. Look in your sub-account's
   settings, under Private Integrations.
3. Change your GoHighLevel password. That should end the login token setup copied from your
   browser.

---

## What's in here

- `cli_anything/gohighlevel/`: the CLI itself.
- `SETUP.md`: the step-by-step runbook Claude follows to set you up.
- `builders/`: real workflows from Lead Gen Jay's account, as examples (see `builders/README.md`).
- `docs/get-firebase-token.md`: the browser snippet for the workflow token.
- `docs/workflows-map.md` and `docs/workflows-live.json`: a map of Jay's own workflows by name,
  for ideas.
