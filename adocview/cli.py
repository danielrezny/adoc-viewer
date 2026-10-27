"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import __version__
from .loader import Source
from .render import render
from .style import to_ansi


def _attr(value: str):
    name, _, val = value.partition("=")
    return name.strip(), val


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="adoc",
        description="View AsciiDoc documents in the terminal.",
        epilog="Press ? inside the viewer for key bindings.",
    )
    ap.add_argument("file", nargs="?", help="AsciiDoc file to view ('-' or omitted reads stdin)")
    ap.add_argument("-p", "--print", action="store_true",
                    help="print the rendered document to stdout instead of opening the viewer")
    ap.add_argument("-w", "--width", type=int, default=0,
                    help="maximum text width in columns (default: 0, the whole window)")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="when to use colors in --print mode (default: auto)")
    ap.add_argument("-a", "--attribute", action="append", default=[], metavar="NAME[=VALUE]",
                    help="set a document attribute (repeatable)")
    ap.add_argument("--no-mouse", action="store_true",
                    help="leave the mouse to the terminal: plain drag selects text, "
                         "but the wheel may not scroll (toggle with m)")
    ap.add_argument("-f", "--font-size", type=float, metavar="PT",
                    help="font size while viewing, restored on exit (Apple Terminal)")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    attrs = dict(_attr(a) for a in args.attribute)

    if args.file and args.file != "-":
        if not os.path.isfile(args.file):
            print(f"adoc: {args.file}: no such file", file=sys.stderr)
            return 1
        source = Source(path=args.file, attrs=attrs)
    elif not sys.stdin.isatty():
        source = Source(text=sys.stdin.read(), attrs=attrs)
    else:
        build_argparser().print_usage(sys.stderr)
        return 2

    interactive = sys.stdout.isatty() and not args.print
    if not interactive:
        return print_document(source, args)

    from .tui import Viewer
    try:
        Viewer(source, max_width=args.width, mouse=not args.no_mouse,
               font_size=args.font_size).run()
    except KeyboardInterrupt:
        pass
    return 0


def print_document(source: Source, args) -> int:
    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    cols = shutil.get_terminal_size((80, 24)).columns if sys.stdout.isatty() else 80
    width = max(20, min(args.width, cols) if args.width > 0 else cols)
    doc = source.load()
    lines, _ = render(doc, width, show_urls=not color)
    rendered = (to_ansi(line.segs, color=color) for line in lines)
    out = "\n".join(s if color else s.rstrip() for s in rendered)
    try:
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
