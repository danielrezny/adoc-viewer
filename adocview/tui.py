"""Full-screen terminal viewer.

Raw ANSI on the alternate screen, no curses: the renderer already produces
styled lines, so drawing is just positioning them.  The main loop waits on
stdin, a SIGWINCH self-pipe and a short timeout used for live reload.
"""

from __future__ import annotations

import bisect
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import termios
import time
from typing import List, Optional, Tuple

from . import clipboard, fontsize, theme
from .inline import plain_text
from .loader import Source
from .render import Renderer
from .style import PLAIN, Line, Seg, Style, dw, overlay, pad, segs_width, to_ansi, truncate

POLL_INTERVAL = 0.4
MESSAGE_SECONDS = 2.5

HELP = [
    ("Scrolling", ""),
    ("j k  ↓ ↑", "line down / up"),
    ("space f  b", "page down / up"),
    ("d u", "half page"),
    ("g G", "top / bottom"),
    ("Navigation", ""),
    ("] [", "next / prev section"),
    ("t  Tab", "contents, focus it"),
    ("w", "full width / column"),
    ("Search", ""),
    ("/  ^F", "search box"),
    ("n N", "next / prev match"),
    ("Esc", "clear search"),
    ("Copying", ""),
    ("DRAG", "select text"),
    ("v", "select lines, j k"),
    ("y  Enter", "copy selection"),
    ("m", "mouse capture on/off"),
    ("Other", ""),
    ("+ -  0", "font size, reset"),
    ("e", "open in $EDITOR"),
    ("r", "reload"),
    ("q", "quit"),
]

CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
            "Z": "backtab"}
LISTING_FRAME_RX = re.compile(r"^\s*(?:┌─(?: [^─┬┐]*)?|└─)\s*$")
NARROW_WIDTH = 100

TILDE_KEYS = {"1": "home", "7": "home", "4": "end", "8": "end", "5": "pgup", "6": "pgdn",
              "3": "delete"}


def parse_keys(data: str) -> List[object]:
    keys: List[object] = []
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == "\x1b":
            if i + 1 < n and data[i + 1] == "[":
                j = i + 2
                while j < n and not ("\x40" <= data[j] <= "\x7e"):
                    j += 1
                seq = data[i + 2:j + 1]
                i = j + 1
                if seq.startswith("<") and seq[-1:] in ("M", "m"):
                    try:
                        b, x, y = (int(v) for v in seq[1:-1].split(";"))
                        keys.append(("mouse", b, x, y, seq[-1] == "M"))
                    except ValueError:
                        pass
                elif seq.endswith("~"):
                    keys.append(TILDE_KEYS.get(seq[:-1].split(";")[0], None))
                elif seq:
                    keys.append(CSI_KEYS.get(seq[-1], None))
            elif i + 2 < n and data[i + 1] == "O":
                keys.append(CSI_KEYS.get(data[i + 2], None))
                i += 3
            else:
                keys.append("esc")
                i += 1
        elif c in "\r\n":
            keys.append("enter")
            i += 1
        elif c in "\x7f\x08":
            keys.append("backspace")
            i += 1
        elif c == "\t":
            keys.append("tab")
            i += 1
        else:
            keys.append(c)
            i += 1
    return [k for k in keys if k is not None]


def drag_hint() -> str:
    """The modifier that makes a drag select text while the mouse is captured."""
    program = os.environ.get("TERM_PROGRAM", "")
    if program == "Apple_Terminal":
        return "fn+drag"
    if program in ("iTerm.app", "WezTerm"):
        return "⌥+drag"
    return "shift+drag"


