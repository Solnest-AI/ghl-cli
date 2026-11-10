#!/usr/bin/env bash
# GHL CLI - Mac/Linux setup. Claude Code runs it for you (SETUP.md, Phase 1):
#   bash setup.sh --no-prompt --auto-install
# Safe to rerun: keeps .venv, and never touches your keys or settings (~/.ghl-cli).
# Exit codes: 0 ready, 1 stopped (see message).
#
# Ported from the Listing Optimizer's setup. Python comes from uv, so a Mac that only has the
# system Python 3.9 still works. This sets up the code only: your keys (SETUP.md Phases 2 and 3)
# and the Claude Code skill (Phase 5) are separate steps Claude runs with you.
set -u
cd "$(dirname "$0")" || exit 1

# Windows (Git Bash is Claude Code's Bash tool there): this script would look for a Mac-style
# .venv/bin, decide the Windows .venv is broken and delete it. Hand off to setup.ps1 instead,
# translating the flags.
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*)
  args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-prompt) args+=(-NoPrompt) ;; --auto-install) args+=(-AutoInstall) ;;
    esac
    shift
  done
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$PWD/setup.ps1")" ${args[@]+"${args[@]}"}
esac

NO_PROMPT=0; AUTO_INSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-prompt) NO_PROMPT=1 ;;
    --auto-install) AUTO_INSTALL=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: bash setup.sh [--no-prompt] [--auto-install]

Builds the GHL CLI in this folder: uv, Python 3.12, a .venv, and the ghl command.
  --auto-install  install uv and Python 3.12 without asking if they are missing
  --no-prompt     never ask a question (anything missing that needs a yes stops setup)
Safe to rerun. Never touches your keys or settings in ~/.ghl-cli.
EOF
      exit 0 ;;
    *) echo "unknown option: $1 (see: bash setup.sh --help)"; exit 1 ;;
  esac
  shift
done
PYV=3.12
step() { printf '\n[%s/3] %s\n' "$1" "$2"; }
ok()   { printf '  OK  %s\n' "$1"; }
warn() { printf '  !!  %s\n' "$1"; }
fail() { printf '\n  SETUP STOPPED: %s\n\n' "$1"; exit 1; }
ask()  { # yes/no; --auto-install answers yes, --no-prompt answers no
  [ "$AUTO_INSTALL" = 1 ] && return 0
  [ "$NO_PROMPT" = 1 ] && return 1
  read -r -p "$1 [Y/n] " r; [[ ! "$r" =~ ^[nN] ]]
}
find_uv() { for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv"; do [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }; done; return 1; }
# --no-project --system: the real interpreter (uv-managed or not), never this folder's .venv.
find_uv_python() { UV_PYTHON_DOWNLOADS=never "$1" python find --no-project --system "$PYV" 2>/dev/null; }
# The .venv runs here and is new enough (an older install may have left a Python 3.9 .venv).
venv_ok() { [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; }

printf '\n  GHL CLI setup\n  Folder: %s\n' "$PWD"
[ -f VERSION ] && [ -f setup.py ] || fail 'This is not the GHL CLI folder (no VERSION or setup.py here). Run setup from the ghl-cli folder.'

# ---------------------------------------------------------------- 1. uv + Python
step 1 "Python $PYV through uv"
UV=$(find_uv || true)
if [ -z "$UV" ] && ask '  Install uv now (Astral installer, no admin prompt)?'; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  UV=$(find_uv || true)
fi
[ -n "$UV" ] || fail 'uv could not be installed. Check your internet connection and run setup again, or install it yourself from https://docs.astral.sh/uv/getting-started/installation/ and rerun.'
ok "uv $("$UV" --version 2>/dev/null | awk '{print $2}')"
PYEXE=$(find_uv_python "$UV" || true)
if [ -z "$PYEXE" ] && ask "  Install Python $PYV through uv now?"; then
  "$UV" python install "$PYV" >/dev/null 2>&1
  PYEXE=$(find_uv_python "$UV" || true)
fi
[ -n "$PYEXE" ] || fail "uv could not provide Python $PYV. Check your internet connection and run setup again."
ok "Python $PYV (uv): $PYEXE"

# ---------------------------------------------------------------- 2. venv + packages
step 2 'Python environment and packages (first time takes a minute)'
if [ -d .venv ] && ! venv_ok; then
  warn '.venv does not run here (copied from another machine, or too old). Rebuilding it.'
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  "$UV" venv .venv --python "$PYEXE" --quiet && venv_ok || fail 'Could not create .venv.'
fi
# Editable on purpose: the version is read from VERSION at import, and `ghl update` swaps files
# in place, so the installed command always runs the code in this folder.
"$UV" pip install --python .venv/bin/python --quiet -e . \
  || fail 'Package install failed. Check your internet connection and run setup again.'
ok "Packages installed in .venv (Python $(.venv/bin/python -c 'import sys; print(sys.version.split()[0])'))"
# A git clone stays linked to Solnest AI's GitHub, and "save" then tries to push there. Unlink an
# untouched copy; a working copy is kept as is. Always exits 0.
.venv/bin/python scripts/detach_from_github.py | sed 's/^\[setup\] /  OK  /'

# ---------------------------------------------------------------- 3. the ghl command
step 3 'Checking the ghl command'
WANT=$(tr -d '[:space:]' < VERSION)
GOT=$(.venv/bin/ghl --version 2>&1) || fail "The ghl command did not start. Run setup again. ($GOT)"
case "$GOT" in
  *"version $WANT") ok "$GOT" ;;
  *) fail "ghl reports \"$GOT\" but this folder is version $WANT. Run setup again." ;;
esac

# ---------------------------------------------------------------- done
printf '\n  ALL SET. The ghl command is:\n    %s/.venv/bin/ghl\n' "$PWD"
printf '  Next, back in Claude Code: SETUP.md, Phase 2 (your GHL key).\n\n'
exit 0
