# GHL CLI: agent instructions

This folder is a member's own copy of the GHL CLI (Lead Gen Jay's GoHighLevel CLI, shared by
Solnest AI). It is a tool, not a project.

**Setting it up?** ("set this up", "set up the GHL CLI", this repo's link pasted.) Read
`SETUP.md` and run its phases in order, 0 to 5. It is a runbook for you to execute, not a doc
for the member to read. Do not skip a gate. Run every command yourself; the member never opens a
terminal.

A `~/ghl-cli` folder **without** a VERSION file is an older copy (for example the August zip from
Skool), not this tool. Never run its `install.sh` or follow its old SETUP.md: Phase 0 of this
SETUP.md handles it (it renames the folder after a plain yes, and can move its keys over).

## Hard rules

- **Never ask for a key in chat.** Not the GHL key, not the workflow token, not "paste it
  here". Keys go in only through `ghl keys paste api-key` or `ghl keys paste workflow-token`,
  which read the clipboard and never show the value. A key pasted into the chat by accident: one
  line, no lecture, then replace it. The GHL key: delete that Private Integration in GoHighLevel
  and make a new one (SETUP.md Phase 2). The workflow token: copying it again gives the same
  token, so the member changes their GoHighLevel password (or signs out of all sessions), logs
  back in, and redoes Phase 3.
- **Never read, `cat` or print `~/.ghl-cli/keys.env`, `~/.ghl-cli/workflow-pass.json`, or any
  `.env` file.** To know whether
  the keys work, run `ghl doctor`. It reports status, never values. (The one time you open
  `keys.env` is in a text editor for the member to paste into, when the clipboard can't be read.
  SETUP.md Phase 2 says how. You still never read it.) The one exception for a `.env`: SETUP.md
  Phase 1, step 1e moves an older copy's keys by piping each value from its `.env` straight into
  `ghl keys paste api-key --from-stdin` (or `workflow-token`). Nothing is printed. Use exactly
  those commands, never anything that shows the file.
- **A plain yes before any write to their GoHighLevel account.** Creating, editing or deleting
  anything, sending a message, enrolling a contact, building a workflow, running `ghl selftest`.
  Say what will change, ask, and wait for a plain yes. Reads are free.
- **Every `ghl selftest` mode** (plain, `--fire`, `--draft-only`, `--check-only`, `--cleanup`)
  runs with a Bash timeout of 600000 ms, or in the background. A big account takes minutes just
  to read its workflows, and a short timeout kills the run halfway.
- **Never commit, push, fork or open a pull request for this folder.** It is the member's copy
  of a tool, not a project, and it is not linked to any GitHub repo. When they say "save",
  everything is already saved on disk: say so. One exception: if `.git/ghl-keep-attached` exists
  (or `.git` is a file, a git worktree), this is a development copy of the tool itself, not a
  member's install, and this rule does not apply there.
- `ghl` is not on the PATH. Use the full path: `~/ghl-cli/.venv/bin/ghl` on Mac,
  `& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe"` on Windows (PowerShell). Start commands with
  `cd ~/ghl-cli &&` when the folder matters, because the working folder can reset between
  commands.

## After setup

- **"Fix my GHL keys":** run `ghl doctor`. It names the key that failed and why. Redo only the
  `SETUP.md` phase for that key (Phase 2 for the GHL key, Phase 3 for the workflow token or a
  location reach problem), then run `ghl doctor` again.
- **"Update the GHL CLI":** run `ghl update`. It downloads the latest release, keeps keys and
  settings, and runs `ghl doctor` with the new code. `ghl update --undo` goes back one version.
  Then run `ghl skill install` so the skill matches the new version.
- **Building workflows and everything else:** the `ghl-cli` skill that Phase 5 installs covers
  it. If it is missing, run `ghl skill install`, then start a new chat.
