"""The eval harness: gold sets, alignment, the failure taxonomy, metrics and reports.

Scored against the invented widget lamp spec (tests/conftest.py), its gold set in
tests/fixtures/gold/ and the canned extraction and lint answers in tests/fixtures/llm/.
"""

import json
import os
import re
from pathlib import Path

import pytest

from spec2test import cli, corpora
from spec2test.evals import (
    EvalRun,
    FailureCategory,
    GoldError,
    GoldSet,
    align,
    compare,
    evaluate,
    gold_path,
    html_report,
    load_gold,
    rescore,
    score,
    text_sha256,
)
from spec2test.evals.score import (
    CONDITIONS_ACCURACY,
    CRITERIA_ACCURACY,
    FIELD_ACCURACY,
    FLAG_PRECISION,
    FLAG_RECALL,
    METHOD_ACCURACY,
    PRECISION,
    RECALL,
    STRENGTH_ACCURACY,
    TYPE_ACCURACY,
)
from spec2test.extract import Extraction, SectionFailure, locate
from spec2test.lint import FlagDrafts
from spec2test.llm import OpenAICompatLLM
from spec2test.schema import Criterion, Requirement, SourceAnchor, TestabilityFlag

REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
GOLD = FIXTURES / "gold" / "widget-lamp.jsonl"
F = FailureCategory


@pytest.fixture
def doc(widget_lamp):
    return widget_lamp


@pytest.fixture
def gold(doc):
    return load_gold(GOLD, doc)


@pytest.fixture
def run(doc, gold, fake_llm):
    return evaluate(doc, gold, fake_llm, corpus="widget-lamp")


def req(doc, id, section, quote, text=None, **fields):
    """A cited record for `quote` in `section`."""
    start, end = locate(quote, doc.section(section), doc)
    anchor = SourceAnchor(
        doc=doc.doc_id,
        page=doc.page_at(start),
        section=section,
        start=start,
        end=end,
        quote=doc.text[start:end],
    )
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=id, source_anchor=anchor, text=text or quote, **fields)


def gold_set(doc, *requirements, scope=None):
    return GoldSet(path="test", doc_id=doc.doc_id, scope=scope, requirements=list(requirements))


def scored(doc, gold_reqs, pred_reqs, **extraction):
    result = Extraction(doc_id=doc.doc_id, requirements=pred_reqs, **extraction)
    return score(gold_set(doc, *gold_reqs), result, doc, corpus="t", model="m")


def categories(run):
    found = [(f.category, f.gold_id, f.pred_id) for f in run.failures]
    return sorted(found, key=lambda f: (f[0], f[1] or "", f[2] or ""))


def write_gold(tmp_path, *lines, scope=None):
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if scope is not None:
        path.with_suffix(".toml").write_text(scope, encoding="utf-8")
    return path


def line(id="g1", section="2.3", quote="The switch shall be operable with one hand.", **extra):
    record = {
        "id": id,
        "source_anchor": {"doc": "widget-lamp", "section": section, "quote": quote},
        "text": quote,
        "type": "functional",
        "strength": "shall",
        **extra,
    }
    return json.dumps(record)


# Gold sets


def test_gold_anchors_are_found_from_section_and_quote(doc, gold):
    assert [g.id for g in gold.requirements][:2] == ["wl-2.1-1", "wl-2.2-1"]
    assert gold.scope == ["2"]
    for g in gold.requirements:
        a = g.source_anchor
        assert doc.text[a.start : a.end] == a.quote
    battery = gold.requirements[4].source_anchor
    assert (battery.section, battery.page) == ("2.4", 3)
    # The gold line joins the list items with spaces; the anchor cites the source text.
    mounting = gold.requirements[5].source_anchor
    assert (
        mounting.quote
        == "The lamp shall mount on either:\n\na. a 35 mm rail; or\n\nb. a wall bracket."
    )


