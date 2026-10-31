"""The eval dashboard: saved runs in, one view out, no model calls."""

from __future__ import annotations

import json
from pathlib import Path

from spec2test import cli
from spec2test.dashboard import collect, html_report, spreads, text_report
from spec2test.evals import evaluate, load_gold, write_report
from spec2test.judge import calibrate, compare, judge, make_sample
from spec2test.judge.models import HumanLabel
from spec2test.judge.rubric import RUBRIC
from spec2test.llm import FakeLLM, provider_name
from spec2test.review import ReviewStore
from spec2test.review.models import Decision
from spec2test.version import code_version

FIXTURES = Path(__file__).parent / "fixtures"
GOLD = FIXTURES / "gold" / "widget-lamp.jsonl"


def judge_llm(model="fake-judge"):
    return FakeLLM.from_dir(FIXTURES / "llm", model=model)


def save(model, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return path


def eval_run(widget_lamp, fake_llm, out: Path):
    run = evaluate(widget_lamp, load_gold(GOLD, widget_lamp), fake_llm, corpus="widget-lamp")
    write_report(run, out)
    return run


def labeled(item):
    label = HumanLabel(labeler="tester", verdicts=dict.fromkeys(RUBRIC, True), acceptable=True)
    return item.model_copy(update={"human": label})


def test_runs_record_how_when_and_by_which_code(widget_lamp, fake_llm, generation, tmp_path):
    run = eval_run(widget_lamp, fake_llm, tmp_path / "run")
    assert run.code_version == code_version()
    assert code_version().startswith("0.")
    judging = judge(generation, judge_llm())
    assert judging.provider == "fake" and judging.created and judging.code_version
    assert provider_name(fake_llm) == "fake"


def test_collect_finds_every_kind_of_saved_run(widget_lamp, fake_llm, generation, tmp_path):
    reports = tmp_path / "eval-reports"
    eval_run(widget_lamp, fake_llm, reports / "widget-lamp" / "a")
    eval_run(widget_lamp, fake_llm, reports / "widget-lamp" / "b")
    first = judge(generation, judge_llm())
    second = judge(generation, judge_llm("other-judge"))
    save(first, reports / "judge" / "first.json")
    save(second, reports / "judge" / "second.json")
    save(compare(first, second), reports / "judge" / "compare.json")
    sample = [labeled(s) for s in make_sample(generation, 4)]
    save(calibrate(sample, saved=first), reports / "judge" / "calibration.json")
    (reports / "notes.json").write_text('{"something": "else"}', encoding="utf-8")
    (reports / "broken.json").write_text("{not json", encoding="utf-8")
    bad = json.loads((reports / "widget-lamp" / "a" / "results.json").read_text("utf-8"))
    bad["metrics"] = "nope"
    (reports / "bad-results.json").write_text(json.dumps(bad), encoding="utf-8")

    board = collect([reports])
    assert [r.corpus for r in board.extraction] == ["widget-lamp", "widget-lamp"]
    row = board.extraction[0]
    assert row.metrics["extraction recall"].denominator == row.gold
    assert row.cost.basis == "scope"
    # Two runs of one code version: their spread is the noise.
    [spread] = board.spreads
    assert spread.runs == 2 and spread.low["extraction recall"] == spread.high["extraction recall"]

    assert {j.judge_model for j in board.judging} == {"fake-judge", "other-judge"}
    j = next(j for j in board.judging if j.judge_model == "fake-judge")
    assert j.judged == len(generation.items) and j.provider == "fake"
    assert j.acceptable.numerator == sum(x.acceptable for x in first.judgements)
    assert sum(r.denominator for r in j.by_kind.values()) == j.judged

    kinds = sorted(a.kind for a in board.agreement)
    assert kinds == ["judge-human", "judge-judge"]
    human = next(a for a in board.agreement if a.kind == "judge-human")
    assert human.items == 4 and human.second == "tester"

    skipped = {Path(s.path).name: s.reason for s in board.skipped}
    assert set(skipped) == {"broken.json", "bad-results.json"}
    assert "eval run" in skipped["bad-results.json"]


def test_spread_leaves_out_runs_without_a_code_version(widget_lamp, fake_llm, tmp_path):
    for name in ("a", "b"):
        run = eval_run(widget_lamp, fake_llm, tmp_path / name)
        run.code_version = None
        save(run, tmp_path / name / "results.json")
    board = collect([tmp_path])
    assert len(board.extraction) == 2 and not board.spreads
    assert spreads(board.extraction) == []


def test_review_logs_give_the_acceptance_rate(generation, tmp_path):
    store = ReviewStore(tmp_path / "review.sqlite")
    gid = store.add(generation)
    first, second = generation.items[:2]
    store.record(gid, first.id, Decision.ACCEPT)
    store.record(gid, second.id, Decision.REJECT)
    board = collect([], [tmp_path / "review.sqlite", tmp_path / "missing.sqlite"])
    [review] = board.reviews
    assert review.stats.overall.accepted == 1 and review.stats.overall.rejected == 1
    assert review.stats.overall.acceptance_rate == 0.5
    assert [Path(s.path).name for s in board.skipped] == ["missing.sqlite"]
    assert "acceptance 50.0%" in text_report(board)


def test_reports_name_session_runs_and_escape_text(generation, tmp_path):
    judging = judge(generation, judge_llm("<b>judge</b>"))
    judging.provider = "session"
    save(judging, tmp_path / "j.json")
    board = collect([tmp_path])
    text = text_report(board)
    assert "session" in text and "<b>judge</b>" in text
    page = html_report(board)
    assert "&lt;b&gt;judge&lt;/b&gt;" in page and "<b>judge</b>" not in page
    assert "Claude Code session" in page


def test_a_judging_saved_before_the_new_fields_still_loads(generation, tmp_path):
    data = json.loads(judge(generation, judge_llm()).model_dump_json())
    for key in ("provider", "created", "code_version"):
        del data[key]
    (tmp_path / "old.json").write_text(json.dumps(data), encoding="utf-8")
    board = collect([tmp_path])
    [row] = board.judging
    assert row.provider is None and row.created is None and row.file_time
    assert "[unknown]" in text_report(board)
    assert "file time" in html_report(board)


def test_cli_writes_the_html_and_json(widget_lamp, fake_llm, tmp_path, capsys):
    reports = tmp_path / "eval-reports"
    eval_run(widget_lamp, fake_llm, reports / "widget-lamp" / "a")
    html, out = tmp_path / "board.html", tmp_path / "board.json"
    code = cli.main(["dashboard", str(reports), "--html", str(html), "--out", str(out)])
    assert code == 0
    assert "Extraction: widget-lamp" in html.read_text("utf-8")
    assert json.loads(out.read_text("utf-8"))["extraction"][0]["corpus"] == "widget-lamp"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["dashboard", str(empty), "--html", str(tmp_path / "e.html")]) == 1
    assert "no saved runs found" in capsys.readouterr().out
