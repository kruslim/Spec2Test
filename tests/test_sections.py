"""Clause splitting on plain blocks. No Docling, so these run in milliseconds.

The block texts imitate each corpus's drafting style; they are written for these tests.
"""

import pytest
from pydantic import ValidationError

from spec2test.ingest import Block, Document, build_document


def H(text, page=1, margin=False):
    return Block("heading", text, page, margin)


def T(text, page=1, margin=False):
    return Block("text", text, page, margin)


def L(text, page=1):
    return Block("list", text, page)


def build(*blocks, **kwargs):
    return build_document("spec", blocks, **kwargs)


def anchors(doc):
    return [s.anchor for s in doc.sections]


def parent_of(doc, anchor):
    parent = doc.section(anchor).parent
    return None if parent is None else doc.sections[parent].anchor


def test_fmvss_style_clauses():
    doc = build(
        T("S5. Requirements for passenger cars."),
        T("S5.1 Inside rearview mirror. Each passenger car shall have an inside mirror."),
        T("S5.1.1 Field of view. The mirror shall provide a field of view of 20 degrees."),
        T("(a) A minimum of a 150-mm wide portion of each test object; and"),
        T("S5.2 Outside rearview mirror."),
    )
    assert anchors(doc) == ["S5", "S5.1", "S5.1.1", "S5.2"]
    assert [s.level for s in doc.sections] == [1, 2, 3, 2]
    assert [s.title for s in doc.sections] == [
        "Requirements for passenger cars",
        "Inside rearview mirror",
        "Field of view",
        "Outside rearview mirror",
    ]
    assert parent_of(doc, "S5.1.1") == "S5.1"
    assert parent_of(doc, "S5.2") == "S5"
    assert doc.section("S5.1.1").text.endswith("each test object; and")


def test_unece_style_clauses_drop_the_trailing_dot():
    doc = build(
        T("5. SPECIFICATIONS"),
        T("5.1. The device shall be designed to operate in reverse gear."),
        T("5.1.1. The image shall be visible within 2.0 s."),
        T('2.1. "Vehicle" means a motor vehicle of category M or N.'),
    )
    assert anchors(doc) == ["5", "5.1", "5.1.1", "2.1"]
    assert doc.section("5").title == "SPECIFICATIONS"
    # A sentence with "shall" is not a title.
    assert doc.section("5.1").title is None
    assert doc.section("2.1").title is None
    assert parent_of(doc, "5.1.1") == "5.1"


def test_nasa_style_clauses_and_lettered_items():
    doc = build(
        H("4. REQUIREMENTS"),
        H("4.2 Design and Test Factors of Safety"),
        L("a. [FSR 18] The design factors shall be as specified in Table 1."),
        T("4.5.1 [FSR 52] All structural items shall be evaluated for buckling."),
    )
    assert anchors(doc) == ["4", "4.2", "4.5.1"]
    assert doc.section("4.2").title == "Design and Test Factors of Safety"
    # The lettered item stays in its clause, as its own paragraph.
    assert doc.section("4.2").text == (
        "4.2 Design and Test Factors of Safety\n\n"
        "a. [FSR 18] The design factors shall be as specified in Table 1."
    )
    assert parent_of(doc, "4.5.1") == "4"  # 4.5 is absent, so the nearest ancestor


def test_fsae_style_lettered_clauses():
    doc = build(
        T("T.1 VEHICLE REQUIREMENTS"),
        T("T.1.2 The vehicle must be open-wheeled."),
        T("EV.5.1 Each accumulator container must be removable."),
    )
    assert anchors(doc) == ["T.1", "T.1.2", "EV.5.1"]
    # The chapter letter is a level of its own: T, then T.1, then T.1.2.
    assert [s.level for s in doc.sections] == [2, 3, 3]
    assert parent_of(doc, "T.1.2") == "T.1"
    assert parent_of(doc, "EV.5.1") is None


def test_sub_clause_attached_to_its_number():
    doc = build(T("S9. Requirements."), T("S9.3(a) For each cylinder, the image shall be visible."))
    assert anchors(doc) == ["S9", "S9.3(a)"]
    assert doc.section("S9.3(a)").level == 3
    assert parent_of(doc, "S9.3(a)") == "S9"


