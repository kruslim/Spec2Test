"""Extraction with the citation check, against FakeLLM fixtures.

The widget lamp spec is invented for these tests; its canned model answers are in
tests/fixtures/llm/extract_widget_lamp.json.
"""

import json
import os
from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import (
    CitationError,
    ExtractedRequirement,
    ExtractedRequirements,
    check_citation,
    extract,
    is_normative,
    locate,
    section_at,
)
from spec2test.extract.prompts import SYSTEM
from spec2test.ingest import Block, build_document
from spec2test.llm import AnthropicLLM, FakeLLM, Fixture
from spec2test.schema import RequirementType, Strength, VerificationMethod

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def doc(widget_lamp):
    return widget_lamp


@pytest.fixture
def result(doc, fake_llm):
    return extract(doc, fake_llm)


def draft(quote, text=None, **kwargs):
    return ExtractedRequirement(
        quote=quote,
        text=text or quote,
        type=kwargs.pop("type", RequirementType.FUNCTIONAL),
        strength=kwargs.pop("strength", Strength.SHALL),
        **kwargs,
    )


def answer(*drafts, prompt_contains=(), name=""):
    return Fixture(
        schema="ExtractedRequirements",
        response=ExtractedRequirements(requirements=list(drafts)).model_dump(mode="json"),
        prompt_contains=tuple(prompt_contains),
        name=name,
    )


def test_every_extracted_quote_is_verbatim_at_its_offsets(doc, result):
    assert result.requirements
    for req in result.requirements:
        a = req.source_anchor
        assert doc.text[a.start : a.end] == a.quote
        assert check_citation(req, doc) == []


def test_records_are_cited_to_document_page_and_section(doc, result):
    req = next(r for r in result.requirements if r.id == "widget-lamp:2.1:1")
    a = req.source_anchor
    assert (a.doc, a.page, a.section) == ("widget-lamp", 2, "2.1")
    assert a.quote == "The lamp shall emit at least 400 lm within 2.0 s of switch-on."
    assert req.type is RequirementType.PERFORMANCE
    assert req.verification_method is VerificationMethod.TEST
    assert [(c.value, c.unit) for c in req.criteria] == [("400", "lm"), ("2.0", "s")]
    assert req.conditions == ["after switch-on"]


def test_compound_requirement_becomes_separate_records(result):
    housing = [r for r in result.requirements if r.source_anchor.section == "2.2"]
    assert [r.id for r in housing] == ["widget-lamp:2.2:1", "widget-lamp:2.2:2"]
    assert [r.text for r in housing] == [
        "The housing shall be red.",
        "The housing shall carry a voltage label.",
    ]
    assert [r.source_anchor.quote for r in housing] == [
        "The housing shall be red",
        "shall carry a voltage label",
    ]


def test_failed_quote_is_retried_once_with_the_error(result, fake_llm):
    switch = next(r for r in result.requirements if r.source_anchor.section == "2.3")
    assert switch.source_anchor.quote == "The switch shall be operable with one hand."

    retries = [c for c in fake_llm.calls if "failed the citation check" in c.prompt]
    assert [c.fixture for c in retries] == [
        "extract-retry-switch-fixed",
        "extract-retry-battery-still-wrong",
    ]
    assert "is not in section 2.3 verbatim" in retries[0].prompt
    assert '"quote": "The switch must be operable with one hand."' in retries[0].prompt
    assert all(c.system == SYSTEM for c in fake_llm.calls)
    assert result.retried == 2


def test_quote_that_fails_twice_is_rejected(doc, result):
    assert not any(r.source_anchor.section == "2.4" for r in result.requirements)
    [rejected] = result.rejected
    assert doc.sections[rejected.section].anchor == "2.4"
    assert rejected.draft.quote == "The battery shall last 8 hours."
    assert "is not in section 2.4 verbatim" in rejected.errors[0]


def test_quote_differing_only_in_whitespace_cites_the_source_text(result):
    [mounting] = [r for r in result.requirements if r.source_anchor.section == "2.5"]
    assert mounting.source_anchor.quote == (
        "The lamp shall mount on either:\n\na. a 35 mm rail; or\n\nb. a wall bracket."
    )
    assert mounting.source_anchor.page == 3


def test_failed_section_is_recorded_and_the_rest_continue(doc, result):
    [failed] = result.failed_sections
    assert doc.sections[failed.section].anchor == "2.6"
    assert "no fixture" in failed.error
    assert len(result.requirements) == 5


def test_sections_without_normative_wording_are_not_sent(doc, result, fake_llm):
    skipped = [doc.sections[i].anchor for i in result.skipped_sections]
    assert skipped == [None, "1", "1.1", "2"]
    assert not any("Section: 1.1" in c.prompt for c in fake_llm.calls)
    assert is_normative(doc.section("2.4"))  # 'should'
    assert not is_normative(doc.section("1.1"))


def test_prompt_gives_the_enclosing_clauses_as_context(fake_llm, doc):
    extract(doc, fake_llm, sections=[doc.section("2.1").index])
    [call] = fake_llm.calls
    assert "Within: 2 REQUIREMENTS\nSection: 2.1 Brightness" in call.prompt
    assert f"<section>\n{doc.section('2.1').text}\n</section>" in call.prompt


