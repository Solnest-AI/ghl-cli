"""Read and clear the system clipboard so a pasted key never passes through chat.

macOS: pbpaste/pbcopy. Windows: PowerShell Get-Clipboard / Set-Clipboard.
Anything else raises ClipboardUnavailable (callers fall back to --from-stdin).
Never log or return anything but the clipboard text itself.
"""
from __future__ import annotations

import subprocess
import sys


class ClipboardUnavailable(RuntimeError):
    pass


def _run(cmd: list, input_bytes: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClipboardUnavailable("The clipboard could not be read on this computer.") from exc
    if proc.returncode != 0:
        raise ClipboardUnavailable("The clipboard could not be read on this computer.")
    return proc.stdout


def read_clipboard() -> str:
    if sys.platform == "darwin":
        out = _run(["pbpaste"])
    elif sys.platform == "win32":
        out = _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])
    else:
        raise ClipboardUnavailable("Clipboard access is not supported on this system.")
    return out.decode("utf-8", errors="replace")


def clear_clipboard() -> None:
    if sys.platform == "darwin":
        _run(["pbcopy"], input_bytes=b"")
    elif sys.platform == "win32":
        _run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $null"])
    else:
        raise ClipboardUnavailable("Clipboard access is not supported on this system.")
