# SETUP.md, the GHL CLI runbook

You are an AI agent setting up the GHL CLI for a member of the Solnest AI Skool community. They
run a business. They are not a developer. Assume no Python, no git and no terminal knowledge, and
zero tolerance for jargon. You run every command. The member clicks around in GoHighLevel and
copies two things, and that is it.

Run Phases 0 to 5 in order. Every phase ends with a **GATE**. Do not move on until the gate
passes. When something fails, use that phase's "If it fails" table instead of improvising.

Setup only proves things work. It does not build workflows for the member. Afterwards they build
their own by talking to Claude.

**Running it again** on a computer that already has it (Phase 0 tells you): go through every
phase, but run the GATE first. If it already passes, say so and move on. Phase 4 writes to their
account, so it only runs again if they want it to.

**Windows:** every phase has Windows commands, but they are written and not yet tested on a real
Windows PC (U6). Read errors closely there, and expect to adjust.

---

## HARD RULES, read before you touch anything

**1. Never ask the member to paste a key into the chat.** Not the GHL key, not the workflow
token, not "just send it to me". Either one opens their GoHighLevel account. They go from
GoHighLevel to the clipboard to `ghl keys paste`, which saves them and clears the clipboard
without ever showing them. If the member offers to paste one anyway, decline in one friendly
sentence and use `ghl keys paste`. If a key lands in the chat by accident: one line, no lecture,
then replace it. Each one is replaced differently:

- **The GHL key:** delete that Private Integration in GoHighLevel and make a new one (Phase 2).
- **The workflow token:** copying it again does not help, because the same browser login hands
  out the same token, and that token is their whole GoHighLevel login. They change their
  GoHighLevel password (or sign out of all sessions), log back in, and redo Phase 3.
  VERIFY IN BUILD (U4)