@pytest.mark.parametrize(
    "text",
    [
        "2.0 seconds after the start of a backing event.",  # a value, not a clause
        "1990 Amendments apply to vehicles built after that year.",  # a year
        "1. The first enumerated item inside a clause.",  # a bare list number
        "4.8 mm nor more than 6.4 mm high.",
        "(b) Each individual test object shall subtend 3 minutes of arc.",
    ],
)
def test_text_that_only_looks_numbered_stays_in_its_clause(text):
    doc = build(T("S5.5.3 Response time. The image shall be displayed."), T(text))
    assert anchors(doc) == ["S5.5.3"]
    assert doc.sections[0].text.endswith(text)


def test_bare_number_heading_is_a_clause():
    doc = build(H("1 Scope"), T("This standard applies to widgets."))
    assert anchors(doc) == ["1"]
    assert doc.sections[0].title == "Scope"


def test_annex_restarts_numbering():
    doc = build(
        T("2.1. The body's definition clause."),
        T("6.1. The device shall work."),
        H("Annex 3"),
        T("1. GENERAL"),
        T("2.1. The test shall be performed at 20 °C."),
        T("Appendix A - Test procedure"),
    )
    assert anchors(doc) == [
        "2.1",
        "6.1",
        "Annex 3",
        "Annex 3 1",
        "Annex 3 2.1",
        "Annex 3 Appendix A",  # an annex's appendix belongs to it
    ]
    annex_clause = doc.section("Annex 3 2.1")
    assert annex_clause.section_number == "2.1"
    assert annex_clause.annex == "Annex 3"
    assert parent_of(doc, "Annex 3 2.1") == "Annex 3"
    assert doc.section("2.1").annex is None
    appendix = doc.section("Annex 3 Appendix A")
    assert appendix.title == "Test procedure"
    assert doc.sections[appendix.parent].anchor == "Annex 3"


def test_appendix_letter_clause_read_before_its_heading():
    doc = build(
        T("4.1 Body clause."),
        T("A.1 Purpose"),
        H("APPENDIX A"),
        T("B.1 Purpose"),
        H("APPENDIX B"),
        T("B.2 Reference Document"),
    )
    assert anchors(doc) == ["4.1", "A.1", "Appendix A", "B.1", "Appendix B", "Appendix B B.2"]
    assert doc.section("B.1").annex is None


def test_annex_mention_in_running_text_is_not_a_heading():
    long = "Annex 3 " + "describes the test procedure in detail. " * 4
    doc = build(T("6.1. The device shall work."), T(long))
    assert anchors(doc) == ["6.1"]


def test_front_matter_headings_get_sections_until_the_first_clause():
    doc = build(
        H("FOREWORD"),
        T("This standard is published by an invented agency."),
        H("TABLE OF CONTENTS"),
        H("1. SCOPE"),
        T("1.1 Purpose. Text."),
        H("Table 1 - Minimum Design Factors"),
        Block("table", "Approach\tFactor\nPrototype\t1.4"),
    )
    assert anchors(doc) == [None, None, "1", "1.1"]
    assert [s.title for s in doc.sections[:2]] == ["FOREWORD", "TABLE OF CONTENTS"]
    assert [s.level for s in doc.sections[:2]] == [0, 0]
    assert doc.section("1.1").text.endswith("Prototype\t1.4")


def test_text_before_any_heading_is_front_matter():
    doc = build(T("Invented Agency, Department of Examples"), T("S1. Scope. Text."))
    assert anchors(doc) == [None, "S1"]
    assert doc.sections[0].title is None


def test_tables_never_start_a_clause():
    doc = build(T("5.1. The device shall work."), Block("table", "5.2\tThe other device"))
    assert anchors(doc) == ["5.1"]


def test_table_of_contents_lines_are_dropped():
    doc = build(T("4.2 Design factors .............. 16"), T("4.2 Design factors. Text."))
    assert anchors(doc) == ["4.2"]
    assert "......" not in doc.text


def test_repeated_margin_lines_are_dropped():
    blocks = []
    for page in (1, 2, 3):
        blocks += [
            H("NASA-STD-5001B W/CHANGE 3", page, margin=True),
            T(f"{page}.1 Clause on page {page}.", page),
            T(f"VerDate Sep<11>2014 Frm 0054{page} Fmt 8010", page, margin=True),
        ]
    doc = build(*blocks)
    assert anchors(doc) == ["1.1", "2.1", "3.1"]
    assert "NASA-STD" not in doc.text
    assert "VerDate" not in doc.text


def test_short_line_repeated_mid_page_is_dropped():
    blocks = []
    for page in (1, 2, 3):
        blocks += [
            T(f"S{page}.1 Clause one on page {page} has text", page),
            H("49 CFR Ch. V (10-1-25 Edition)", page),  # running head between columns
            T(f"and continues on page {page}.", page),
        ]
    doc = build(*blocks)
    assert anchors(doc) == ["S1.1", "S2.1", "S3.1"]
    assert doc.section("S1.1").text == "S1.1 Clause one on page 1 has text and continues on page 1."


