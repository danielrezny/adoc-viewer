"""Parsing of AsciiDoc attribute lists such as ``[source,python,linenums]``."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

_NAMED_RX = re.compile(r"^([\w-]+)\s*=\s*(.*)$", re.S)
_SHORTHAND_RX = re.compile(r"([#.%])([^#.%]*)")


class BlockAttrs:
    """Attributes collected from ``[...]``, ``[[id]]`` and ``.Title`` lines."""

    def __init__(self) -> None:
        self.style: Optional[str] = None
        self.pos: List[str] = []
        self.named: Dict[str, str] = {}
        self.options: Set[str] = set()
        self.roles: List[str] = []
        self.id: Optional[str] = None
        self.title: Optional[str] = None
        self.reftext: Optional[str] = None

    def positional(self, index: int) -> Optional[str]:
        """1-based positional attribute (1 is the block style)."""
        if 0 < index <= len(self.pos):
            value = self.pos[index - 1].strip()
            return value or None
        return None

    def merge(self, other: "BlockAttrs") -> None:
        if other.style:
            self.style = other.style
        if other.pos:
            self.pos = other.pos
        self.named.update(other.named)
        self.options |= other.options
        self.roles.extend(other.roles)
        self.id = other.id or self.id
        self.title = other.title or self.title
        self.reftext = other.reftext or self.reftext

    def empty(self) -> bool:
        return not (self.style or self.pos or self.named or self.options or self.roles
                    or self.id or self.title)


def _split(text: str) -> List[str]:
    parts, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'" and (not "".join(buf).strip() or "".join(buf).rstrip().endswith("=")):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_attrlist(text: str) -> BlockAttrs:
    attrs = BlockAttrs()
    for index, raw in enumerate(_split(text)):
        raw = raw.strip()
        m = _NAMED_RX.match(raw)
        if m and not raw.startswith(("\"", "'")):
            key, value = m.group(1), _unquote(m.group(2))
            attrs.named[key] = value
            if key in ("options", "opts"):
                attrs.options.update(o.strip() for o in value.split(",") if o.strip())
            elif key == "role":
                attrs.roles.extend(value.split())
            elif key == "id":
                attrs.id = value
            continue
        value = _unquote(raw)
        if index == 0 and not raw.startswith(("\"", "'")):
            head = re.split(r"[#.%]", value, maxsplit=1)[0]
            for sigil, name in _SHORTHAND_RX.findall(value[len(head):]):
                if not name:
                    continue
                if sigil == "#":
                    attrs.id = name
                elif sigil == ".":
                    attrs.roles.append(name)
                else:
                    attrs.options.add(name)
            attrs.style = head.strip() or None
            attrs.pos.append(head.strip())
        else:
            attrs.pos.append(value)
    return attrs