**2. Never read, `cat`, `type`, `Get-Content`, print or echo `~/.ghl-cli/keys.env`,
`~/.ghl-cli/workflow-pass.json` (the CLI's saved one-hour Google pass), or any `.env` file,
including an old install's.** Check that a file exists, never look inside. To know
whether keys work, run `ghl doctor`: it reports status, never values. The only time you read an
old `.env` at all is the Phase 1 key move, which pipes each value straight into
`ghl keys paste` without printing it.

**3. A plain yes before any write to their GoHighLevel account.** In setup that is Phase 4
(`ghl selftest`), and nothing else. Say what will happen, ask, and wait for a plain yes. No typed
codes, no "type this exact line".

**4. Never skip a gate.** A skipped gate turns a one-minute fix into a confusing failure three
phases later.

**5. The code lives at `~/ghl-cli`** (`%USERPROFILE%\ghl-cli` on Windows). Never under
Documents, Desktop, Downloads or OneDrive: macOS blocks Claude from those folders. Keys and
settings live separately, in `~/.ghl-cli`, so updates never touch them.

**6. Download, never `git clone`.** And never commit, push, fork or open a pull request for this
folder. It is the member's copy of a tool, not a project. When they say "save", everything is
already saved on disk.

**7. `ghl` is not on the PATH.** Always use the full path:

| | Mac | Windows (PowerShell) |
|---|---|---|
| ghl | `~/ghl-cli/.venv/bin/ghl` | `& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe"` |
| python | `~/ghl-cli/.venv/bin/python` | `& "$env:USERPROFILE\ghl-cli\.venv\Scripts\python.exe"` |

The Windows blocks below are PowerShell. If your shell tool on Windows is Git Bash, the Mac lines
work there with `.venv/Scripts/ghl.exe` and `.venv/Scripts/python.exe` in place of
`.venv/bin/ghl` and `.venv/bin/python`. The Mac-only tools (Spotlight's `mdfind` in Phase 0,
`open -e` in Phase 2) don't exist there: run those steps' Windows blocks through
`powershell.exe -NoProfile -Command` instead.

**Tell the member up front, in plain words:** this takes about 15 minutes. They will copy two
things from GoHighLevel (a key and a login token), and you will never ask them to paste either
into the chat. The one thing they paste into the chat is their GoHighLevel address bar, which is
not a secret.

---

## Phase 0. Look around before you install anything

**Goal:** know the OS, whether uv is there, whether Claude Code's skills folder exists, and
whether the GHL CLI was installed before, new style or old.

Mac:

```bash
uname -s
command -v uv || { test -x ~/.local/bin/uv && echo "uv at ~/.local/bin/uv"; } || echo "UV NOT INSTALLED"
test -d ~/.claude/skills && echo "SKILLS FOLDER OK" || echo "NO SKILLS FOLDER YET"
if [ -f ~/ghl-cli/VERSION ]; then echo "INSTALL FOUND: ~/ghl-cli, version $(cat ~/ghl-cli/VERSION)"
elif [ -f ~/ghl-cli/cli_anything/gohighlevel/gohighlevel_cli.py ]; then echo "OLD COPY AT ~/ghl-cli (no VERSION file)"
elif [ -e ~/ghl-cli ]; then echo "LEFTOVER ~/ghl-cli (no code in it)"; fi
test -f ~/.ghl-cli/keys.env && echo "KEYS FILE FOUND (not opened)"
test -f ~/.ghl-cli/config.json && echo "SETTINGS FILE FOUND"
command -v ghl && echo "A ghl COMMAND IS ON THE PATH (an older install)"
test -f ~/.claude/skills/ghl-cli/SKILL.md && echo "SKILL FILE FOUND"
```

Windows (PowerShell):

```powershell
$PSVersionTable.PSVersion
if (Get-Command uv -ErrorAction SilentlyContinue) { uv --version } elseif (Test-Path "$env:USERPROFILE\.local\bin\uv.exe") { "uv at $env:USERPROFILE\.local\bin\uv.exe" } else { "UV NOT INSTALLED" }
if (Test-Path "$env:USERPROFILE\.claude\skills") { "SKILLS FOLDER OK" } else { "NO SKILLS FOLDER YET" }
$D = "$env:USERPROFILE\ghl-cli"
if (Test-Path "$D\VERSION") { "INSTALL FOUND: ghl-cli, version " + (Get-Content "$D\VERSION") }
elseif (Test-Path "$D\cli_anything\gohighlevel\gohighlevel_cli.py") { "OLD COPY AT ghl-cli (no VERSION file)" }
elseif (Test-Path $D) { "LEFTOVER ghl-cli (no code in it)" }
if (Test-Path "$env:USERPROFILE\.ghl-cli\keys.env") { "KEYS FILE FOUND (not opened)" }
if (Test-Path "$env:USERPROFILE\.ghl-cli\config.json") { "SETTINGS FILE FOUND" }
$old = Get-Command ghl -ErrorAction SilentlyContinue
if ($old) { "A ghl COMMAND IS ON THE PATH (an older install): " + $old.Source }
if (Test-Path "$env:USERPROFILE\.claude\skills\ghl-cli\SKILL.md") { "SKILL FILE FOUND" }
```

**Then look for an older copy** somewhere else (for example the zip shared in Skool in August,
which kept its keys in a `.env` beside the code). These print folder paths only.

Mac. The loop looks in the home folder but stays out of Desktop, Documents and Downloads, because
macOS guards those and would pop up permission prompts. Spotlight (`mdfind`) covers them without
opening them:

```bash
{ for d in ~/*/; do
    case "$d" in */Desktop/|*/Documents/|*/Downloads/|*/Library/|*/Movies/|*/Music/|*/Pictures/) continue ;; esac
    [ -f "$d/cli_anything/gohighlevel/gohighlevel_cli.py" ] && echo "${d%/}"
  done
  mdfind -name gohighlevel_cli.py 2>/dev/null | sed -n 's#/cli_anything/gohighlevel/gohighlevel_cli.py$##p'
} | grep -v -e '/\.venv/' -e '/site-packages/' -e '/\.update-backup/' | sort -u
```

Windows:

```powershell
foreach ($d in @($env:USERPROFILE, "$env:USERPROFILE\Desktop", "$env:USERPROFILE\Documents", "$env:USERPROFILE\Downloads", "$env:USERPROFILE\OneDrive")) {
  Get-ChildItem -LiteralPath $d -Directory -Depth 1 -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'cli_anything\gohighlevel\gohighlevel_cli.py') } |
    ForEach-Object { $_.FullName }
}
```

For each folder it prints (skip `~/ghl-cli` when it has a VERSION file, that is the new one),
check whether it has keys beside it, without opening anything. **On a Mac, if the folder is under
Desktop, Documents or Downloads, tell the member first:** "macOS may ask whether Claude can
access that folder. Click Allow, and setup can find your old keys. Don't Allow is fine too, you'll
just make fresh keys."

Mac:

```bash
OLD="/full/path/the/search/printed"
if ! ls "$OLD" >/dev/null 2>&1; then echo "CAN'T LOOK INSIDE (macOS privacy): $OLD"
else
  test -f "$OLD/VERSION" && echo "VERSION $(cat "$OLD/VERSION")" || echo "NO VERSION FILE (older than 2.1.0)"
  test -f "$OLD/.env" && echo "OLD KEYS FILE FOUND (not opened)" || echo "NO .env"
fi
```

Windows:

```powershell
$OLD = "C:\full\path\the\search\printed"
try { [void](Get-ChildItem -LiteralPath $OLD -ErrorAction Stop); $canLook = $true } catch { $canLook = $false }
if (-not $canLook) { "CAN'T LOOK INSIDE: $OLD" }
else {
  if (Test-Path "$OLD\VERSION") { "VERSION " + (Get-Content "$OLD\VERSION") } else { "NO VERSION FILE (older than 2.1.0)" }
  if (Test-Path "$OLD\.env") { "OLD KEYS FILE FOUND (not opened)" } else { "NO .env" }
}
```

A blocked look is never the same as "nothing there". `CAN'T LOOK INSIDE` means you don't know
whether keys are in that folder.

**Decide, and write it down for later phases:**

| What you found | What to do |
|---|---|
| Nothing | A fresh install. Go on. |
| `INSTALL FOUND: ~/ghl-cli` | The code is already there (installed before, or just downloaded from the README's link). Phase 1 skips the download. Run every gate first (see "Running it again" above). |
| An older copy with `OLD KEYS FILE FOUND` | Ask: "I found an older copy of the GHL CLI at `<path>`. I'll install the new version in `ghl-cli` in your home folder and move your saved keys over without showing them. The old folder stays where it is until you delete it. OK?" On a plain yes, keep `<path>` for Phase 1, step 1e. |
| `OLD COPY AT ~/ghl-cli` | An older copy is sitting where the new one goes. Never run its `install.sh` or follow its old SETUP.md. Ask before touching it, then rename it (commands below; nothing is deleted). Use the renamed folder as the older copy above. |
| `LEFTOVER ~/ghl-cli` | Usually a failed download. If it's empty, remove it: Mac `rmdir ~/ghl-cli`, Windows `Remove-Item "$env:USERPROFILE\ghl-cli"` (both refuse a folder with anything in it). If it isn't empty, ask the member, then rename it like an older copy. |
| `CAN'T LOOK INSIDE (macOS privacy)` | macOS is blocking Claude from that folder. Two ways on: the member clicks Allow if macOS asks again, or drags the old folder into their home folder in Finder (it's being replaced anyway), and you run this check again on its new path. Or skip it: Phases 2 and 3 make fresh keys, which takes a few minutes. |
| An older copy with `NO .env` | Nothing to move. Install fresh. The member makes new keys in Phases 2 and 3. |
| `KEYS FILE FOUND` with no install | Keys from an earlier attempt. Leave the file alone. The Phase 2 and 3 gates say whether they still work. |
| `A ghl COMMAND IS ON THE PATH` | An older install's command. Setup never uses it (rule 7). Mention it in the Done summary so they know which one to remove later. |
| `SKILL FILE FOUND` | Maybe one the old setup had people copy by hand. Phase 5 handles it. |

**Renaming an older copy at `~/ghl-cli`** (after a plain yes). The dated name never lands inside
an existing folder.

Mac:

```bash
T=~/ghl-cli-old-$(date +%Y%m%d-%H%M%S)
if [ -e "$T" ]; then echo "$T already exists, run this again"; else mv ~/ghl-cli "$T" && echo "Renamed to $T"; fi
```

Windows:

```powershell
$T = "ghl-cli-old-" + (Get-Date -Format "yyyyMMdd-HHmmss")
Rename-Item -LiteralPath "$env:USERPROFILE\ghl-cli" -NewName $T
"Renamed to $env:USERPROFILE\$T"
```

**GATE:** you can state the OS, whether uv is installed, whether the skills folder exists, what
`~/ghl-cli` is (nothing, this tool, an older copy, or a leftover), every older copy and whether it
has a `.env` (or that you can't look inside it), and the member's answer about moving keys.

**If it fails:**

| What you see | What to do |
|---|---|
| You have no terminal at all (a chat with no shell tool) | Say you can't run setup from here. They open the Code tab in the Claude desktop app (or Claude Code), paste the repo link and say "set this up". Never fake progress. |
| `mdfind` prints nothing (Mac) | Spotlight may be off. Ask the member: "Did you install the GHL CLI before, for example from a zip in Skool? Where did you put it?" Use the folder they name. |
| Windows search shows red access errors | Harmless. It skips folders it can't read. |
| Several older copies | Ask which one they actually used. Move keys from that one only. |
| The member says no to moving keys | Fine. Install fresh. They make new keys in Phases 2 and 3. |
| This chat is open inside a copy of this repo somewhere else (Documents, Desktop, Downloads) | Don't build there (rule 5). Phase 1 puts a fresh copy in `~/ghl-cli`. Tell them they can delete the other copy once setup is done. |
| This chat is open inside an older copy (no VERSION file) | Don't run its `install.sh` and don't follow its SETUP.md. Follow this one. |

---

## Phase 1. Install the code

**Goal:** the code in `~/ghl-cli`, a working `.venv`, and the `ghl` command answering with the
right version.

One line for the member first: "uv is a small tool that installs the right Python and everything
the CLI needs, so you never have to think about Python versions. The first run downloads a bit
and can take a few minutes."

**1a. Get the code.** What Phase 0 said about `~/ghl-cli` decides this:

- `INSTALL FOUND` (it has a VERSION file): it's this tool. Do **not** download over it. Skip to 1c.
  Do 1d afterwards only if it was there before this session (not if you downloaded it today from
  the README's line).
- `OLD COPY` or `LEFTOVER`: it must be renamed or removed first (Phase 0 table). Never download
  into it, never run its `install.sh`.
- Nothing there: download it. This puts the **latest release** in `~/ghl-cli` with no git needed
  (never `git clone`, rule 6), and removes the empty folder again if the download fails. It asks
  GitHub which release is the latest (`releases/latest` points at its tag), then downloads that
  tag. Never download the `main` branch instead: it can hold changes that aren't released yet.

Mac:

```bash
D=~/ghl-cli; R=https://github.com/Solnest-AI/ghl-cli
if [ -e "$D" ]; then echo "ALREADY THERE: $D (back to the Phase 0 table)"
else
  U=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$R/releases/latest") || U=
  case "$U" in */releases/tag/v[0-9]*|*/releases/tag/[0-9]*) TAG=${U##*/} ;; *) TAG= ;; esac
  if [ -z "$U" ]; then echo "DOWNLOAD FAILED (could not reach GitHub)"
  elif [ -z "$TAG" ]; then echo "NO RELEASE PUBLISHED YET"
  elif mkdir "$D" && curl -fsSL -o "$D.tar.gz" "$R/archive/refs/tags/$TAG.tar.gz" && tar -xzf "$D.tar.gz" -C "$D" --strip-components 1; then rm -f "$D.tar.gz"; echo "DOWNLOADED $TAG"; ls "$D/setup.sh"
  else rm -f "$D.tar.gz"; rmdir "$D" 2>/dev/null; echo "DOWNLOAD FAILED"; fi
fi
```

Windows:

```powershell
$D = "$env:USERPROFILE\ghl-cli"; $Repo = "https://github.com/Solnest-AI/ghl-cli"
if (Test-Path $D) { "ALREADY THERE: $D (back to the Phase 0 table)" } else {
  $U = curl.exe -fsSLI -o NUL -w '%{url_effective}' "$Repo/releases/latest"
  if ($LASTEXITCODE -ne 0) { $U = "" }
  $TAG = if ("$U" -match '/releases/tag/(v?[0-9][^/?#]*)$') { $Matches[1] } else { "" }
  if (-not $U) { "DOWNLOAD FAILED (could not reach GitHub)" }
  elseif (-not $TAG) { "NO RELEASE PUBLISHED YET" }
  else {
    New-Item -ItemType Directory -Path $D | Out-Null
    curl.exe -fsSL -o "$D.tar.gz" "$Repo/archive/refs/tags/$TAG.tar.gz"
    if ($LASTEXITCODE -eq 0) { tar.exe -xzf "$D.tar.gz" -C $D --strip-components 1 }
    $ok = ($LASTEXITCODE -eq 0) -and (Test-Path "$D\setup.ps1")
    Remove-Item "$D.tar.gz" -ErrorAction SilentlyContinue
    if ($ok) { "DOWNLOADED $TAG to $D" } else {
      if (-not (Get-ChildItem -LiteralPath $D -Force)) { Remove-Item -LiteralPath $D }
      "DOWNLOAD FAILED"
    }
  }
}
```

**Windows without `curl.exe` or `tar.exe`** (older Windows 10). Download the release's zip
instead. The first line turns on TLS 1.2, which GitHub needs and older Windows PowerShell leaves
off. Moving the unpacked folder only works while `ghl-cli` does not exist, so an empty one from
the failed try is removed first. The zip holds one folder named after the release, so the block
moves whatever single folder it finds:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$D = "$env:USERPROFILE\ghl-cli"; $Repo = "https://github.com/Solnest-AI/ghl-cli"
if ((Test-Path $D) -and -not (Get-ChildItem -LiteralPath $D -Force)) { Remove-Item -LiteralPath $D }
if (Test-Path $D) { "ALREADY THERE: $D (back to the Phase 0 table)" } else {
  try { $resp = Invoke-WebRequest "$Repo/releases/latest" -Method Head -UseBasicParsing -ErrorAction Stop } catch { $resp = $null }
  $U = if (-not $resp) { "" } elseif ($resp.BaseResponse.ResponseUri) { $resp.BaseResponse.ResponseUri.AbsoluteUri } else { $resp.BaseResponse.RequestMessage.RequestUri.AbsoluteUri }
  $TAG = if ("$U" -match '/releases/tag/(v?[0-9][^/?#]*)$') { $Matches[1] } else { "" }
  if (-not $U) { "DOWNLOAD FAILED (could not reach GitHub)" }
  elseif (-not $TAG) { "NO RELEASE PUBLISHED YET" }
  else {
    Remove-Item -Recurse -Force "$env:TEMP\ghl-cli-dl" -ErrorAction SilentlyContinue
    Invoke-WebRequest "$Repo/archive/refs/tags/$TAG.zip" -OutFile "$env:TEMP\ghl-cli.zip" -UseBasicParsing
    Expand-Archive "$env:TEMP\ghl-cli.zip" "$env:TEMP\ghl-cli-dl" -Force
    $top = Get-ChildItem -LiteralPath "$env:TEMP\ghl-cli-dl" -Directory | Select-Object -First 1
    Move-Item $top.FullName $D
    Remove-Item "$env:TEMP\ghl-cli.zip"; Remove-Item -Recurse "$env:TEMP\ghl-cli-dl"
    Test-Path "$D\setup.ps1"
  }
}
```

**1b. Nothing to install by hand.** Setup in 1c finds or installs uv and Python 3.12 itself. Never
send the member off to install Python.

**1c. Run setup.** Give it the longest timeout your Bash tool allows (600000 ms): a first run on
a fresh laptop downloads uv and Python. It is safe to run again, and it never touches keys.

Mac:

```bash
cd ~/ghl-cli && bash setup.sh --no-prompt --auto-install
```

Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\ghl-cli\setup.ps1" -NoPrompt -AutoInstall
```

**Expected output:** three numbered steps marked `OK`, one `GitHub:` line (relay it), and
`ALL SET.` with the path of the `ghl` command. Exit code 0.

**1d. Only if `~/ghl-cli` was there before this session:** bring it to the latest release. Skip
this when you downloaded it in 1a (or from the README's line) this session: that download already
is the latest release.

Mac:

```bash
~/ghl-cli/.venv/bin/ghl update
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" update
```

Relay its lines. "Already on the latest release" is fine. An `update:` error is not a blocker
either: the copy they have still works, so carry on with the gate (the table says which ones).

**1e. Only if Phase 0 found an older copy with keys and the member said yes:** move its keys over.
Each value goes from the old `.env` straight into `ghl keys paste` through a pipe, read by the
CLI's own `read_env_file`. Nothing is printed. Never `cat` the old file. It first checks the file
can be opened at all, so a blocked read says `CAN'T READ` instead of looking like an empty value.
The sub-account goes first, so the key check can run. Phase 2a still runs afterwards, because the
old copy only saved a bare id and the domain GoHighLevel uses for links comes from the address
bar.

Mac (one command, so the variables carry through):

```bash
OLD="/full/path/to/the/old/folder"
PY=~/ghl-cli/.venv/bin/python
GET='import sys; from pathlib import Path; from cli_anything.gohighlevel.utils.settings import read_env_file; sys.stdout.write(read_env_file(Path(sys.argv[1])).get(sys.argv[2]) or str())'
if "$PY" -c 'import sys; open(sys.argv[1]).close()' "$OLD/.env" 2>/dev/null; then
  ~/ghl-cli/.venv/bin/ghl config set-location "$("$PY" -c "$GET" "$OLD/.env" GHL_LOCATION_ID)"
  "$PY" -c "$GET" "$OLD/.env" GHL_API_KEY | ~/ghl-cli/.venv/bin/ghl keys paste api-key --from-stdin
  "$PY" -c "$GET" "$OLD/.env" GHL_FIREBASE_REFRESH_TOKEN | ~/ghl-cli/.venv/bin/ghl keys paste workflow-token --from-stdin
else echo "CAN'T READ $OLD/.env (macOS privacy or file permissions). Nothing was moved."; fi
```

Windows (one command; the Python code has no double quotes, on purpose, because Windows
PowerShell 5.1 mangles them when it calls another program):

```powershell
$OLD = "C:\full\path\to\the\old\folder"
$PY = "$env:USERPROFILE\ghl-cli\.venv\Scripts\python.exe"
$GET = 'import sys; from pathlib import Path; from cli_anything.gohighlevel.utils.settings import read_env_file; sys.stdout.write(read_env_file(Path(sys.argv[1])).get(sys.argv[2]) or str())'
& $PY -c 'import sys; open(sys.argv[1]).close()' "$OLD\.env" 2>$null
if ($LASTEXITCODE -eq 0) {
  & "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" config set-location (& $PY -c $GET "$OLD\.env" GHL_LOCATION_ID)
  & $PY -c $GET "$OLD\.env" GHL_API_KEY | & "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" keys paste api-key --from-stdin
  & $PY -c $GET "$OLD\.env" GHL_FIREBASE_REFRESH_TOKEN | & "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" keys paste workflow-token --from-stdin
} else { "CAN'T READ $OLD\.env (file permissions). Nothing was moved." }
```

**Expected output:** `Sub-account set: <id>`, then `saved, works` (or `saved, but: ...`) once
per key. Relay each line. Any key that did not come over cleanly gets made fresh in its phase
(see the table). Tell the member the old folder is still there and they can delete it once
setup is done.

**GATE:**

Mac:

```bash
~/ghl-cli/.venv/bin/ghl --version; cat ~/ghl-cli/VERSION
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" --version; Get-Content "$env:USERPROFILE\ghl-cli\VERSION"
```

Must print `ghl, version 2.1.0` (or a newer number) and the same number from the VERSION file.

**If it fails:**

| What you see | What to do |
|---|---|
| `ALREADY THERE` | Never download over it. With a VERSION file it's this tool: go to 1c. Without one, back to the Phase 0 table (older copy or leftover). |
| `DOWNLOAD FAILED` or `DOWNLOAD FAILED (could not reach GitHub)` | Usually the network: try again. The empty folder was removed, so nothing is left behind. If it keeps failing, ask the member for the exact repo link they were given and use its owner and name in the URL (a wrong link fails the same way). |
| `NO RELEASE PUBLISHED YET` | GitHub has the repo but no release of it yet, so there is nothing to install. Never download the `main` branch instead: it can hold unfinished changes. Tell the member plainly, stop here, and run Phase 1 again once a release shows on the repo's Releases page (`https://github.com/Solnest-AI/ghl-cli/releases`). Nothing was created, so there's nothing to clean up. |
| Windows: `curl.exe` or `tar.exe` not found (older Windows 10) | The zip download above. |
| A `~/ghl-cli` with some files but no `setup.sh` after a failed try | A half-finished download. Phase 0 reports it as a leftover or an older copy. Rename it aside (Phase 0) and download again. |
| `SETUP STOPPED: uv could not be installed` | Usually the network. Run setup again. On a VPN or work network, try another network. |
| `SETUP STOPPED: uv could not provide Python 3.12` | Same: run setup again on a working connection. |
| `SETUP STOPPED: Package install failed` | Same. It is safe to rerun. |
| `SETUP STOPPED: ... not the GHL CLI folder` | The download didn't land. Check that `~/ghl-cli/setup.sh` exists, then redo 1a. |
| Windows says running scripts is disabled | Use the exact `-ExecutionPolicy Bypass` command above. Never tell the member to change their machine-wide policy. |
| The app blocks the command ("Blocked", "no verdict") | That's the app's Auto mode classifier, not the command. Switch the session to Manual and run it again. |
| The command timed out | Run it again with the longest timeout. A second run is fast. |
| `GitHub: kept attached` | The folder is a git clone with someone's own edits. It still works. `ghl update` refuses a linked folder, so updates for it come from git. |
| `ghl --version` shows a different number than VERSION | Run setup (1c) again. |
| 1d: `update: could not reach GitHub` or `could not download` | Not a blocker. Their copy works as it is. Carry on, and run `ghl update` another day. |
| 1d: `update: GitHub has no published release of the GHL CLI yet` | Not a blocker. Carry on. |
| 1d: `update: this folder is still linked to git` | Not a blocker. It's a developer's copy (see `GitHub: kept attached`). Carry on. |
| 1d: any other `update:` line ending in `nothing was changed`, or saying it put the previous version back | Not a blocker. The folder is exactly as it was. Carry on with the gate. |
| 1e: `CAN'T READ ... Nothing was moved.` | A blocked read, not an empty file. On a Mac, the old folder is probably under Desktop, Documents or Downloads: the member drags it into their home folder in Finder and you run 1e again with the new path. Or skip it, and Phases 2 and 3 make fresh keys. |
| 1e: `That doesn't look like ... (it's empty)` | The file was readable and that value really isn't in it. Its phase (2 for the key, 3 for the token) makes it fresh. |
| 1e: `... (it's N characters and not the right shape)` for the GHL key | The old copy used an older kind of key, not a Private Integration. Phase 2 makes a new one. |
| 1e: `saved, but: GHL refused the key` | The old key was revoked. Phase 2 makes a new one. |
| 1e: `saved, but: Google would not accept the saved token` | The old login token expired. Phase 3 gets a fresh one. |
| 1e: `Missing argument` or `does not look like a sub-account` | The old copy had no sub-account saved. Phase 2a sets it. |

---

## Phase 2. The GHL key (a Private Integration)

**Goal:** their sub-account saved, a Private Integration key saved, and `ghl doctor` saying the
key works for that sub-account.

**2a always runs**, even when you're running setup again and even after 1e moved keys over: it
saves the domain GoHighLevel uses for links and for Phase 3, and confirms the sub-account. After
2a, run the GATE. If it already passes, skip 2b and 2c.

**2a. Which sub-account.** Say to the member: "Open GoHighLevel in your browser and switch into
the sub-account you want this to work on (the account switcher at the top left). Then copy the
whole address from the address bar and paste it here. It's not a secret."

It looks like `https://app.gohighlevel.com/v2/location/<20 letters and numbers>/dashboard`, or
the same on their agency's own address.

Mac:

```bash
~/ghl-cli/.venv/bin/ghl config set-location "PASTE_THE_ADDRESS_HERE"
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" config set-location "PASTE_THE_ADDRESS_HERE"
```

**Expected output:** `Sub-account set: <id> (domain https://...)`. Remember the domain: Phase 3
happens on that same address.

**2b. Make the key.** The member does the clicks, in that same sub-account, never at the agency
level. These steps are being verified against GoHighLevel's current screens (U5). If a screen
looks different, tell the member what you're looking for and let them describe what they see.

1. Open **Settings** (bottom of the left menu). VERIFY IN BUILD (U5)
2. Open **Private Integrations**. VERIFY IN BUILD (U5)
3. Click **Create new Integration**. VERIFY IN BUILD (U5)
4. Name it `ghl-cli` (any name works) and go on to the permissions. VERIFY IN BUILD (U5)
5. Tick the boxes in the table below. VERIFY IN BUILD (U5)
6. Create it. GoHighLevel shows the key once. Click **Copy**. VERIFY IN BUILD (U5)
7. Come back here and say "copied". Don't paste it.

A key made at the agency level does not reach contacts and the rest; it has to be made inside the
sub-account. VERIFY IN BUILD (U5)

**The boxes to tick.** This list comes from the GoHighLevel API calls the CLI actually makes, and
it is being verified (U5). The names on GHL's screen may differ a little. The `GHL scope` column
is GHL's own name for each permission, so match on that when the labels are unclear. To finish
setup they need Contacts and Locations. The rest is for the matching commands, and ticking it now
saves a trip back. VERIFY IN BUILD (U5)

| Area | Tick | GHL scope | Used by |
|---|---|---|---|
| Contacts | View and Edit | `contacts.readonly`, `contacts.write` | `contacts`, adding and removing someone from a workflow, the Phase 4 test contact |
| Locations | View | `locations.readonly` | `locations get`, `ghl doctor` |
| Opportunities | View and Edit | `opportunities.readonly`, `opportunities.write` | `opportunities`, including pipelines |
| Calendars | View | `calendars.readonly` | `calendars list`, `get`, `slots` |
| Calendar Groups | View | `calendars/groups.readonly` | `calendars groups` |
| Calendar Events | View and Edit | `calendars/events.readonly`, `calendars/events.write` | `calendars appointments`, `book` |
| Conversations | View | `conversations.readonly` | `conversations list`, `get` |
| Conversation Messages | View and Edit | `conversations/message.readonly`, `conversations/message.write` | `conversations messages`, `get-email`, `send` |
| Workflows | View | `workflows.readonly` | `workflows list` |
| Campaigns | View | `campaigns.readonly` | `emails list-campaigns` |
| Email Templates | View | `emails/builder.readonly` | `emails templates` |
| Email Schedules | View | `emails/schedule.readonly` | `emails scheduled` |
| Invoices | View and Edit | `invoices.readonly`, `invoices.write` | `payments invoices`, `create-invoice`, `send-invoice`, `void-invoice` |
| Payment Orders | View | `payments/orders.readonly` | `payments orders` |
| Payment Transactions | View | `payments/transactions.readonly` | `payments transactions` |
| Products | View | `products.readonly` | `payments products` |
| Forms | View | `forms.readonly` | `forms list`, `submissions` |
| Social Planner accounts | View | `socialplanner/account.readonly` | `social accounts` |
| Social Planner posts | View and Edit | `socialplanner/post.readonly`, `socialplanner/post.write` | `social posts`, `create-post` |
| Custom Fields | View | `locations/customFields.readonly` | `locations custom-fields` |
| Custom Values | View | `locations/customValues.readonly` | `locations custom-values` |
| Tags | View | `locations/tags.readonly` | `locations tags` |
| Users | View | `users.readonly` | `locations users` |
| Documents and Contracts | View and Send | `documents_contracts/list.readonly`, `documents_contracts/sendLink.write` | `documents list`, `send` |
| Document Templates | View and Send | `documents_contracts_template/list.readonly`, `documents_contracts_template/sendLink.write` | `documents templates`, `send-template` |

`locations search` needs agency-level access, so a sub-account key can't run it. That's expected.

**Private Integrations is not in the sub-account's Settings?** Their login isn't allowed to make
one in this sub-account. That's an agency setting, not something setup can change from here.
VERIFY IN BUILD (U2). Tell the member exactly that, and that the fix is to ask their agency
admin to turn on Private Integrations for this sub-account and their user. For a sub-account in
the GHL Member Hub, the agency admin is the Solnest AI team. Pause setup there. When it's on,
they say "set up the GHL CLI" again: Phase 0 finds the install, and setup picks up here.

**2c. Save the key.** After the member says "copied":

Mac:

```bash
~/ghl-cli/.venv/bin/ghl keys paste api-key
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" keys paste api-key
```

**Expected output:** `saved, works`. `saved. Next: set your sub-account.` means 2a was skipped: do
2a, then the gate. `saved, but: ...` comes with a `fix:` line: follow it (the table below has
each one).

**GATE:**

Mac:

```bash
~/ghl-cli/.venv/bin/ghl doctor --only api-key
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" doctor --only api-key
```

Then read one contact. That proves Contacts View even if they stop before Phase 4 (Phase 4's test
contact proves Edit). It prints only OK or the error, never the contact:

Mac:

```bash
~/ghl-cli/.venv/bin/ghl contacts list --limit 1 >/dev/null && echo "CONTACTS READ OK"
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" contacts list --limit 1 | Out-Null; if ($LASTEXITCODE -eq 0) { "CONTACTS READ OK" }
```

Must print `GHL key: OK for <sub-account name>` (exit 0), then `CONTACTS READ OK`. Read the name to
the member and check it's the sub-account they meant. An account with no contacts yet still prints
`CONTACTS READ OK`.

**If it fails:**

| What you see | What to do |
|---|---|
| `no GHL key is saved yet` | 2c didn't save. Have them copy the key again and rerun 2c. |
| `no sub-account is set` | Do 2a. |
| `That address is not inside a sub-account` (2a) | They copied the agency view. Switch into the sub-account first, then copy the address bar again. |
| `this key belongs to a different sub-account` | The key was made in another sub-account, or at the agency level. Make it inside the sub-account from 2a and rerun 2c. Or, if the key's sub-account is the right one, redo 2a with that sub-account's address. |
| `the key works but is missing a permission` | Open the Private Integration, tick the missing boxes from the table, save, and run the gate again. If GHL hands out a new key when you save, copy it and rerun 2c. |
| `GHL refused the key` | Revoked or copied wrong. Make a fresh key in the Private Integration and rerun 2c. |
| `could not reach GoHighLevel` | Check the internet connection, then run the gate again. |
| `GHL answered with HTTP <number>` | GHL had a moment. Wait a minute and run the gate again. |
| The contacts read prints `API Error (401)` or `API Error (403)` | The Contacts boxes aren't ticked. Open the Private Integration, tick Contacts View and Edit, save, and run the gate again (if GHL hands out a new key, copy it and rerun 2c). |
| `That doesn't look like a GHL Private Integration key (...)` | The clipboard held something else. The message says what it looked like (a web address, the login token, too short). Copy the key again and rerun 2c. If GHL only showed it once and it's gone, make a new one. |
| `The clipboard can't be read on this computer` | Open the key file in a text editor for the member (commands below the table). Never read it back. |
| `(it's empty)` and then `If you did copy it, this computer may be blocking the clipboard` | Ask once whether they clicked **Copy**. If not, they copy it and you rerun 2c. If they did, the clipboard is blocked (a privacy setting, a remote desktop, a clipboard manager): use the key file route below the table instead. Never ask them to paste the key into the chat. |
| No Private Integrations in Settings | The agency setting above. Pause there. Never a dead end: say what to ask for and who to ask. |
| The member pasted the key into the chat | One line: that key is exposed now. Delete that Private Integration in GHL, make a new one, copy it, say "copied". |

**When the clipboard can't be read.** Open the key file in a text editor for the member. You never
read it, before or after.

Mac:

```bash
mkdir -p ~/.ghl-cli && touch ~/.ghl-cli/keys.env && chmod 600 ~/.ghl-cli/keys.env && open -e ~/.ghl-cli/keys.env
```

Windows:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ghl-cli" | Out-Null
if (-not (Test-Path "$env:USERPROFILE\.ghl-cli\keys.env")) { New-Item -ItemType File "$env:USERPROFILE\.ghl-cli\keys.env" | Out-Null }
notepad "$env:USERPROFILE\.ghl-cli\keys.env"
```

Tell them: on a new line, type `GHL_API_KEY=` and paste the key right after the `=` (for the
Phase 3 token, `GHL_FIREBASE_REFRESH_TOKEN=` instead), save, close the editor, and say "saved".
Then run the gate.

---

## Phase 3. The workflow key (the browser login token)

**Goal:** the login token saved, and `ghl doctor` showing it reaches their sub-account.

If you are running it again and the gate already passes, move on to Phase 4.

**3a. Say this once, plainly:** "GoHighLevel's public API can't build workflows, so this step uses
the same login token GoHighLevel's own website uses. It's unofficial, and it can break when GHL
updates. If you'd rather not, we can stop here and you can still use every other command."

If they'd rather not: skip Phase 3 and Phase 4 (the live test needs this token), go to Phase 5,
and say in the Done summary that workflow building isn't set up.

**3b. Get the token.** The member does this in their browser:

1. In Chrome (or Edge, Brave, any Chromium browser), open GoHighLevel on the **same address** as
   Phase 2 (the domain `ghl config set-location` printed), logged in, on a normal page inside the
   app such as the dashboard.
2. Open the Console: **Cmd+Option+J** on Mac, **Ctrl+Shift+J** on Windows.
3. Paste the snippet and press Enter. Give it to them in the chat, copied exactly from the code
   block in `docs/get-firebase-token.md`, first line `var _copy = copy;` included. It isn't a
   secret, it only reads their own browser. If Chrome blocks the paste, they type
   `allow pasting`, press Enter and paste again.
4. The console prints `✓ Copied ... chars. Now go back to Claude and say copied.` The token is
   on their clipboard. They come back and say "copied".

**3c. Save the token.** After "copied":

Mac:

```bash
~/ghl-cli/.venv/bin/ghl keys paste workflow-token
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" keys paste workflow-token
```

**Expected output:** `saved, works`, or `saved, but: ...` with a `fix:` line (see the table).

**GATE:**

Mac:

```bash
~/ghl-cli/.venv/bin/ghl doctor
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" doctor
```

Must print all three as OK and exit 0:

```
  GHL key: OK for <sub-account name>
  Workflow token: OK Google accepts the saved token
  Location reach: OK can list workflows
```

**If it fails:**

| What you see | What to do |
|---|---|
| Console: `No refresh token found` | Not logged in on that tab, or on a marketing page. Open the app itself, log in, run the snippet again. |
| Console: `copy is not defined` | An old copy of the snippet. Use the one in `docs/get-firebase-token.md` exactly, first line included. |
| Console won't take the paste | Type `allow pasting`, press Enter, paste again. |
| `That doesn't look like the GHL login token (... looks like a GHL Private Integration key)` | The clipboard still held the Phase 2 key. Run the snippet again and don't copy anything else before saying "copied". |
| `Workflow token: PROBLEM no workflow token is saved yet` | Rerun 3c. |
| `The clipboard can't be read on this computer` | Same as Phase 2's "When the clipboard can't be read", with the line `GHL_FIREBASE_REFRESH_TOKEN=`. |
| `Google would not accept the saved token` | That login token has ended (a password change does it, for example). Run the snippet again, then 3c. |
| `could not reach Google to check the saved token` | Check the internet connection, then run the gate again. |
| `Google is rate limiting token checks right now` | The token is fine. Google is slowing down requests for a minute (the CLI already waited and retried 3 times). Wait a minute, then run the gate again. Do not grab a new token for this. |
| `Location reach: PROBLEM GHL refused <id>. This login can reach: ...` | The browser was logged in as someone who can't reach that sub-account (often a different agency). Log in on the browser as the user who owns the sub-account and redo 3b. If the list shows the sub-account they meant, run `ghl config set-location <that id>` and redo Phase 2's gate. |
| `GHL changed something on its side. This is not your setup.` | Run `ghl update` (Phase 1, step 1d), then the gate again. |
| `GHL is having trouble right now. Try again in a few minutes.` | GHL's own servers are having a moment. Wait a few minutes and run the gate again. If it keeps happening for an hour or more, run `ghl update` (Phase 1, step 1d), then the gate. |
| `GHL could not find workflows for <id>. The sub-account may be wrong.` | The saved sub-account id isn't one GHL knows. Redo 2a with the address bar copied from inside the right sub-account, then run the gate again. |
| `GHL is rate limiting you right now` | Wait a minute, then run the gate again. |
| Their agency uses its own address and the snippet finds nothing on `app.gohighlevel.com` | Run it on the address they log in on every day, the domain from 2a. VERIFY IN BUILD (U3) |
| The member doesn't want this step | Skip to Phase 5. Phase 4 needs this token. |
| The member pasted the token into the chat | Copying it again won't fix it: the same browser login gives the same token, and it is their whole GoHighLevel login. They change their GoHighLevel password (or sign out of all sessions), log back in, run the snippet again, then 3c. VERIFY IN BUILD (U4) |

---

## Phase 4. The live test

**Goal:** proof that a workflow trigger fires in their own account, with nothing left behind.

Say to the member, plainly: "This is a real test in your GoHighLevel account. It builds a
throwaway workflow called `ghl-cli setup check <run id>`, turns it on, makes a test contact with
no email or phone so nothing can be texted or emailed to it, checks the workflow ran, then
deletes everything it made." Then this one line too: it checks workflows only. GHL's old-style
Triggers and Conversation AI aren't checked.

**4a. Check what could be set off.** This writes nothing. It reads every published workflow, which
takes minutes on an account with a lot of them, so run it with a Bash timeout of 600000 ms (or in
the background and check on it). On a big account it prints `Reading N published workflows...`
first: that means it's working.

Mac:

```bash
~/ghl-cli/.venv/bin/ghl selftest --check-only
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" selftest --check-only
```

**4b. Ask, based on what it printed.**

- `No published workflow would be set off by the selftest.`: ask "Run the live test now? It
  writes to your account for a minute or two and deletes what it makes." On a plain yes, run
  `ghl selftest`.
- `N published workflow(s) could be set off by the selftest:` with a list, or `Could not read all
  your workflows`: show them the list exactly as printed. Say plainly: "If we fire the test,
  those workflows will really run on the test contact. Their actions will really happen
  (including anything they send to your team), and the selftest won't undo them." Then offer two
  choices. Fire anyway: on a plain yes to that, run `ghl selftest --fire`. Or don't fire: on a
  plain yes, run `ghl selftest --draft-only`, which builds, reads back and deletes the test
  workflow without firing it.
- A no to everything: skip to Phase 5, and the Done summary says the live test didn't run.

**4c. Run it.** Run every selftest mode (plain, `--fire`, `--draft-only`, and `--check-only` and
`--cleanup` too) with a Bash timeout of 600000 ms, or in the background and check on it. A run
that doesn't fire waits over two minutes, and an account with many workflows takes minutes just
to read them. A short timeout kills it halfway.

Mac (pick the one they said yes to):

```bash
~/ghl-cli/.venv/bin/ghl selftest
~/ghl-cli/.venv/bin/ghl selftest --fire
~/ghl-cli/.venv/bin/ghl selftest --draft-only
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" selftest
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" selftest --fire
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" selftest --draft-only
```

Its first line is `Run id <run>. If this stops early, run: ghl selftest --cleanup <run>`. Note
that run id. If the run is interrupted or times out, the next thing you do is
`ghl selftest --cleanup RUN` with that id, before anything else.

**GATE:** the output has a line that reads exactly `PASS` or exactly `PASS, firing not tested`,
and the exit code is 0. Lines can follow it (a list of workflows the test could set off, or
`To test firing later: ghl selftest --fire`), so don't look only at the last line. With
`PASS, firing not tested`, tell the member plainly that firing wasn't tested, and that
`ghl selftest --fire` tests it later. The Done summary says it again.

**If it fails:**

| What you see | What to do |
|---|---|
| `FAIL: ...` with `Still in your account:` lines | Each line names what's left and the command that removes it. Run `ghl selftest --cleanup RUN` with the run id, then deal with the reason. |
| The run was interrupted, or your Bash call timed out | `ghl selftest --cleanup RUN` with the id from the first line. Then run the test again with a longer timeout. |
| `--cleanup` itself says `FAIL: part of selftest run ... is still in your account` | Run it once more. If GHL still refuses, have the member delete what's listed by hand in GHL: the `ghl-cli setup check` workflow folder, the test contact, and the test tags the lines name. |
| Exit 4 with `GoHighLevel's internal API refused this location` | The workflow token can't reach this sub-account. Back to Phase 3's gate. |
| `a GHL key stopped working partway through. Run: ghl doctor` | Run `ghl doctor` and redo the phase for whichever key it names. |
| `could not make the test contact` | Usually the Contacts Edit box. Fix the Private Integration (Phase 2 table), then run the test again. |
| `the test workflow did not fire within ...` | GHL's workflow queue was slow. Run it again later. If it keeps happening, `ghl selftest --draft-only` proves building works, and the Done summary says firing wasn't tested. |
| `GHL did not build the test workflow` or `GHL would not turn the test workflow on` | Run `ghl doctor`. If it says GHL changed something, run `ghl update`, then the test again. If `ghl doctor` is all OK, their GoHighLevel user may not be allowed to create or publish workflows in this sub-account: they ask their agency admin to allow it (for GHL Member Hub sub-accounts, the Solnest AI team), then run the test again. Until then, finish with Phase 5 and say in the Done summary that the live test didn't pass. VERIFY IN BUILD (U1) |

---

## Phase 5. Install the skill

**Goal:** every future Claude Code chat knows how to drive the CLI, from this install's own path.

Mac:

```bash
~/ghl-cli/.venv/bin/ghl skill install
```

Windows:

```powershell
& "$env:USERPROFILE\ghl-cli\.venv\Scripts\ghl.exe" skill install
```

**Expected output:** `Skill written: <path>/.claude/skills/ghl-cli/SKILL.md`.

If it says the file already exists and `ghl` did not write it, that's an older hand-copied skill
(Phase 0 may have spotted it). Tell the member, and on a plain yes run `ghl skill install --force`.
Their old file is saved as `SKILL.md.bak` next to it.

**GATE:** the file exists, and the `ghl` path written in it runs.

Mac:

```bash
F=~/.claude/skills/ghl-cli/SKILL.md; test -f "$F" && P=$(sed -n 's/^GHL="\(.*\)"$/\1/p' "$F" | head -n 1) && echo "skill points at: $P" && "$P" --version
```

Windows:

```powershell
$F = "$env:USERPROFILE\.claude\skills\ghl-cli\SKILL.md"
$P = (Select-String -LiteralPath $F -Pattern '^GHL="(.+)"$' | Select-Object -First 1).Matches[0].Groups[1].Value
"skill points at: $P"; & $P --version
```

Must print the path of this install's `ghl` and `ghl, version 2.1.0` (or newer).

**If it fails:**

| What you see | What to do |
|---|---|
| Says the file already exists and `ghl` did not write it | The older skill above. Plain yes, then `ghl skill install --force`. |
| `Could not find the ghl script next to this Python` | The install is broken. Run Phase 1, step 1c again, then `ghl skill install`. |
| The gate prints no path | The file isn't the generated one. Plain yes, then `ghl skill install --force`. |
| The path in the skill doesn't run | The folder moved or the install broke. Run Phase 1, step 1c again, then `ghl skill install`. |

---

## Done. Confirm all of it

Before you tell them it's finished, confirm every one of these:

1. The code is in `~/ghl-cli` (`%USERPROFILE%\ghl-cli`), and `ghl --version` matches its VERSION.
2. Keys are in `~/.ghl-cli/keys.env` and settings in `~/.ghl-cli/config.json`. (You never opened
   either.)
3. `ghl doctor` is all OK. If they skipped Phase 3, `ghl doctor --only api-key` is OK instead.
4. The live test said `PASS`, said `PASS, firing not tested`, or was skipped by their choice.
5. The skill is written and its `ghl` path runs.

Then tell them, in one short friendly paragraph:

- What's installed: the code in `ghl-cli` in their home folder, their keys and settings in
  `.ghl-cli` (updates never touch those), and the skill.
- What can expire: the workflow login token ends now and then (a password change does it, for
  example), and the GHL key stops if someone deletes or changes the Private Integration.
- How to fix and update: just say "fix my GHL keys" or "update the GHL CLI" in any chat.
- How to remove it: the README's "Removing it" section.
- If firing wasn't tested, or Phase 3 or 4 was skipped, say so again plainly.
- If Phase 0 found an older copy, it's still where it was. They can delete it now. If an older
  `ghl` command was on the PATH, that's the old one; this install never uses it.
- To start: open a **new** chat and ask for something like "show me my last 5 contacts", "list my
  workflows" or "build me a draft workflow that tags new leads and sends a welcome email".
  Reads just happen. Anything that writes gets shown first and needs their yes. Workflows are
  always built as drafts, and they click Publish in GoHighLevel themselves.

## When it breaks later

| They say or see | What to do |
|---|---|
| "fix my GHL keys", any 401 or 403, "unauthorized" | Run `ghl doctor`. It names the key that failed and why. Redo only that phase (Phase 2 for the GHL key, Phase 3 for the workflow token or location reach), then run `ghl doctor` again. |
| Workflow commands fail with an auth error | The login token ended. Phase 3, then `ghl doctor`. |
| "update the GHL CLI" | Run `ghl update` and relay its lines. Then `ghl skill install` so the skill matches. `ghl update --undo` puts the previous version back. |
| `GHL changed something on its side` | Run `ghl update`. If it's already the latest, the fix comes in a later release. Only the workflow commands use the unofficial API, so everything else keeps working. |
| `GHL is having trouble right now` | GHL's servers, not their setup. Try again in a few minutes. If it lasts, run `ghl update`. |
| `ghl` not found, or the folder moved | Phase 1, step 1c (safe to rerun), then `ghl skill install`. |
| A selftest was interrupted | `ghl selftest --cleanup RUN` with the run id it printed. |
| They want it gone | Below. |

**Removing it.** Deleting can't be undone and takes their saved keys with it, so get a plain yes
first. Say plainly what goes: `~/.ghl-cli` holds their keys and settings and also the snapshots
that `workflows edit` saved for its undo lines, so those undo commands stop working. The skill
folder may also hold a `SKILL.md.bak` (an older hand-copied skill that `ghl skill install --force`
set aside); it goes too. Then:

Mac:

```bash
rm -rf ~/ghl-cli ~/.ghl-cli ~/.claude/skills/ghl-cli
```

Windows:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\ghl-cli", "$env:USERPROFILE\.ghl-cli", "$env:USERPROFILE\.claude\skills\ghl-cli"
```

Then the member does two things in GoHighLevel: delete the `ghl-cli` Private Integration (Settings,
Private Integrations, VERIFY IN BUILD (U5)), and change their GoHighLevel password, which ends the
login token setup copied (VERIFY IN BUILD (U4)).
