import random

import pytest

from adocview.inline import parse_inline, plain_text
from adocview.parser import (Container, Heading, Listing, ListBlock, Paragraph, Table, parse)
from adocview.preprocess import preprocess
from adocview.render import render
from adocview.style import PLAIN, dw, wrap
from adocview.tui import parse_keys
from adocview import theme


def doc_of(text, **attrs):
    return parse(text.splitlines(), attrs)


def rendered(text, width=60):
    lines, _ = render(doc_of(text), width)
    return [l.text.rstrip() for l in lines]


# -- parser -------------------------------------------------------------------

def test_header_and_attributes():
    doc = doc_of("= Title\nJane Doe <jane@x.org>\nv1.0\n:product: Thing\n\nUse {product}.")
    assert doc.title.text == "Title"
    assert doc.author == "Jane Doe"
    assert doc.revision == "v1.0"
    assert doc.blocks[0].text == "Use Thing."


def test_sections_ids_and_numbers():
    doc = doc_of(":sectnums:\n\n== One\n\n=== Sub\n\n== Two")
    hs = [b for b in doc.blocks if isinstance(b, Heading)]
    assert [(h.level, h.number, h.id) for h in hs] == [
        (1, "1.", "_one"), (2, "1.1.", "_sub"), (1, "2.", "_two")]


def test_separate_lists_stay_separate():
    doc = doc_of("* a\n* b\n\n. one\n. two")
    assert [type(b) for b in doc.blocks] == [ListBlock, ListBlock]
    assert doc.blocks[0].kind == "ul" and doc.blocks[1].kind == "ol"


def test_nested_list_and_continuation():
    doc = doc_of("* a\n** a1\n* b\n+\n----\ncode\n----")
    lst = doc.blocks[0]
    assert len(lst.items) == 2
    assert isinstance(lst.items[0].blocks[0], ListBlock)
    assert isinstance(lst.items[1].blocks[0], Listing)


def test_description_list():
    doc = doc_of("CPU:: brain\nRAM::\nmemory")
    lst = doc.blocks[0]
    assert lst.kind == "dl"
    assert [(i.term, i.text) for i in lst.items] == [("CPU", "brain"), ("RAM", "memory")]


def test_checklist():
    doc = doc_of("* [x] done\n* [ ] todo")
    assert [i.checked for i in doc.blocks[0].items] == [True, False]


def test_source_block_language_and_title():
    doc = doc_of("[source,python]\n.hello.py\n----\nprint(1)\n----")
    b = doc.blocks[0]
    assert isinstance(b, Listing) and b.lang == "python" and b.title == "hello.py"


def test_admonitions():
    doc = doc_of("NOTE: inline\n\n[WARNING]\n====\nblock\n====")
    assert [(b.kind, b.label) for b in doc.blocks] == [
        ("admonition", "NOTE"), ("admonition", "WARNING")]


def test_table_with_quoted_cols_and_header():
    doc = doc_of('[cols="1,2",options="header"]\n|===\n|A |B\n|1 |2\n|===')
    t = doc.blocks[0]
    assert isinstance(t, Table) and len(t.cols) == 2 and t.header
    assert [[c.text for c in r] for r in t.rows] == [["A", "B"], ["1", "2"]]


def test_table_implicit_header_and_colspan():
    doc = doc_of("|===\n|A |B |C\n\n2+|wide |x\n|===")
    t = doc.blocks[0]
    assert t.header
    assert [c.span for c in t.rows[1]] == [2, 1]


def test_paragraph_breaks_at_block_attribute_line():
    doc = doc_of("text\n[source,sh]\n----\nls\n----")
    assert isinstance(doc.blocks[0], Paragraph)
    assert isinstance(doc.blocks[1], Listing) and doc.blocks[1].lang == "sh"


def test_comments_are_dropped():
    doc = doc_of("// line\n////\nblock\n////\nvisible")
    assert [b.text for b in doc.blocks] == ["visible"]


# -- preprocessor ---------------------------------------------------------------

def test_conditionals():
    lines, _ = preprocess(":a:\nifdef::a[]\nyes\nendif::[]\nifndef::a[]\nno\nendif::[]\nifdef::b[x]", ".")
    assert lines == [":a:", "yes"]


def test_include_with_tags(tmp_path):
    (tmp_path / "code.py").write_text("# tag::t[]\nkept\n# end::t[]\ndropped\n")
    (tmp_path / "main.adoc").write_text("include::code.py[tag=t]\n")
    lines, files = preprocess((tmp_path / "main.adoc").read_text(), str(tmp_path))
    assert lines == ["kept"]
    assert files == [str(tmp_path / "code.py")]


def test_missing_include_is_reported():
    lines, _ = preprocess("include::nope.adoc[]", ".")
    assert "Unresolved" in lines[0]


# -- inline -----------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("*bold* and _it_", "bold and it"),
    ("**un**constrained", "unconstrained"),
    ("snake_case_name", "snake_case_name"),
    ("a * b * c", "a * b * c"),
    ("H~2~O E=mc^2^", "H₂O E=mc²"),
    ("(C) -- ...", "© — …"),
    ("it's", "it’s"),
    ("`*literal*`", "*literal*"),
    ("https://example.com.", "https://example.com."),
    ("https://example.com[Example]", "Example"),
    ("kbd:[Ctrl+C]", "Ctrl+C"),
    ("\\*not bold*", "*not bold*"),
])
def test_inline_text(src, expected):
    assert plain_text(src) == expected


