"""Block-level AsciiDoc parser producing a small document tree.

The tree is deliberately flat: sections are not containers, a ``Heading``
block simply marks where one starts.  That is all a viewer needs, and it
keeps the renderer simple.  Inline markup is left as raw text and parsed
at render time (see ``inline.py``).
"""

from __future__ import annotations

import csv
import re
import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .attrlist import BlockAttrs, parse_attrlist
from .inline import plain_text

ADMONITIONS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")

DEFAULT_ATTRS: Dict[str, str] = {
    "toclevels": "2", "sectnumlevels": "3", "toc-title": "Table of Contents",
    "idprefix": "_", "idseparator": "_",
    "example-caption": "Example", "table-caption": "Table", "figure-caption": "Figure",
    "appendix-caption": "Appendix",
    "empty": "", "blank": "", "nbsp": " ", "sp": " ", "zwsp": "​", "wj": "⁠",
    "apos": "'", "quot": '"', "lsquo": "‘", "rsquo": "’", "ldquo": "“",
    "rdquo": "”", "deg": "°", "plus": "+", "brvbar": "¦", "vbar": "|",
    "amp": "&", "lt": "<", "gt": ">", "startsb": "[", "endsb": "]", "caret": "^",
    "asterisk": "*", "tilde": "~", "backslash": "\\", "backtick": "`",
    "two-colons": "::", "two-semicolons": ";;", "cpp": "C++", "cxx": "C++", "pp": "++",
    # Common status placeholders; documents can still define their own values.
    "yes": "✅", "no": "❌", "check": "✅", "checkmark": "✅", "done": "✅",
    "cross": "❌", "fail": "❌", "partial": "🟡", "maybe": "❓", "unknown": "❓",
    "na": "➖", "warn": "⛔", "tip-icon": "💡", "wip": "🚧", "todo": "📝", "star": "⭐",
    "thumbsup": "👍", "thumbsdown": "👎",
}

