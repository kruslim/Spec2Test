"""Generation, the LLM judge and judge calibration, against FakeLLM fixtures.

The widget lamp spec is invented for these tests (tests/conftest.py); its canned
generation and judge answers are in tests/fixtures/llm/generate_*.json and judge_*.json.
Every other requirement here is invented too.
"""

import json
import os
from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import extract
from spec2test.generate import (
    Generation,
    check_item,
    context,
    generate,
    not_verifiable,
    references,
    render,
    source_context,
)
from spec2test.generate.prompts import SYSTEM as GENERATE_SYSTEM
from spec2test.ingest import Block, build_document
from spec2test.judge import (
    RUBRIC,
    Agreement,
    CalibrationItem,
    HumanLabel,
    JudgeAnswer,
    LabelsExistError,
    SameModelError,
    Verdict,
    calibrate,
    compare,
    judge,
    judge_warnings,
    load_sample,
    make_sample,
    quality_report,
    write_sample,
)
from spec2test.lint import lint
from spec2test.llm import FakeLLM, Fixture, OpenAICompatLLM
from spec2test.schema import (
    ChecklistFields,
    Criterion,
    EvidenceFields,
    Requirement,
    SourceAnchor,
    TestCaseFields,
    VerificationItemKind,
)

FIXTURES = Path(__file__).parent / "fixtures"


def req(text, id="spec:1:1", section="1", **fields):
    anchor = SourceAnchor(doc="spec", page=1, section=section, start=0, end=len(text), quote=text)
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=id, source_anchor=anchor, text=text, **fields)


def fixture(schema, response, *prompt_contains):
    return Fixture(schema=schema.__name__, response=response, prompt_contains=prompt_contains)


TIMING = req(
    "The display shall show the image within 2.0 s of power-on.",
    verification_method="test",
    criteria=[Criterion(parameter="display time", comparator="le", value="2.0", unit="s")],
)


def case_fields(pass_criterion, expected="Image shown."):
    return {"steps": ["Power on", "Time the image"], "expected_result": expected,
            "pass_criterion": pass_criterion}  # fmt: skip


@pytest.fixture
def linted(widget_lamp, fake_llm):
    return lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements


@pytest.fixture
def generation(linted, fake_llm, widget_lamp):
    return generate(linted, fake_llm, doc=widget_lamp)


# Generation


def test_each_method_renders_its_output_type(generation):
    kinds = {i.requirement_ids[0]: (i.kind, type(i.fields)) for i in generation.items}
    assert kinds == {
        "widget-lamp:2.1:1": (VerificationItemKind.TEST_CASE, TestCaseFields),
        "widget-lamp:2.2:1": (VerificationItemKind.CHECKLIST, ChecklistFields),
        "widget-lamp:2.2:2": (VerificationItemKind.CHECKLIST, ChecklistFields),
        "widget-lamp:2.3:1": (VerificationItemKind.EVIDENCE, EvidenceFields),
        "widget-lamp:2.5:1": (VerificationItemKind.CHECKLIST, ChecklistFields),
    }
    assert not generation.rejected and not generation.failed and not generation.unclassified
    assert generation.model == "FakeLLM"
    assert {r.id for r in generation.requirements} == set(kinds)


def test_items_are_named_after_their_requirement_and_kind(generation):
    assert [i.id for i in generation.items][:2] == ["widget-lamp:2.1:1:TC", "widget-lamp:2.2:1:CL"]


def test_analysis_renders_an_evidence_item_without_a_procedure():
    r = req("The beam shall carry 5 kN.", verification_method="analysis",
            criteria=[Criterion(parameter="load", value="5", unit="kN")])  # fmt: skip
    answer = {"description": "Stress analysis of the beam.", "evidence": ["Calculation report"],
              "acceptance_criterion": "Margin of safety >= 0 at 5 kN."}  # fmt: skip
    llm = FakeLLM([fixture(EvidenceFields, answer, "an analysis")])
    [item] = generate([r], llm).items
    assert item.kind is VerificationItemKind.EVIDENCE and item.fields.procedure == []


def test_prompt_carries_criteria_conditions_and_flags(linted, fake_llm, widget_lamp):
    generate(linted, fake_llm, doc=widget_lamp)
    prompt = next(c.prompt for c in fake_llm.calls if c.schema is EvidenceFields)
    assert "Write an evidence item for a demonstration" in prompt
    assert "operable with one hand" in prompt and "testability_flags" in prompt
    assert all(c.system == GENERATE_SYSTEM for c in fake_llm.calls if c.schema is EvidenceFields)