def selection_text(texts: List[str]) -> str:
    """Plain text for copied lines: no code-block frames, gutters or trailing blanks."""
    texts = [t.rstrip() for t in texts if not LISTING_FRAME_RX.match(t)]
    body = [t for t in texts if t.strip(" │┃")]  # a lone gutter bar counts as blank
    if body:
        prefix = 0
        # Strip the indentation and quote/code/admonition bars all lines share.
        while all(len(t) > prefix and t[prefix] in " │┃" for t in body) and \
                len({t[prefix] for t in body}) == 1:
            prefix += 1
        texts = [t[prefix:] if t.strip(" │┃") else "" for t in texts]
    while texts and not texts[0].strip():
        texts.pop(0)
    while texts and not texts[-1].strip():
        texts.pop()
    return "\n".join(texts) + ("\n" if len(texts) > 1 else "")


def rstrip_segs(segs: List[Seg]) -> List[Seg]:
    """Drop trailing blanks, so text copied from the screen has no padding."""
    segs = list(segs)
    while segs:
        text, st = segs[-1]
        if st.bg is not None or st.reverse or st.underline or st.strike:
            break
        stripped = text.rstrip(" ")
        if stripped:
            segs[-1] = (stripped, st)
            break
        segs.pop()
    return segs


class Viewer:
    def __init__(self, source: Source, max_width: int = 0, mouse: bool = True,
                 font_size: Optional[float] = None):
        self.source = source
        self.full_width = max_width <= 0
        self.narrow_width = max_width if max_width > 0 else NARROW_WIDTH
        self.sel_anchor: Optional[int] = None
        self.sel_cursor = 0
        self.font_size = font_size
        self.sizer: Optional[fontsize.FontSizer] = None
        self.mouse = mouse

        self.doc = None
        self.lines: List[Line] = []
        self.toc: List[Tuple[object, int]] = []
        self.anchors = {}
        self.rendered_width: Optional[int] = None
        self.top = 0

        self.cols = 80
        self.rows = 24
        self.view_h = 23
        self.doc_w = 80
        self.margin = 0
        self.toc_w = 0

        self.toc_open = False
        self.toc_focus = False
        self.toc_sel = 0
        self.toc_top = 0
        self.show_help = False

        self.search_input: Optional[str] = None  # text in the open search box
        self.search_fresh = False  # first keystroke replaces the remembered query
        self.search_origin = 0
        self.query: Optional[str] = None
        self.matches: List[Tuple[int, int, int]] = []
        self.match_idx = -1

        self.message: Optional[str] = None
        self.message_until = 0.0
        self.mtimes = {}
        self.dirty = True

        self.in_fd = -1
        self.out_fd = sys.stdout.fileno()
        self.saved_tty = None
        self.wake_r = self.wake_w = -1

    # -- lifecycle ----------------------------------------------------------------

    def run(self) -> None:
        self.load(initial=True)
        if sys.stdin.isatty():
            self.in_fd = sys.stdin.fileno()
        else:
            self.in_fd = os.open("/dev/tty", os.O_RDONLY)
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        old_winch = signal.signal(signal.SIGWINCH, lambda *_: self._wake())
        old_term = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        self.sizer = fontsize.detect(self.out_fd)
        self.enter_screen()
        try:
            if self.font_size:
                self.zoom(None, self.font_size)
            self.loop()
        finally:
            self.leave_screen()
            if self.sizer:
                self.sizer.restore()
            signal.signal(signal.SIGWINCH, old_winch)
            signal.signal(signal.SIGTERM, old_term)

    def _wake(self) -> None:
        try:
            os.write(self.wake_w, b"w")
        except OSError:
            pass

    def enter_screen(self) -> None:
        self.saved_tty = termios.tcgetattr(self.in_fd)
        mode = termios.tcgetattr(self.in_fd)
        mode[0] &= ~(termios.IXON | termios.ICRNL)
        mode[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(self.in_fd, termios.TCSAFLUSH, mode)
        self.write("\x1b[?1049h\x1b[?25l\x1b[?7l" + self._mouse_seq(self.mouse))
        self.dirty = True

    def leave_screen(self) -> None:
        self.write(self._mouse_seq(False) + "\x1b[?1007l\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
        if self.saved_tty is not None:
            termios.tcsetattr(self.in_fd, termios.TCSAFLUSH, self.saved_tty)

    @staticmethod
    def _mouse_seq(on: bool) -> str:
        # Without mouse reporting the terminal selects text on drag; ?1007 asks it to
        # turn the wheel into arrow keys meanwhile, so scrolling still works.
        return "\x1b[?1007l\x1b[?1000h\x1b[?1006h" if on else "\x1b[?1000l\x1b[?1006l\x1b[?1007h"

    def write(self, data: str) -> None:
        buf = data.encode("utf-8", "replace")
        while buf:
            try:
                written = os.write(self.out_fd, buf)
            except InterruptedError:
                continue
            buf = buf[written:]

    def loop(self) -> None:
        last_poll = time.monotonic()
        self.layout()
        while True:
            if self.message and time.monotonic() > self.message_until:
                self.message = None
                self.dirty = True
            if self.dirty:
                self.draw()
                self.dirty = False
            ready, _, _ = select.select([self.in_fd, self.wake_r], [], [], POLL_INTERVAL)
            if self.wake_r in ready:
                os.read(self.wake_r, 1024)
                self.layout()
            if self.in_fd in ready:
                data = os.read(self.in_fd, 4096).decode("utf-8", "replace")
                if not data:
                    return
                for key in parse_keys(data):
                    if self.handle(key) is False:
                        return
            now = time.monotonic()
            if now - last_poll >= POLL_INTERVAL:
                last_poll = now
                if self.source.path and self.source.mtimes() != self.mtimes:
                    self.load()
                    self.flash("Reloaded")

    # -- document -----------------------------------------------------------------

    def load(self, initial: bool = False) -> None:
        try:
            doc = self.source.load()
        except Exception as exc:  # keep showing the last good render
            if initial:
                raise
            self.mtimes = self.source.mtimes()
            self.flash(f"Reload failed: {exc}")
            return
        self.doc = doc
        self.mtimes = self.source.mtimes()
        self.rendered_width = None
        if not initial:
            self.layout()

    def layout(self) -> None:
        size = shutil.get_terminal_size()
        self.cols, self.rows = max(size.columns, 10), max(size.lines, 3)
        self.view_h = self.rows - 1
        if self.toc_open:
            tw = min(40, max(24, self.cols // 4))
            self.toc_w = self.cols if self.cols - tw < 40 else tw
        else:
            self.toc_w = 0
        avail = self.cols - self.toc_w - (1 if self.toc_w else 0)
        if avail >= 20:
            usable = avail - 4 if avail > 44 else avail - 2
            self.doc_w = usable if self.full_width else min(self.narrow_width, usable)
            self.margin = self.toc_w + (1 if self.toc_w else 0) + (avail - self.doc_w) // 2
        if self.doc_w != self.rendered_width:
            self.rerender()
        self.clamp()
        self.dirty = True

    def rerender(self) -> None:
        old_len = len(self.lines)
        r = Renderer(self.doc, self.doc_w)
        self.lines = r.render()
        self.toc = r.toc
        self.anchors = r.anchors
        if self.rendered_width is not None and old_len:
            self.top = round(self.top * len(self.lines) / old_len)
        self.rendered_width = self.doc_w
        if self.query:
            self.find_matches()

    @property
    def max_top(self) -> int:
        return max(0, len(self.lines) - self.view_h)

    def clamp(self) -> None:
        self.top = max(0, min(self.top, self.max_top))

    def scroll(self, delta: int) -> None:
        self.top += delta
        self.clamp()
        self.dirty = True

    def flash(self, message: str) -> None:
        self.message = message
        self.message_until = time.monotonic() + MESSAGE_SECONDS
        self.dirty = True

    def current_section(self) -> int:
        positions = [line for _, line in self.toc]
        return bisect.bisect_right(positions, self.top + 1) - 1

    def jump_to_line(self, line: int) -> None:
        self.top = line
        self.clamp()
        self.dirty = True

    # -- search -------------------------------------------------------------------

    def find_matches(self) -> None:
        self.matches = []
        if not self.query:
            return
        flags = 0 if any(c.isupper() for c in self.query) else re.IGNORECASE
        rx = re.compile(re.escape(self.query), flags)
        for idx, line in enumerate(self.lines):
            for m in rx.finditer(line.text):
                self.matches.append((idx, m.start(), m.end()))

    def goto_match(self, forward: bool = True, include_current: bool = False,
                   start: Optional[int] = None) -> None:
        boxed = self.search_input is not None  # the box shows the count itself
        if not self.matches:
            if not boxed:
                self.flash(f"Pattern not found: {self.query}")
            return
        if self.match_idx < 0 or include_current:
            starts = [m[0] for m in self.matches]
            k = bisect.bisect_left(starts, self.top if start is None else start)
            self.match_idx = k % len(self.matches) if forward else (k - 1) % len(self.matches)
        else:
            self.match_idx = (self.match_idx + (1 if forward else -1)) % len(self.matches)
        line = self.matches[self.match_idx][0]
        hidden = 4 if boxed else 0  # rows under the search box
        if not self.top + hidden <= line < self.top + self.view_h:
            self.top = line - max(self.view_h // 3, hidden)
            self.clamp()

    # -- input --------------------------------------------------------------------

    def handle(self, key) -> Optional[bool]:
        self.dirty = True
        if isinstance(key, tuple):
            self.handle_mouse(*key[1:])
            return None
        if key == "\x03":
            return False
        if key == "\x1a":
            self.suspend()
            return None
        if key == "\x0c":
            self.layout()
            return None
        if self.show_help:
            self.show_help = False
            return None
        if self.search_input is not None:
            self.handle_search_key(key)
            return None
        if self.sel_anchor is not None:
            self.handle_select_key(key)
            return None
        if self.toc_focus and self.handle_toc_key(key):
            return None

        page = max(self.view_h - 2, 1)
        if key in ("q", "Q"):
            return False
        elif key in ("j", "down", "enter", "\x05", "\x0e"):
            self.scroll(1)
        elif key in ("k", "up", "\x19", "\x10"):
            self.scroll(-1)
        elif key == "\x06":
            self.open_search()
        elif key in (" ", "f", "pgdn"):
            self.scroll(page)
        elif key in ("b", "pgup", "\x02"):
            self.scroll(-page)
        elif key in ("d", "\x04"):
            self.scroll(self.view_h // 2)
        elif key in ("u", "\x15"):
            self.scroll(-(self.view_h // 2))
        elif key in ("g", "home", "<"):
            self.jump_to_line(0)
        elif key in ("G", "end", ">"):
            self.jump_to_line(self.max_top)
        elif key in ("]", "}"):
            positions = [line for _, line in self.toc]
            k = bisect.bisect_right(positions, self.top)
            if k < len(positions):
                self.jump_to_line(positions[k])
        elif key in ("[", "{"):
            positions = [line for _, line in self.toc]
            k = bisect.bisect_left(positions, self.top) - 1
            if k >= 0:
                self.jump_to_line(positions[k])
            else:
                self.jump_to_line(0)
        elif key == "t":
            self.toggle_toc()
        elif key in ("tab", "backtab") and self.toc_open:
            self.toc_focus = not self.toc_focus
            self.toc_sel = max(self.current_section(), 0)
        elif key == "/":
            self.open_search()
        elif key == "n" and self.query:
            self.goto_match(True)
        elif key == "N" and self.query:
            self.goto_match(False)
        elif key == "esc":
            self.query, self.matches, self.match_idx = None, [], -1
        elif key == "r":
            self.load()
            self.flash("Reloaded")
        elif key == "e":
            self.edit()
        elif key == "m":
            self.mouse = not self.mouse
            self.write(self._mouse_seq(self.mouse))
            self.flash(f"Mouse on: wheel scrolls, {drag_hint()} selects" if self.mouse
                       else "Mouse off: drag selects, wheel may not scroll")
        elif key == "v":
            if self.lines:
                # start at the current search match when it is on screen
                start = self.top
                if 0 <= self.match_idx < len(self.matches):
                    line = self.matches[self.match_idx][0]
                    if self.top <= line < self.top + self.view_h:
                        start = line
                self.sel_cursor = min(start, len(self.lines) - 1)
                self.sel_anchor = self.sel_cursor
        elif key == "w":
            self.full_width = not self.full_width
            self.layout()
            self.flash("Full width" if self.full_width else f"Column of {self.narrow_width}")
        elif key in ("+", "="):
            self.zoom(1)
        elif key in ("-", "_"):
            self.zoom(-1)
        elif key == "0":
            self.zoom(0)
        elif key == "?":
            self.show_help = True
        return None

    def zoom(self, direction: Optional[int], size: Optional[float] = None) -> None:
        """Change the terminal's font size; the resize that follows reflows the text."""
        if not self.sizer:
            self.flash(fontsize.manual_hint())
            return
        try:
            if size is not None:
                new = self.sizer.apply(size)
            else:
                new = self.sizer.change(direction or 0)
        except fontsize.NotOurTerminal:
            self.sizer = None
            self.flash(fontsize.manual_hint())
            return
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.flash(f"Font size: {exc}")
            return
        self.flash(f"Font size {new:g}pt")
        self.layout()

    def handle_select_key(self, key) -> None:
        page = max(self.view_h - 2, 1)
        moves = {"j": 1, "down": 1, "k": -1, "up": -1, " ": page, "f": page,
                 "pgdn": page, "b": -page, "pgup": -page, "d": self.view_h // 2,
                 "u": -(self.view_h // 2)}
        last = len(self.lines) - 1
        if key in moves:
            self.sel_cursor = min(max(self.sel_cursor + moves[key], 0), last)
        elif key in ("g", "home"):
            self.sel_cursor = 0
        elif key in ("G", "end"):
            self.sel_cursor = last
        elif key in ("y", "Y", "enter", "c"):
            self.copy_selection()
            return
        elif key in ("esc", "v", "q"):
            self.sel_anchor = None
            return
        # keep the moving end on screen
        if self.sel_cursor < self.top:
            self.top = self.sel_cursor
        elif self.sel_cursor >= self.top + self.view_h:
            self.top = self.sel_cursor - self.view_h + 1
        self.clamp()

    def selection(self) -> Tuple[int, int]:
        a, b = sorted((self.sel_anchor or 0, self.sel_cursor))
        return a, b

    def copy_selection(self) -> None:
        a, b = self.selection()
        self.sel_anchor = None
        text = selection_text([line.text for line in self.lines[a:b + 1]])
        if not text:
            self.flash("Nothing to copy")
            return
        ok, fallback = clipboard.copy(text)
        if not ok:
            self.write(fallback)
        count = b - a + 1
        self.flash(f"Copied {count} line{'s' if count != 1 else ''}"
                   + ("" if ok else " (via the terminal)"))

    def open_search(self) -> None:
        self.search_input = self.query or ""
        self.search_fresh = bool(self.search_input)
        self.search_origin = self.top

    def update_search(self, text: str) -> None:
        """Search as you type, from where the search box was opened."""
        self.search_input, self.search_fresh = text, False
        self.query = text or None
        self.match_idx = -1
        self.find_matches()
        if self.matches:
            self.goto_match(True, include_current=True, start=self.search_origin)
        else:
            self.top = self.search_origin
            self.clamp()

    def handle_search_key(self, key) -> None:
        text = self.search_input or ""
        if key == "esc":
            self.search_input = None
        elif key in ("enter", "down", "tab", "\x0e"):
            if self.matches:
                self.goto_match(True)
        elif key in ("up", "backtab", "\x10"):
            if self.matches:
                self.goto_match(False)
        elif key == "backspace":
            self.update_search("" if self.search_fresh else text[:-1])
        elif key in ("\x15", "\x17"):
            self.update_search("")
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.update_search(key if self.search_fresh else text + key)

    def toggle_toc(self) -> None:
        if not self.toc:
            self.flash("This document has no sections")
            return
        self.toc_open = not self.toc_open
        self.toc_focus = self.toc_open
        self.toc_sel = max(self.current_section(), 0)
        self.layout()

    def handle_toc_key(self, key) -> bool:
        if key in ("j", "down"):
            self.toc_sel = min(self.toc_sel + 1, len(self.toc) - 1)
        elif key in ("k", "up"):
            self.toc_sel = max(self.toc_sel - 1, 0)
        elif key in ("g", "home"):
            self.toc_sel = 0
        elif key in ("G", "end"):
            self.toc_sel = len(self.toc) - 1
        elif key in ("enter", "l", "right", " "):
            if self.toc:
                self.jump_to_line(self.toc[self.toc_sel][1])
                if self.toc_w >= self.cols:  # full-screen TOC on narrow terminals
                    self.toggle_toc()
        elif key in ("esc", "t", "h", "left"):
            self.toggle_toc()
        else:
            return False
        return True

    def handle_mouse(self, button: int, x: int, y: int, pressed: bool) -> None:
        if button == 64:
            self.scroll(-3)
        elif button == 65:
            self.scroll(3)
        elif button == 0 and pressed:
            row = y - 1
            if self.toc_w and x <= self.toc_w:
                k = self.toc_top + row - 1  # row 0 is the "Contents" header
                if row > 0 and 0 <= k < len(self.toc):
                    self.toc_sel = k
                    self.jump_to_line(self.toc[k][1])
                return
            idx = self.top + row
            if row < self.view_h and 0 <= idx < len(self.lines):
                link = self.link_at(self.lines[idx], x - 1 - self.margin)
                if link:
                    self.follow(link)

    @staticmethod
    def link_at(line: Line, col: int) -> Optional[str]:
        pos = 0
        for text, st in line.segs:
            w = dw(text)
            if pos <= col < pos + w:
                return st.link
            pos += w
        return None

    def follow(self, link: str) -> None:
        if link.startswith("#"):
            target = link[1:]
            if target in self.anchors:
                self.jump_to_line(self.anchors[target])
            else:
                self.flash(f"Unknown reference: {target}")
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, link], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.flash(f"Opening {link}")
        except OSError:
            self.flash(f"Could not open {link}")

    def edit(self) -> None:
        if not self.source.path:
            self.flash("Cannot edit stdin")
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        self.leave_screen()
        try:
            subprocess.call(shlex.split(editor) + [self.source.path])
        except OSError as exc:
            self.flash(f"Could not start editor: {exc}")
        finally:
            self.enter_screen()
        self.load()

    def suspend(self) -> None:
        self.leave_screen()
        os.kill(os.getpid(), signal.SIGTSTP)
        self.enter_screen()
        self.layout()

    # -- drawing ------------------------------------------------------------------

    def draw(self) -> None:
        out = ["\x1b[?2026h"]
        current = self.current_section()
        toc_rows = self.toc_rows(current) if self.toc_w else None
        hl = self.highlights()
        sel = self.selection() if self.sel_anchor is not None else None

        for row in range(self.view_h):
            out.append(f"\x1b[{row + 1};1H\x1b[0m\x1b[2K")
            if toc_rows is not None:
                out.append(to_ansi(toc_rows[row]))
                if self.toc_w < self.cols:
                    out.append(to_ansi([("│", theme.TOC_BORDER)]))
                else:
                    continue
            idx = self.top + row
            if idx < len(self.lines):
                segs = rstrip_segs(self.lines[idx].segs)
                if sel and sel[0] <= idx <= sel[1]:
                    segs = overlay(segs, [(0, sum(len(t) for t, _ in segs), theme.SELECTION)]) \
                        if segs else [(" ", theme.SELECTION)]
                if idx in hl:
                    segs = overlay(segs, hl[idx])
                out.append(f"\x1b[{row + 1};{self.margin + 1}H")
                out.append(to_ansi(truncate(segs, max(self.cols - self.margin, 0))))

        out.append(f"\x1b[{self.rows};1H\x1b[0m\x1b[2K")
        out.append(to_ansi(self.status_line(current)))
        if self.search_input is not None:
            out.append(self.search_box())
        if self.show_help:
            out.append(self.help_overlay())
        out.append("\x1b[?2026l")
        self.write("".join(out))

    def highlights(self):
        result = {}
        if not self.matches:
            return result
        lo = bisect.bisect_left(self.matches, (self.top, -1, -1))
        for k in range(lo, len(self.matches)):
            idx, s, e = self.matches[k]
            if idx >= self.top + self.view_h:
                break
            style = theme.SEARCH_CURRENT if k == self.match_idx else theme.SEARCH_MATCH
            result.setdefault(idx, []).append((s, e, style))
        return result

    def toc_rows(self, current: int) -> List[List[Seg]]:
        w = self.toc_w
        rows: List[List[Seg]] = []
        header = [(" Contents", theme.BOLD)]
        rows.append(pad(header, w))
        body_h = self.view_h - 1
        sel = self.toc_sel if self.toc_focus else max(current, 0)
        if sel < self.toc_top:
            self.toc_top = sel
        elif sel >= self.toc_top + body_h:
            self.toc_top = sel - body_h + 1
        self.toc_top = max(0, min(self.toc_top, max(0, len(self.toc) - body_h)))
        for k in range(self.toc_top, self.toc_top + body_h):
            if k >= len(self.toc):
                rows.append(pad([], w))
                continue
            heading, _ = self.toc[k]
            indent = "  " * max(heading.level - 1, 0) if heading.level else ""
            label = (heading.number + " " if heading.number else "") + plain_text(heading.text)
            style = theme.TOC_TEXT
            if k == current:
                style = theme.TOC_CURRENT
            if self.toc_focus and k == self.toc_sel:
                style = style + theme.TOC_SELECTED
            segs = [(" " + indent, PLAIN)] + truncate([(label, style)], w - 2 - len(indent), "…")
            rows.append(pad(segs, w))
        return rows

    def status_line(self, current: int) -> List[Seg]:
        if self.sel_anchor is not None:
            a, b = self.selection()
            n = b - a + 1
            segs = [(" SELECT ", theme.STATUS_NAME),
                    (f" {n} line{'s' if n != 1 else ''}   j k move · y copy · Esc cancel ",
                     theme.STATUS)]
            return pad(truncate(segs, self.cols, "…"), self.cols, style=theme.STATUS)
        name = f" {self.source.name} "
        left: List[Seg] = [(name, theme.STATUS_NAME)]
        if self.message:  # a passing message wins over the section name
            left.append(("  " + self.message + " ", theme.STATUS + Style(bold=True)))
        elif 0 <= current < len(self.toc):
            heading = self.toc[current][0]
            label = plain_text(heading.text)
            if heading.number:
                label = heading.number + " " + label
            left.append((" › " + label + " ", theme.STATUS))
        total = len(self.lines)
        if total <= self.view_h:
            pos = "All"
        elif self.top >= self.max_top:
            pos = "End"
        elif self.top == 0:
            pos = "Top"
        else:
            pos = f"{100 * self.top // self.max_top}%"
        found = ""
        if self.query and self.search_input is None:
            found = (f"{self.match_idx + 1}/{len(self.matches)}  " if self.match_idx >= 0
                     else f"{len(self.matches)} found  ") if self.matches else "not found  "
        right = f" {found}{pos}  ? help "
        avail = self.cols - dw(right)
        left = pad(truncate(left, avail, "…"), avail, style=theme.STATUS)
        return left + [(right, theme.STATUS)]

    def search_box(self) -> str:
        width = min(48, self.cols)
        inner = width - 4
        if self.matches:
            count = f"{max(self.match_idx, 0) + 1} of {len(self.matches)}"
            count_style = theme.DIM
        elif self.search_input:
            count, count_style = "no matches", Style(fg=1, bold=True)
        else:
            count, count_style = "", theme.DIM
        text = self.search_input or ""
        field_w = max(inner - dw(count) - 2, 4)
        shown = text if dw(text) < field_w else "…" + text[-(field_w - 2):]
        text_style = Style(reverse=True) if self.search_fresh and text else PLAIN
        field = [(shown, text_style), ("█", Style(fg=8))]
        line = pad(field, inner - dw(count)) + [(count, count_style)]
        hint = truncate([("Enter ↓ next  ↑ prev  Esc close", theme.DIM)], inner, "…")
        bs = theme.BOX_BORDER
        title = " Search "
        rows = [
            [("╭─", bs), (title, theme.BOLD), ("─" * max(width - 3 - dw(title), 0) + "╮", bs)],
            [("│ ", bs)] + pad(truncate(line, inner, "…"), inner) + [(" │", bs)],
            [("│ ", bs)] + pad(hint, inner) + [(" │", bs)],
            [("╰" + "─" * (width - 2) + "╯", bs)],
        ]
        x = max(self.cols - width, 0) + 1
        return "".join(f"\x1b[{1 + k};{x}H\x1b[0m" + to_ansi(r) for k, r in enumerate(rows))

    def help_overlay(self) -> str:
        help_items = [(drag_hint() if k == "DRAG" else k, d) for k, d in HELP]
        key_w = max(dw(k) for k, _ in help_items) + 2
        desc_w = max(dw(d) for _, d in HELP)
        col_w = key_w + desc_w

        def entry(key: str, desc: str) -> List[Seg]:
            if not desc:
                return [(key, theme.BLOCK_TITLE)]
            return [(key.ljust(key_w), Style(fg=6, bold=True)), (desc, PLAIN)]

        entries = [entry(k, d) for k, d in help_items]
        columns = [entries]
        if len(entries) + 4 > self.view_h and 2 * col_w + 7 <= self.cols:
            # split at the section heading closest to the middle
            starts = [i for i, (_, d) in enumerate(HELP) if not d and i]
            cut = min(starts, key=lambda i: abs(i - len(HELP) / 2))
            columns = [entries[:cut], entries[cut:]]
        inner = col_w * len(columns) + 3 * (len(columns) - 1)
        width = min(inner + 4, self.cols)
        body_h = max(len(c) for c in columns)
        height = min(body_h + 3, self.view_h)
        x = max((self.cols - width) // 2, 0) + 1
        y = max((self.view_h - height) // 2, 0) + 1
        bs = theme.BOX_BORDER
        rows: List[List[Seg]] = []
        title = " Keys "
        rows.append([("╭─", bs), (title, theme.BOLD),
                     ("─" * max(width - 3 - dw(title), 0) + "╮", bs)])
        body: List[List[Seg]] = [[]]
        for i in range(body_h):
            line: List[Seg] = []
            for c, col in enumerate(columns):
                if c:
                    line.append(("   ", PLAIN))
                line += pad(col[i] if i < len(col) else [], col_w)
            body.append(line)
        for segs in body[: height - 2]:
            rows.append([("│ ", bs)] + pad(truncate(segs, width - 4), width - 4) + [(" │", bs)])
        rows.append([("╰" + "─" * (width - 2) + "╯", bs)])
        return "".join(f"\x1b[{y + k};{x}H" + to_ansi(r) for k, r in enumerate(rows))
