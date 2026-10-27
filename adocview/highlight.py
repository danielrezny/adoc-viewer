"""Optional syntax highlighting for source blocks via Pygments."""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from . import theme
from .style import PLAIN, Seg, Style, _append

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound

    HAVE_PYGMENTS = True
except ImportError:  # pragma: no cover - depends on environment
    HAVE_PYGMENTS = False


@lru_cache(maxsize=256)
def _token_style(ttype) -> Style:
    t = ttype
    while t is not None and len(t):
        key = ".".join(t)
        if key in theme.SYNTAX:
            return theme.SYNTAX[key]
        t = t.parent
    return PLAIN


def highlight(lines: List[str], lang: Optional[str], base: Style = PLAIN) -> List[List[Seg]]:
    plain_lines = [[(l, base)] if l else [] for l in lines]
    if not HAVE_PYGMENTS or not lang:
        return plain_lines
    try:
        lexer = get_lexer_by_name(lang.lower(), stripnl=False, stripall=False, ensurenl=False)
    except ClassNotFound:
        return plain_lines
    out: List[List[Seg]] = [[]]
    try:
        for ttype, value in lex("\n".join(lines), lexer):
            st = base + _token_style(ttype)
            for k, part in enumerate(value.split("\n")):
                if k:
                    out.append([])
                if part:
                    _append(out[-1], part, st)
    except Exception:  # a lexer bug must never take the viewer down
        return plain_lines
    out = out[:len(lines)]
    while len(out) < len(lines):
        out.append([])
    return out
