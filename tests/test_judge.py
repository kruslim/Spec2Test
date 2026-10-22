"""The judge: rubric grading, the model-family warning, calibration against a person's
grades, and the CLI, all against FakeLLM.

The calibration sample in tests/fixtures/calibration/ grades the invented widget-lamp
items; its grades were made up to give known agreement numbers.
"""

import json
import os
from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import extract
from spec2test.generate import Generation, generate
from spec2test.judge import (
    RUBRIC,
    Agreement,
    Calibration,
    Criterion,
    Grade,
    Grades,
    Judging,
    SampleError,
    calibrate,
    judge,
    judge_warnings,
    load_sample,
    new_sample,
    pass_rates,
)
from spec2test.judge.prompts import SYSTEM
from spec2test.lint import lint
from spec2test.llm import AnthropicLLM, FakeLLM, OpenAICompatLLM

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "calibration" / "widget-lamp.jsonl"
ROOT = Path(__file__).parent.parent


@pytest.fixture
def generation(widget_lamp, fake_llm) -> Generation:
    linted = lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements
    result = generate(linted, fake_llm)
    fake_llm.calls.clear()
    return result


def grades(**failed: str) -> Grades:
    return Grades(
        **{
            c.value: Grade(reason=failed.get(c.value, "fine"), passed=c.value not in failed)
            for c in Criterion
        }
    )


# Judging


def test_the_judge_grades_every_item_against_the_rubric(generation, fake_llm):
    result = judge(generation, fake_llm)
    assert [(j.item_id, j.grades.acceptable) for j in result.judgements] == [
        ("widget-lamp:2.1:1:test_case", True),
        ("widget-lamp:2.2:1:checklist", True),
        ("widget-lamp:2.2:2:checklist", False),
        ("widget-lamp:2.3:1:evidence", False),
        ("widget-lamp:2.5:1:checklist", True),
    ]
    assert not result.failed
    call = fake_llm.calls[0]
    assert call.system == SYSTEM and call.schema is Grades
    assert '<item kind="test_case">' in call.prompt
    assert '"pass_criterion": "Luminous flux ≥ 400 lm, reached ≤ 2.0 s after switch-on"' in (
        call.prompt
    )
    assert "The lamp shall emit at least 400 lm within 2.0 s of switch-on." in call.prompt


def test_test_case_quality_is_reported_per_kind(generation, fake_llm):
    rates = pass_rates(judge(generation, fake_llm))
    assert list(rates) == ["all", "checklist", "test_case", "evidence"]
    summary = {k: {m.name: (m.numerator, m.denominator) for m in v} for k, v in rates.items()}
    assert summary["all"] == {
        "complete": (5, 5),
        "faithful": (5, 5),
        "executable": (4, 5),
        "decisive": (4, 5),
        "acceptable": (3, 5),
    }
    assert summary["test_case"]["acceptable"] == (1, 1)
    assert summary["evidence"]["decisive"] == (0, 1)


def test_the_judge_only_grades_the_kinds_asked_for(generation, fake_llm):
    result = judge(generation, fake_llm, kinds=["test_case"])
    assert [j.item_id for j in result.judgements] == ["widget-lamp:2.1:1:test_case"]


def test_a_judge_from_the_generators_family_is_warned_about(generation, fake_llm):
    # FakeLLM wrote the items and FakeLLM judges them.
    [warning] = judge(generation, fake_llm).warnings
    assert warning.startswith("the judge 'FakeLLM' is from the same family as the generator")
    assert judge_warnings("claude-opus-5-5", generators=["kimi-for-coding"]) == []
    [same] = judge_warnings("claude-sonnet-5-5", generators=["claude-opus-5-5", None])
    assert "'claude-opus-5-5'" in same
    [labeler] = judge_warnings("kimi-for-coding", labeler="kimi-k3")
    assert "agreeing with itself" in labeler
    assert judge_warnings("claude-opus-5-5", labeler="Kenneth") == []


def test_items_the_judge_cannot_grade_are_kept_as_failures(generation):
    orphan = generation.items[0].model_copy(update={"requirement_ids": ["nope:1:1"]})
    broken = generation.model_copy(update={"items": [orphan, *generation.items[1:2]]})
    result = judge(broken, FakeLLM([]))
    assert [(f.item_id, f.error[:20]) for f in result.failed] == [
        ("widget-lamp:2.1:1:test_case", "requirement(s) nope:"),
        ("widget-lamp:2.2:1:checklist", "no fixture for schem"),
    ]


