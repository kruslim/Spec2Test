"""Generation: one item per requirement shaped by its method, the item check and retry,
the source context, rendering and the CLI, all against FakeLLM.

Every requirement and document here is invented for the tests.
"""

from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import extract, locate
from spec2test.generate import (
    Generation,
    check_item,
    generate,
    references,
    render,
    render_generation,
    source_context,
)
from spec2test.generate.prompts import SYSTEM
from spec2test.ingest import Block, build_document
from spec2test.lint import lint
from spec2test.llm import FakeLLM, Fixture
from spec2test.schema import (
    ChecklistFields,
    Criterion,
    EvidenceFields,
    Requirement,
    SourceAnchor,
    TestCaseFields,
    VerificationItem,
)

FIXTURES = Path(__file__).parent / "fixtures"


def req(text, id="spec:1:1", method="test", **fields):
    anchor = SourceAnchor(doc="spec", page=1, section="1", start=0, end=len(text), quote=text)
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=id, source_anchor=anchor, text=text, verification_method=method, **fields)


BRACKET = Requirement(
    id="bracket:3.1:1",
    source_anchor=SourceAnchor(
        doc="bracket",
        page=1,
        section="3.1",
        start=0,
        end=80,
        quote="The bracket shall withstand a static load of 50 N without permanent deformation.",
    ),
    text="The bracket shall withstand a static load of 50 N without permanent deformation.",
    type="safety",
    strength="shall",
    criteria=[Criterion(parameter="static load", comparator="ge", value="50", unit="N")],
    verification_method="analysis",
)


@pytest.fixture
def linted(widget_lamp, fake_llm):
    return lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements


def test_each_verification_method_renders_its_output_type(linted, fake_llm):
    result = generate([*linted, BRACKET], fake_llm)
    assert not result.rejected and not result.failed and not result.skipped
    kinds = {i.id: (i.kind.value, type(i.fields)) for i in result.items}
    assert kinds == {
        "widget-lamp:2.1:1:test_case": ("test_case", TestCaseFields),
        "widget-lamp:2.2:1:checklist": ("checklist", ChecklistFields),
        "widget-lamp:2.2:2:checklist": ("checklist", ChecklistFields),
        "widget-lamp:2.3:1:evidence": ("evidence", EvidenceFields),
        "widget-lamp:2.5:1:checklist": ("checklist", ChecklistFields),
        "bracket:3.1:1:evidence": ("evidence", EvidenceFields),
    }
    assert all(i.requirement_ids == [i.id.rsplit(":", 1)[0]] for i in result.items)
    assert result.model == "FakeLLM" and result.requirements == [*linted, BRACKET]

    page = render_generation(result)
    for title in [
        "### Test case: widget-lamp:2.1:1:test_case",
        "### Checklist item: widget-lamp:2.2:1:checklist",
        "### Evidence item (demonstration): widget-lamp:2.3:1:evidence",
        "### Evidence item (analysis): bracket:3.1:1:evidence",
    ]:
        assert title in page
    assert "**Pass criterion:** Luminous flux ≥ 400 lm, reached ≤ 2.0 s after switch-on" in page
    assert "- [ ] **Inspect:** Colour of the outer housing" in page
    assert "1. Hold the lamp in one hand, or mount it as installed" in page


def test_the_prompt_asks_for_the_methods_output(linted, fake_llm):
    fake_llm.calls.clear()
    generate(linted, fake_llm)
    by_fixture = {c.fixture: c for c in fake_llm.calls}
    assert all(c.system == SYSTEM for c in fake_llm.calls)
    assert by_fixture["generate-housing-red"].schema is ChecklistFields
    assert "verified by inspection" in by_fixture["generate-housing-red"].prompt
    switch = by_fixture["generate-switch-demonstration"].prompt
    # The flag the linter raised goes to the generator, with its rewrite.
    assert "operable with one hand' sets no force" in switch
    assert "[maximum force in N]" in switch


def test_an_item_missing_a_limit_is_sent_back_once(linted, fake_llm):
    fake_llm.calls.clear()
    result = generate(linted[:1], fake_llm)
    assert result.retried == 1 and len(result.items) == 1
    first, retry = fake_llm.calls
    assert first.fixture == "generate-brightness-missing-time-limit"
    assert retry.fixture == "generate-brightness-retried"
    assert "- pass_criterion does not state the requirement's time to reach luminous flux " in (
        retry.prompt
    )
    assert '"pass_criterion": "Luminous flux ≥ 400 lm"' in retry.prompt


