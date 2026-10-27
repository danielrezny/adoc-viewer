"""Inline AsciiDoc markup: *strong*, _emphasis_, `monospace`, links, xrefs,
macros, footnotes and typographic replacements."""

from __future__ import annotations

import html
import re
from typing import Callable, List, Optional, Tuple

from . import theme
from .style import PLAIN, Seg, Style

SUP = dict(zip("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"))
SUB = dict(zip("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ"))

REPLACEMENTS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"\(C\)"), "\u00a9"),
    (re.compile(r"\(R\)"), "\u00ae"),
    (re.compile(r"\(TM\)"), "\u2122"),
    (re.compile(r"(^|(?<=\s))--(?=\s|$)"), "\u2014"),
    (re.compile(r"(?<=\w)--(?=\w)"), "\u2014"),
    (re.compile(r"(?<!\.)\.\.\.(?!\.)"), "\u2026"),
    (re.compile(r"(?<![-<])->"), "\u2192"),
    (re.compile(r"<-(?![->])"), "\u2190"),
    (re.compile(r"(?<![=<])=>"), "\u21d2"),
    (re.compile(r"<=(?![=>])"), "\u21d0"),
    (re.compile(r"(?<=\w)'(?=\w)"), "\u2019"),
]
ENTITY_RX = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+|#x[0-9a-fA-F]+);")
URL_TRAILING = ".,;:!?"

COLOR_ROLES = {"black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4,
               "purple": 5, "fuchsia": 5, "magenta": 5, "aqua": 6, "teal": 6, "cyan": 6,
               "white": 7, "gray": 8, "grey": 8, "silver": 7, "maroon": 1, "navy": 4,
               "olive": 3, "lime": 10}


class Context:
    """What the inline parser needs from the renderer.  The default
    implementation is used for plain-text extraction (TOC, ids, search)."""

    show_urls = False

    def xref_text(self, target: str) -> str:
        return target

    def footnote(self, fid: Optional[str], text: str) -> Optional[int]:
        return None


Handler = Callable[["InlineParser", "re.Match[str]", Style, List[Seg]], None]
PATTERNS: List[Tuple["re.Pattern[str]", Handler]] = []


def pattern(rx: str, flags: int = 0):
    def register(fn: Handler) -> Handler:
        PATTERNS.append((re.compile(rx, flags), fn))
        return fn
    return register


class InlineParser:
    def __init__(self, ctx: Optional[Context] = None):
        self.ctx = ctx or Context()

    def parse(self, text: str, style: Style = PLAIN) -> List[Seg]:
        out: List[Seg] = []
        self._parse(text, style, out)
        return out

    def _parse(self, text: str, style: Style, out: List[Seg]) -> None:
        pos = 0
        cache: List[object] = [None] * len(PATTERNS)
        while True:
            best = None
            best_i = -1
            for i, (rx, _) in enumerate(PATTERNS):
                m = cache[i]
                if m is False:
                    continue
                if m is None or m.start() < pos:  # type: ignore[union-attr]
                    m = rx.search(text, pos)
                    cache[i] = m if m else False
                    if not m:
                        continue
                if best is None or m.start() < best.start():  # type: ignore[union-attr]
                    best, best_i = m, i
            if best is None:
                break
            if best.start() > pos:  # type: ignore[union-attr]
                self.text(text[pos:best.start()], style, out)  # type: ignore[union-attr]
            PATTERNS[best_i][1](self, best, style, out)  # type: ignore[arg-type]
            pos = max(best.end(), pos + 1)  # type: ignore[union-attr]
        if pos < len(text):
            self.text(text[pos:], style, out)

    # -- emitters ---------------------------------------------------------

    def text(self, text: str, style: Style, out: List[Seg]) -> None:
        if not text:
            return
        for rx, rep in REPLACEMENTS:
            text = rx.sub(rep, text)
        if "&" in text:
            text = ENTITY_RX.sub(lambda m: html.unescape(m.group(0)), text)
        self.literal(text, style, out)

    @staticmethod
    def literal(text: str, style: Style, out: List[Seg]) -> None:
        if not text:
            return
        if out and out[-1][1] == style:
            out[-1] = (out[-1][0] + text, style)
        else:
            out.append((text, style))

    def link(self, url: str, text: str, style: Style, out: List[Seg]) -> None:
        text = text.strip()
        if text.endswith("^"):
            text = text[:-1]
        if text.startswith('"') and '"' in text[1:]:
            text = text[1:text.index('"', 1)]
        elif "," in text and "=" in text:
            text = text.split(",", 1)[0]
        link_style = style + theme.LINK + Style(link=url)
        if text and not style.link:
            self._parse(text, link_style, out)
        else:
            self.literal(re.sub(r"^mailto:", "", url), link_style, out)
        if self.ctx.show_urls and text and text != url:
            self.literal(f" <{url}>", style + theme.DIM, out)