def test_a_verdict_without_the_criterion_value_is_sent_back_once():
    llm = FakeLLM(
        [
            fixture(TestCaseFields, case_fields("Display time <= 2.0 s."), "failed the check"),
            fixture(TestCaseFields, case_fields("Image appears quickly.")),
        ]
    )
    result = generate([TIMING], llm)
    assert result.retried == 1 and len(result.items) == 1
    assert "'2.0'" in llm.calls[1].prompt and "Image appears quickly." in llm.calls[1].prompt


def test_an_item_still_wrong_after_the_retry_is_rejected():
    llm = FakeLLM([fixture(TestCaseFields, case_fields("Image appears quickly."))])
    result = generate([TIMING], llm)
    assert not result.items
    [rejected] = result.rejected
    assert rejected.requirement_id == "spec:1:1" and "'2.0'" in rejected.errors[0]


def test_check_item_matches_whole_values_only():
    fields = TestCaseFields(**case_fields("Display time <= 12.05 s."))
    assert check_item(TIMING, fields)
    assert not check_item(TIMING, TestCaseFields(**case_fields("Time (<=2.0 s).")))
    between = req("Between 5 and 10 V.", verification_method="inspection",
                  criteria=[Criterion(parameter="v", comparator="between", value="5",
                                      upper_value="10", unit="V")])  # fmt: skip
    errors = check_item(between, ChecklistFields(inspect="Meter", pass_condition="Reads 5 V."))
    assert len(errors) == 1 and "'10'" in errors[0]


def test_a_demonstration_needs_a_procedure():
    r = req("The door shall open by hand.", verification_method="demonstration")
    fields = EvidenceFields(description="d", evidence=["video"], acceptance_criterion="Opens.")
    assert "procedure" in check_item(r, fields)[0]


def test_unclassified_requirements_are_listed_not_sent():
    llm = FakeLLM([])
    result = generate([req("The lamp shall be safe.")], llm)
    assert result.unclassified == ["spec:1:1"] and not llm.calls


def test_a_failed_call_is_recorded():
    result = generate([TIMING], FakeLLM([]))
    assert result.failed[0].requirement_id == "spec:1:1" and not result.items


SEALING = "The lamp shall meet S3.1 through S3.3 after the warm-up period."


@pytest.fixture
def outdoor_spec():
    return build_document(
        "spec",
        [
            Block("text", "S1 Definitions.", 1),
            Block("text", "Warm-up period means the time from power-on until output is steady:", 1),
            Block("text", "(a) measured at 20 °C.", 1),
            Block("text", "Housing means the outer shell of the lamp.", 1),
            Block("text", "S2 Lamps.", 1),
            Block("text", "S2.1 Outdoor lamp.", 1),
            Block("text", f"S2.1.1 Sealing. {SEALING}", 1),
            Block("text", "S3 Tests.", 1),
            Block("text", "S3.1 Spray. Spray it with water.", 1),
            Block("text", "S3.1.1 Use fresh water.", 1),
            Block("text", "S3.2 Drop. Drop it from 1 m.", 1),
            Block("text", "S3.3 Heat. Heat it to 50 °C.", 1),
            Block("text", "S3.4 Unrelated.", 1),
        ],
    )


def cited(doc, anchor, text, **fields):
    """A requirement quoting `text` from section `anchor` of `doc`, at its real offsets."""
    section = doc.section(anchor)
    start = doc.text.index(text, section.start)
    quote = SourceAnchor(doc=doc.doc_id, page=1, section=anchor, start=start,
                         end=start + len(text), quote=text)  # fmt: skip
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=f"spec:{anchor}:1", source_anchor=quote, text=text, **fields)


def test_context_has_headings_definitions_and_referenced_ranges(outdoor_spec):
    ctx = source_context(cited(outdoor_spec, "S2.1.1", SEALING), outdoor_spec)
    assert ctx.headings == ["S2 Lamps", "S2.1 Outdoor lamp", "S2.1.1 Sealing"]
    assert ctx.section.anchor == "S2.1.1" and ctx.section.text.startswith("S2.1.1 Sealing.")
    [warm_up] = ctx.definitions
    assert warm_up.anchor == "Warm-up period" and "(a) measured at 20 °C." in warm_up.text
    assert "Housing" not in warm_up.text
    assert [r.anchor for r in ctx.references] == ["S3.1", "S3.2", "S3.3"]
    assert "S3.1.1 Use fresh water." in ctx.references[0].text
    assert all("S3.4" not in r.text for r in ctx.references)


