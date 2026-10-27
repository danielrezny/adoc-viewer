"""Changing the terminal's font size.

A program can't set the font size through escape sequences in most terminals,
so this talks to the terminal application itself where it can.  Currently
that is Apple Terminal, through AppleScript, for the tab that owns our tty
only.  The window keeps its size on screen, so the text reflows (SIGWINCH).

Anywhere else ``detect()`` returns None and the viewer explains the
terminal's own zoom shortcut instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

SIZES = [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 42, 48, 60, 72]

_APPLE_SCRIPT = """
on run argv
    set ttyPath to item 1 of argv
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is ttyPath then
                    if (count of argv) > 1 then
                        set keep to bounds of w
                        set font size of current settings of t to (item 2 of argv as number)
                        set bounds of w to keep
                    end if
                    return font size of current settings of t
                end if
            end repeat
        end repeat
    end tell
    error "tab not found"
end run
"""


def step(size: float, direction: int) -> float:
    """The next size up (direction > 0) or down from ``size``."""
    if direction > 0:
        return next((s for s in SIZES if s > size), size)
    return next((s for s in reversed(SIZES) if s < size), size)


class FontSizer:
    """Tracks the original size so it can be restored."""

    def __init__(self) -> None:
        self.original: Optional[float] = None

    def get(self) -> float:
        raise NotImplementedError

    def set(self, size: float) -> float:
        raise NotImplementedError

    def change(self, direction: int) -> float:
        """Zoom in (+1), out (-1) or back to the original size (0)."""
        current = self.get()
        if self.original is None:
            self.original = current
        if direction == 0:
            return self.set(self.original)
        return self.set(step(current, direction))

    def apply(self, size: float) -> float:
        if self.original is None:
            self.original = self.get()
        return self.set(size)

    def restore(self) -> None:
        if self.original is not None:
            try:
                self.set(self.original)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass


class NotOurTerminal(RuntimeError):
    """The terminal app doesn't own our tty (tmux, ssh, a pty...)."""


class AppleTerminal(FontSizer):
    def __init__(self, tty: str) -> None:
        super().__init__()
        self.tty = tty

    def _run(self, *args: str) -> float:
        proc = subprocess.run(["osascript", "-", self.tty, *args], input=_APPLE_SCRIPT,
                              capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            if "tab not found" in proc.stderr:
                raise NotOurTerminal(self.tty)
            raise RuntimeError(proc.stderr.strip() or "osascript failed")
        return float(proc.stdout.strip().replace(",", "."))

    def get(self) -> float:
        return self._run()

    def set(self, size: float) -> float:
        return self._run(f"{size:g}")


def detect(fd: int) -> Optional[FontSizer]:
    try:
        tty = os.ttyname(fd)
    except OSError:
        return None
    program = os.environ.get("TERM_PROGRAM", "")
    if program == "Apple_Terminal" and shutil.which("osascript") and not os.environ.get("TMUX"):
        return AppleTerminal(tty)
    return None


def manual_hint() -> str:
    """How to zoom by hand when we can't do it."""
    if sys.platform == "darwin":
        return "Zoom with ⌘+ / ⌘− in this terminal"
    return "Zoom with Ctrl+Shift+= / Ctrl+- in this terminal"