def test_text_repeated_on_two_pages_is_kept():
    blocks = [
        T("S5.5.2 Size.", 1),
        T("(b) Each object shall not be less than 3 minutes of arc.", 1),
        T("S6.2.2 Size.", 2),
        T("(b) Each object shall not be less than 3 minutes of arc.", 2),
    ]
    doc = build(*blocks)
    assert doc.text.count("3 minutes of arc") == 2


def test_sentence_broken_across_pages_is_rejoined():
    doc = build(
        T("S5.3 Passenger side. The mirror shall be in-", page=1),
        T("stalled on the passenger's side. The location shall be", page=2),
        T("those established in S5.1.", page=2),
        T("S5.4 Convex mirror.", page=3),
    )
    s53 = doc.section("S5.3")
    assert s53.text == (
        "S5.3 Passenger side. The mirror shall be installed on the passenger's side. "
        "The location shall be those established in S5.1."
    )
    assert (s53.page, s53.page_end) == (1, 2)
    assert doc.page_at(doc.text.index("stalled")) == 2
    assert doc.page_at(doc.text.index("installed")) == 1


def test_hyphen_before_a_capital_or_enumerator_is_kept():
    doc = build(
        T("S6.1 Each vehicle shall have either-"),
        T("(a) Mirrors that conform to S5.; or"),
        H("4.2 Design"),
        T("a. General text."),
    )
    assert "either-\n\n(a) Mirrors" in doc.text
    assert "4.2 Design\n\na. General" in doc.text


def test_offsets_and_pages_cover_the_whole_text():
    doc = build(
        T("Front matter.", 1),
        T("1.1 First.", 1),
        T("1.2 Second.", 2),
        T("1.3 Third.", 4),
    )
    for s in doc.sections:
        assert doc.text[s.start : s.end] == s.text
    assert [(p.page, p.start, p.end) for p in doc.pages] == [
        (1, 0, doc.text.index("1.2")),
        (2, doc.text.index("1.2"), doc.text.index("1.3")),
        (4, doc.text.index("1.3"), len(doc.text)),
    ]
    assert doc.section("1.3").page == 4
    with pytest.raises(IndexError):
        doc.page_at(len(doc.text))


def test_pages_never_go_backwards():
    doc = build(T("1.1 First.", 2), T("1.2 Read out of order.", 1))
    assert [p.page for p in doc.pages] == [2]
    assert doc.section("1.2").page == 2


def test_end_marker_drops_the_next_document():
    doc = build(
        T("S15.7 Last clause."),
        H("§571.112 [Reserved]"),
        T("S1. Purpose of the next standard."),
        end_marker="§571.112 [Reserved]",
    )
    assert anchors(doc) == ["S15.7"]
    with pytest.raises(ValueError, match="not found"):
        build(T("S1. Text."), end_marker="nowhere")


def test_empty_document_is_an_error():
    with pytest.raises(ValueError, match="no text"):
        build(T("   "))


def test_section_lookup_by_anchor():
    doc = build(T("1.1 First."))
    assert doc.section("1.1").index == 0
    with pytest.raises(KeyError):
        doc.section("9.9")


def test_document_rejects_a_section_that_does_not_match_its_offsets():
    doc = build(T("1.1 First."), T("1.2 Second."))
    data = doc.model_dump()
    data["sections"][1]["text"] = "1.2 Edited."
    with pytest.raises(ValidationError, match="does not match"):
        Document.model_validate(data)


def test_document_round_trips_through_json():
    doc = build(H("1. SCOPE"), T("1.1 Purpose. Text.", 2))
    assert Document.model_validate_json(doc.model_dump_json()) == doc


def test_unece_top_level_clause_written_as_a_list_item():
    # Docling reads UNECE's "1. Scope" as list marker "1." plus text "Scope".
    doc = build(
        L("1. Scope"),
        T("This Regulation applies to:"),
        L("1.1. Approval of devices for reversing safety."),
        L("7. Modification of the type of device for reversing motion and extension of approval"),
    )
    assert anchors(doc) == ["1", "1.1", "7"]
    assert doc.section("1").title == "Scope"
    assert doc.section("7").title.startswith("Modification of the type")