def test_gold_anchor_with_offsets_is_checked_verbatim(doc, tmp_path):
    s = doc.section("2.3")
    start = s.text.index("The switch") + s.start
    quote = "The switch shall be operable with one hand."
    anchor = {"doc": "widget-lamp", "page": 2, "section": "2.3", "quote": quote}
    good = {**anchor, "start": start, "end": start + len(quote)}
    bad = {**anchor, "start": start + 1, "end": start + 1 + len(quote)}
    base = {"text": quote, "type": "functional", "strength": "shall"}
    path = write_gold(
        tmp_path,
        json.dumps({"id": "a", "source_anchor": good, **base}),
        json.dumps({"id": "b", "source_anchor": bad, **base}),
    )
    with pytest.raises(GoldError) as exc:
        load_gold(path, doc)
    assert str(exc.value).splitlines() == [
        f"bad.jsonl:2: quote is not verbatim at [{start + 1}, {start + 1 + len(quote)}); "
        f"the source there is {doc.text[start + 1 : start + 1 + len(quote)]!r}"
    ]


def test_gold_errors_name_every_bad_line(doc, tmp_path):
    path = write_gold(
        tmp_path,
        line(),
        "{not json",
        line(id="g1", section="2.4", quote="The battery should last 8 h at full brightness."),
        line(id="g3", quote="The switch must be operable with one hand."),
        line(id="g4", section="9.9"),
        line(id="g5", type="mood"),
        "",
        line(id="g6", section="1.1", quote="This document specifies requirements"),
        scope='scope = ["2", "7"]',
    )
    with pytest.raises(GoldError) as exc:
        load_gold(path, doc)
    errors = str(exc.value).splitlines()
    assert errors[0] == "bad.toml: no section '7' in widget-lamp"
    assert errors[1].startswith("bad.jsonl:2: not JSON")
    assert errors[2] == "bad.jsonl:3: duplicate id 'g1' (first on line 1)"
    assert errors[3].startswith("bad.jsonl:4: the quote is not in section 2.3 verbatim")
    assert errors[4] == "bad.jsonl:5: no section '9.9' in widget-lamp"
    assert errors[5].startswith("bad.jsonl:6: type: Input should be")
    assert errors[6] == "bad.jsonl:8: section '1.1' is outside the scope"
    assert len(errors) == 7


def test_gold_path_defaults_to_the_gold_folder():
    assert gold_path("fmvss-111") == Path("gold", "fmvss-111.jsonl")


# Alignment and the failure taxonomy


def test_compound_split_records_sharing_a_quote_align_by_text(doc):
    sentence = "The housing shall be red and shall carry a voltage label."
    red, label = "The housing shall be red.", "The housing shall carry a voltage label."
    gold = [req(doc, "g-red", "2.2", sentence, red), req(doc, "g-label", "2.2", sentence, label)]
    pred = [req(doc, "p-label", "2.2", sentence, label), req(doc, "p-red", "2.2", sentence, red)]
    assert [(m.gold_id, m.pred_id) for m in align(gold, pred)] == [
        ("g-red", "p-red"),
        ("g-label", "p-label"),
    ]


def test_unsplit_compound_is_a_merged_miss(doc):
    gold = [
        req(doc, "g-red", "2.2", "The housing shall be red"),
        req(doc, "g-label", "2.2", "shall carry a voltage label"),
    ]
    pred = [req(doc, "p", "2.2", "The housing shall be red and shall carry a voltage label.")]
    run = scored(doc, gold, pred)
    assert [(m.gold_id, m.pred_id) for m in run.matches] == [("g-label", "p")]
    [miss] = run.failures
    assert (miss.category, miss.gold_id) == (F.MERGED, "g-red")
    assert miss.detail == "covered by p, which aligned with g-label"


def test_split_of_a_single_gold_record_is_an_over_split_extra(doc):
    gold = [req(doc, "g", "2.2", "The housing shall be red and shall carry a voltage label.")]
    pred = [
        req(doc, "p-red", "2.2", "The housing shall be red"),
        req(doc, "p-label", "2.2", "shall carry a voltage label"),
    ]
    run = scored(doc, gold, pred)
    assert [(m.gold_id, m.pred_id) for m in run.matches] == [("g", "p-label")]
    assert categories(run) == [(F.OVER_SPLIT, None, "p-red")]


def test_small_overlap_is_a_span_mismatch_on_both_sides(doc):
    gold = [req(doc, "g", "2.1", "The lamp shall emit at least 400 lm")]
    pred = [req(doc, "p", "2.1", "400 lm within 2.0 s of switch-on.")]
    run = scored(doc, gold, pred)
    assert run.matches == []
    assert categories(run) == [(F.SPAN_MISMATCH, None, "p"), (F.SPAN_MISMATCH, "g", None)]