def test_the_written_rubric_is_the_judges_rubric():
    doc = (ROOT / "docs" / "RUBRIC.md").read_text(encoding="utf-8")
    for criterion, text in RUBRIC.items():
        assert f"**{criterion.value}**: {text}\n" in doc
        assert f"- {criterion.value}: {text}\n" in SYSTEM
    assert set(RUBRIC) == set(Criterion) == {c for c in Criterion if c.value in Grades.model_fields}


# Calibration


def test_agreement_and_kappa():
    a = Agreement(criterion="x", both_pass=4, both_fail=1)
    assert (a.n, a.agreement, a.kappa) == (5, 1.0, 1.0)
    lenient = Agreement(criterion="x", both_pass=4, judge_only_pass=1)
    assert lenient.agreement == 0.8 and lenient.kappa == pytest.approx(0.0)
    assert Agreement(criterion="x", both_pass=5).kappa is None  # one verdict throughout
    assert Agreement(criterion="x").agreement is None
    mixed = Agreement(
        criterion="x", both_pass=2, both_fail=1, judge_only_pass=1, person_only_pass=1
    )
    assert mixed.kappa == pytest.approx((0.6 - 0.52) / 0.48)


def test_calibrate_reports_judge_vs_person_agreement(fake_llm):
    sample = load_sample(SAMPLE)
    assert sample.labeler == "Spec2Test tests" and len(sample.lines) == 6
    result = calibrate(sample, fake_llm)
    assert result.unlabeled == 1 and not result.failed
    assert len(fake_llm.calls) == 5  # the ungraded line is not sent
    table = {
        a.criterion: (a.n, a.both_pass, a.both_fail, a.judge_only_pass, a.person_only_pass)
        for a in result.agreements
    }
    assert table == {
        "complete": (5, 5, 0, 0, 0),
        "faithful": (5, 4, 0, 1, 0),
        "executable": (5, 4, 0, 0, 1),
        "decisive": (5, 4, 1, 0, 0),
        "acceptable": (5, 2, 1, 1, 1),
    }
    assert [(d.item_id, d.criterion, d.judge, d.person) for d in result.disagreements] == [
        ("widget-lamp:2.5:1:checklist", "faithful", True, False),
        ("widget-lamp:2.2:2:checklist", "executable", False, True),
    ]
    assert result.disagreements[1].judge_reason.startswith("Does not say where")
    assert result.disagreements[1].note.startswith("The housing has one flat face")
    assert [w[:40] for w in result.warnings] == ["the judge 'FakeLLM' is from the same fam"]


def test_a_partly_graded_line_counts_only_where_graded(tmp_path):
    line = json.loads(SAMPLE.read_text(encoding="utf-8").splitlines()[0])
    line["labels"] = {"complete": False}
    path = tmp_path / "partial.jsonl"
    path.write_text(json.dumps(line), encoding="utf-8")
    result = calibrate(load_sample(path), FakeLLM.from_dir(FIXTURES / "llm"))
    assert [a.n for a in result.agreements] == [1, 0, 0, 0, 0]
    assert any("does not say who graded it" in w for w in result.warnings)


def test_rescoring_reuses_the_judges_grades(fake_llm, tmp_path):
    first = calibrate(load_sample(SAMPLE), fake_llm)
    saved = Calibration.model_validate_json(first.model_dump_json())
    assert calibrate(load_sample(SAMPLE), None, previous=saved).agreements == first.agreements

    # Fix one grade: the person now agrees the mounting item is faithful.
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    fixed = [ln.replace('"faithful":false', '"faithful":true') for ln in lines]
    path = tmp_path / "widget-lamp.jsonl"
    path.write_text("\n".join(fixed), encoding="utf-8")
    again = calibrate(load_sample(path), None, previous=saved)
    assert again.agreements[1].agreement == 1.0
    with pytest.raises(ValueError, match="needs a judge model"):
        calibrate(load_sample(path), None)