def test_inline_styles():
    segs = parse_inline("*b* `m` https://x.io")
    styles = {t: s for t, s in segs}
    assert styles["b"].bold
    assert styles["m"] == theme.MONO
    assert styles["https://x.io"].link == "https://x.io"


# -- rendering ----------------------------------------------------------------------

def test_wrap_respects_width_and_glues_punctuation():
    segs = parse_inline("word " * 30 + "**end**.")
    lines = wrap(segs, 20)
    assert all(sum(dw(t) for t, _ in l) <= 20 for l in lines)
    assert "".join(t for t, _ in lines[-1]).endswith("end.")


def test_wide_characters():
    lines = wrap([("漢字" * 10, PLAIN)], 9)
    assert all(sum(dw(t) for t, _ in l) <= 9 for l in lines)


def test_render_never_exceeds_width():
    src = open("examples/showcase.adoc").read()
    for width in (20, 33, 60, 100):
        lines, _ = render(parse(preprocess(src, "examples")[0]), width)
        too_wide = [l.text for l in lines if dw(l.text) > width]
        assert not too_wide, (width, too_wide[:3])


def test_toc_entries_point_at_heading_lines():
    lines, r = render(doc_of("= T\n\n== A\n\ntext\n\n== B"), 40)
    assert [(h.text, lines[i].text) for h, i in r.toc] == [("T", "T"), ("A", "A"), ("B", "B")]


def test_xref_uses_section_title():
    out = rendered("== Install Guide\n\nSee <<_install_guide>>.")
    assert "See Install Guide." in out


def test_footnotes_collected():
    out = rendered("Text.footnote:[A note.]")
    assert out[0] == "Text.[1]"
    assert out[-1] == "[1] A note."


def test_fuzz_does_not_crash():
    pieces = ["*", "_", "`", "#", "+", "|===", "----", "====", "* item", ". item", "term::",
              "[source]", ".Title", "<<x>>", "footnote:[", "]", "\n", "\n\n", "text", "|a |b",
              "NOTE: n", "++++", "[[id]]", "include::x[]", "ifdef::a[]", "endif::[]", "{attr}",
              "image::a.png[]", "--", "____", "kbd:[", "https://x", "\t", "漢"]
    rng = random.Random(1)
    for _ in range(300):
        src = "".join(rng.choice(pieces) + rng.choice(["", " ", "\n"]) for _ in range(40))
        lines, _ = preprocess(src, ".")
        render(parse(lines), rng.randint(20, 120))


# -- tui ----------------------------------------------------------------------------

def test_parse_keys():
    assert parse_keys("j\x1b[A\x1b[6~\x1b[<64;3;4M\x1bq\r") == [
        "j", "up", "pgdn", ("mouse", 64, 3, 4, True), "esc", "q", "enter"]


def test_status_placeholders_become_emoji():
    doc = doc_of(":no: nope\n\n{yes} {partial} {no}")
    assert doc.blocks[0].text == "✅ 🟡 nope"


def test_font_size_steps_and_restore():
    from adocview import fontsize

    class Fake(fontsize.FontSizer):
        size = 13.0

        def get(self):
            return self.size

        def set(self, size):
            self.size = size
            return size

    f = Fake()
    assert [f.change(1), f.change(1), f.change(-1)] == [14, 15, 14]
    assert f.change(0) == 13
    f.change(1)
    f.restore()
    assert f.size == 13
    assert fontsize.step(72, 1) == 72 and fontsize.step(8, -1) == 8
    assert fontsize.step(13.5, 1) == 14


def test_selection_text_strips_frames_and_gutters():
    from adocview.tui import selection_text
    lines, _ = render(doc_of("[source,python]\n----\ndef f():\n\n    return 1\n----"), 40)
    assert selection_text([l.text for l in lines]) == "def f():\n\n    return 1\n"
    assert selection_text(["┃ ℹ NOTE", "┃ careful"]) == "ℹ NOTE\ncareful\n"
    assert selection_text(["  just text  "]) == "just text"


def test_table_cell_text_that_looks_like_a_spec():
    doc = doc_of("|===\n|a|b\n|===")
    assert [c.text for c in doc.blocks[0].rows[0]] == ["a", "b"]


def test_search_box_live_search_and_navigation():
    from adocview.loader import Source
    from adocview.tui import Viewer
    v = Viewer(Source(text="\n\n".join(f"para {i} needle" for i in range(60))))
    v.load(initial=True)
    v.layout()
    v.handle("/")
    for ch in "need":
        v.handle(ch)
    assert v.search_input == "need" and len(v.matches) == 60 and v.match_idx == 0
    v.handle("enter")
    v.handle("enter")
    v.handle("up")
    assert v.match_idx == 1
    v.handle("x")
    assert v.matches == [] and "no matches" in v.search_box()
    v.handle("backspace")
    v.handle("esc")
    assert v.search_input is None and v.query == "need"
    v.handle("n")
    assert v.match_idx == 1
    v.handle("/")
    v.handle("p")  # typing replaces the remembered query
    assert v.search_input == "p"


def test_listing_subs_attributes():
    doc = doc_of(":repo: https://x.org/r\n\n[source,sh,subs=attributes]\n----\ngit clone {repo}\n----"
                 "\n\n----\nkeep {repo}\n----")
    assert doc.blocks[0].lines == ["git clone https://x.org/r"]
    assert doc.blocks[1].lines == ["keep {repo}"]