def test_misses_are_categorized_by_what_happened_to_their_section(doc):
    gold = [
        req(doc, "g-skipped", "1.1", "This document specifies requirements for a widget lamp."),
        req(doc, "g-failed", "2.6", "The lamp shall be packed in a carton."),
        req(doc, "g-missed", "2.3", "The switch shall be operable with one hand."),
    ]
    pred = [req(doc, "p-extra", "2.5", "The lamp shall mount on either:")]
    failed = [SectionFailure(section=doc.section("2.6").index, error="refused")]
    run = scored(doc, gold, pred, failed_sections=failed)
    assert categories(run) == [
        (F.NOT_EXTRACTED, "g-missed", None),
        (F.NOT_IN_GOLD, None, "p-extra"),
        (F.SECTION_FAILED, "g-failed", None),
        (F.SECTION_SKIPPED, "g-skipped", None),
    ]
    assert next(f for f in run.failures if f.gold_id == "g-failed").detail == "refused"


def test_extractions_outside_the_scope_are_not_scored(doc):
    gold = [req(doc, "g", "2.3", "The switch shall be operable with one hand.")]
    pred = [
        req(doc, "p", "2.3", "The switch shall be operable with one hand."),
        req(doc, "p-out", "1.1", "This document specifies requirements for a widget lamp."),
    ]
    result = Extraction(doc_id=doc.doc_id, requirements=pred)
    run = score(gold_set(doc, *gold, scope=["2"]), result, doc, corpus="t", model="m")
    assert [p.id for p in run.predicted] == ["p"]
    assert run.failures == []
    assert run.metric(PRECISION).value == 1.0
    assert run.pages == 2  # clause 2 runs over pages 2 and 3


def test_field_errors_of_an_aligned_pair(doc):
    quote = "The lamp shall emit at least 400 lm within 2.0 s of switch-on."
    g = req(
        doc,
        "g",
        "2.1",
        quote,
        type="performance",
        criteria=[
            Criterion(parameter="flux", comparator="ge", value="400", unit="lm"),
            Criterion(parameter="time", comparator="le", value="2.0", unit="s", tolerance="±0.1"),
            Criterion(parameter="colour temperature", comparator="eq", value="3000", unit="K"),
        ],
        conditions=["after switch-on", "at 20 °C"],
        verification_method="test",
        testability_flags=[TestabilityFlag(kind="missing_condition", reason="no temperature")],
    )
    p = req(
        doc,
        "p",
        "2.1",
        quote,
        strength="should",
        criteria=[
            Criterion(parameter="luminous flux", comparator="gt", value="400", unit="lm"),
            Criterion(parameter="rise time", comparator="le", value="2.0", unit="ms"),
            Criterion(parameter="voltage", comparator="eq", value="12", unit="V"),
        ],
        conditions=["After  switch-on", "in daylight"],
        verification_method="inspection",
        testability_flags=[TestabilityFlag(kind="unbounded", reason="no upper limit")],
    )
    found = [(f.category, f.detail) for f in compare(g, p)]
    assert found == [
        (F.WRONG_TYPE, "gold performance, extracted functional"),
        (F.WRONG_STRENGTH, "gold shall, extracted should"),
        (F.CRITERION_COMPARATOR, "flux ge 400 lm: gold comparator ge, extracted gt"),
        (F.CRITERION_UNIT, "time le 2.0 s ±0.1: gold unit s, extracted ms"),
        (F.CRITERION_TOLERANCE, "time le 2.0 s ±0.1: gold tolerance ±0.1, extracted None"),
        (F.CRITERION_MISSING, "colour temperature eq 3000 K"),
        (F.CRITERION_EXTRA, "voltage eq 12 V"),
        (F.CONDITION_MISSING, "at 20 °C"),
        (F.CONDITION_EXTRA, "in daylight"),
        (F.WRONG_METHOD, "gold test, extracted inspection"),
        (F.FLAG_MISSED, "missing_condition: no temperature"),
        (F.FLAG_FALSE, "unbounded: no upper limit"),
    ]
    assert all((f.gold_id, f.pred_id, f.section) == ("g", "p", "2.1") for f in compare(g, p))


