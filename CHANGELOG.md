# Changelog

To update: say "update the GHL CLI" in Claude Code, or run `ghl update`. Your keys and settings
are kept. `ghl update --undo` goes back one version.

## 2.2.0 (2026-11-24)

- **It updates itself.** Setup now turns on automatic updates. Once a day, when Claude Code
  starts, the CLI quietly checks for a new release and installs it, with the same rules as
  `ghl update`: your keys and settings in `~/.ghl-cli` are never touched. If a new release
  doesn't set up cleanly, it puts the old version back by itself. Saying "update the GHL CLI"
  still works any time.
- **Your own changes are safe.** Ask Claude to change how it uses the CLI for you, and it saves
  that in `~/.solnest/ghl-cli/my-instructions.md`, outside the code folder, so no update wipes it.

After updating to this version, run setup once more (or just say "set up the GHL CLI") so the
automatic updates turn on.

## 2.1.1 (2026-11-09)

Two fixes found on a member's setup.

- **Newer GoHighLevel keys work.** GoHighLevel now hands out Private Integration keys in a newer
  format, and `ghl keys paste api-key` said they didn't look like a key. Any `pit-` key is now
  accepted, and `ghl doctor` checks it against GoHighLevel right after saving, same as before.
- **No more Google rate limiting on workflow commands.** Every workflow command used to ask
  Google for a fresh one-hour login pass, so a few commands in a row got "too many requests" and
  the CLI wrongly said the workflow token might be revoked. Now:
  - the pass is saved in `~/.ghl-cli` (locked to you, like your keys) and reused for its hour,
    so it's about one Google request an hour instead of one per command
  - if Google does say "too many requests", the CLI waits a few seconds and tries again, up to
    3 times
  - if it still can't, it says Google is rate limiting and your token is fine, instead of
    telling you to get a new token
  - `ghl doctor` asks Google once per run instead of twice
  - getting a new workflow token, or GoHighLevel refusing the pass, throws the old pass out

Nothing to do after updating. Your workflow token and GHL key stay as they are.

## 2.1.0 (2026-10-31)

First member release: `ghl keys paste`, `ghl config set-location`, `ghl doctor`,
`ghl selftest`, `ghl skill install`, `ghl update`, and the SETUP.md runbook for Claude Code.