def test_a_clause_inside_one_already_sent_is_not_sent_again(outdoor_spec):
    r = cited(outdoor_spec, "S2.1.1", SEALING)
    r = r.model_copy(update={"conditions": ["as in S3.1.1 and S3"]})
    assert [c.anchor for c in references(r, outdoor_spec)] == ["S3"]


@pytest.fixture
def numbered_spec():
    """Plain-numbered clauses and an annex that restarts its numbering, as in UNECE."""
    return build_document(
        "num",
        [
            Block("text", "1. SCOPE", 1),
            Block("text", "1.1 This regulation applies to lamps.", 1),
            Block("text", "2. REQUIREMENTS", 1),
            Block("text", "2.1 The lamp shall have 2 bulbs.", 1),
            Block("text", "2.2 The lamp shall pass the spray test of paragraph 2.1 of Annex 3.", 1),
            Block("text", "2.3 Each bulb shall be marked as specified in paragraph 1.", 1),
            Block("text", "2.4 The lamp shall pass Annex 3, paragraph 2.2, and paragraph 2.1.", 1),
            Block("heading", "Annex 3", 2),
            Block("text", "Spray test", 2),
            Block("text", "2.1 Spray the lamp for 10 min.", 2),
            Block("text", "2.2 Then dry it as in 2.1.", 2),
        ],
    )


def refs(doc, anchor):
    text = doc.section(anchor).text.split(" ", 1)[1]
    return [c.anchor for c in references(cited(doc, anchor, text), doc)]


def test_a_bare_number_is_a_reference_only_after_a_clause_word(numbered_spec):
    assert refs(numbered_spec, "2.1") == []  # "2 bulbs" is not clause 2
    assert refs(numbered_spec, "2.3") == ["1"]  # "paragraph 1" is


def test_annex_clauses_resolve_inside_their_annex(numbered_spec):
    assert refs(numbered_spec, "2.2") == ["Annex 3 2.1"]  # not the body's 2.1
    assert refs(numbered_spec, "2.4") == ["Annex 3 2.2", "2.1"]  # then the body's 2.1
    assert refs(numbered_spec, "Annex 3 2.2") == ["Annex 3 2.1"]  # its own annex first


def test_referenced_text_is_capped(outdoor_spec, monkeypatch):
    monkeypatch.setattr(context, "MAX_CONTEXT_CHARS", 40)
    refs = references(cited(outdoor_spec, "S2.1.1", SEALING), outdoor_spec)
    assert refs[-1].text.endswith(" [...]")
    assert sum(len(r.text.removesuffix(" [...]")) for r in refs) <= 40


def test_the_prompts_carry_the_context(outdoor_spec):
    r = cited(outdoor_spec, "S2.1.1", SEALING, verification_method="test")
    llm = FakeLLM([fixture(TestCaseFields, case_fields("Meets S3.1 to S3.3."))])
    [item] = generate([r], llm, doc=outdoor_spec).items
    prompt = llm.calls[0].prompt
    assert "Within: S2 Lamps > S2.1 Outdoor lamp > S2.1.1 Sealing" in prompt
    assert '<definition term="Warm-up period">' in prompt
    assert '<source_section anchor="S2.1.1">' in prompt
    assert '<referenced_clause anchor="S3.2">' in prompt
    judging = judge(Generation(model="gen", requirements=[r], items=[item]),
                    FakeLLM([], model="judge"), doc=outdoor_spec)  # fmt: skip
    assert judging.failed  # no fixture; the prompt was still built from the same context


@pytest.mark.parametrize(
    ("text", "strength", "skipped"),
    [
        ("The lid may be opened by hand.", "may", True),
        ("The lid need not be removable.", "shall", True),
        ("The reflectance shall be determined in accordance with SAE J964.", "shall", True),
        ("The reference points shall be those established in Standard No. 104.", "shall", True),
        ("The lid shall not be removable.", "shall", False),
        ("The lid need not be removable but shall be lockable.", "shall", False),
    ],
)
def test_permissions_and_definitions_are_not_verifiable(text, strength, skipped):
    assert (not_verifiable(req(text, strength=strength)) is not None) is skipped


def test_a_definition_with_a_limit_is_kept():
    radius = Criterion(parameter="radius", comparator="ge", value="889", unit="mm")
    r = req("The radius shall be determined using S12 and be at least 889 mm.", criteria=[radius])
    assert not_verifiable(r) is None