def test_units_compare_by_meaning_not_spelling(doc):
    quote = "The lamp shall emit at least 400 lm within 2.0 s of switch-on."

    def unit(u):
        return req(doc, u, "2.1", quote, criteria=[Criterion(parameter="t", value="2", unit=u)])

    for a, b in [("seconds", "s"), ("mm 2", "mm²"), ("percent", "%"), ("°", "degrees")]:
        assert compare(unit(a), unit(b)) == [], (a, b)
    [f] = compare(unit("s"), unit("ms"))
    assert f.category is F.CRITERION_UNIT


def test_a_condition_contained_in_another_matches_it(doc):
    quote = "The switch shall be operable with one hand."

    def cond(*conditions):
        return req(doc, "x", "2.3", quote, conditions=list(conditions))

    gold = cond("during the backing event", "when tested in accordance with S12")
    pred = cond(
        "during the backing event, until either the driver modifies the view",
        "When each convex mirror is tested in accordance with the procedures in S12.",
    )
    assert compare(gold, pred) == []
    # Similar text, different numbers: not the same condition.
    found = [(f.category, f.detail) for f in compare(cond("at 20 °C"), cond("at 25 °C"))]
    assert found == [(F.CONDITION_MISSING, "at 20 °C"), (F.CONDITION_EXTRA, "at 25 °C")]


def test_unclassified_extraction_is_a_missing_method(doc):
    quote = "The switch shall be operable with one hand."
    g = req(doc, "g", "2.3", quote, verification_method="demonstration")
    [f] = compare(g, req(doc, "p", "2.3", quote))
    assert (f.category, f.detail) == (F.METHOD_MISSING, "gold demonstration")
    assert compare(req(doc, "g", "2.3", quote), g) == []  # gold leaves the method unlabeled


# The whole run on the widget lamp


def test_eval_scores_the_canned_extraction(run, fake_llm):
    values = {m.name: (m.numerator, m.denominator) for m in run.metrics}
    assert values == {
        PRECISION: (5, 5),
        RECALL: (5, 7),
        FIELD_ACCURACY: (4, 5),
        CRITERIA_ACCURACY: (4, 5),
        CONDITIONS_ACCURACY: (5, 5),
        TYPE_ACCURACY: (4, 5),
        STRENGTH_ACCURACY: (5, 5),
        METHOD_ACCURACY: (5, 5),
        FLAG_PRECISION: (1, 2),
        FLAG_RECALL: (1, 1),
    }
    assert categories(run) == [
        (F.CITATION_REJECTED, "wl-2.4-1", None),
        (F.CRITERION_EXTRA, "wl-2.5-1", "widget-lamp:2.5:1"),
        (F.FLAG_FALSE, "wl-2.1-1", "widget-lamp:2.1:1"),
        (F.SECTION_FAILED, "wl-2.6-1", None),
        (F.WRONG_TYPE, "wl-2.2-2", "widget-lamp:2.2:2"),
    ]
    assert run.method_confusion == {
        "test": {"test": 1},
        "inspection": {"inspection": 3},
        "demonstration": {"demonstration": 1},
    }
    assert [(c.kind, c.true_positives, c.false_positives) for c in run.flags] == [
        ("missing_condition", 0, 1),
        ("vague_term", 1, 0),
    ]
    # The LLM pass's flag that quotes words not in the requirement was rejected.
    assert run.lint is not None and run.lint.llm_pass
    assert [r.draft.phrase for r in run.lint.rejected] == ["dazzling glare"]
    # Only the scope's sections with normative wording were sent.
    assert not any("Section: 1.1" in c.prompt for c in fake_llm.calls)
    assert run.model == "FakeLLM" and run.seconds is not None and run.pages == 2


def test_every_miss_has_a_category(run):
    gold_missed = {f.gold_id for f in run.failures if f.pred_id is None}
    aligned = {m.gold_id for m in run.matches}
    assert gold_missed | aligned == {g.id for g in run.gold}
    assert all(isinstance(f.category, FailureCategory) and f.detail for f in run.failures)


def test_gold_flags_on_missed_records_are_counted_in_a_warning(doc):
    flag = {"kind": "vague_term", "reason": "'one hand' has no force limit"}
    switch = req(doc, "g1", "2.3", "The switch shall be operable with one hand.")
    switch = switch.model_copy(update={"testability_flags": [TestabilityFlag(**flag)]})
    run = scored(doc, [switch], [])
    [warning] = [w for w in run.warnings if "gold flag" in w]
    assert warning.startswith("1 gold flag(s) sit on 1 requirement(s) the extraction missed")
    assert run.metric(FLAG_RECALL).denominator == 0