# Order matters: at equal positions, the earlier pattern wins.

@pattern(r"\\([*_`#^~+{\[<]|\w+:)")
def _escape(p, m, st, out):
    p.literal(m.group(1), st, out)


@pattern(r"\+\+\+(.+?)\+\+\+")
def _pass3(p, m, st, out):
    p.literal(m.group(1), st, out)


@pattern(r"(?<![\w+])\+\+(.+?)\+\+(?!\+)")
def _pass2(p, m, st, out):
    p.literal(m.group(1), st, out)


@pattern(r"\bpass:[a-z,]*\[(.*?)\]")
def _pass_macro(p, m, st, out):
    p.literal(m.group(1), st, out)


@pattern(r"\"`(.+?)`\"")
def _double_quote(p, m, st, out):
    p.literal("\u201c", st, out)
    p._parse(m.group(1), st, out)
    p.literal("\u201d", st, out)


@pattern(r"'`(.+?)`'")
def _single_quote(p, m, st, out):
    p.literal("\u2018", st, out)
    p._parse(m.group(1), st, out)
    p.literal("\u2019", st, out)


def _mono_content(text: str) -> str:
    if len(text) >= 2 and text[0] == "+" and text[-1] == "+":
        return text[1:-1]
    return text


@pattern(r"``(.+?)``")
def _mono2(p, m, st, out):
    p.literal(_mono_content(m.group(1)), st + theme.MONO, out)


@pattern(r"(?<![\w`])`(?=\S)(.+?)(?<=\S)`(?![\w`])")
def _mono1(p, m, st, out):
    p.literal(_mono_content(m.group(1)), st + theme.MONO, out)


@pattern(r"(?<![\w+])\+(?=\S)(.+?)(?<=\S)\+(?![\w+])")
def _pass1(p, m, st, out):
    p.literal(m.group(1), st, out)


@pattern(r"<((?:https?|ftp|irc|file)://[^\s>]+)>")
def _angle_url(p, m, st, out):
    p.link(m.group(1), "", st, out)


@pattern(r"\b((?:https?|ftp|irc|file)://[^\s\[\]<>\"]+)\[([^\]]*)\]")
def _url_text(p, m, st, out):
    p.link(m.group(1), m.group(2), st, out)


@pattern(r"\blink:([^\s\[]+)\[([^\]]*)\]")
def _link_macro(p, m, st, out):
    p.link(m.group(1), m.group(2), st, out)


@pattern(r"\bmailto:([^\s\[]+)\[([^\]]*)\]")
def _mailto(p, m, st, out):
    p.link("mailto:" + m.group(1), m.group(2), st, out)


@pattern(r"(?<![\w/\"'=])(?:https?|ftp|irc|file)://[^\s\[\]<>\"]+")
def _bare_url(p, m, st, out):
    url = m.group(0)
    trail = ""
    while url and (url[-1] in URL_TRAILING or (url[-1] == ")" and "(" not in url)):
        trail = url[-1] + trail
        url = url[:-1]
    p.link(url, "", st, out)
    p.text(trail, st, out)