def test_skipped_requirements_are_listed_not_sent():
    llm = FakeLLM([])
    result = generate(
        [req("The lid may be opened.", strength="may", verification_method="test")], llm
    )
    [skipped] = result.skipped
    assert skipped.requirement_id == "spec:1:1" and "permission" in skipped.reason
    assert not llm.calls and not result.failed


def test_render_shows_each_kind(generation):
    text = {i.kind: render(i) for i in generation.items}
    assert "Steps:\n    1. Start the photometer" in text[VerificationItemKind.TEST_CASE]
    assert "Pass if: Luminous flux >= 400 lm" in text[VerificationItemKind.TEST_CASE]
    assert "Inspect:" in text[VerificationItemKind.CHECKLIST]
    assert "Evidence:\n    - Video" in text[VerificationItemKind.EVIDENCE]


# Judge


def judge_llm(model="fake-judge"):
    return FakeLLM.from_dir(FIXTURES / "llm", model=model)


def test_judge_scores_every_item_against_the_rubric(generation):
    result = judge(generation, judge_llm())
    assert len(result.judgements) == len(generation.items) and not result.failed
    by_id = {j.item_id: j for j in result.judgements}
    assert set(by_id["widget-lamp:2.1:1:TC"].verdicts) == set(RUBRIC)
    assert by_id["widget-lamp:2.1:1:TC"].score == 1.0
    switch = by_id["widget-lamp:2.3:1:EV"]
    assert switch.score == 0.8 and not switch.acceptable
    assert result.judge_model == "fake-judge" and result.generator_model == "FakeLLM"
    assert not result.warnings


def test_judge_answer_fields_are_the_rubric():
    assert [n for n in JudgeAnswer.model_fields if n != "acceptable"] == list(RUBRIC)


def test_the_generator_cannot_judge_itself(generation):
    with pytest.raises(SameModelError):
        judge(generation, judge_llm(model=None))
    with pytest.raises(SameModelError):
        judge_warnings("Kimi-For-Coding", "kimi-for-coding")


def test_a_judge_from_the_generator_family_is_warned_about():
    [warning] = judge_warnings("kimi-k3", "kimi-for-coding")
    assert "same family" in warning
    assert judge_warnings("gemini-3.1-pro-preview", "kimi-for-coding") == []


def test_quality_report_lists_scores_per_kind(generation):
    report = quality_report(judge(generation, judge_llm()))
    assert "test_case" in report and "checklist" in report and "evidence" in report
    assert "acceptable" in report and "objective_verdict" in report
    rows = {line.split()[0]: line.split()[1:] for line in report.splitlines() if line.strip()}
    assert rows["items"] == ["3", "1", "1", "5"]  # checklist, test_case, evidence, all


def test_judge_failures_are_recorded(generation):
    result = judge(generation, FakeLLM([], model="empty-judge"))
    assert len(result.failed) == len(generation.items) and not result.judgements


# Calibration


def labeled(item, verdict=True, acceptable=True):
    return item.model_copy(
        update={
            "human": HumanLabel(
                labeler="tester",
                verdicts=dict.fromkeys(RUBRIC, verdict),
                acceptable=acceptable,
            )
        }
    )


def test_sample_spreads_across_kinds_and_is_repeatable(generation, widget_lamp):
    sample = make_sample(generation, 3, doc=widget_lamp, seed=1)
    assert {s.item.kind for s in sample} == set(VerificationItemKind)
    assert [s.item.id for s in sample] == [
        s.item.id for s in make_sample(generation, 3, doc=widget_lamp, seed=1)
    ]
    assert all(not s.human.complete for s in sample)
    assert len(make_sample(generation, 50)) == len(generation.items)


def test_sample_round_trips_through_jsonl(generation, tmp_path):
    path = tmp_path / "sample.jsonl"
    write_sample(make_sample(generation, 5), path)
    assert [s.item.id for s in load_sample(path)] == [s.item.id for s in make_sample(generation, 5)]
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["human"] == {
        "labeler": None,
        "verdicts": dict.fromkeys(RUBRIC),
        "acceptable": None,
        "notes": "",
    }