def test_rescoring_a_saved_run_calls_no_model(doc, gold, run):
    saved = EvalRun.model_validate_json(run.model_dump_json())
    again = rescore(doc, gold, saved, corpus="widget-lamp")
    assert again.metrics == run.metrics
    assert (again.model, again.seconds) == (run.model, run.seconds)
    assert rescore(doc, gold, run.extraction, corpus="x").model == "saved extraction"


def test_rescoring_warns_when_the_document_text_changed(doc, gold, run):
    saved = EvalRun.model_validate_json(run.model_dump_json())
    assert saved.doc_sha256 == text_sha256(doc)
    assert not any("document text" in w for w in rescore(doc, gold, saved, corpus="w").warnings)

    shifted = saved.model_copy(update={"doc_sha256": "0" * 64, "docling": "1.0.0"})
    [warning] = [w for w in rescore(doc, gold, shifted, corpus="w").warnings if "text" in w]
    assert "differs" in warning and "Docling 1.0.0 then" in warning

    old = saved.model_copy(update={"doc_sha256": None})  # saved before runs had the hash
    assert any("did not record" in w for w in rescore(doc, gold, old, corpus="w").warnings)


def test_rescoring_reruns_the_rules_and_keeps_the_saved_llm_flags(doc, gold, run):
    saved = EvalRun.model_validate_json(run.model_dump_json())
    assert rescore(doc, gold, saved, corpus="w").flags == run.flags

    rules_only = rescore(doc, gold, saved, corpus="w", lint="rules")
    assert rules_only.lint is not None and not rules_only.lint.llm_pass
    assert rules_only.metric(FLAG_RECALL).numerator == 0  # 'one hand' needs the LLM pass

    # A run saved before the linter existed has no lint: the rules still run.
    old = saved.model_copy(update={"lint": None})
    assert rescore(doc, gold, old, corpus="w").lint.llm_flags == {}


def test_eval_lint_modes(doc, gold, fake_llm):
    rules = evaluate(doc, gold, fake_llm, corpus="w", lint="rules")
    assert all(c.schema is not FlagDrafts for c in fake_llm.calls)
    assert rules.lint is not None and rules.lint.llm_flags == {}

    off = evaluate(doc, gold, fake_llm, corpus="w", lint="off")
    assert off.lint is None
    assert off.warnings == [
        FAILED_SECTION,
        "the extraction was not linted: flag precision and recall measure nothing",
    ]


def test_failed_lint_batches_are_a_warning(doc, gold, fake_llm):
    fake_llm.fixtures = [f for f in fake_llm.fixtures if f.schema != "FlagDrafts"]
    run = evaluate(doc, gold, fake_llm, corpus="w")
    assert len(run.lint.failed) == 4  # sections 2.1, 2.2, 2.3 and 2.5
    assert run.warnings == [
        FAILED_SECTION,
        "the LLM lint pass failed for 4 batch(es); flag recall is understated",
    ]


FAILED_SECTION = (
    "the extraction call failed for 1 section(s); recall is understated: run again before "
    "quoting it"
)


def test_failed_extraction_sections_are_a_warning(run):
    # The canned extraction fails section 2.6 on purpose. An outage lowers recall without
    # saying anything about the model, so the run says so.
    assert [f.section for f in run.extraction.failed_sections] and FAILED_SECTION in run.warnings


def test_scoring_a_model_against_its_own_labels_warns(doc):
    labeled = gold_set(doc).model_copy(update={"labeler": "claude-opus-5-5"})
    empty = Extraction(doc_id=doc.doc_id)

    [same, unreviewed] = score(labeled, empty, doc, corpus="t", model="claude-sonnet-5-5").warnings
    assert "same family as the gold set's labeler 'claude-opus-5-5'" in same
    assert (
        unreviewed == "the gold set (labeled by claude-opus-5-5) has not been reviewed by a person"
    )

    reviewed = labeled.model_copy(update={"reviewed": True})
    assert score(reviewed, empty, doc, corpus="t", model="kimi-for-coding").warnings == []


def test_gold_set_records_who_labeled_it(gold, run):
    assert (gold.labeler, gold.reviewed) == ("Spec2Test tests", True)
    assert (run.gold_labeler, run.warnings) == ("Spec2Test tests", [FAILED_SECTION])


