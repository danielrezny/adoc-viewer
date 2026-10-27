"""Putting text on the system clipboard.

Uses the platform's copy command when there is one; otherwise returns an
OSC 52 sequence for the caller to write, which asks the terminal itself to
set the clipboard (works over ssh in terminals that allow it).
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from typing import List, Optional, Tuple

COMMANDS: List[List[str]] = [
    ["pbcopy"],
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["clip.exe"],
]


def osc52(text: str) -> str:
    return "\x1b]52;c;" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "\x07"


def copy(text: str) -> Tuple[bool, Optional[str]]:
    """Copy with a clipboard command.

    Returns (True, None) on success, or (False, osc52_sequence) when no command
    worked and the terminal should be asked instead.
    """
    for cmd in COMMANDS:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, input=text.encode("utf-8"), timeout=3,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return True, None
    return False, osc52(text)