def test_calibration_reports_agreement_per_criterion(generation):
    sample = make_sample(generation, 5)
    # The person passes everything; the judge fails the switch on objective_verdict.
    sample = [labeled(s) for s in sample[:4]] + [sample[4]]
    result = calibrate(sample, judge_llm())
    assert result.labeled == 4 and result.unlabeled == 1 and result.labelers == ["tester"]
    agreements = {a.name: a for a in result.agreements}
    assert set(agreements) == {*RUBRIC, "acceptable"}
    assert agreements["covers"].agreement == 1.0 and agreements["covers"].n == 4
    switch_in_sample = any(s.item.id == "widget-lamp:2.3:1:EV" for s in sample[:4])
    expected = 0.75 if switch_in_sample else 1.0
    assert agreements["objective_verdict"].agreement == expected
    assert any("rough estimates" in w for w in result.warnings)


def test_disagreements_carry_the_judges_reason(generation):
    [switch] = [
        s for s in make_sample(generation, 5) if s.item.kind is VerificationItemKind.EVIDENCE
    ]
    result = calibrate([labeled(switch)], judge_llm())
    names = {d.name for d in result.disagreements}
    assert names == {"objective_verdict", "acceptable"}
    reason = next(d.judge_reason for d in result.disagreements if d.name == "objective_verdict")
    assert "force limit" in reason


def test_kappa():
    a = Agreement(name="x", both_pass=40, both_fail=40, judge_only=10, human_only=10)
    assert a.agreement == 0.8 and a.kappa == pytest.approx(0.6)
    assert Agreement(name="x", both_pass=5).kappa is None
    assert Agreement(name="x").agreement is None


def test_calibration_can_reuse_saved_judgements(generation):
    saved = judge(generation, judge_llm())
    sample = [labeled(s) for s in make_sample(generation, 5)]
    result = calibrate(sample, saved=saved)
    assert result.labeled == 5 and not result.judging.failed
    assert result.judge_model == "fake-judge"
    with pytest.raises(ValueError, match="either"):
        calibrate(sample)


def test_a_saved_judgement_of_a_different_text_is_not_scored(generation):
    saved = judge(generation, judge_llm())
    assert all(j.graded for j in saved.judgements)
    sample = [labeled(s) for s in make_sample(generation, 5)]
    # Regenerated: the same deterministic id now names an item with another text.
    first = sample[0]
    requirement = first.requirement.model_copy(update={"text": "The lamp shall be blue."})
    sample[0] = first.model_copy(update={"requirement": requirement})
    result = calibrate(sample, saved=saved)
    [failed] = result.judging.failed
    assert failed.item_id == first.item.id and "different text" in failed.error
    assert sum(a.n for a in result.agreements if a.name == "covers") == 4

    old = saved.model_copy(
        update={"judgements": [j.model_copy(update={"graded": None}) for j in saved.judgements]}
    )
    assert any("no hash" in w for w in calibrate(sample[1:], saved=old).warnings)


def test_compare_leaves_out_items_graded_on_different_texts(generation):
    first, second = judge(generation, judge_llm()), judge(generation, judge_llm("other-judge"))
    moved = second.judgements[0].model_copy(update={"graded": "0" * 64})
    second.judgements[0] = moved
    result = compare(first, second)
    assert result.compared == len(first.judgements) - 1
    assert any("different texts" in w for w in result.warnings)


def test_a_labeler_named_like_a_model_is_warned_about(generation):
    sample = [labeled(s) for s in make_sample(generation, 2)]
    sample[0].human.labeler = "claude-opus-5-5"
    result = calibrate(sample, judge_llm())
    assert any("'claude-opus-5-5' looks like a model ID" in w for w in result.warnings)


def test_a_sample_with_human_labels_is_not_overwritten(generation, tmp_path):
    path = tmp_path / "sample.jsonl"
    write_sample(make_sample(generation, 3), path)
    write_sample(make_sample(generation, 3), path)  # nothing labeled yet: fine
    started = make_sample(generation, 3)
    started[1].human.notes = "half done"
    write_sample(started, path, force=True)
    with pytest.raises(LabelsExistError, match="holds human labels on 1 line"):
        write_sample(make_sample(generation, 3), path)
    assert load_sample(path)[1].human.notes == "half done"
    path.write_text("not a sample\n", encoding="utf-8")
    with pytest.raises(LabelsExistError, match="not a readable sample"):
        write_sample(make_sample(generation, 3), path)


def test_calibration_refuses_the_generator_as_judge(generation):
    sample = [labeled(s) for s in make_sample(generation, 2)]
    with pytest.raises(SameModelError):
        calibrate(sample, judge_llm(model=None))


