"""Reads a document from disk (or stdin) and runs preprocessing + parsing."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .parser import Document, parse
from .preprocess import preprocess


class Source:
    def __init__(self, path: Optional[str] = None, text: Optional[str] = None,
                 attrs: Optional[Dict[str, str]] = None):
        self.path = os.path.abspath(path) if path else None
        self._text = text
        self.attrs = attrs or {}
        self.files: List[str] = []

    @property
    def name(self) -> str:
        return os.path.basename(self.path) if self.path else "<stdin>"

    @property
    def base_dir(self) -> str:
        return os.path.dirname(self.path) if self.path else os.getcwd()

    def read(self) -> str:
        if self.path is None:
            return self._text or ""
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def load(self) -> Document:
        text = self.read()
        attrs = dict(self.attrs)
        if self.path:
            attrs.setdefault("docname", os.path.splitext(self.name)[0])
            attrs.setdefault("docfile", self.path)
            attrs.setdefault("docdir", self.base_dir)
        lines, included = preprocess(text, self.base_dir, attrs)
        self.files = ([self.path] if self.path else []) + included
        return parse(lines, attrs)

    def mtimes(self) -> Dict[str, float]:
        stamps = {}
        for f in self.files:
            try:
                stamps[f] = os.stat(f).st_mtime_ns
            except OSError:
                stamps[f] = -1
        return stamps