def test_extraction_follows_the_gold_set_policy(fake_llm):
    # Invented. The canned answer is the one the policy asks for: the permission that only
    # relaxes the requirement is no record; the exception and the named test procedure are
    # conditions. This checks the pipeline keeps such an answer, not that a model gives it.
    doc = build_document(
        "reflector",
        [
            Block(
                "text",
                "4.1 Reflector. Except as provided in 4.2, the reflector shall return at least "
                "80 % of incident light when tested in accordance with 5.1. The reflector need "
                "not be removable by hand.",
            )
        ],
    )
    result = extract(doc, fake_llm)
    [req] = result.requirements
    assert req.strength == Strength.SHALL
    assert req.conditions == ["except as provided in 4.2", "when tested in accordance with 5.1"]
    assert "need not" not in req.source_anchor.quote
    for rule in ['"need not"', "relaxes or exempts", "test procedure the clause itself names"]:
        assert rule in SYSTEM


def test_explicit_sections_are_sent_even_without_normative_wording(doc):
    llm = FakeLLM([answer()])
    result = extract(doc, llm, sections=[doc.section("1.1").index])
    assert len(llm.calls) == 1
    assert result.requirements == [] and result.skipped_sections == []


def test_ambiguous_quote_asks_for_more_context():
    doc = build_document(
        "d",
        [
            Block("text", "3.1 Lens. The lens shall be clear."),
            Block("text", "After the drop test, the lens shall be clear."),
        ],
    )
    with pytest.raises(CitationError, match="appears 2 times"):
        locate("lens shall be clear.", doc.section("3.1"), doc)
    start, end = locate("After the drop test, the lens shall be clear.", doc.section("3.1"), doc)
    assert doc.text[start:end].startswith("After the drop test")


def test_quote_from_another_section_names_that_section(doc):
    with pytest.raises(CitationError, match=r"from section 2.1, not section 2.2"):
        locate("The lamp shall emit at least 400 lm", doc.section("2.2"), doc)


def test_retry_that_fails_outright_rejects_the_draft(doc):
    # Matches only the first request, so the retry finds no fixture and raises.
    first = ["Extract every requirement this section states."]
    llm = FakeLLM([answer(draft("The switch is one-handed."), prompt_contains=first)])
    result = extract(doc, llm, sections=[doc.section("2.3").index])
    [rejected] = result.rejected
    assert rejected.errors[0].startswith("the quote is not in section 2.3 verbatim")
    assert rejected.errors[1].startswith("retry failed: no fixture")


def test_draft_withdrawn_on_retry_is_rejected(doc):
    llm = FakeLLM(
        [
            answer(prompt_contains=["failed the citation check"]),
            answer(draft("The switch is one-handed.")),
        ]
    )
    result = extract(doc, llm, sections=[doc.section("2.3").index])
    assert result.requirements == []
    assert result.rejected[0].errors[-1] == "withdrawn on retry"


def test_retry_may_split_a_compound_draft(doc):
    llm = FakeLLM(
        [
            answer(
                draft("The housing shall be red"),
                draft("shall carry a voltage label"),
                prompt_contains=["failed the citation check"],
            ),
            answer(draft("The housing is red and labelled.")),
        ]
    )
    result = extract(doc, llm, sections=[doc.section("2.2").index])
    assert [r.id for r in result.requirements] == ["widget-lamp:2.2:1", "widget-lamp:2.2:2"]
    assert result.rejected == []


def test_duplicate_drafts_are_kept_once(doc):
    quote = "The switch shall be operable with one hand."
    llm = FakeLLM([answer(draft(quote), draft(quote))])
    result = extract(doc, llm, sections=[doc.section("2.3").index])
    assert [r.id for r in result.requirements] == ["widget-lamp:2.3:1"]


def test_check_citation_catches_tampered_anchors(doc, result):
    req = result.requirements[0]

    def tamper(**anchor):
        return req.model_copy(update={"source_anchor": req.source_anchor.model_copy(update=anchor)})

    a = req.source_anchor
    assert "not verbatim" in check_citation(tamper(start=a.start + 1), doc)[0]
    assert "not verbatim" in check_citation(tamper(quote=a.quote.upper()), doc)[0]
    assert "page 1" in check_citation(tamper(page=1), doc)[0]
    assert "section '9.9'" in check_citation(tamper(section="9.9"), doc)[0]
    assert "document 'other'" in check_citation(tamper(doc="other"), doc)[0]
    assert "past the end" in check_citation(tamper(end=len(doc.text) + 1), doc)[0]

    two = doc.section("2.2")
    across = tamper(start=a.start, end=two.end, quote=doc.text[a.start : two.end])
    assert check_citation(across, doc) == ["quote is not inside a single section"]


def test_section_at(doc):
    s = doc.section("2.2")
    assert section_at(doc, s.start) == s
    assert section_at(doc, s.end - 1) == s
    assert section_at(doc, s.end) is None  # the blank line between sections


def test_cli_extract_with_fake_llm(doc, tmp_path, capsys):
    path = tmp_path / "widget-lamp.json"
    path.write_text(doc.model_dump_json(), encoding="utf-8")

    assert cli.main(["extract", str(path), "--fake", str(FIXTURES / "llm")]) == 1  # 2.6 failed
    out = capsys.readouterr().out
    assert "widget-lamp:2.1:1  p2  test" in out
    assert "rejected  2.4" in out
    summary, cost = out.strip().splitlines()[-2:]
    assert summary == "5 requirements, 1 rejected, 2 retried, 1 sections failed, 4 skipped"
    assert cost.startswith("cost unknown (no price for FakeLLM); 7 calls")

    cli.main(["extract", str(path), "--fake", str(FIXTURES / "llm"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["requirements"]) == 5


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and ANTHROPIC_API_KEY",
)
def test_live_extraction_cites_verbatim_and_splits_compounds(doc):
    result = extract(doc, AnthropicLLM(effort="low"), sections=[doc.section("2.2").index])
    assert len(result.requirements) >= 2
    assert all(check_citation(r, doc) == [] for r in result.requirements)