def test_human_label_is_complete_only_when_every_field_is_set():
    assert not HumanLabel().complete
    label = HumanLabel(labeler="x", verdicts=dict.fromkeys(RUBRIC, True), acceptable=True)
    assert label.complete
    assert not label.model_copy(update={"verdicts": {"covers": True}}).complete


def test_judgement_score_survives_a_round_trip(generation):
    saved = judge(generation, judge_llm())
    again = type(saved).model_validate_json(saved.model_dump_json())
    assert again.judgements[0].score == saved.judgements[0].score
    assert isinstance(again.judgements[0].verdicts["covers"], Verdict)


def test_calibration_item_needs_the_generator_model(generation):
    with pytest.raises(ValueError):
        CalibrationItem(requirement=generation.requirements[0], item=generation.items[0])


# CLI


@pytest.fixture
def lint_json(tmp_path, widget_lamp, fake_llm):
    extraction = extract(widget_lamp, fake_llm)
    path = tmp_path / "lint.json"
    path.write_text(lint(extraction.requirements, fake_llm).model_dump_json(), encoding="utf-8")
    doc_json = tmp_path / "doc.json"
    doc_json.write_text(widget_lamp.model_dump_json(), encoding="utf-8")
    return path, doc_json


def test_cli_generate_judge_calibrate(lint_json, tmp_path, capsys):
    path, doc_json = lint_json
    fake = str(FIXTURES / "llm")
    assert cli.main(["generate", str(path), "--fake", fake]) == 0
    out = capsys.readouterr().out
    assert "Test case widget-lamp:2.1:1:TC" in out
    assert "5 items (3 checklist, 1 evidence, 1 test_case), 0 rejected" in out
    assert "0 unclassified, 0 skipped" in out

    assert cli.main(["generate", str(path), "--fake", fake, "--doc", str(doc_json), "--json"]) == 0
    gen_path = tmp_path / "generation.json"
    gen_path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert len(Generation.model_validate_json(gen_path.read_text(encoding="utf-8")).items) == 5

    assert cli.main(["judge", str(gen_path), "--fake", fake]) == 2
    assert "is the model that generated the items" in capsys.readouterr().err
    assert cli.main(["judge", str(gen_path), "--fake", fake, "--model", "fake-judge"]) == 0
    assert "judge fake-judge, generator FakeLLM: 5 items judged" in capsys.readouterr().out

    sample = tmp_path / "sample.jsonl"
    assert (
        cli.main(["calibrate", "sample", str(gen_path), "--out", str(sample), "--size", "3"]) == 0
    )
    assert "3 items (1 checklist, 1 evidence, 1 test_case)" in capsys.readouterr().out
    lines = [labeled(s) for s in load_sample(sample)]
    write_sample(lines, sample)
    score = ["calibrate", "score", str(sample), "--fake", fake, "--model", "fake-judge"]
    assert cli.main(score) == 0
    out = capsys.readouterr().out
    assert "judge fake-judge vs. tester: 3 labeled items" in out
    assert "objective_verdict" in out and "disagreements:" in out


def test_cli_generate_reads_an_eval_run(tmp_path, widget_lamp, fake_llm, capsys):
    from spec2test.evals import evaluate, load_gold

    gold = load_gold(FIXTURES / "gold" / "widget-lamp.jsonl", widget_lamp)
    run = evaluate(widget_lamp, gold, fake_llm, corpus="widget-lamp")
    path = tmp_path / "results.json"
    path.write_text(run.model_dump_json(), encoding="utf-8")
    assert cli.main(["generate", str(path), "--fake", str(FIXTURES / "llm")]) == 0
    assert f"{len(run.predicted)} items" in capsys.readouterr().out


def test_cli_reports_bad_input(tmp_path, capsys):
    assert cli.main(["generate", str(tmp_path / "missing.json"), "--fake", "x"]) == 2
    assert "error:" in capsys.readouterr().err


def test_gemini_is_a_preset():
    llm = OpenAICompatLLM.preset("gemini")
    assert llm.api_key_env == "GEMINI_API_KEY" and llm.model.startswith("gemini")


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("KIMI_CODE_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and KIMI_CODE_API_KEY",
)
def test_live_generate_with_kimi():
    # No judge step: Gemini judges only the calibration sample, and Opus judges in sessions.
    result = generate([TIMING], OpenAICompatLLM.preset("kimi-code"))
    [item] = result.items
    assert item.kind is VerificationItemKind.TEST_CASE and "2.0" in item.fields.pass_criterion
