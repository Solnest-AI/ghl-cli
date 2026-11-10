# GHL CLI - Windows setup. Claude Code runs it for you (SETUP.md, Phase 1):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -NoPrompt -AutoInstall
# or double-click setup.cmd. Safe to rerun: keeps .venv, and never touches your keys or
# settings (%USERPROFILE%\.ghl-cli).
# Exit codes: 0 ready, 1 stopped (see message).
#
# Ported from the Listing Optimizer's setup.ps1. Written for Windows but not yet run on a
# Windows PC (SETUP.md says so, U6).
#   * Python comes from uv, never winget and never the Microsoft Store shim. The Claude Code
#     desktop app is a Store (MSIX) app that silently redirects AppData writes, so uv's Python
#     lives under %USERPROFILE%\.uv\python, the same place the STR Secrets kit puts it.
#   * This sets up the code only: your keys (SETUP.md Phases 2 and 3) and the Claude Code skill
#     (Phase 5) are separate steps Claude runs with you.
# Keep this file ASCII-only: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

param(
    [switch]$NoPrompt,       # never ask questions (Claude Code / scripted runs)
    [switch]$AutoInstall     # install missing uv and Python without asking
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$GhlExe = Join-Path $Root '.venv\Scripts\ghl.exe'
$PyVersion = '3.12'

function Step($n, $text) { Write-Host ""; Write-Host "[$n/3] $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""; Write-Host "  SETUP STOPPED: $text" -ForegroundColor Red
    Write-Host ""; exit 1
}
function Ask($question) {
    if ($AutoInstall) { return $true }
    if ($NoPrompt) { return $false }
    return (Read-Host "$question [Y/n]") -notmatch '^[nN]'
}

# uv: on PATH, or where its installer and winget put it (the app's PATH is fixed at launch,
# so a uv installed earlier today may be on disk but not on PATH yet).
function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("$HOME\.local\bin\uv.exe", "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
# The Python 3.12 uv can run right now, without downloading anything. Tries the member's
# configured location first, then the kit's profile location, then uv's default. Leaves
# UV_PYTHON_INSTALL_DIR exactly as it found it.
function Find-UvPython($uv) {
    $orig = $env:UV_PYTHON_INSTALL_DIR
    $homes = @()
    if ($orig) { $homes += $orig }
    $homes += "$env:USERPROFILE\.uv\python"
    $homes += ''   # uv's own default
    $found = $null
    foreach ($h in $homes) {
        $env:UV_PYTHON_INSTALL_DIR = $h
        $env:UV_PYTHON_DOWNLOADS = 'never'
        # --no-project --system: the real interpreter (uv-managed or not), never this folder's
        # .venv. No Select-Object in this pipeline: it ends the process early and corrupts
        # $LASTEXITCODE.
        try { $p = @(& $uv python find --no-project --system $PyVersion 2>$null) } catch { $p = @() }
        $rc = $LASTEXITCODE
        $env:UV_PYTHON_DOWNLOADS = $null
        if ($rc -eq 0 -and $p.Count -gt 0 -and (Test-Path "$($p[0])".Trim())) { $found = "$($p[0])".Trim(); break }
    }
    $env:UV_PYTHON_INSTALL_DIR = $orig
    return $found
}
# Does this venv's python actually run, and is it new enough? A .venv copied from another
# machine still has python.exe but points at an interpreter that is not there, and an older
# install may have left a Python 3.9 one.
function Test-Venv($py) {
    if (-not (Test-Path $py)) { return $false }
    try { & $py -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

Write-Host ""
Write-Host "  GHL CLI setup" -ForegroundColor White
Write-Host "  Folder: $Root"
if (-not ((Test-Path 'VERSION') -and (Test-Path 'setup.py'))) {
    Fail 'This is not the GHL CLI folder (no VERSION or setup.py here). Run setup from the ghl-cli folder.'
}

# ---------------------------------------------------------------- 1. uv + Python
Step 1 "Python $PyVersion through uv (never the Microsoft Store)"
$uv = Find-Uv
if (-not $uv -and (Ask '  Install uv now (Astral installer, no admin prompt)?')) {
    Write-Host '  Installing uv into your user folder...'
    try {
        $env:UV_NO_MODIFY_PATH = $null
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    } catch { Warn "uv installer failed: $($_.Exception.Message)" }
    $uv = Find-Uv
}
if (-not $uv) {
    Fail ('uv could not be installed. Check your internet connection and run setup again, or install it ' +
          'yourself from https://docs.astral.sh/uv/getting-started/installation/ and rerun.')
}
Ok ("uv " + ((& $uv --version 2>$null) -replace '^uv ', ''))
$PyExe = Find-UvPython $uv
if (-not $PyExe -and (Ask "  Install Python $PyVersion through uv now?")) {
    $pyHome = if ($env:UV_PYTHON_INSTALL_DIR) { $env:UV_PYTHON_INSTALL_DIR } else { "$env:USERPROFILE\.uv\python" }
    Write-Host "  Installing Python $PyVersion under $pyHome ..."
    $env:UV_PYTHON_INSTALL_DIR = $pyHome
    & $uv python install $PyVersion | Out-Null
    if (-not $env:GHL_NO_PERSIST) { setx UV_PYTHON_INSTALL_DIR $pyHome | Out-Null }   # same pin as the kit
    $PyExe = Find-UvPython $uv
}
if (-not $PyExe) {
    Fail "uv could not provide Python $PyVersion. Check your internet connection and run setup again."
}
Ok "Python $PyVersion (uv): $PyExe"

# ---------------------------------------------------------------- 2. venv + packages
Step 2 'Python environment and packages (first time takes a minute)'
if ((Test-Path '.venv') -and -not (Test-Venv $VenvPy)) {
    Warn '.venv does not run here (copied from another machine or a Mac, or too old). Rebuilding it.'
    Remove-Item -Recurse -Force '.venv'
}
if (-not (Test-Path $VenvPy)) {
    & $uv venv .venv --python $PyExe --quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Venv $VenvPy)) { Fail 'Could not create .venv.' }
}
# Editable on purpose: the version is read from VERSION at import, and `ghl update` swaps files
# in place, so the installed command always runs the code in this folder.
& $uv pip install --python $VenvPy --quiet -e .
if ($LASTEXITCODE -ne 0) { Fail 'Package install failed. Check your internet connection and run setup again.' }
Ok "Packages installed in .venv (Python $(& $VenvPy -c 'import sys; print(sys.version.split()[0])'))"
# A git clone stays linked to Solnest AI's GitHub, and "save" then tries to push there. Unlink an
# untouched copy; a working copy is kept as is. Always exits 0.
& $VenvPy scripts\detach_from_github.py | ForEach-Object { Ok ($_ -replace '^\[setup\] ', '') }

# ---------------------------------------------------------------- 3. the ghl command
Step 3 'Checking the ghl command'
$want = (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim()
if (-not (Test-Path $GhlExe)) { Fail 'The ghl command was not installed (.venv\Scripts\ghl.exe is missing). Run setup again.' }
$got = ((& $GhlExe --version) | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "The ghl command did not start. Run setup again. ($got)" }
if (-not $got.EndsWith("version $want")) { Fail "ghl reports `"$got`" but this folder is version $want. Run setup again." }
Ok $got

# ---------------------------------------------------------------- done
Write-Host ""
Write-Host '  ALL SET. The ghl command is:' -ForegroundColor Green
Write-Host "    $GhlExe"
Write-Host '  Next, back in Claude Code: SETUP.md, Phase 2 (your GHL key).'
Write-Host ""
exit 0