def test_html_report_is_self_contained_and_escaped(doc, gold, run):
    page = html_report(run)
    assert page.startswith("<!doctype html>")
    for heading in ("Metrics", "Failure taxonomy", "Verification method", "Failures"):
        assert f"<h2>{heading}</h2>" in page
    assert "<td>extraction recall</td><td class=num>71.4%</td><td class=num>5/7</td>" in page
    assert "id='section_failed'" in page
    assert "<th>Linter</th><td>rules and LLM pass</td>" in page
    assert "<td><code>vague_term</code></td>" in page and "<th>Recall</th>" in page
    assert "src=" not in page and "href='http" not in page

    evil = run.model_copy(update={"corpus": "<script>alert(1)</script>"})
    assert "<script>" not in html_report(evil)


def test_cli_eval_prints_the_table_and_writes_the_report(doc, tmp_path, capsys):
    doc_json = tmp_path / "widget-lamp.json"
    doc_json.write_text(doc.model_dump_json(), encoding="utf-8")
    out = tmp_path / "report"
    args = ["eval", "widget-lamp", "--doc", str(doc_json), "--gold", str(GOLD)]

    assert cli.main([*args, "--fake", str(FIXTURES / "llm"), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("widget-lamp: 7 gold, 5 extracted, 5 aligned, 2 pages")
    assert re.search(r"^extraction recall +71\.4% +5/7$", printed, re.M)
    assert re.search(r"^flag precision +50\.0% +1/2$", printed, re.M)
    assert re.search(r"^vague_term +1 +0 +0 +100\.0% +100\.0%$", printed, re.M)
    assert re.search(r"^section_failed +1$", printed, re.M)
    assert f"report:  {out / 'report.html'}" in printed
    assert "Failure taxonomy" in (out / "report.html").read_text(encoding="utf-8")
    results = EvalRun.model_validate_json((out / "results.json").read_text(encoding="utf-8"))
    assert results.metric(RECALL).value == pytest.approx(5 / 7)

    # Rescoring needs no fixtures: no model is called.
    again = tmp_path / "again"
    assert cli.main([*args, "--rescore", str(out / "results.json"), "--out", str(again)]) == 0
    assert re.search(r"^extraction recall +71\.4% +5/7$", capsys.readouterr().out, re.M)


def test_cli_eval_reports_bad_input(doc, tmp_path, capsys):
    doc_json = tmp_path / "widget-lamp.json"
    doc_json.write_text(doc.model_dump_json(), encoding="utf-8")
    missing = tmp_path / "nope.jsonl"
    assert cli.main(["eval", "widget-lamp", "--doc", str(doc_json), "--gold", str(missing)]) == 2
    assert "nope.jsonl" in capsys.readouterr().err

    bad = write_gold(tmp_path, line(section="9.9"))
    assert cli.main(["eval", "widget-lamp", "--doc", str(doc_json), "--gold", str(bad)]) == 2
    assert "bad.jsonl:1: no section '9.9'" in capsys.readouterr().err

    assert cli.main(["eval", "not-a-corpus", "--dir", str(REPO / "corpora")]) == 2
    assert "'not-a-corpus' is not in" in capsys.readouterr().err


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("KIMI_CODE_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and KIMI_CODE_API_KEY",
)
def test_live_eval_on_the_widget_lamp(doc, gold):
    run = evaluate(doc, gold, OpenAICompatLLM.preset("kimi-code"), corpus="widget-lamp")
    assert run.metric(RECALL).value >= 0.5
    assert "<h2>Failure taxonomy</h2>" in html_report(run)


GOLD_SETS = sorted((REPO / "gold").glob("*.jsonl"))


@pytest.mark.corpora
@pytest.mark.skipif(os.environ.get("SPEC2TEST_CORPORA") != "1", reason="set SPEC2TEST_CORPORA=1")
@pytest.mark.skipif(not GOLD_SETS, reason="no gold set in gold/ yet")
@pytest.mark.parametrize("path", GOLD_SETS, ids=lambda p: p.stem)
def test_gold_set_checks_out_against_its_corpus_document(path):
    [entry] = [d for d in corpora.load_manifest(REPO / "corpora") if d.id == path.stem]
    gold = load_gold(path, corpora.parse(entry, REPO / "corpora"))
    assert gold.requirements
    assert all(g.verification_method is not None for g in gold.requirements)
