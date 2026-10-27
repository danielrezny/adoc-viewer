"""Colors.  Only the 16 standard ANSI colors are used so the viewer follows
the terminal's own palette and stays readable on light and dark backgrounds."""

from .style import Style

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
GRAY, BRIGHT_RED, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE = 8, 9, 10, 11, 12

TITLE = Style(fg=MAGENTA, bold=True)
TITLE_RULE = Style(fg=MAGENTA)
HEADINGS = [
    TITLE,
    Style(fg=CYAN, bold=True),
    Style(fg=BRIGHT_BLUE, bold=True),
    Style(fg=GREEN, bold=True),
    Style(fg=YELLOW, bold=True),
    Style(bold=True),
]
SECTION_NUMBER = Style(fg=GRAY)
H1_RULE = Style(fg=GRAY)

DIM = Style(fg=GRAY)
DIM_ITALIC = Style(fg=GRAY, italic=True)
BOLD = Style(bold=True)
ITALIC = Style(italic=True)
BLOCK_TITLE = Style(bold=True, italic=True)

STRONG = Style(bold=True)
EMPHASIS = Style(italic=True)
MONO = Style(fg=YELLOW)
MARK = Style(fg=BLACK, bg=YELLOW)
LINK = Style(fg=BRIGHT_BLUE, underline=True)
XREF = Style(fg=BRIGHT_BLUE, underline=True)
FOOTNOTE_REF = Style(fg=CYAN)
KBD = Style(bold=True, fg=CYAN)
MENU = Style(bold=True)
INLINE_IMAGE = Style(fg=GRAY, italic=True)

BULLET = Style(fg=CYAN)
LIST_NUMBER = Style(fg=CYAN)
CHECK_DONE = Style(fg=GREEN, bold=True)
CHECK_TODO = Style(fg=GRAY)
DL_TERM = Style(bold=True)
CALLOUT = Style(fg=CYAN, bold=True)

CODE_BORDER = Style(fg=GRAY)
CODE_LABEL = Style(fg=GRAY, italic=True)
CODE_TEXT = Style()
LITERAL_TEXT = Style()
LINE_NUMBER = Style(fg=GRAY)

QUOTE_BAR = Style(fg=GRAY)
QUOTE_TEXT = Style(italic=True)
BOX_BORDER = Style(fg=GRAY)
EXAMPLE_BORDER = Style(fg=CYAN)

TABLE_BORDER = Style(fg=GRAY)
TABLE_HEADER = Style(bold=True)

ADMONITION = {
    "NOTE": Style(fg=BRIGHT_BLUE, bold=True),
    "TIP": Style(fg=GREEN, bold=True),
    "IMPORTANT": Style(fg=RED, bold=True),
    "WARNING": Style(fg=YELLOW, bold=True),
    "CAUTION": Style(fg=MAGENTA, bold=True),
}
ADMONITION_ICON = {
    "NOTE": "ℹ",       # ℹ
    "TIP": "✦",        # ✦
    "IMPORTANT": "!",
    "WARNING": "⚠",    # ⚠
    "CAUTION": "☠",    # ☠
}

# Syntax highlighting (Pygments token type name -> style); see highlight.py.
SYNTAX = {
    "Comment": Style(fg=GRAY, italic=True),
    "Keyword": Style(fg=MAGENTA),
    "Keyword.Constant": Style(fg=YELLOW),
    "Keyword.Type": Style(fg=CYAN),
    "Name.Builtin": Style(fg=CYAN),
    "Name.Function": Style(fg=BRIGHT_BLUE),
    "Name.Class": Style(fg=YELLOW, bold=True),
    "Name.Decorator": Style(fg=CYAN),
    "Name.Tag": Style(fg=BRIGHT_BLUE),
    "Name.Attribute": Style(fg=YELLOW),
    "Name.Namespace": Style(fg=YELLOW),
    "Name.Exception": Style(fg=RED),
    "Literal.String": Style(fg=GREEN),
    "Literal.String.Escape": Style(fg=CYAN),
    "Literal.String.Interpol": Style(fg=CYAN),
    "Literal.Number": Style(fg=YELLOW),
    "Literal": Style(fg=YELLOW),
    "Operator.Word": Style(fg=MAGENTA),
    "Operator": Style(fg=CYAN),
    "Generic.Inserted": Style(fg=GREEN),
    "Generic.Deleted": Style(fg=RED),
    "Generic.Heading": Style(bold=True),
    "Generic.Subheading": Style(fg=CYAN),
    "Generic.Prompt": Style(fg=GRAY, bold=True),
    "Generic.Emph": Style(italic=True),
    "Generic.Strong": Style(bold=True),
}

# TUI chrome
STATUS = Style(reverse=True)
STATUS_NAME = Style(reverse=True, bold=True)
SEARCH_MATCH = Style(fg=BLACK, bg=YELLOW)
SEARCH_CURRENT = Style(fg=BLACK, bg=BRIGHT_YELLOW, bold=True)
TOC_CURRENT = Style(fg=CYAN, bold=True)
TOC_SELECTED = Style(reverse=True)
SELECTION = Style(reverse=True)
TOC_TEXT = Style()
TOC_BORDER = Style(fg=GRAY)
