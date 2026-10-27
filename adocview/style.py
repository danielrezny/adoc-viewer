"""Styled text primitives shared by the renderer and the terminal UI.

A rendered document is a list of ``Line`` objects; each line is a list of
``(text, Style)`` segments.  Everything here is terminal-agnostic except
``to_ansi``, which turns a segment list into an escape-coded string.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Style:
    fg: Optional[int] = None  # 0-15, standard ANSI palette
    bg: Optional[int] = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    reverse: bool = False
    link: Optional[str] = None  # URL (OSC 8) or "#id" for internal cross references

    def __add__(self, other: Optional["Style"]) -> "Style":
        if other is None or other == PLAIN:
            return self
        return Style(
            fg=self.fg if other.fg is None else other.fg,
            bg=self.bg if other.bg is None else other.bg,
            bold=self.bold or other.bold,
            dim=self.dim or other.dim,
            italic=self.italic or other.italic,
            underline=self.underline or other.underline,
            strike=self.strike or other.strike,
            reverse=self.reverse or other.reverse,
            link=other.link or self.link,
        )


PLAIN = Style()
Seg = Tuple[str, Style]


class Line:
    __slots__ = ("segs", "anchor")

    def __init__(self, segs: Optional[List[Seg]] = None, anchor=None):
        self.segs = segs if segs is not None else []
        self.anchor = anchor  # Heading object when this line starts a section

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.segs)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Line({self.text!r})"


# --------------------------------------------------------------------------
# Display width


@lru_cache(maxsize=8192)
def char_width(ch: str) -> int:
    o = ord(ch)
    if o < 32 or 0x7F <= o < 0xA0:
        return 0
    if o in (0x200B, 0x200C, 0x200D, 0x2060, 0xFE0E, 0xFE0F):
        return 0
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dw(s: str) -> int:
    """Display width of a string in terminal cells."""
    if s.isascii():
        return len(s)
    return sum(char_width(c) for c in s)


def segs_width(segs: List[Seg]) -> int:
    return sum(dw(t) for t, _ in segs)


def plain(segs: List[Seg]) -> str:
    return "".join(t for t, _ in segs)


def _append(line: List[Seg], text: str, style: Style) -> None:
    if line and line[-1][1] == style:
        line[-1] = (line[-1][0] + text, style)
    else:
        line.append((text, style))


# --------------------------------------------------------------------------
# Wrapping

_TOKEN_RX = re.compile(r"\n|[ \t]+|[^ \t\n]+")


def wrap(segs: List[Seg], width: int) -> List[List[Seg]]:
    """Word-wrap styled segments to ``width`` cells.

    Runs of whitespace collapse to one space, ``\\n`` forces a break, and
    words longer than the line are split.  Adjacent non-space segments with
    different styles (``**bold**,``) stay glued together as one word.
    """
    width = max(width, 1)
    lines: List[List[Seg]] = []
    cur: List[Seg] = []
    cur_w = 0
    space: Optional[Seg] = None
    word: List[Seg] = []
    word_w = 0

    def flush_word() -> None:
        nonlocal cur, cur_w, space, word, word_w
        if not word:
            return
        sp_w = 1 if (space is not None and cur) else 0
        if cur and cur_w + sp_w + word_w > width:
            lines.append(cur)
            cur, cur_w = [], 0
        elif sp_w:
            _append(cur, space[0], space[1])
            cur_w += 1
        space = None
        if word_w > width:
            for text, st in word:
                for ch in text:
                    w = char_width(ch)
                    if cur and cur_w + w > width:
                        lines.append(cur)
                        cur, cur_w = [], 0
                    _append(cur, ch, st)
                    cur_w += w
        else:
            for text, st in word:
                _append(cur, text, st)
            cur_w += word_w
        word, word_w = [], 0

    for text, st in segs:
        for tok in _TOKEN_RX.findall(text):
            if tok == "\n":
                flush_word()
                lines.append(cur)
                cur, cur_w, space = [], 0, None
            elif tok[0] in " \t":
                flush_word()
                if cur:
                    space = (" ", st)
            else:
                word.append((tok, st))
                word_w += dw(tok)
    flush_word()
    if cur or not lines:
        lines.append(cur)
    return lines


def hard_wrap(segs: List[Seg], width: int) -> List[List[Seg]]:
    """Split segments at exactly ``width`` cells, preserving all whitespace."""
    width = max(width, 1)
    lines: List[List[Seg]] = [[]]
    w = 0
    for text, st in segs:
        tw = dw(text)
        if w + tw <= width:
            _append(lines[-1], text, st)
            w += tw
            continue
        for ch in text:
            cw = char_width(ch)
            if w + cw > width and lines[-1]:
                lines.append([])
                w = 0
            _append(lines[-1], ch, st)
            w += cw
    return lines


def truncate(segs: List[Seg], width: int, ellipsis: str = "") -> List[Seg]:
    if segs_width(segs) <= width:
        return segs
    limit = width - dw(ellipsis)
    out: List[Seg] = []
    w = 0
    for text, st in segs:
        for ch in text:
            cw = char_width(ch)
            if w + cw > limit:
                if ellipsis:
                    _append(out, ellipsis, st)
                return out
            _append(out, ch, st)
            w += cw
    return out


def pad(segs: List[Seg], width: int, align: str = "left", style: Style = PLAIN) -> List[Seg]:
    segs = truncate(segs, width)
    gap = width - segs_width(segs)
    if gap <= 0:
        return list(segs)
    if align == "right":
        return [(" " * gap, style)] + list(segs)
    if align == "center":
        left = gap // 2
        return [(" " * left, style)] + list(segs) + [(" " * (gap - left), style)]
    return list(segs) + [(" " * gap, style)]


def overlay(segs: List[Seg], ranges: List[Tuple[int, int, Style]]) -> List[Seg]:
    """Apply extra styles to character ranges (used for search highlights)."""
    if not ranges:
        return segs
    out: List[Seg] = []
    pos = 0
    for text, st in segs:
        start, end = pos, pos + len(text)
        cuts = {0, len(text)}
        for s, e, _ in ranges:
            if s < end and e > start:
                cuts.add(max(s, start) - start)
                cuts.add(min(e, end) - start)
        cuts_sorted = sorted(cuts)
        for a, b in zip(cuts_sorted, cuts_sorted[1:]):
            extra = None
            for s, e, hs in ranges:
                if s <= start + a < e:
                    extra = hs
            out.append((text[a:b], st + extra if extra else st))
        pos = end
    return out


# --------------------------------------------------------------------------
# ANSI output


@lru_cache(maxsize=1024)
def sgr(style: Style) -> str:
    codes = ["0"]
    if style.bold:
        codes.append("1")
    if style.dim:
        codes.append("2")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if style.reverse:
        codes.append("7")
    if style.strike:
        codes.append("9")
    if style.fg is not None:
        codes.append(str(30 + style.fg if style.fg < 8 else 90 + style.fg - 8))
    if style.bg is not None:
        codes.append(str(40 + style.bg if style.bg < 8 else 100 + style.bg - 8))
    return "\x1b[" + ";".join(codes) + "m"


def to_ansi(segs: List[Seg], color: bool = True, hyperlinks: bool = True) -> str:
    if not color:
        return "".join(t for t, _ in segs)
    out = []
    cur_link = None
    for text, st in segs:
        link = st.link if (hyperlinks and st.link and not st.link.startswith("#")) else None
        if link != cur_link:
            out.append(f"\x1b]8;;{link or ''}\x1b\\")
            cur_link = link
        out.append(sgr(st))
        out.append(text)
    if cur_link:
        out.append("\x1b]8;;\x1b\\")
    out.append("\x1b[0m")
    return "".join(out)
