"""End-to-end ingestion through Docling, one invented mini-spec written in each format.

The PDF case needs Docling's layout model weights. The first run downloads them to the
Hugging Face cache, and later runs are offline.
"""

from pathlib import Path

import docx
import openpyxl
import pytest
from fpdf import FPDF

from spec2test.ingest import UnsupportedFormatError, doc_id_for, ingest

TITLE = "Widget Lamp Specification"
INTRO = "This specification is invented for the Spec2Test ingestion tests."
# (clause number, heading or first sentence, body); one page each in the PDF.
CLAUSES = [
    [
        ("1.", "SCOPE", None),
        ("1.1", "Purpose.", "This document specifies requirements for a widget lamp."),
    ],
    [
        ("2.", "REQUIREMENTS", None),
        ("2.1", "Brightness.", "The lamp shall emit at least 400 lm within 2.0 s of switch-on."),
        ("2.2", "Label.", "The housing shall carry a voltage label."),
    ],
    [
        ("3.", "TESTS", None),
        ("3.1", "Warm-up.", "The lamp shall be tested at an ambient temperature of 23 C."),
    ],
]
EXPECTED = ["1", "1.1", "2", "2.1", "2.2", "3", "3.1"]
FOOTER = "Widget Lamp Specification - invented test fixture"


def write_markdown(path: Path) -> None:
    lines = [f"# {TITLE}", "", INTRO, ""]
    for page in CLAUSES:
        for number, head, body in page:
            if body is None:
                lines += [f"## {number} {head}", ""]
            else:
                lines += [f"{number} {head} {body}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_docx(path: Path) -> None:
    d = docx.Document()
    d.add_heading(TITLE, level=0)
    d.add_paragraph(INTRO)
    for page in CLAUSES:
        for number, head, body in page:
            if body is None:
                d.add_heading(f"{number} {head}", level=1)
            else:
                d.add_paragraph(f"{number} {head} {body}")
    d.save(path)


def write_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Requirements"
    ws.append(["ID", "Requirement"])
    for page in CLAUSES:
        for number, head, body in page:
            ws.append([number.rstrip("."), head if body is None else f"{head} {body}"])
    wb.save(path)


def write_pdf(path: Path) -> None:
    pdf = FPDF(format="letter")
    pdf.set_auto_page_break(auto=False)
    for i, page in enumerate(CLAUSES):
        pdf.add_page()
        if i == 0:
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(0, 10, TITLE, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, INTRO, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        for number, head, body in page:
            if body is None:
                pdf.set_font("Helvetica", "B", 14)
                pdf.multi_cell(0, 9, f"{number} {head}", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_font("Helvetica", "", 11)
                pdf.multi_cell(0, 6, f"{number} {head} {body}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_y(-18)
        pdf.cell(0, 5, FOOTER, align="C")
    pdf.output(str(path))


WRITERS = {"md": write_markdown, "docx": write_docx, "xlsx": write_xlsx, "pdf": write_pdf}


@pytest.fixture(scope="session")
def ingested(tmp_path_factory):
    """Ingest each format once; Docling conversions are the slow part of the suite."""
    folder = tmp_path_factory.mktemp("docs")
    cache = {}

    def get(fmt):
        if fmt not in cache:
            path = folder / f"Widget Lamp Spec.{fmt}"
            WRITERS[fmt](path)
            cache[fmt] = ingest(path)
        return cache[fmt]

    return get


@pytest.mark.parametrize("fmt", WRITERS)
def test_every_format_yields_the_same_clauses(ingested, fmt):
    doc = ingested(fmt)
    numbered = [s for s in doc.sections if s.section_number is not None]
    assert [s.anchor for s in numbered] == EXPECTED
    assert doc.format == fmt
    assert doc.doc_id == "widget-lamp-spec"


@pytest.mark.parametrize("fmt", WRITERS)
def test_clause_structure_is_preserved(ingested, fmt):
    doc = ingested(fmt)
    s21 = doc.section("2.1")
    assert s21.title == "Brightness"
    assert s21.level == 2
    assert doc.sections[s21.parent].anchor == "2"
    assert "at least 400 lm within 2.0 s" in s21.text
    assert doc.section("2").title == "REQUIREMENTS"


@pytest.mark.parametrize("fmt", WRITERS)
def test_offsets_point_into_the_document_text(ingested, fmt):
    doc = ingested(fmt)
    for s in doc.sections:
        assert doc.text[s.start : s.end] == s.text
        assert doc.page_at(s.start) == s.page
    quote = "The housing shall carry a voltage label."
    start = doc.text.index(quote)
    section = doc.section("2.2")
    assert section.start <= start < start + len(quote) <= section.end


def test_pdf_sections_carry_their_page(ingested):
    doc = ingested("pdf")
    assert [p.page for p in doc.pages] == [1, 2, 3]
    assert [doc.section(a).page for a in ("1.1", "2.1", "2.2", "3.1")] == [1, 2, 2, 3]
    assert FOOTER not in doc.text


@pytest.mark.parametrize("fmt", ["md", "docx"])
def test_formats_without_pages_report_page_one(ingested, fmt):
    doc = ingested(fmt)
    assert {s.page for s in doc.sections} == {1}


def test_title_and_intro_are_front_matter(ingested):
    doc = ingested("md")
    front = doc.sections[0]
    assert front.section_number is None
    assert front.title == TITLE
    assert INTRO in front.text


def test_unsupported_format(tmp_path):
    path = tmp_path / "notes.rtf"
    path.write_text("1.1 Text.", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError, match=r"\.rtf"):
        ingest(path)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest(tmp_path / "absent.pdf")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("FMVSS 111.pdf", "fmvss-111"), ("UN_R158e (1).PDF", "un-r158e-1"), ("a.md", "a")],
)
def test_doc_id_for(name, expected):
    assert doc_id_for(name) == expected
