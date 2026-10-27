"""Turns a parsed document into styled, wrapped terminal lines."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import theme
from .highlight import highlight
from .inline import Context, InlineParser
from .parser import (Container, Document, Heading, Image, Listing, ListBlock, Node,
                     Paragraph, Rule, Table, TocMacro, Verse, Cell)
from .style import (PLAIN, Line, Seg, Style, dw, hard_wrap, pad, plain, segs_width,
                    truncate, wrap)

BULLETS = ["•", "◦", "▪", "▫"]  # • ◦ ▪ ▫
OL_STYLES = ["arabic", "loweralpha", "lowerroman", "upperalpha", "upperroman"]
CALLOUT_RX = re.compile(r"(\s*(?://|#|--|;;?|<!--)?\s*(?:<!?-*\d+-*>\s*)+(?:-->)?)\s*$")
CALLOUT_NUM_RX = re.compile(r"<!?-*(\d+)-*>")


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out or "0"


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("a") + r) + s
    return s or "0"


def ol_marker(n: int, style: str) -> str:
    if style == "loweralpha":
        return _alpha(n) + "."
    if style == "upperalpha":
        return _alpha(n).upper() + "."
    if style == "lowerroman":
        return _roman(n) + ")"
    if style == "upperroman":
        return _roman(n).upper() + ")"
    return f"{n}."


def prefix(lines: List[Line], first: List[Seg], rest: Optional[List[Seg]] = None) -> List[Line]:
    rest = first if rest is None else rest
    return [Line(list(first if k == 0 else rest) + ln.segs, ln.anchor)
            for k, ln in enumerate(lines)]


def restyle(lines: List[Line], extra: Style) -> List[Line]:
    return [Line([(t, s + extra) for t, s in ln.segs], ln.anchor) for ln in lines]


class Renderer(Context):
    def __init__(self, doc: Document, width: int, show_urls: bool = False):
        self.doc = doc
        self.width = max(width, 20)
        self.show_urls = show_urls
        self.inline = InlineParser(self)
        self.footnotes: List[Tuple[int, str]] = []
        self.footnote_ids: Dict[str, int] = {}
        self.counters: Dict[str, int] = defaultdict(int)
        self.list_depth = 0
        self.ol_depth = 0
        self.toc: List[Tuple[Heading, int]] = []      # (heading, line index)
        self.anchors: Dict[str, int] = {}             # id -> line index

    # -- inline context -----------------------------------------------------

    def xref_text(self, target: str) -> str:
        frag = target.split("#")[-1]
        if frag in self.doc.ids:
            return self.doc.ids[frag]
        for h in self.doc.headings:
            if h.text == target:
                return h.text
        return f"[{target}]"

    def footnote(self, fid: Optional[str], text: str) -> Optional[int]:
        if fid and fid in self.footnote_ids and not text.strip():
            return self.footnote_ids[fid]
        num = len(self.footnotes) + 1
        self.footnotes.append((num, text))
        if fid:
            self.footnote_ids[fid] = num
        return num

    def fmt(self, text: str, style: Style = PLAIN) -> List[Seg]:
        return self.inline.parse(text, style)

    # -- document -----------------------------------------------------------

    def render(self) -> List[Line]:
        doc = self.doc
        w = self.width
        out: List[Line] = []

        if doc.title and "notitle" not in doc.attrs:
            out.extend(self.render_doc_title(doc, w))

        toc_attr = doc.attrs.get("toc")
        if toc_attr is not None and toc_attr != "macro" and doc.headings:
            if out:
                out.append(Line())
            out.extend(self.render_toc(w))

        body = self.render_blocks(doc.blocks, w)
        if body:
            if out:
                out.append(Line())
            out.extend(body)

        if self.footnotes:
            out.append(Line())
            out.append(Line([("─" * min(20, w), theme.DIM)]))
            for num, text in self.footnotes:
                label = f"[{num}] "
                body_lines = [Line(s) for s in wrap(self.fmt(text), w - dw(label))]
                out.extend(prefix(body_lines, [(label, theme.FOOTNOTE_REF)],
                                  [(" " * dw(label), PLAIN)]))

        for idx, line in enumerate(out):
            if isinstance(line.anchor, Heading):
                h = line.anchor
                if h.id:
                    self.anchors.setdefault(h.id, idx)
                if not h.discrete:
                    self.toc.append((h, idx))
        return out

    def render_doc_title(self, doc: Document, w: int) -> List[Line]:
        assert doc.title is not None
        lines = [Line(s) for s in wrap(self.fmt(doc.title.text, theme.TITLE), w)]
        lines[0].anchor = doc.title
        lines.append(Line([("═" * w, theme.TITLE_RULE)]))
        if doc.author:
            lines.extend(Line(s) for s in wrap([(doc.author, theme.DIM_ITALIC)], w))
        if doc.revision:
            lines.extend(Line(s) for s in wrap([(doc.revision, theme.DIM)], w))
        return lines

    def render_toc(self, w: int) -> List[Line]:
        try:
            levels = int(self.doc.attrs.get("toclevels", "2") or 2)
        except ValueError:
            levels = 2
        title = self.doc.attrs.get("toc-title", "Table of Contents")
        out = [Line([(title, theme.BLOCK_TITLE)])]
        for h in self.doc.headings:
            if h.discrete or not 1 <= h.level <= levels:
                continue
            indent = "  " * (h.level - 1)
            segs: List[Seg] = []
            if h.number:
                segs.append((h.number + " ", theme.DIM))
            segs += self.fmt(h.text, Style(link="#" + (h.id or "")))
            body = [Line(s) for s in wrap(segs, w - len(indent) - 2)]
            out.extend(prefix(body, [(indent + "› ", theme.DIM)], [(indent + "  ", PLAIN)]))
        return out

    # -- blocks ---------------------------------------------------------------

    def render_blocks(self, blocks: Sequence[Node], width: int) -> List[Line]:
        out: List[Line] = []
        for block in blocks:
            lines = self.render_block(block, width)
            if not lines:
                continue
            if out:
                out.append(Line())
            out.extend(lines)
        return out

    def render_block(self, b: Node, w: int) -> List[Line]:
        if isinstance(b, Heading):
            return self.render_heading(b, w)
        if isinstance(b, Paragraph):
            return self.titled(b, self.render_paragraph(b, w), w)
        if isinstance(b, Listing):
            return self.render_listing(b, w)
        if isinstance(b, ListBlock):
            return self.titled(b, self.render_list(b, w), w)
        if isinstance(b, Container):
            return self.render_container(b, w)
        if isinstance(b, Verse):
            return self.render_verse(b, w)
        if isinstance(b, Table):
            return self.render_table(b, w)
        if isinstance(b, Image):
            return self.render_image(b, w)
        if isinstance(b, Rule):
            ch = "─" if b.kind == "rule" else "┈"
            return [Line([(ch * w, theme.DIM)])]
        if isinstance(b, TocMacro):
            return self.render_toc(w)
        return []

    def title_lines(self, title: Optional[str], w: int, caption: str = "") -> List[Line]:
        if not title:
            return []
        segs = ([(caption, theme.BLOCK_TITLE)] if caption else []) + self.fmt(title, theme.BLOCK_TITLE)
        return [Line(s) for s in wrap(segs, w)]

    def titled(self, b: Node, lines: List[Line], w: int) -> List[Line]:
        return self.title_lines(b.title, w) + lines

    def render_heading(self, h: Heading, w: int) -> List[Line]:
        style = theme.HEADINGS[min(h.level, len(theme.HEADINGS) - 1)]
        segs: List[Seg] = []
        if h.number:
            segs.append((h.number + " ", style))
        segs += self.fmt(h.text, style)
        lines = [Line(s) for s in wrap(segs, w)]
        lines[0].anchor = h
        if h.level == 0:
            lines.append(Line([("═" * w, theme.TITLE_RULE)]))
        elif h.level == 1:
            lines.append(Line([("─" * w, theme.H1_RULE)]))
        return lines

    def render_paragraph(self, p: Paragraph, w: int) -> List[Line]:
        style = PLAIN
        if p.role in ("lead", "abstract"):
            style = theme.ITALIC if p.role == "abstract" else theme.BOLD
        return [Line(s) for s in wrap(self.fmt(p.text, style), w)]

    # -- code -----------------------------------------------------------------

    def render_listing(self, b: Listing, w: int) -> List[Line]:
        code = [l.expandtabs(4) for l in b.lines]
        while code and not code[-1].strip():
            code.pop()
        while code and not code[0].strip():
            code.pop(0)

        callouts: List[List[str]] = []
        if b.kind == "listing":
            for k, line in enumerate(code):
                m = CALLOUT_RX.search(line)
                if m and line[:m.start()].strip():
                    callouts.append(CALLOUT_NUM_RX.findall(m.group(1)))
                    code[k] = line[:m.start()]
                else:
                    callouts.append([])

        base = theme.CODE_TEXT if b.kind == "listing" else theme.LITERAL_TEXT
        if b.kind == "pass":
            base = theme.DIM
        rows = highlight(code, b.lang if b.kind == "listing" else None, base)

        out = self.title_lines(b.title, w)
        bs = theme.CODE_BORDER
        header: List[Seg] = [("┌─", bs)]
        label = b.lang if b.kind == "listing" else ("literal" if b.kind == "literal" else "passthrough")
        if label and b.kind != "literal":
            header.append((" " + label, theme.CODE_LABEL))
        out.append(Line(header))

        numw = len(str(len(rows))) if b.linenums else 0
        inner = max(w - 2 - (numw + 1 if numw else 0), 4)
        for k, segs in enumerate(rows):
            segs = list(segs)
            if callouts and k < len(callouts) and callouts[k]:
                segs.append((" " + " ".join(f"({n})" for n in callouts[k]), theme.CALLOUT))
            for j, piece in enumerate(hard_wrap(segs, inner)):
                gutter: List[Seg] = [("│ ", bs)]
                if numw:
                    num = str(k + 1).rjust(numw) if j == 0 else " " * numw
                    gutter = [("│", bs), (num + " ", theme.LINE_NUMBER)]
                out.append(Line(gutter + piece))
        out.append(Line([("└─", bs)]))
        return out

    # -- lists ------------------------------------------------------------------

    def render_list(self, lst: ListBlock, w: int) -> List[Line]:
        if lst.kind == "dl":
            return self.render_dlist(lst, w)
        out: List[Line] = []
        depth = self.list_depth
        self.list_depth += 1
        if lst.kind == "ol":
            ol_style = lst.style or OL_STYLES[self.ol_depth % len(OL_STYLES)]
            self.ol_depth += 1
        loose = any(any(not isinstance(b, ListBlock) for b in it.blocks) for it in lst.items)

        markers: List[List[Seg]] = []
        for k, item in enumerate(lst.items):
            if lst.kind == "ol":
                markers.append([(ol_marker(lst.start + k, ol_style), theme.LIST_NUMBER)])
            elif lst.kind == "colist":
                markers.append([(f"({k + 1})", theme.CALLOUT)])
            elif item.checked is True:
                markers.append([("✓", theme.CHECK_DONE)])
            elif item.checked is False:
                markers.append([("○", theme.CHECK_TODO)])
            else:
                markers.append([(BULLETS[depth % len(BULLETS)], theme.BULLET)])
        mw = max(segs_width(m) for m in markers) + 1

        for k, item in enumerate(lst.items):
            body_w = max(w - mw, 8)
            body: List[Line] = []
            if item.text:
                text_style = theme.DIM if item.checked else PLAIN
                body = [Line(s) for s in wrap(self.fmt(item.text, text_style), body_w)]
            for blk in item.blocks:
                lines = self.render_block(blk, body_w)
                if not lines:
                    continue
                if body and not isinstance(blk, ListBlock):
                    body.append(Line())
                body.extend(lines)
            if not body:
                body = [Line()]
            marker = markers[k]
            if lst.kind == "ol":
                marker = pad(marker, mw - 1, "right")
            marker = marker + [(" " * (mw - segs_width(marker)), PLAIN)]
            out.extend(prefix(body, marker, [(" " * mw, PLAIN)]))
            if loose and k < len(lst.items) - 1:
                out.append(Line())

        self.list_depth -= 1
        if lst.kind == "ol":
            self.ol_depth -= 1
        return out

    def render_dlist(self, lst: ListBlock, w: int) -> List[Line]:
        out: List[Line] = []
        indent = 4
        self.list_depth += 1
        for k, item in enumerate(lst.items):
            if k and (item.text or item.blocks):
                out.append(Line())
            out.extend(Line(s) for s in wrap(self.fmt(item.term or "", theme.DL_TERM), w))
            body: List[Line] = []
            if item.text:
                body = [Line(s) for s in wrap(self.fmt(item.text), w - indent)]
            for blk in item.blocks:
                lines = self.render_block(blk, w - indent)
                if lines:
                    if body and not isinstance(blk, ListBlock):
                        body.append(Line())
                    body.extend(lines)
            out.extend(prefix(body, [(" " * indent, PLAIN)]))
        self.list_depth -= 1
        return out

    # -- containers ---------------------------------------------------------------

    def render_container(self, c: Container, w: int) -> List[Line]:
        if c.kind == "admonition":
            label = c.label or "NOTE"
            color = theme.ADMONITION.get(label, theme.ADMONITION["NOTE"])
            bar = [("┃ ", Style(fg=color.fg))]
            head: List[Seg] = [(theme.ADMONITION_ICON.get(label, "") + " " + label, color)]
            if c.title:
                head += [("  ", PLAIN)] + self.fmt(c.title, theme.BOLD)
            inner = self.render_blocks(c.blocks, w - 2)
            return prefix([Line(s) for s in wrap(head, w - 2)] + inner, bar)

        if c.kind == "quote":
            inner = restyle(self.render_blocks(c.blocks, w - 2), theme.QUOTE_TEXT)
            out = self.title_lines(c.title, w) + prefix(inner, [("│ ", theme.QUOTE_BAR)])
            out.extend(self.attribution(c.attribution, c.citation, w))
            return out

        if c.kind in ("sidebar", "example"):
            caption = ""
            if c.kind == "example" and c.title:
                self.counters["example"] += 1
                caption = f"{self.doc.attrs.get('example-caption', 'Example')} {self.counters['example']}. "
            inner = self.render_blocks(c.blocks, w - 4)
            border = theme.EXAMPLE_BORDER if c.kind == "example" else theme.BOX_BORDER
            title = ([(caption, theme.BLOCK_TITLE)] if caption else []) + (
                self.fmt(c.title, theme.BLOCK_TITLE) if c.title else [])
            return self.box(inner, w, title, border)

        inner = self.render_blocks(c.blocks, w)
        if c.kind == "abstract":
            inner = restyle(inner, theme.ITALIC)
        return self.title_lines(c.title, w) + inner

    def attribution(self, who: Optional[str], cite: Optional[str], w: int) -> List[Line]:
        if not who and not cite:
            return []
        segs: List[Seg] = [("— ", theme.DIM)]
        if who:
            segs += self.fmt(who, theme.DIM_ITALIC)
        if cite:
            segs += [(", " if who else "", theme.DIM)] + self.fmt(cite, theme.DIM)
        return prefix([Line(s) for s in wrap(segs, w - 2)], [("  ", PLAIN)])

    def box(self, inner: List[Line], w: int, title: List[Seg], bs: Style) -> List[Line]:
        iw = w - 4
        top: List[Seg] = [("╭─", bs)]
        if title:
            title = truncate(title, w - 6, "…")
            top += [(" ", bs)] + title + [(" ", bs)]
        top.append(("─" * max(w - 1 - segs_width(top), 0) + "╮", bs))
        out = [Line(top)]
        for ln in inner:
            out.append(Line([("│ ", bs)] + pad(ln.segs, iw) + [(" │", bs)], ln.anchor))
        out.append(Line([("╰" + "─" * (w - 2) + "╯", bs)]))
        return out

    def render_verse(self, v: Verse, w: int) -> List[Line]:
        lines: List[Line] = []
        for raw in v.lines:
            lines.extend(Line(s) for s in wrap(self.fmt(raw, theme.QUOTE_TEXT), w - 2))
        while lines and not lines[-1].segs:
            lines.pop()
        out = self.title_lines(v.title, w) + prefix(lines, [("│ ", theme.QUOTE_BAR)])
        out.extend(self.attribution(v.attribution, v.citation, w))
        return out

    def render_image(self, img: Image, w: int) -> List[Line]:
        segs: List[Seg] = [("▣ ", theme.BULLET), (img.alt, theme.ITALIC),
                           ("  " + img.target, theme.DIM + Style(link=img.target))]
        out = [Line(s) for s in wrap(segs, w)]
        if img.title:
            self.counters["figure"] += 1
            caption = f"{self.doc.attrs.get('figure-caption', 'Figure')} {self.counters['figure']}. "
            out += self.title_lines(img.title, w, caption)
        return out

    # -- tables ---------------------------------------------------------------------

    def _cell_content(self, cell: Cell, header: bool) -> Tuple[Optional[List[Seg]], Optional[list]]:
        if cell.blocks is not None:
            return None, cell.blocks
        style = PLAIN
        if header or cell.style in ("h", "s"):
            style = theme.TABLE_HEADER
        elif cell.style == "e":
            style = theme.ITALIC
        elif cell.style == "m":
            style = theme.MONO
        if cell.style == "l":
            return [(cell.text, style)], None
        return self.fmt(cell.text, style), None

    def _cell_lines(self, segs, blocks, width: int, literal: bool) -> List[List[Seg]]:
        if blocks is not None:
            return [ln.segs for ln in self.render_blocks(blocks, width)] or [[]]
        if literal:
            out: List[List[Seg]] = []
            for raw in plain(segs).split("\n"):
                out.extend(hard_wrap([(raw, segs[0][1] if segs else PLAIN)], width))
            return out
        return wrap(segs, width)

    def render_table(self, t: Table, w: int) -> List[Line]:
        if not t.rows:
            return []
        ncols = max(len(t.cols), 1)
        avail = max(w - (3 * ncols + 1), ncols)

        content = []
        nat = [1] * ncols
        mins = [1] * ncols
        for r, row in enumerate(t.rows):
            header = (t.header and r == 0) or (t.footer and r == len(t.rows) - 1)
            row_content = []
            col = 0
            for cell in row:
                segs, blocks = self._cell_content(cell, header)
                row_content.append((cell, segs, blocks))
                if cell.span == 1 and col < ncols:
                    if blocks is not None:
                        lines = self._cell_lines(None, blocks, 200, False)
                        natural = max((segs_width(l) for l in lines), default=1)
                        longest = 12
                    else:
                        text = plain(segs or [])
                        natural = max((dw(p) for p in text.split("\n")), default=1)
                        longest = max((dw(word) for word in text.split()), default=1)
                    nat[col] = max(nat[col], natural)
                    mins[col] = max(mins[col], min(longest, 24))
                col += max(cell.span, 1)
            content.append(row_content)

        widths = _distribute(nat, mins, avail)
        bs = theme.TABLE_BORDER

        def border(left: str, mid: str, right: str, fill: str) -> Line:
            return Line([(left + mid.join(fill * (cw + 2) for cw in widths) + right, bs)])

        rendered_rows = []
        for r, row_content in enumerate(content):
            cells = []
            col = 0
            for cell, segs, blocks in row_content:
                span = min(max(cell.span, 1), ncols - col) if col < ncols else 1
                cw = sum(widths[col:col + span]) + 3 * (span - 1)
                lines = self._cell_lines(segs, blocks, cw, cell.style == "l")
                cells.append((lines, cw, cell.align or "left"))
                col += span
            while col < ncols:
                cells.append(([[]], widths[col], "left"))
                col += 1
            rendered_rows.append(cells)

        multiline = any(len(lines) > 1 for cells in rendered_rows for lines, _, _ in cells)

        out: List[Line] = []
        if t.title:
            self.counters["table"] += 1
            caption = f"{self.doc.attrs.get('table-caption', 'Table')} {self.counters['table']}. "
            out += self.title_lines(t.title, w, caption)
        out.append(border("┌", "┬", "┐", "─"))
        for r, cells in enumerate(rendered_rows):
            height = max(len(lines) for lines, _, _ in cells)
            for k in range(height):
                segs: List[Seg] = [("│", bs)]
                for lines, cw, align in cells:
                    piece = lines[k] if k < len(lines) else []
                    segs += [(" ", PLAIN)] + pad(piece, cw, align) + [(" ", PLAIN), ("│", bs)]
                out.append(Line(segs))
            last = r == len(rendered_rows) - 1
            if last:
                break
            if (t.header and r == 0) or (t.footer and r == len(rendered_rows) - 2):
                out.append(border("╞", "╪", "╡", "═"))
            elif multiline:
                out.append(border("├", "┼", "┤", "─"))
        out.append(border("└", "┴", "┘", "─"))
        return out


def _distribute(nat: List[int], mins: List[int], avail: int) -> List[int]:
    if sum(nat) <= avail:
        return list(nat)
    mins = [min(m, n) for m, n in zip(mins, nat)]
    if sum(mins) >= avail:
        total = sum(mins)
        return [max(1, m * avail // total) for m in mins]
    extra = avail - sum(mins)
    flex = [n - m for n, m in zip(nat, mins)]
    total_flex = sum(flex)
    widths = [m + f * extra // total_flex for m, f in zip(mins, flex)]
    left = avail - sum(widths)
    order = sorted(range(len(widths)), key=lambda k: -flex[k])
    k = 0
    while left > 0:
        widths[order[k % len(order)]] += 1
        left -= 1
        k += 1
    return widths


def render(doc: Document, width: int, show_urls: bool = False) -> Tuple[List[Line], "Renderer"]:
    r = Renderer(doc, width, show_urls=show_urls)
    return r.render(), r