DOC_TITLE_RX = re.compile(r"^=\s+(\S.*?)\s*$")
SECTION_RX = re.compile(r"^(={1,6}|#{1,6})\s+(\S.*?)(?:\s+\1)?\s*$")
ATTR_ENTRY_RX = re.compile(r"^:(!?)(\w[\w-]*)(!?):(?:[ \t]+(.*?))?\s*$")
ANCHOR_RX = re.compile(r"^\[\[([A-Za-z_:][\w:.-]*)(?:,\s*(.+?))?\]\]\s*$")
BLOCK_ATTR_RX = re.compile(r"^\[(|[#.%\w\"'{,].*)\]\s*$")
BLOCK_TITLE_RX = re.compile(r"^\.([^.\s].*)$")
DELIM_RX = re.compile(r"^(-{4,}|\.{4,}|_{4,}|\*{4,}|={4,}|\+{4,}|/{4,}|--|[|,:!]={3,}|```.*?)\s*$")
UL_RX = re.compile(r"^\s*(-|\*{1,5})\s+(\S.*)$")
OL_RX = re.compile(r"^\s*(\.{1,5}|\d{1,9}\.)\s+(\S.*)$")
COLIST_RX = re.compile(r"^<(\d+|\.)>\s+(\S.*)$")
DL_RX = re.compile(r"^\s*(?!//)(\S.*?)(:{2,4}|;;)(?:\s+(.*))?$")
CHECK_RX = re.compile(r"^\[([ xX*])\]\s+(.*)$")
IMAGE_RX = re.compile(r"^image::([^\[\s][^\[]*)\[(.*)\]\s*$")
TOC_MACRO_RX = re.compile(r"^toc::\[.*\]\s*$")
RULE_RX = re.compile(r"^('{3,}|-{3}|\*{3}|_{3}|- - -|\* \* \*)\s*$")
PAGEBREAK_RX = re.compile(r"^<{3,}\s*$")
ADMON_PARA_RX = re.compile(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s+(.*)$")
ATTR_REF_RX = re.compile(r"(\\)?\{(\w[\w-]*)\}")
COUNTER_RX = re.compile(r"\{(counter2?):([\w-]+)(?::(\w+))?\}")
COLSPEC_RX = re.compile(r"^(?:(\d+)\*)?([<^>])?(?:\.([<^>]))?(\d+%?|~)?([adehlmsv])?$")
CELLSPEC_RX = re.compile(
    r"(?:^|(?<=\s))((?:\d+\*)?(?:\d+(?:\.\d+)?\+|\.\d+\+)?[<^>]?(?:\.[<^>])?[adehlmsv]?)$")


# --------------------------------------------------------------------------
# Tree


@dataclass
class Node:
    title: Optional[str] = None
    id: Optional[str] = None


@dataclass
class Heading(Node):
    level: int = 1
    text: str = ""
    discrete: bool = False
    number: str = ""


@dataclass
class Paragraph(Node):
    text: str = ""
    role: Optional[str] = None


@dataclass
class Listing(Node):
    lines: List[str] = field(default_factory=list)
    lang: Optional[str] = None
    kind: str = "listing"  # listing | literal | pass
    linenums: bool = False


@dataclass
class Container(Node):
    kind: str = "open"  # open | quote | sidebar | example | admonition | abstract
    blocks: List[Node] = field(default_factory=list)
    label: Optional[str] = None  # admonition kind
    attribution: Optional[str] = None
    citation: Optional[str] = None


@dataclass
class Verse(Node):
    lines: List[str] = field(default_factory=list)
    attribution: Optional[str] = None
    citation: Optional[str] = None


@dataclass
class ListItem:
    text: str = ""
    term: Optional[str] = None
    checked: Optional[bool] = None
    blocks: List[Node] = field(default_factory=list)


@dataclass
class ListBlock(Node):
    kind: str = "ul"  # ul | ol | dl | colist
    items: List[ListItem] = field(default_factory=list)
    start: int = 1
    style: Optional[str] = None


@dataclass
class Cell:
    text: str = ""
    span: int = 1
    style: Optional[str] = None
    align: Optional[str] = None
    blocks: Optional[List[Node]] = None


@dataclass
class ColSpec:
    align: str = "left"
    style: Optional[str] = None


@dataclass
class Table(Node):
    rows: List[List[Cell]] = field(default_factory=list)
    cols: List[ColSpec] = field(default_factory=list)
    header: bool = False
    footer: bool = False


@dataclass
class Image(Node):
    target: str = ""
    alt: str = ""


@dataclass
class Rule(Node):
    kind: str = "rule"  # rule | pagebreak


@dataclass
class TocMacro(Node):
    pass


@dataclass
class Document:
    title: Optional[Heading] = None
    author: Optional[str] = None
    revision: Optional[str] = None
    blocks: List[Node] = field(default_factory=list)
    attrs: Dict[str, str] = field(default_factory=dict)
    headings: List[Heading] = field(default_factory=list)
    ids: Dict[str, str] = field(default_factory=dict)


@dataclass
class _RawItem:
    kind: str
    key: str
    text: List[str]
    term: Optional[str] = None
    checked: Optional[bool] = None
    number: Optional[int] = None
    blocks: List[Node] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parser


class Parser:
    def __init__(self, attrs: Optional[Dict[str, str]] = None):
        self.attrs: Dict[str, str] = dict(DEFAULT_ATTRS)
        if attrs:
            self.attrs.update(attrs)
        self.pending = BlockAttrs()
        self.headings: List[Heading] = []
        self.ids: Dict[str, str] = {}
        self.leveloffset = 0
        self.sect_counters: List[int] = []
        self.appendix = 0

    # -- public -----------------------------------------------------------

    def parse(self, lines: List[str]) -> Document:
        doc = Document()
        i = self._parse_header(lines, doc)
        doc.blocks = self._parse_blocks(lines[i:])
        doc.attrs = self.attrs
        doc.headings = self.headings
        doc.ids = self.ids
        return doc

    # -- substitutions ----------------------------------------------------

    def subst(self, text: str) -> str:
        if "{" not in text:
            return text

        def counter(m: "re.Match[str]") -> str:
            name = "counter-" + m.group(2)
            current = self.attrs.get(name)
            if current is None:
                value = m.group(3) or "1"
            elif current.isdigit():
                value = str(int(current) + 1)
            elif len(current) == 1:
                value = chr(ord(current) + 1)
            else:
                value = current
            self.attrs[name] = value
            return value if m.group(1) == "counter" else ""

        text = COUNTER_RX.sub(counter, text)

        def ref(m: "re.Match[str]") -> str:
            if m.group(1):
                return m.group(0)[1:]
            return self.attrs.get(m.group(2), m.group(0))

        return ATTR_REF_RX.sub(ref, text)

    # -- header -----------------------------------------------------------

    def _parse_header(self, lines: List[str], doc: Document) -> int:
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip() or (line.startswith("//") and not line.startswith("////")):
                i += 1
                continue
            if re.match(r"^/{4,}\s*$", line):
                i = self._find_close(lines, i, line.rstrip()) + 1
                continue
            m = ATTR_ENTRY_RX.match(line)
            if m:
                i = self._attr_entry(lines, i, m)
                continue
            break
        if i >= n:
            return i
        m = DOC_TITLE_RX.match(lines[i])
        if not m:
            return i
        title = self.subst(m.group(1))
        doc.title = Heading(level=0, text=title, id="_top")
        self.attrs.setdefault("doctitle", plain_text(title))
        i += 1
        header_line = 0
        while i < n and lines[i].strip():
            line = lines[i]
            if line.startswith("//"):
                i += 1
                continue
            m = ATTR_ENTRY_RX.match(line)
            if m:
                i = self._attr_entry(lines, i, m)
                continue
            if header_line == 0:
                names = [re.sub(r"\s*<[^>]*>", "", a).strip()
                         for a in self.subst(line.strip()).split(";")]
                doc.author = ", ".join(n for n in names if n)
                self.attrs.setdefault("author", names[0])
            elif header_line == 1:
                doc.revision = self.subst(line.strip())
            else:
                break
            header_line += 1
            i += 1
        if not doc.author and "author" in self.attrs:
            doc.author = self.attrs["author"]
        if not doc.revision and "revnumber" in self.attrs:
            rev = "v" + self.attrs["revnumber"].lstrip("v")
            if "revdate" in self.attrs:
                rev += ", " + self.attrs["revdate"]
            doc.revision = rev
        return i

    def _attr_entry(self, lines: List[str], i: int, m: "re.Match[str]") -> int:
        name = m.group(2)
        value = m.group(4) or ""
        i += 1
        while value.endswith(" \\") and i < len(lines):  # soft-wrapped value
            value = value[:-2] + " " + lines[i].strip()
            i += 1
        if m.group(1) or m.group(3):
            self.attrs.pop(name, None)
            if name == "leveloffset":
                self.leveloffset = 0
            return i
        value = self.subst(value)
        if name == "leveloffset":
            try:
                if value[:1] in "+-" and value[:1]:
                    self.leveloffset += int(value)
                else:
                    self.leveloffset = int(value or 0)
            except ValueError:
                pass
            value = str(self.leveloffset)
        self.attrs[name] = value
        return i

    # -- blocks -----------------------------------------------------------

    def _parse_blocks(self, lines: List[str], in_list: bool = False) -> List[Node]:
        saved, self.pending = self.pending, BlockAttrs()
        blocks: List[Node] = []
        i, n = 0, len(lines)
        while i < n:
            block, i = self._next_block(lines, i, in_list)
            if block is not None:
                blocks.append(block)
        self.pending = saved
        return blocks

    def _take(self) -> BlockAttrs:
        attrs, self.pending = self.pending, BlockAttrs()
        return attrs

    def _finish(self, block: Node, attrs: BlockAttrs) -> Node:
        if attrs.title and block.title is None:
            block.title = self.subst(attrs.title)
        if isinstance(block, Listing):
            subs = [v.strip() for v in attrs.named.get("subs", "").split(",")]
            if any(v.strip("+") == "attributes" for v in subs):
                block.lines = [self.subst(line) for line in block.lines]
        if attrs.id:
            block.id = attrs.id
            self.ids.setdefault(attrs.id, attrs.reftext or block.title or attrs.id)
        return block

    def _find_close(self, lines: List[str], i: int, delim: str) -> int:
        j = i + 1
        while j < len(lines) and lines[j].rstrip() != delim:
            j += 1
        return j

    def _next_block(self, lines: List[str], i: int, in_list: bool = False
                    ) -> Tuple[Optional[Node], int]:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            return None, i + 1

        if re.match(r"^/{4,}\s*$", line):
            return None, self._find_close(lines, i, line.rstrip()) + 1
        if line.startswith("//"):
            return None, i + 1

        m = ATTR_ENTRY_RX.match(line)
        if m:
            return None, self._attr_entry(lines, i, m)

        m = ANCHOR_RX.match(line)
        if m:
            self.pending.id = m.group(1)
            self.pending.reftext = m.group(2)
            return None, i + 1

        if line.startswith("[") and not line.startswith("[[") and BLOCK_ATTR_RX.match(line):
            self.pending.merge(parse_attrlist(self.subst(line.strip()[1:-1])))
            return None, i + 1

        m = BLOCK_TITLE_RX.match(line)
        if m and not DELIM_RX.match(line):
            self.pending.title = m.group(1)
            return None, i + 1

        m = SECTION_RX.match(line)
        if m:
            return self._heading(m), i + 1

        m = DELIM_RX.match(line)
        if m:
            return self._delimited(lines, i, m.group(1).rstrip())

        if RULE_RX.match(line):
            self._take()
            return Rule(kind="rule"), i + 1
        if PAGEBREAK_RX.match(line):
            self._take()
            return Rule(kind="pagebreak"), i + 1

        if self._match_item(line):
            return self._parse_list(lines, i)

        m = IMAGE_RX.match(line)
        if m:
            attrs = self._take()
            img_attrs = parse_attrlist(m.group(2)) if m.group(2).strip() else BlockAttrs()
            target = self.subst(m.group(1).strip())
            alt = img_attrs.positional(1) or attrs.named.get("alt") or ""
            if not alt:
                alt = re.sub(r"[-_]", " ", target.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            if img_attrs.named.get("title") and not attrs.title:
                attrs.title = img_attrs.named["title"]
            return self._finish(Image(target=target, alt=alt), attrs), i + 1

        if TOC_MACRO_RX.match(line):
            self._take()
            return TocMacro(), i + 1

        if line[0] in " \t" and not in_list:
            buf, j = [], i
            while j < len(lines) and lines[j].strip():
                buf.append(lines[j])
                j += 1
            attrs = self._take()
            text = textwrap.dedent("\n".join(buf)).split("\n")
            return self._finish(Listing(lines=text, kind="literal"), attrs), j

        return self._paragraph(lines, i, in_list)

    # -- headings ---------------------------------------------------------

    def _heading(self, m: "re.Match[str]") -> Heading:
        attrs = self._take()
        level = len(m.group(1)) - 1 + self.leveloffset
        level = max(0, min(level, 5))
        text = self.subst(m.group(2))
        discrete = attrs.style in ("discrete", "float")
        hid = attrs.id or self._gen_id(text)
        heading = Heading(level=level, text=text, id=hid, discrete=discrete)
        self.ids.setdefault(hid, attrs.reftext or plain_text(text))

        if not discrete and level >= 1:
            if attrs.style == "appendix" and level == 1:
                self.appendix += 1
                letter = chr(ord("A") + self.appendix - 1)
                heading.number = f"{self.attrs.get('appendix-caption', 'Appendix')} {letter}:"
                self.sect_counters = [0]
            elif "sectnums" in self.attrs:
                counters = self.sect_counters[:level]
                counters += [0] * (level - len(counters))
                counters[level - 1] += 1
                self.sect_counters = counters
                if level <= int(self.attrs.get("sectnumlevels", "3") or 3):
                    heading.number = ".".join(str(c) for c in counters) + "."
        self.headings.append(heading)
        return heading

    def _gen_id(self, text: str) -> str:
        prefix = self.attrs.get("idprefix", "_")
        sep = self.attrs.get("idseparator", "_")
        s = plain_text(text).lower()
        s = re.sub(r"[^\w\s.-]", "", s)
        s = re.sub(r"[\s.-]+", sep or "", s).strip(sep or None)
        base = prefix + s
        hid, k = base, 2
        while hid in self.ids:
            hid = f"{base}{sep}{k}"
            k += 1
        return hid

    # -- paragraphs -------------------------------------------------------

    def _join(self, lines: List[str], hardbreaks: bool = False) -> str:
        parts = []
        last = len(lines) - 1
        for k, line in enumerate(lines):
            line = line.strip()
            if line == "+" or line.endswith(" +"):
                parts.append(line[:-1].rstrip() + "\n")
            elif hardbreaks and k < last:
                parts.append(line + "\n")
            else:
                parts.append(line + " ")
        return self.subst("".join(parts).rstrip())

    def _paragraph(self, lines: List[str], i: int, in_list: bool) -> Tuple[Node, int]:
        buf: List[str] = []
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                break
            if line.startswith("//") and not re.match(r"^/{4,}\s*$", line):
                i += 1
                continue
            if buf and (DELIM_RX.match(line)
                        or (line.startswith("[") and BLOCK_ATTR_RX.match(line))):
                break
            if buf and in_list and (line.strip() == "+" or self._match_item(line)):
                break
            buf.append(line)
            i += 1

        attrs = self._take()
        style = attrs.style
        hardbreaks = "hardbreaks" in attrs.options or "hardbreaks-option" in self.attrs

        m = ADMON_PARA_RX.match(buf[0].strip()) if buf else None
        if m and not style:
            text = self._join([m.group(2)] + buf[1:])
            block: Node = Container(kind="admonition", label=m.group(1),
                                    blocks=[Paragraph(text=text)])
        elif style in ADMONITIONS:
            block = Container(kind="admonition", label=style,
                              blocks=[Paragraph(text=self._join(buf, hardbreaks))])
        elif style == "quote":
            block = Container(kind="quote", blocks=[Paragraph(text=self._join(buf, hardbreaks))],
                              attribution=attrs.positional(2), citation=attrs.positional(3))
        elif style == "verse":
            block = Verse(lines=[self.subst(l.strip()) for l in buf],
                          attribution=attrs.positional(2), citation=attrs.positional(3))
        elif style in ("literal", "listing", "source"):
            lang = attrs.positional(2) if style == "source" else None
            block = Listing(lines=buf, lang=lang, kind="literal" if style == "literal" else "listing")
        else:
            role = attrs.roles[0] if attrs.roles else (style if style in ("abstract", "lead") else None)
            block = Paragraph(text=self._join(buf, hardbreaks), role=role)
        return self._finish(block, attrs), i

    # -- delimited blocks -------------------------------------------------

    def _delimited(self, lines: List[str], i: int, delim: str) -> Tuple[Optional[Node], int]:
        attrs = self._take()
        if delim.startswith("```"):
            close = "```"
            fence_lang = delim[3:].strip() or None
        else:
            close = delim
            fence_lang = None
        j = i + 1
        while j < len(lines) and lines[j].rstrip() != close:
            j += 1
        content = lines[i + 1:j]
        nxt = j + 1
        style = attrs.style
        c = delim[0]

        if delim.startswith("/"):
            return None, nxt

        if delim.startswith("```") or (c == "-" and delim != "--"):
            lang = fence_lang or attrs.named.get("language")
            if not lang and style == "source":
                lang = attrs.positional(2) or self.attrs.get("source-language")
            if style in ADMONITIONS:
                block: Node = Container(kind="admonition", label=style,
                                        blocks=[Listing(lines=content)])
            else:
                block = Listing(lines=content, lang=lang,
                                kind="literal" if style == "literal" else "listing",
                                linenums="linenums" in attrs.options or attrs.positional(3) == "linenums")
        elif c == ".":
            block = Listing(lines=content, kind="literal")
        elif c == "+":
            block = Listing(lines=content, kind="pass")
        elif c == "_" or (delim == "--" and style in ("quote", "verse")):
            if style == "verse":
                block = Verse(lines=[self.subst(l) for l in content],
                              attribution=attrs.positional(2), citation=attrs.positional(3))
            else:
                block = Container(kind="quote", blocks=self._parse_blocks(content),
                                  attribution=attrs.positional(2), citation=attrs.positional(3))
        elif c == "*":
            block = Container(kind="sidebar", blocks=self._parse_blocks(content))
        elif c == "=":
            if style in ADMONITIONS:
                block = Container(kind="admonition", label=style, blocks=self._parse_blocks(content))
            else:
                block = Container(kind="example", blocks=self._parse_blocks(content))
        elif delim == "--":
            if style in ADMONITIONS:
                block = Container(kind="admonition", label=style, blocks=self._parse_blocks(content))
            elif style in ("source", "listing"):
                block = Listing(lines=content, lang=attrs.positional(2))
            elif style == "sidebar":
                block = Container(kind="sidebar", blocks=self._parse_blocks(content))
            elif style == "example":
                block = Container(kind="example", blocks=self._parse_blocks(content))
            else:
                block = Container(kind="abstract" if style == "abstract" else "open",
                                  blocks=self._parse_blocks(content))
        elif c in "|,:!":
            block = self._table(content, attrs, c)
        else:  # pragma: no cover - DELIM_RX guarantees one of the above
            block = Listing(lines=content)
        return self._finish(block, attrs), nxt

    # -- lists ------------------------------------------------------------

    def _match_item(self, line: str) -> Optional[_RawItem]:
        m = UL_RX.match(line)
        if m:
            text = m.group(2)
            checked = None
            cm = CHECK_RX.match(text)
            if cm:
                checked = cm.group(1) != " "
                text = cm.group(2)
            return _RawItem("ul", "ul" + m.group(1), [text], checked=checked)
        m = OL_RX.match(line)
        if m:
            marker = m.group(1)
            if marker[0].isdigit():
                return _RawItem("ol", "ol1.", [m.group(2)], number=int(marker[:-1]))
            return _RawItem("ol", "ol" + marker, [m.group(2)])
        m = COLIST_RX.match(line)
        if m:
            num = int(m.group(1)) if m.group(1).isdigit() else None
            return _RawItem("colist", "colist", [m.group(2)], number=num)
        m = DL_RX.match(line)
        if m and not line.lstrip().startswith(("[", "//")):
            text = [m.group(3)] if m.group(3) else []
            return _RawItem("dl", "dl" + m.group(2), text, term=m.group(1).strip())
        return None

    def _parse_list(self, lines: List[str], i: int) -> Tuple[Node, int]:
        attrs = self._take()
        n = len(lines)
        items: List[_RawItem] = []
        while i < n:
            item = self._match_item(lines[i])
            if item is None:
                break
            i += 1
            while i < n:
                line = lines[i]
                if not line.strip() or line.strip() == "+" or self._match_item(line):
                    break
                if DELIM_RX.match(line) or (line.startswith("[") and BLOCK_ATTR_RX.match(line)):
                    break
                if line.startswith("//"):
                    i += 1
                    continue
                item.text.append(line)
                i += 1
            while i < n and lines[i].strip() == "+":
                i += 1
                while i < n:
                    block, i = self._next_block(lines, i, in_list=True)
                    if block is not None:
                        item.blocks.append(block)
                        break
            items.append(item)
            j = i
            while j < n and (not lines[j].strip()
                             or (lines[j].startswith("//") and not lines[j].startswith("////"))):
                j += 1
            # An indented literal paragraph after a blank line belongs to the item.
            while j < n and lines[j][:1] in (" ", "\t") and not self._match_item(lines[j]):
                buf = []
                while j < n and lines[j].strip():
                    buf.append(lines[j])
                    j += 1
                item.blocks.append(Listing(lines=textwrap.dedent("\n".join(buf)).split("\n"),
                                           kind="literal"))
                i = j
                while j < n and not lines[j].strip():
                    j += 1
            nxt = self._match_item(lines[j]) if j < n else None
            # Without a blank line any item continues the list.  After one, only
            # items of a kind already in this list do, so separate lists stay apart.
            if nxt and (j == i or any(nxt.kind == it.kind for it in items)):
                i = j
                continue
            break

        tree, _ = self._build_list(items, 0, [])
        if attrs.style in ("arabic", "loweralpha", "upperalpha", "lowerroman", "upperroman"):
            tree.style = attrs.style
        if attrs.named.get("start", "").lstrip("-").isdigit():
            tree.start = int(attrs.named["start"])
        if "checklist" in attrs.options:
            for it in tree.items:
                if it.checked is None:
                    it.checked = False
        return self._finish(tree, attrs), i

    def _build_list(self, items: List[_RawItem], idx: int, ancestors: List[str]
                    ) -> Tuple[ListBlock, int]:
        first = items[idx]
        block = ListBlock(kind=first.kind)
        if first.number is not None and first.kind == "ol":
            block.start = first.number
        key = first.key
        while idx < len(items):
            raw = items[idx]
            if raw.key == key:
                block.items.append(ListItem(
                    text=self._join(raw.text) if raw.text else "",
                    term=self.subst(raw.term) if raw.term is not None else None,
                    checked=raw.checked, blocks=raw.blocks))
                idx += 1
            elif raw.key in ancestors:
                break
            else:
                child, idx = self._build_list(items, idx, ancestors + [key])
                block.items[-1].blocks.append(child)
        return block, idx

    # -- tables -----------------------------------------------------------

    def _table(self, content: List[str], attrs: BlockAttrs, sep: str) -> Table:
        table = Table()
        named = attrs.named
        sep = named.get("separator", sep)
        if named.get("format") == "csv":
            sep = ","
        elif named.get("format") == "dsv":
            sep = ":"

        cols: List[ColSpec] = []
        if "cols" in named:
            specs = [s.strip() for s in re.split(r"[,;]", named["cols"]) if s.strip()]
            if len(specs) == 1 and specs[0].isdigit():
                cols = [ColSpec() for _ in range(int(specs[0]))]
            else:
                for spec in specs:
                    m = COLSPEC_RX.match(spec)
                    if not m:
                        cols.append(ColSpec())
                        continue
                    repeat = int(m.group(1) or 1)
                    align = {"<": "left", "^": "center", ">": "right"}.get(m.group(2) or "<")
                    cols.extend(ColSpec(align=align, style=m.group(5)) for _ in range(repeat))

        options = attrs.options
        first_nonblank = next((k for k, l in enumerate(content) if l.strip()), None)

        if sep == ",":
            cells = [[Cell(text=c.strip()) for c in row]
                     for row in csv.reader(l for l in content if l.strip())]
            flat = [c for row in cells for c in row]
            ncols = len(cols) or (len(cells[0]) if cells else 1)
            first_line_cells = len(cells[0]) if cells else 0
        elif sep == ":":
            cells = [[Cell(text=c.strip()) for c in re.split(r"(?<!\\):", l)]
                     for l in content if l.strip()]
            flat = [c for row in cells for c in row]
            ncols = len(cols) or (len(cells[0]) if cells else 1)
            first_line_cells = len(cells[0]) if cells else 0
        else:
            flat = self._psv_cells(content, sep)
            first_line_cells = 0
            if first_nonblank is not None:
                first_line_cells = len(self._psv_cells([content[first_nonblank]], sep))
            ncols = len(cols) or max(first_line_cells, 1)

        if not cols:
            cols = [ColSpec() for _ in range(ncols)]
        table.cols = cols

        if "header" in options:
            table.header = True
        elif "noheader" not in options and first_nonblank is not None:
            nb = first_nonblank
            table.header = (first_line_cells == ncols and nb + 1 < len(content)
                            and not content[nb + 1].strip())
        table.footer = "footer" in options

        row: List[Cell] = []
        used = 0
        for cell in flat:
            col = cols[min(used, ncols - 1)]
            style = cell.style or col.style
            cell.style = style
            if cell.align is None:
                cell.align = col.align
            if style == "a":
                cell.blocks = self._parse_blocks(cell.text.split("\n"))
            elif style == "l":
                pass
            else:
                paras = re.split(r"\n\s*\n", cell.text)
                cell.text = "\n\n".join(" ".join(p.split()) for p in paras if p.strip())
            cell.text = self.subst(cell.text) if style != "l" else cell.text
            row.append(cell)
            used += max(cell.span, 1)
            if used >= ncols:
                table.rows.append(row)
                row, used = [], 0
        if row:
            table.rows.append(row)
        return table

    def _psv_cells(self, content: List[str], sep: str) -> List[Cell]:
        text = "\n".join(content)
        parts = re.split(r"(?<!\\)" + re.escape(sep), text)
        cells: List[Cell] = []
        spec = parts[0].strip()
        for k in range(1, len(parts)):
            body = parts[k]
            next_spec = ""
            if k < len(parts) - 1:
                m = CELLSPEC_RX.search(body)
                # A spec right after the separator ("|a|b") is the cell's text.
                if m and m.group(1) and m.start(1) > 0:
                    candidate = m.group(1)
                    at_line_start = body[m.start(1) - 1] == "\n"
                    if re.search(r"[\d<^>]", candidate) or at_line_start:
                        next_spec = candidate
                        body = body[:m.start(1)]
            cell = Cell(text=body.replace("\\" + sep, sep).strip())
            self._apply_cellspec(cell, spec)
            repeat = 1
            dm = re.match(r"^(\d+)\*", spec)
            if dm:
                repeat = int(dm.group(1))
            cells.extend(Cell(**vars(cell)) for _ in range(repeat))
            spec = next_spec
        return cells

    @staticmethod
    def _apply_cellspec(cell: Cell, spec: str) -> None:
        if not spec:
            return
        m = re.match(r"^(?:\d+\*)?(?:(\d+)?(?:\.\d+)?\+)?([<^>])?(?:\.[<^>])?([adehlmsv])?$", spec)
        if not m:
            return
        if m.group(1):
            cell.span = int(m.group(1))
        if m.group(2):
            cell.align = {"<": "left", "^": "center", ">": "right"}[m.group(2)]
        if m.group(3):
            cell.style = m.group(3)


def parse(lines: List[str], attrs: Optional[Dict[str, str]] = None) -> Document:
    return Parser(attrs).parse(lines)