def test_new_sample_draws_ungraded_items(generation):
    everything = new_sample(generation)
    assert [line.item.id for line in everything] == [i.id for i in generation.items]
    assert all(line.labels.acceptable is None for line in everything)
    assert everything[0].generator == "FakeLLM"
    assert everything[0].requirements[0].id == "widget-lamp:2.1:1"
    some = new_sample(generation, size=2, seed=1)
    assert len(some) == 2 and some == new_sample(generation, size=2, seed=1)


def test_load_sample_lists_every_problem(tmp_path):
    line = json.loads(SAMPLE.read_text(encoding="utf-8").splitlines()[0])
    orphan = {**line, "requirements": [line["requirements"][0] | {"id": "other"}]}
    path = tmp_path / "bad.jsonl"
    path.write_text(
        "\n".join([json.dumps(line), "{", json.dumps(line), json.dumps(orphan)]), encoding="utf-8"
    )
    with pytest.raises(SampleError) as exc:
        load_sample(path)
    problems = str(exc.value).splitlines()
    assert problems[0].startswith("bad.jsonl:2: not JSON")
    assert problems[1] == (
        "bad.jsonl:3: duplicate item 'widget-lamp:2.1:1:test_case' (first on line 1)"
    )
    assert problems[2] == "bad.jsonl:4: no record for requirement(s) widget-lamp:2.1:1"


# CLI


def test_cli_judge(generation, tmp_path, capsys):
    saved = tmp_path / "generation.json"
    saved.write_text(generation.model_dump_json(), encoding="utf-8")
    assert cli.main(["judge", str(saved), "--fake", str(FIXTURES / "llm")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("judge: FakeLLM · generator: FakeLLM · 5 items judged, 0 failed\n")
    assert "warning: the judge 'FakeLLM' is from the same family" in out
    assert "acceptable       60.0% 3/5" in out
    assert "widget-lamp:2.3:1:evidence  not acceptable\n    decisive: The [maximum force" in out

    args = ["judge", str(saved), "--fake", str(FIXTURES / "llm"), "--kind", "test_case", "--json"]
    assert cli.main(args) == 0
    assert len(Judging.model_validate_json(capsys.readouterr().out).judgements) == 1


def test_cli_calibrate(generation, tmp_path, capsys):
    saved = tmp_path / "generation.json"
    saved.write_text(generation.model_dump_json(), encoding="utf-8")
    assert cli.main(["calibrate", str(saved), "--new-sample", "--size", "3"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3

    fake = ["--fake", str(FIXTURES / "llm")]
    assert cli.main(["calibrate", str(SAMPLE), *fake]) == 0
    out = capsys.readouterr().out
    assert "labeler: Spec2Test tests · 5 items compared, 1 ungraded, 0 failed" in out
    assert "decisive      5     100.0%    1.00          4          1           0            0" in (
        out
    )
    assert "widget-lamp:2.5:1:checklist  faithful: judge pass, person fail" in out

    assert cli.main(["calibrate", str(SAMPLE), *fake, "--json"]) == 0
    results = tmp_path / "calibration.json"
    results.write_text(capsys.readouterr().out, encoding="utf-8")
    assert cli.main(["calibrate", str(SAMPLE), "--rescore", str(results)]) == 0
    assert "5 items compared" in capsys.readouterr().out


def test_cli_calibrate_reports_bad_input(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{", encoding="utf-8")
    assert cli.main(["calibrate", str(bad), "--fake", "."]) == 2
    assert "bad.jsonl:1: not JSON" in capsys.readouterr().err


def test_grades_schema_is_strict():
    with pytest.raises(ValueError):
        Grades.model_validate({**grades().model_dump(), "overall": {"reason": "", "passed": True}})
    with pytest.raises(ValueError):
        Grades.model_validate({"complete": {"reason": "r", "passed": True}})


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("KIMI_CODE_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and KIMI_CODE_API_KEY",
)
def test_live_kimi_generates_and_claude_judges(widget_lamp, fake_llm):
    linted = lint(extract(widget_lamp, fake_llm).requirements).requirements
    generation = generate(linted, OpenAICompatLLM.preset("kimi-code"), doc=widget_lamp)
    assert {i.kind.value for i in generation.items} == {"test_case", "checklist", "evidence"}
    result = judge(generation, AnthropicLLM(effort="low"), kinds=["test_case"])
    assert result.judgements and not result.warnings
