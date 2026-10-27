"""Line preprocessor: ``include::`` directives and conditional blocks.

Runs before block parsing, the same way Asciidoctor's reader does.  It keeps
track of attribute entries it passes so ``ifdef::`` sees attributes defined
earlier in the document.
"""

from __future__ import annotations

import os
import re
import textwrap
from typing import Dict, List, Optional, Set

from .attrlist import parse_attrlist

INCLUDE_RX = re.compile(r"^(\\?)include::([^\[\s][^\[]*)\[(.*)\]\s*$")
COND_RX = re.compile(r"^(\\?)(ifdef|ifndef)::([^\[]*)\[(.*)\]\s*$")
IFEVAL_RX = re.compile(r"^(\\?)ifeval::\[(.*)\]\s*$")
ENDIF_RX = re.compile(r"^(\\?)endif::([^\[]*)\[\]\s*$")
ATTR_RX = re.compile(r"^:(!?)(\w[\w-]*)(!?):(?:[ \t]+(.*))?$")
TAG_RX = re.compile(r"\b(tag|end)::([\w-]+)\[\]")
ATTR_REF_RX = re.compile(r"\{(\w[\w-]*)\}")
EVAL_RX = re.compile(r"^(.*?)\s*(==|!=|<=|>=|<|>)\s*(.*)$")

MAX_DEPTH = 16


class Preprocessor:
    def __init__(self, attrs: Dict[str, str]):
        self.attrs = dict(attrs)
        self.files: List[str] = []

    def subst(self, text: str) -> str:
        return ATTR_REF_RX.sub(lambda m: self.attrs.get(m.group(1), m.group(0)), text)

    def process(self, lines: List[str], base_dir: str, depth: int = 0) -> List[str]:
        out: List[str] = []
        stack: List[bool] = []  # one entry per open conditional; True = lines are kept
        for line in lines:
            active = all(stack)

            m = COND_RX.match(line)
            if m and not m.group(1):
                names, content = m.group(3), m.group(4)
                if "+" in names:
                    defined = all(n in self.attrs for n in names.split("+") if n)
                else:
                    defined = any(n in self.attrs for n in names.split(",") if n)
                keep = defined if m.group(2) == "ifdef" else not defined
                if content:
                    if active and keep:
                        out.append(content)
                else:
                    stack.append(active and keep)
                continue
            m = IFEVAL_RX.match(line)
            if m and not m.group(1):
                stack.append(active and self._eval(m.group(2)))
                continue
            m = ENDIF_RX.match(line)
            if m and not m.group(1):
                if stack:
                    stack.pop()
                continue
            if not active:
                continue

            m = ATTR_RX.match(line)
            if m:
                name = m.group(2)
                if m.group(1) or m.group(3):
                    self.attrs.pop(name, None)
                else:
                    self.attrs[name] = self.subst(m.group(4) or "")
                out.append(line)
                continue

            m = INCLUDE_RX.match(line)
            if m:
                if m.group(1):
                    out.append(line[1:])
                else:
                    out.extend(self._include(m.group(2), m.group(3), base_dir, depth, line))
                continue

            if line.startswith("\\") and (COND_RX.match(line[1:]) or ENDIF_RX.match(line[1:])
                                          or IFEVAL_RX.match(line[1:])):
                line = line[1:]
            out.append(line)
        return out

    # ------------------------------------------------------------------

    def _eval(self, expr: str) -> bool:
        m = EVAL_RX.match(self.subst(expr).strip())
        if not m:
            return True
        lhs, op, rhs = (s.strip().strip("\"'") for s in m.groups())
        try:
            a, b = float(lhs), float(rhs)
        except ValueError:
            a, b = lhs, rhs  # type: ignore[assignment]
        try:
            return {
                "==": a == b, "!=": a != b, "<": a < b,
                ">": a > b, "<=": a <= b, ">=": a >= b,
            }[op]
        except TypeError:
            return True

    def _include(self, target: str, attrlist: str, base_dir: str, depth: int,
                 original: str) -> List[str]:
        target = self.subst(target.strip())
        if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I):
            return [f"link:{target}[]"]
        path = os.path.normpath(os.path.join(base_dir, os.path.expanduser(target)))
        if depth >= MAX_DEPTH:
            return [original]
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return [f"Unresolved directive - include::{target}[{attrlist}]"]
        if path not in self.files:
            self.files.append(path)

        attrs = parse_attrlist(attrlist) if attrlist.strip() else None
        named = attrs.named if attrs else {}
        if "lines" in named:
            lines = _select_lines(lines, named["lines"])
        elif "tag" in named or "tags" in named:
            lines = _select_tags(lines, named.get("tags") or named.get("tag", ""))
        if "indent" in named:
            try:
                indent = int(named["indent"])
            except ValueError:
                indent = 0
            lines = textwrap.indent(textwrap.dedent("\n".join(lines)), " " * indent).split("\n")

        lines = self.process(lines, os.path.dirname(path), depth + 1)

        offset = named.get("leveloffset")
        if offset:
            if offset[0] in "+-":
                undo = ("-" if offset[0] == "+" else "+") + offset[1:]
            else:
                undo = self.attrs.get("leveloffset", "0")
            lines = ["", f":leveloffset: {offset}", ""] + lines + ["", f":leveloffset: {undo}", ""]
        return lines


def _select_lines(lines: List[str], spec: str) -> List[str]:
    picked: List[str] = []
    for part in re.split(r"[;,]", spec.strip("\"'")):
        part = part.strip()
        if not part:
            continue
        if ".." in part:
            a, b = part.split("..", 1)
            start = int(a) if a else 1
            end = len(lines) if b in ("", "-1") else int(b)
        else:
            start = end = int(part)
        picked.extend(lines[max(start - 1, 0):end])
    return picked


def _select_tags(lines: List[str], spec: str) -> List[str]:
    wanted: Set[str] = set()
    everything = False
    excluded: Set[str] = set()
    for name in re.split(r"[;,]", spec):
        name = name.strip()
        if name in ("*", "**"):
            everything = True
        elif name.startswith("!"):
            excluded.add(name[1:])
        elif name:
            wanted.add(name)
    open_tags: List[str] = []
    out: List[str] = []
    for line in lines:
        m = TAG_RX.search(line)
        if m:
            kind, name = m.groups()
            if kind == "tag":
                open_tags.append(name)
            elif name in open_tags:
                open_tags.remove(name)
            continue
        if any(t in excluded for t in open_tags):
            continue
        if everything or any(t in wanted for t in open_tags):
            out.append(line)
    return out


def preprocess(text: str, base_dir: str, attrs: Optional[Dict[str, str]] = None):
    pre = Preprocessor(attrs or {})
    lines = pre.process(text.splitlines(), base_dir)
    return lines, pre.files
