"""Judging in a Claude Code session (`SessionLLM`) and comparing two judges, offline.

The widget lamp spec is invented for these tests (tests/conftest.py). The "session"
answers are written here from the FakeLLM judge fixtures (tests/fixtures/llm/judge_*.json),
standing in for the answers a model writes in a session.
"""

import json
from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import extract
from spec2test.generate import generate
from spec2test.judge import (
    JudgeAnswer,
    Verdict,
    compare,
    comparison_report,
    judge,
    make_sample,
    write_sample,
)
from spec2test.lint import lint
from spec2test.llm import (
    AnswerMissingError,
    FakeLLM,
    LLMOutputError,
    SessionLLM,
    answer_line,
    load_requests,
    request_key,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def generation(widget_lamp, fake_llm):
    requirements = lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements
    return generate(requirements, fake_llm, doc=widget_lamp)


def judge_llm(model="fake-judge"):
    return FakeLLM.from_dir(FIXTURES / "llm", model=model)


def answer_all(requests, path):
    """Stand in for the session: answer every request with the judge fixtures."""
    fake = judge_llm()
    lines = [
        answer_line(r.key, fake.generate(r.prompt, JudgeAnswer, system=r.system)) for r in requests
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


ANSWER = {
    **{
        name: {"reason": "Holds.", "passed": True}
        for name in ["covers", "faithful", "objective_verdict", "executable", "method_fit"]
    },
    "acceptable": True,
}


# SessionLLM


def test_an_unanswered_request_is_recorded_once():
    llm = SessionLLM({})
    for _ in range(2):
        with pytest.raises(AnswerMissingError):
            llm.generate("Grade this.", JudgeAnswer, system="You grade.")
    [request] = llm.pending
    assert request.key == request_key("Grade this.", JudgeAnswer, "You grade.")
    assert request.schema_name == "JudgeAnswer" and request.system == "You grade."
    assert "acceptable" in request.json_schema["properties"]
    assert llm.model == "claude-opus-5-5"


def test_an_answer_answers_only_its_own_request():
    key = request_key("Grade this.", JudgeAnswer, "You grade.")
    llm = SessionLLM({key: ANSWER}, model="claude-opus-5-5")
    assert llm.generate("Grade this.", JudgeAnswer, system="You grade.").acceptable
    assert not llm.pending
    with pytest.raises(AnswerMissingError):
        llm.generate("Grade this, changed.", JudgeAnswer, system="You grade.")
    with pytest.raises(AnswerMissingError):
        llm.generate("Grade this.", Verdict, system="You grade.")


def test_an_answer_that_does_not_fit_the_schema_fails_and_is_asked_again():
    key = request_key("Grade this.", JudgeAnswer)
    llm = SessionLLM({key: {"acceptable": True}})
    with pytest.raises(LLMOutputError, match="does not match JudgeAnswer"):
        llm.generate("Grade this.", JudgeAnswer)
    [request] = llm.pending
    assert request.key == key and "does not match JudgeAnswer" in request.error
    assert llm.invalid == [request]


def test_answers_load_from_jsonl(tmp_path):
    path = tmp_path / "answers.jsonl"
    assert SessionLLM.from_file(path).answers == {}  # not written yet
    first = {**ANSWER, "acceptable": False}
    path.write_text(
        answer_line("k1", first) + "\n\n" + answer_line("k1", ANSWER) + "\n", encoding="utf-8"
    )
    assert SessionLLM.from_file(path).answers == {"k1": ANSWER}  # the last line wins
    path.write_text('{"key": "k1"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"answers\.jsonl:1"):
        SessionLLM.from_file(path)


def test_session_judging_matches_the_api_judge(generation):
    first = SessionLLM({})
    result = judge(generation, first)
    assert len(result.failed) == len(generation.items) == len(first.pending)

    fake = judge_llm()
    answers = {
        r.key: fake.generate(r.prompt, JudgeAnswer, system=r.system).model_dump()
        for r in first.pending
    }
    session = judge(generation, SessionLLM(answers))
    assert not session.failed and session.judge_model == "claude-opus-5-5"
    by_api = judge(generation, judge_llm())
    assert [j.model_dump() for j in session.judgements] == [
        j.model_dump() for j in by_api.judgements
    ]


# Comparing two judges


def test_compare_counts_agreement_and_keeps_both_reasons(generation):
    main = judge(generation, judge_llm("claude-opus-5-5"))
    second = judge(generation, judge_llm("gemini-3.1-pro-preview"))
    switch = next(j for j in second.judgements if j.item_id == "widget-lamp:2.3:1:EV")
    switch.verdicts["objective_verdict"] = Verdict(reason="Observable.", passed=True)
    switch.acceptable = True
    second.judgements = [j for j in second.judgements if j.kind.value != "test_case"]

    result = compare(main, second)
    assert result.compared == len(second.judgements) and not result.warnings
    assert result.first_only == len(main.judgements) - len(second.judgements)
    agreements = {a.name: a for a in result.agreements}
    assert agreements["covers"].agreement == 1.0
    assert agreements["objective_verdict"].human_only == 1  # only the second judge passed it
    names = {d.name for d in result.disagreements}
    assert names == {"objective_verdict", "acceptable"}
    split = next(d for d in result.disagreements if d.name == "objective_verdict")
    assert "force limit" in split.first_reason and split.second_reason == "Observable."
    report = comparison_report(result)
    assert "judge claude-opus-5-5 vs. judge gemini-3.1-pro-preview" in report
    assert "gemini-3.1-pro-preview: Observable." in report


def test_compare_warns_about_two_judges_of_one_family(generation):
    result = compare(judge(generation, judge_llm("claude-opus-5-5")),
                     judge(generation, judge_llm("claude-sonnet-5-5")))  # fmt: skip
    assert any("'claude' family" in w for w in result.warnings)


# CLI


def test_cli_judge_in_two_session_passes_then_compare(generation, tmp_path, capsys):
    gen_path = tmp_path / "generation.json"
    gen_path.write_text(generation.model_dump_json(), encoding="utf-8")
    answers = tmp_path / "opus.jsonl"
    judge_cmd = ["judge", str(gen_path), "--answers", str(answers)]  # the session is the default

    assert cli.main(judge_cmd) == 1
    requests_path = tmp_path / "opus.requests.jsonl"
    assert f"5 request(s) without a valid session answer written to {requests_path}" in (
        capsys.readouterr().err
    )
    requests = load_requests(requests_path)
    answer_all(requests[:2], answers)
    assert cli.main(judge_cmd) == 1  # answers can come in several sittings
    assert len(load_requests(requests_path)) == 3
    capsys.readouterr()
    answer_all(load_requests(requests_path), answers)

    opus = tmp_path / "opus-judging.json"
    assert cli.main([*judge_cmd, "--out", str(opus)]) == 0
    assert "every request has a session answer" in capsys.readouterr().err
    assert json.loads(opus.read_text(encoding="utf-8"))["judge_model"] == "claude-opus-5-5"

    fake = str(FIXTURES / "llm")
    second = ["judge", str(gen_path), "--fake", fake, "--model", "gemini-3.1-pro-preview"]
    assert cli.main([*second, "--json"]) == 0
    gemini = tmp_path / "gemini-judging.json"
    gemini.write_text(capsys.readouterr().out, encoding="utf-8")
    assert cli.main(["calibrate", "compare", str(opus), str(gemini)]) == 0
    out = capsys.readouterr().out
    assert "5 items graded by both" in out and "100.0%" in out


def test_cli_asks_again_for_an_answer_that_does_not_fit(generation, tmp_path, capsys):
    gen_path = tmp_path / "generation.json"
    gen_path.write_text(generation.model_dump_json(), encoding="utf-8")
    answers = tmp_path / "opus.jsonl"
    judge_cmd = ["judge", str(gen_path), "--answers", str(answers)]
    cli.main(judge_cmd)
    requests_path = tmp_path / "opus.requests.jsonl"
    requests = load_requests(requests_path)
    answer_all(requests[1:], answers)
    with answers.open("a", encoding="utf-8") as f:
        f.write(answer_line(requests[0].key, {"acceptable": True}) + "\n")
    capsys.readouterr()

    assert cli.main(judge_cmd) == 1
    err = capsys.readouterr().err
    assert "every request has a session answer" not in err
    assert "1 request(s) without a valid session answer (1 whose answer did not match" in err
    [again] = load_requests(requests_path)
    assert again.key == requests[0].key and "does not match JudgeAnswer" in again.error
    answer_all([again], answers)  # a corrected line for the same key replaces the bad one
    assert cli.main(judge_cmd) == 0


def test_cli_session_needs_an_answers_file(generation, tmp_path, capsys):
    gen_path = tmp_path / "generation.json"
    gen_path.write_text(generation.model_dump_json(), encoding="utf-8")
    assert cli.main(["judge", str(gen_path)]) == 2
    assert "--provider session needs --answers" in capsys.readouterr().err


def test_cli_judges_a_calibration_sample(generation, widget_lamp, tmp_path, capsys):
    sample = tmp_path / "sample.jsonl"
    write_sample(make_sample(generation, 3, doc=widget_lamp), sample)
    fake = str(FIXTURES / "llm")
    assert cli.main(["judge", str(sample), "--fake", fake, "--model", "gemini-3.1-pro"]) == 0
    assert "judge gemini-3.1-pro, generator FakeLLM: 3 items judged" in capsys.readouterr().out

    # The sample stores each item's context, so its prompts are the generation's prompts:
    # a session answer for an item answers it in both.
    answers = tmp_path / "opus.jsonl"
    assert cli.main(["judge", str(sample), "--answers", str(answers)]) == 1
    from_sample = {r.key for r in load_requests(tmp_path / "opus.requests.jsonl")}
    llm = SessionLLM({})
    judge(generation, llm, doc=widget_lamp)
    assert from_sample < {r.key for r in llm.pending}