def test_an_item_that_fails_again_is_rejected_not_kept():
    r = req(
        "The pump shall deliver at least 20 L/min.",
        criteria=[Criterion(parameter="flow", comparator="ge", value="20", unit="L/min")],
    )
    vague = TestCaseFields(steps=["Run the pump"], expected_result="Flow", pass_criterion="Flows")
    llm = FakeLLM([Fixture(schema="TestCaseFields", response=vague.model_dump())])
    result = generate([r], llm)
    assert result.items == [] and result.retried == 1
    [rejected] = result.rejected
    assert rejected.requirement_id == "spec:1:1"
    assert rejected.errors == [
        "pass_criterion does not state the requirement's flow limit: 20 L/min"
    ]


def test_failed_calls_and_unclassified_requirements_get_no_item():
    result = generate(
        [req("The case shall be blue."), req("It shall hum.", "spec:1:2", None)], FakeLLM([])
    )
    assert result.items == [] and result.skipped == ["spec:1:2"]
    [failure] = result.failed
    assert failure.requirement_id == "spec:1:1" and "no fixture" in failure.error
    page = render_generation(result)
    assert "- spec:1:1: failed: no fixture" in page
    assert "- spec:1:2: skipped: no verification method" in page


# The item check


def item(fields, kind="test_case"):
    return VerificationItem(id="i", requirement_ids=["spec:1:1"], kind=kind, fields=fields)


def test_check_reads_numbers_as_numbers():
    r = req(
        "The radius shall be 1,651 mm to 2,000 mm within 2 s.",
        criteria=[
            Criterion(
                parameter="radius",
                comparator="between",
                value="1,651",
                upper_value="2,000",
                unit="mm",
            ),
            Criterion(parameter="time", comparator="le", value="2", unit="s"),
            Criterion(parameter="temperature", comparator="ge", value="-40", unit="°C"),
        ],
    )
    fields = TestCaseFields(
        steps=["Measure"],
        expected_result="-",
        pass_criterion="1651 mm ≤ radius ≤ 2000 mm, time ≤ 2.0 s, at \u221240 °C",
    )
    assert check_item(item(fields), r) == []
    fields.pass_criterion = "radius between 1651 mm and 2100 mm"
    assert check_item(item(fields), r) == [
        "pass_criterion does not state the requirement's radius limit: 2,000 mm",
        "pass_criterion does not state the requirement's time limit: 2 s",
        "pass_criterion does not state the requirement's temperature limit: -40 °C",
    ]


def test_check_reads_words_and_the_verdict_field_of_each_kind():
    r = req("The housing shall be red.", method="inspection")
    r.criteria = [Criterion(parameter="colour", comparator="eq", value="Red")]
    ok = ChecklistFields(inspect="Housing colour", pass_condition="The housing is  red.")
    assert check_item(item(ok, "checklist"), r) == []
    wrong = ChecklistFields(inspect="The housing is red", pass_condition="Correct colour")
    assert check_item(item(wrong, "checklist"), r) == [
        "pass_condition does not state the requirement's colour limit: Red"
    ]


def test_a_demonstration_needs_a_procedure_and_an_analysis_does_not():
    no_steps = EvidenceFields(description="d", evidence=["video"], acceptance_criterion="works")
    demo = req("The door shall open by hand.", method="demonstration")
    assert check_item(item(no_steps, "evidence"), demo) == [
        "a demonstration needs a procedure: the operations to perform and observe"
    ]
    analysis = req("The beam shall carry its own weight.", method="analysis")
    assert check_item(item(no_steps, "evidence"), analysis) == []


# Source context

MIRROR = [
    Block("text", "S5. Requirements.", 1),
    Block(
        "text",
        "S5.1 Field of view. The mirror shall show a level road surface when tested in "
        "accordance with S14.1.",
        1,
    ),
    Block("text", "S5.2 Label. The label shall comply with paragraph 7.2 and S5.", 1),
    Block("text", "S14. Test procedures.", 2),
    Block("text", "S14.1 Field of view test. Place the vehicle on a level surface.", 2),
    Block("text", "S14.1.1 Eye points. Locate the driver's eye reference points.", 2),
    Block("text", "S14.12 Other test. Not referenced.", 2),
    Block("text", "7.2 Units. Values are in SI units.", 3),
]


def cited(doc, anchor, quote, **fields):
    start, end = locate(quote, doc.section(anchor), doc)
    a = SourceAnchor(doc=doc.doc_id, page=1, section=anchor, start=start, end=end, quote=quote)
    return Requirement(
        id=f"mirror:{anchor}:1",
        source_anchor=a,
        text=quote,
        type="performance",
        strength="shall",
        verification_method="test",
        **fields,
    )