@pytest.mark.parametrize(
    "text",
    [
        "6 July 2021",  # a cover-page date
        "240 MPa (34.8 ksi)",  # a value from a table Docling read as text
        "100 V DC < U < 200 V DC",
        "5 Ohm",
        "1.0 Changes in sections: AD.2, F.4.2",  # a version number
        "12. Approval granted/refused/extended/withdrawn: 2",  # a form field
        "3.1. Location of that marking: ..........................",
    ],
)
def test_numbers_that_are_not_clauses(text):
    doc = build(T("5.1. The device shall work."), T(text))
    assert anchors(doc) == ["5.1"]


def test_bare_number_with_all_caps_heading_is_a_clause():
    doc = build(T("1\tSCOPE"), T("2\tREQUIREMENTS"))  # spreadsheet rows
    assert anchors(doc) == ["1", "2"]


def test_part_division_groups_clauses_without_restarting_numbers():
    doc = build(
        L("1. Scope"),
        H("Part I Devices for Reversing Motion"),
        L("2. Definitions"),
        L("2.1. A definition."),
        H("Part II Installation of devices"),
        L("12. Definitions"),
    )
    assert anchors(doc) == ["1", "Part I", "2", "2.1", "Part II", "12"]
    assert doc.section("Part I").title == "Devices for Reversing Motion"
    assert doc.section("Part I").level == 0
    assert parent_of(doc, "2") == "Part I"
    assert parent_of(doc, "2.1") == "2"
    assert parent_of(doc, "12") == "Part II"
    assert parent_of(doc, "1") is None


def test_running_text_mentioning_a_part_is_not_a_division():
    doc = build(L("1.1. Approval of devices."), T("Part I devices shall be approved first."))
    assert anchors(doc) == ["1.1"]


def test_fsae_chapter_headings():
    doc = build(
        H("GR - GENERAL REGULATIONS"),
        H("GR.1 FORMULA SAE COMPETITION OBJECTIVE"),
        T("GR.1.1 Collegiate Design Series"),
        H("S - STATIC EVENTS"),
        H("S.1 GENERAL STATIC"),
    )
    assert anchors(doc) == ["GR", "GR.1", "GR.1.1", "S", "S.1"]
    assert [s.level for s in doc.sections] == [1, 2, 3, 1, 2]
    assert doc.section("GR").title == "GENERAL REGULATIONS"
    assert parent_of(doc, "GR.1") == "GR"
    # 'S.1' is the static events chapter's clause, not an FMVSS 'S1'.
    assert parent_of(doc, "S.1") == "S"


def test_chapter_pattern_needs_a_heading():
    doc = build(T("F.1.1 The frame must be steel."), T("A - the first option listed"))
    assert anchors(doc) == ["F.1.1"]


def test_annex_title_on_the_next_line():
    doc = build(
        T("6.1. The device shall work."),
        H("Annex 2"),
        H("Information document for type approval of a vehicle"),
        T("1. GENERAL"),
    )
    assert doc.section("Annex 2").title == "Information document for type approval of a vehicle"


def test_appendices_belong_to_their_annex():
    doc = build(
        H("Annex 4"),
        H("Annex 4 - Appendix"),
        L("2. Close-proximity rear-view mirror"),
        H("Annex 8"),
        H("Appendix 1 - Description of the three dimensional H point machine"),
        H("Annex 9"),
    )
    assert anchors(doc) == [
        "Annex 4",
        "Annex 4 Appendix",
        "Annex 4 Appendix 2",
        "Annex 8",
        "Annex 8 Appendix 1",
        "Annex 9",
    ]
    assert parent_of(doc, "Annex 4 Appendix") == "Annex 4"
    assert parent_of(doc, "Annex 8 Appendix 1") == "Annex 8"
    assert doc.section("Annex 8 Appendix 1").level == 2
    assert parent_of(doc, "Annex 9") is None


def test_numbered_annex_headings_in_the_top_margin_are_kept():
    # Each annex starts a new page with its heading near the top: same mask, different text.
    blocks = []
    for n in (1, 2, 3):
        blocks += [H(f"Annex {n}", page=n, margin=True), T(f"{n}.1. Clause of annex {n}.", n)]
    doc = build(*blocks)
    assert [s.anchor for s in doc.sections if s.annex == s.anchor] == [
        "Annex 1",
        "Annex 2",
        "Annex 3",
    ]


def test_figure_labels_in_the_margin_are_kept():
    blocks = [T("6.1. See Figure 1.", 1)]
    for n in (1, 2, 3):
        blocks.append(T(f"Figure {n}", page=n, margin=True))
    doc = build(*blocks)
    assert all(f"Figure {n}" in doc.text for n in (1, 2, 3))