@pattern(r"(?<![\w.+-])[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
def _email(p, m, st, out):
    p.link("mailto:" + m.group(0), "", st, out)


@pattern(r"<<([^\s,>][^,>]*?)(?:,\s*([^>]+?))?>>")
def _xref(p, m, st, out):
    target = m.group(1).strip()
    label = m.group(2)
    if label:
        p._parse(label, st + theme.XREF + Style(link="#" + target.split("#")[-1]), out)
    else:
        p.literal(p.ctx.xref_text(target), st + theme.XREF + Style(link="#" + target.split("#")[-1]), out)


@pattern(r"\bxref:([^\s\[]+)\[([^\]]*)\]")
def _xref_macro(p, m, st, out):
    target = m.group(1)
    link = Style(link="#" + target.split("#")[-1])
    if m.group(2):
        p._parse(m.group(2), st + theme.XREF + link, out)
    else:
        p.literal(p.ctx.xref_text(target), st + theme.XREF + link, out)


@pattern(r"\[\[[^\]]*\]\]|\banchor:[^\s\[]+\[[^\]]*\]")
def _anchor(p, m, st, out):
    pass


@pattern(r"\bfootnote(?:ref)?:([\w-]*)\[((?:[^\]\\]|\\.)*)\]")
def _footnote(p, m, st, out):
    num = p.ctx.footnote(m.group(1) or None, m.group(2).replace("\\]", "]"))
    if num is not None:
        p.literal(f"[{num}]", st + theme.FOOTNOTE_REF, out)


@pattern(r"\bkbd:\[((?:[^\]\\]|\\.)*)\]")
def _kbd(p, m, st, out):
    spec = m.group(1).replace("\\]", "]").strip()
    if len(spec) <= 1:
        keys = [spec]
    elif spec.endswith(("++", ",+")):
        keys = re.split(r"\s*[+,]\s*", spec[:-2]) + ["+"]
    else:
        keys = re.split(r"\s*[+,]\s*", spec)
    for k, key in enumerate(keys):
        if k:
            p.literal("+", st + theme.DIM, out)
        p.literal(key.strip(), st + theme.KBD, out)


@pattern(r"\bbtn:\[([^\]]*)\]")
def _btn(p, m, st, out):
    p.literal(f"[{m.group(1)}]", st + theme.KBD, out)


@pattern(r"\bmenu:([^\s\[]+)\[([^\]]*)\]")
def _menu(p, m, st, out):
    parts = [m.group(1)] + [s.strip() for s in re.split(r">|,", m.group(2)) if s.strip()]
    p.literal(" \u203a ".join(parts), st + theme.MENU, out)


@pattern(r"\bimage:([^\s\[:][^\s\[]*)\[([^\]]*)\]")
def _inline_image(p, m, st, out):
    alt = m.group(2).split(",")[0].strip() or m.group(1).rsplit("/", 1)[-1]
    p.literal(f"[{alt}]", st + theme.INLINE_IMAGE, out)


@pattern(r"\bicon:([\w-]+)\[[^\]]*\]")
def _icon(p, m, st, out):
    p.literal(f"[{m.group(1)}]", st + theme.DIM, out)


def _role_style(roles: str) -> Style:
    result = PLAIN
    for role in roles.replace(".", " ").split():
        role = role.lstrip("#%")
        if role == "underline":
            result += Style(underline=True)
        elif role == "line-through":
            result += Style(strike=True)
        elif role.endswith("-background") and role[:-11] in COLOR_ROLES:
            result += Style(bg=COLOR_ROLES[role[:-11]])
        elif role in COLOR_ROLES:
            result += Style(fg=COLOR_ROLES[role])
        elif role in ("mark", "highlight"):
            result += theme.MARK
    return result


@pattern(r"\[([^\]\[]*)\]##(.+?)##")
def _role2(p, m, st, out):
    p._parse(m.group(2), st + _role_style(m.group(1)), out)


@pattern(r"(?<![\w\]])\[([^\]\[]*)\]#(?=\S)(.+?)(?<=\S)#(?![\w#])")
def _role1(p, m, st, out):
    p._parse(m.group(2), st + _role_style(m.group(1)), out)


@pattern(r"\*\*(.+?)\*\*")
def _strong2(p, m, st, out):
    p._parse(m.group(1), st + theme.STRONG, out)


@pattern(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
def _strong1(p, m, st, out):
    p._parse(m.group(1), st + theme.STRONG, out)


@pattern(r"__(.+?)__")
def _em2(p, m, st, out):
    p._parse(m.group(1), st + theme.EMPHASIS, out)


@pattern(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
def _em1(p, m, st, out):
    p._parse(m.group(1), st + theme.EMPHASIS, out)


@pattern(r"##(.+?)##")
def _mark2(p, m, st, out):
    p._parse(m.group(1), st + theme.MARK, out)


@pattern(r"(?<![\w#&])#(?=\S)(.+?)(?<=\S)#(?![\w#])")
def _mark1(p, m, st, out):
    p._parse(m.group(1), st + theme.MARK, out)


@pattern(r"\^(\S+?)\^")
def _sup(p, m, st, out):
    text = m.group(1)
    if all(c in SUP for c in text):
        p.literal("".join(SUP[c] for c in text), st, out)
    else:
        p.literal("^", st + theme.DIM, out)
        p._parse(text, st, out)


@pattern(r"(?<!~)~(\S+?)~(?!~)")
def _sub(p, m, st, out):
    text = m.group(1)
    if all(c in SUB for c in text):
        p.literal("".join(SUB[c] for c in text), st, out)
    else:
        p.literal("_", st + theme.DIM, out)
        p._parse(text, st, out)


_PLAIN_PARSER = InlineParser()


def parse_inline(text: str, style: Style = PLAIN, ctx: Optional[Context] = None) -> List[Seg]:
    return InlineParser(ctx).parse(text, style)


def plain_text(text: str) -> str:
    """Strip inline markup, returning only the visible text."""
    return "".join(t for t, _ in _PLAIN_PARSER.parse(text))