def test_context_is_the_clause_and_the_clauses_it_references():
    doc = build_document("mirror", MIRROR)
    r = cited(
        doc,
        "S5.1",
        "The mirror shall show a level road surface when tested in accordance with S14.1.",
    )
    assert source_context(doc, r) == (
        '<clause heading="S5.1 Field of view">\n'
        "S5.1 Field of view. The mirror shall show a level road surface when tested in "
        "accordance with S14.1.\n"
        "</clause>\n"
        '<referenced_clause heading="S14.1 Field of view test">\n'
        "S14.1 Field of view test. Place the vehicle on a level surface.\n\n"
        "S14.1.1 Eye points. Locate the driver's eye reference points.\n"
        "</referenced_clause>"
    )


def test_a_bare_number_is_a_reference_only_after_a_reference_word():
    doc = build_document("mirror", MIRROR)
    r = cited(doc, "S5.2", "The label shall comply with paragraph 7.2 and S5.")
    # S5 encloses the requirement: it is its own context, not a reference.
    assert [s.anchor for s in references(doc, r)] == ["7.2"]
    r.text = r.source_anchor.quote = "The label shall be 7.2 mm high."
    assert references(doc, r) == []
    assert source_context(doc, req("Other doc.")) is None


def test_generate_with_the_document_sends_the_context(fake_llm):
    doc = build_document("mirror", MIRROR)
    r = cited(
        doc,
        "S5.1",
        "The mirror shall show a level road surface when tested in accordance with S14.1.",
    )
    answer = TestCaseFields(steps=["Follow S14.1"], expected_result="r", pass_criterion="p")
    llm = FakeLLM([Fixture(schema="TestCaseFields", response=answer.model_dump())])
    result = generate([r], llm, doc=doc)
    assert len(result.items) == 1
    [call] = llm.calls
    assert '<source>\n<clause heading="S5.1 Field of view">' in call.prompt
    assert "S14.1.1 Eye points." in call.prompt
    generate([r], llm)
    assert "<source>" not in llm.calls[-1].prompt


def test_render_without_the_requirement():
    fields = EvidenceFields(description="d", evidence=["e"], acceptance_criterion="a")
    text = render(item(fields, "evidence"))
    assert text.startswith("### Evidence item: i\n\nVerifies spec:1:1.\n")


# CLI


def test_cli_generate_renders_items_and_saves_json(widget_lamp, fake_llm, tmp_path, capsys):
    saved = tmp_path / "lint.json"
    linted = lint(extract(widget_lamp, fake_llm).requirements, fake_llm)
    saved.write_text(linted.model_dump_json(), encoding="utf-8")

    assert cli.main(["generate", str(saved), "--fake", str(FIXTURES / "llm")]) == 0
    out = capsys.readouterr().out
    assert "### Test case: widget-lamp:2.1:1:test_case" in out
    assert out.rstrip().endswith(
        "5 items from 5 requirements, 0 rejected, 1 retried, 0 failed, 0 skipped (no method)"
    )

    extraction = tmp_path / "extraction.json"
    extraction.write_text(extract(widget_lamp, fake_llm).model_dump_json(), encoding="utf-8")
    args = ["generate", str(extraction), "--fake", str(FIXTURES / "llm"), "--json"]
    assert cli.main(args) == 0
    result = Generation.model_validate_json(capsys.readouterr().out)
    assert len(result.items) == 5
    # Without the lint flags: the generator never saw them.
    assert all(not r.testability_flags for r in result.requirements)


def test_cli_generate_reports_bad_input(tmp_path, capsys):
    assert cli.main(["generate", str(tmp_path / "nope.json"), "--fake", "."]) == 2
    assert "nope.json" in capsys.readouterr().err


def test_cli_generate_with_the_document(widget_lamp, fake_llm, tmp_path, capsys):
    saved = tmp_path / "extraction.json"
    saved.write_text(extract(widget_lamp, fake_llm).model_dump_json(), encoding="utf-8")
    doc_json = tmp_path / "doc.json"
    doc_json.write_text(widget_lamp.model_dump_json(), encoding="utf-8")
    fake = ["--fake", str(FIXTURES / "llm")]

    assert cli.main(["generate", str(saved), "--doc", str(doc_json), *fake]) == 0
    assert capsys.readouterr().err == ""

    other = tmp_path / "mirror.json"
    other.write_text(build_document("mirror", MIRROR).model_dump_json(), encoding="utf-8")
    assert cli.main(["generate", str(saved), "--doc", str(other), *fake]) == 0
    assert capsys.readouterr().err == (
        f"warning: {other} is 'mirror', but the requirements are from 'widget-lamp': "
        "no source clauses will be sent\n"
    )
