"""Render the viewer into an SVG screenshot for the README.

Runs adoc in a pseudo-terminal, replays keys, and draws the resulting screen
(via pyte) as SVG text, one positioned run of cells at a time.

    uv run --no-project --with pyte --with pygments python scripts/screenshot.py \\
        docs/screenshot.svg examples/showcase.adoc --keys t
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from html import escape

import pyte

CELL_W, CELL_H, FONT = 8.4, 18, 14
PAD = 16
BAR = 30  # window title bar

BG, FG = "#1e2030", "#c8d3f5"
PALETTE = {
    "black": "#1b1d2b", "red": "#ff757f", "green": "#c3e88d", "brown": "#ffc777",
    "blue": "#82aaff", "magenta": "#c099ff", "cyan": "#86e1fc", "white": "#c8d3f5",
    "brightblack": "#636da6", "brightred": "#ff98a4", "brightgreen": "#d4f0a8",
    "brightbrown": "#ffd8a0", "brightblue": "#9ab8ff", "brightmagenta": "#d0b0ff",
    "brightcyan": "#b4f0ff", "brightwhite": "#ffffff",
}


def color(name: str, default: str) -> str:
    if name == "default":
        return default
    if name in PALETTE:
        return PALETTE[name]
    if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
        return "#" + name
    return default


def capture(doc: str, cols: int, rows: int, keys: list, env: dict) -> pyte.Screen:
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(env)
        os.execvp(sys.executable, [sys.executable, "-m", "adocview", doc])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    stream.feed(os.read(fd, 65536))
                except OSError:
                    return

    pump(1.5)
    for key in keys:
        os.write(fd, key.encode())
        pump(0.6)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return screen


def to_svg(screen: pyte.Screen, title: str) -> str:
    width = PAD * 2 + screen.columns * CELL_W
    height = BAR + PAD * 2 + screen.lines * CELL_H
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        "<style>text{font-family:'SF Mono',Menlo,Consolas,'DejaVu Sans Mono',monospace;"
        f"font-size:{FONT}px;white-space:pre}}.b{{font-weight:bold}}.i{{font-style:italic}}"
        ".u{text-decoration:underline}.s{text-decoration:line-through}</style>",
        f'<rect width="100%" height="100%" rx="10" fill="{BG}"/>',
        f'<circle cx="20" cy="15" r="6" fill="#ff5f57"/><circle cx="40" cy="15" r="6" '
        f'fill="#febc2e"/><circle cx="60" cy="15" r="6" fill="#28c840"/>',
        f'<text x="{width / 2:.0f}" y="20" fill="#7a88cf" text-anchor="middle">'
        f"{escape(title)}</text>",
    ]
    for y in range(screen.lines):
        row = screen.buffer[y]
        top = BAR + PAD + y * CELL_H
        x = 0
        while x < screen.columns:
            ch = row[x]
            fg, bg = color(ch.fg, FG), color(ch.bg, BG)
            if ch.reverse:
                fg, bg = (bg if ch.bg != "default" else BG), (fg if ch.fg != "default" else FG)
            key = (fg, bg, ch.bold, ch.italics, ch.underscore, ch.strikethrough)
            start, cells_text = x, []
            while x < screen.columns:
                c = row[x]
                cfg, cbg = color(c.fg, FG), color(c.bg, BG)
                if c.reverse:
                    cfg, cbg = (cbg if c.bg != "default" else BG), (cfg if c.fg != "default" else FG)
                if (cfg, cbg, c.bold, c.italics, c.underscore, c.strikethrough) != key:
                    break
                cells_text.append((x, c.data))
                x += 1
            left = PAD + start * CELL_W
            cells = x - start
            if bg != BG:
                out.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{cells * CELL_W:.1f}" '
                           f'height="{CELL_H}" fill="{bg}"/>')
            if any(d.strip() for _, d in cells_text):
                cls = " ".join(c for c, on in (("b", ch.bold), ("i", ch.italics),
                                                 ("u", ch.underscore), ("s", ch.strikethrough)) if on)
                attr = f' class="{cls}"' if cls else ""
                # one <text> per cell keeps box drawing and wide characters on the grid
                for cx, c in cells_text:
                    if c.strip():
                        out.append(f'<text x="{PAD + cx * CELL_W:.1f}" y="{top + CELL_H - 5:.1f}" '
                                   f'fill="{fg}"{attr}>{escape(c)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output")
    ap.add_argument("document")
    ap.add_argument("--cols", type=int, default=110)
    ap.add_argument("--rows", type=int, default=32)
    ap.add_argument("--keys", nargs="*", default=[])
    args = ap.parse_args()
    keys = [k.encode().decode("unicode_escape") for k in args.keys]
    screen = capture(args.document, args.cols, args.rows, keys,
                     {"TERM": "xterm-256color", "TERM_PROGRAM": "screenshot"})
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(to_svg(screen, "adoc " + os.path.basename(args.document)))


if __name__ == "__main__":
    main()
