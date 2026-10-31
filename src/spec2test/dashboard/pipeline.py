"""Collect saved runs into one dashboard: no model calls, only files already written.

It reads every `.json` file under the folders given (by default `eval-reports/`) and
keeps the ones that are an eval run (`results.json`), a judging (`judge --json`), a
judge-human calibration (`calibrate score --json`) or a judge comparison
(`calibrate compare --json`), plus the acceptance rate of any review logs named.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from spec2test.dashboard.models import (
    AgreementRun,
    CostSummary,
    Dashboard,
    ExtractionRun,
    JudgeRun,
    Rate,
    ReviewSummary,
    Skipped,
    Spread,
)
from spec2test.evals.models import EvalRun
from spec2test.judge.compare import JudgeComparison
from spec2test.judge.models import Calibration, Judging
from spec2test.llm import TokenUsage, cost
from spec2test.review.store import ReviewStore
from spec2test.textio import read_text

ACCEPTABLE = "acceptable"


def collect(sources: Iterable[str | Path], dbs: Iterable[str | Path] = ()) -> Dashboard:
    """Every run saved under `sources` (folders searched recursively, or single files)
    and the review stats of each log in `dbs`, oldest run first."""
    sources = [Path(s) for s in sources]
    board = Dashboard(
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        sources=[str(s) for s in sources],
    )
    for path in _json_files(sources):
        try:
            data = json.loads(read_text(path))
        except (OSError, ValueError) as exc:
            board.skipped.append(Skipped(path=str(path), reason=f"not readable JSON: {exc}"))
            continue
        kind = _kind(data)
        if kind is None:
            continue
        try:
            _add(board, kind, data, path)
        except ValidationError as exc:
            reason = f"looks like a {kind} but does not load: {exc.error_count()} error(s)"
            board.skipped.append(Skipped(path=str(path), reason=reason))
    board.extraction.sort(key=lambda r: (r.created, r.path))
    board.judging.sort(key=lambda r: (r.created or r.file_time, r.path))
    board.spreads = spreads(board.extraction)
    for db in dbs:
        db = Path(db)
        if not db.is_file():
            board.skipped.append(Skipped(path=str(db), reason="no review log at this path"))
            continue
        board.reviews.append(ReviewSummary(db=str(db), stats=ReviewStore(db).stats()))
    return board


def _json_files(sources: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for source in sources:
        if source.is_file():
            files.add(source)
        elif source.is_dir():
            files.update(p for p in source.rglob("*.json") if p.is_file())
    return sorted(files)


def _kind(data: object) -> str | None:
    """Which saved run `data` is, judged by its keys; None for any other JSON."""
    if not isinstance(data, dict):
        return None
    keys = set(data)
    if {"corpus", "gold", "metrics", "extraction"} <= keys:
        return "eval run"
    if {"judge_model", "labelers", "agreements", "judging"} <= keys:
        return "calibration"
    if {"first_model", "second_model", "agreements"} <= keys:
        return "judge comparison"
    if {"judge_model", "generator_model", "judgements"} <= keys:
        return "judging"
    return None


def _add(board: Dashboard, kind: str, data: dict, path: Path) -> None:
    if kind == "eval run":
        board.extraction.append(extraction_row(EvalRun.model_validate(data), path))
    elif kind == "judging":
        board.judging.append(judge_row(Judging.model_validate(data), path))
    elif kind == "calibration":
        cal = Calibration.model_validate(data)
        board.agreement.append(
            _agreement_row(path, "judge-human", cal.judge_model, ", ".join(cal.labelers), cal)
        )
    else:
        cmp = JudgeComparison.model_validate(data)
        board.agreement.append(
            _agreement_row(path, "judge-judge", cmp.first_model, cmp.second_model, cmp)
        )


def extraction_row(run: EvalRun, path: Path) -> ExtractionRun:
    return ExtractionRun(
        path=str(path),
        corpus=run.corpus,
        model=run.model,
        created=run.created,
        code_version=run.code_version,
        gold_labeler=run.gold_labeler,
        gold_reviewed=run.gold_reviewed,
        gold=len(run.gold),
        extracted=len(run.predicted),
        metrics={
            m.name: Rate(numerator=m.numerator, denominator=m.denominator) for m in run.metrics
        },
        failed_sections=len(run.extraction.failed_sections),
        cost=_cost(run.usage, run.pages, "scope"),
        warnings=run.warnings,
    )


def judge_row(judging: Judging, path: Path) -> JudgeRun:
    js = judging.judgements
    by_kind: dict[str, Rate] = {}
    for j in js:
        rate = by_kind.setdefault(j.kind.value, Rate(numerator=0, denominator=0))
        rate.numerator += j.acceptable
        rate.denominator += 1
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
    return JudgeRun(
        path=str(path),
        judge_model=judging.judge_model,
        generator_model=judging.generator_model,
        provider=judging.provider,
        created=judging.created,
        file_time=mtime,
        code_version=judging.code_version,
        judged=len(js),
        failed=len(judging.failed),
        acceptable=Rate(numerator=sum(j.acceptable for j in js), denominator=len(js)),
        criteria=Rate(
            numerator=sum(v.passed for j in js for v in j.verdicts.values()),
            denominator=sum(len(j.verdicts) for j in js),
        ),
        by_kind=dict(sorted(by_kind.items())),
        cost=_cost(judging.usage, judging.pages, "cited"),
        warnings=judging.warnings,
    )


def _agreement_row(
    path: Path, kind: str, first: str, second: str, result: Calibration | JudgeComparison
) -> AgreementRun:
    a = next((a for a in result.agreements if a.name == ACCEPTABLE), None)
    return AgreementRun(
        path=str(path),
        kind=kind,
        first=first,
        second=second or "[no labeler named]",
        items=0 if a is None else a.n,
        agreement=None if a is None else a.agreement,
        kappa=None if a is None else a.kappa,
        warnings=result.warnings,
    )


def _cost(usage: list[TokenUsage], pages: int, basis: str) -> CostSummary:
    c = cost(usage)
    return CostSummary(
        calls=c.calls,
        input_tokens=c.input_tokens,
        output_tokens=c.output_tokens,
        dollars=c.dollars,
        per_page=c.per(pages),
        basis=basis,
    )


def spreads(runs: list[ExtractionRun]) -> list[Spread]:
    """Min and max of each metric over runs sharing a corpus, model and code version.

    Runs that did not record their code version are left out: runs of different code
    would mix a change's effect into the noise."""
    groups: dict[tuple[str, str, str], list[ExtractionRun]] = defaultdict(list)
    for r in runs:
        if r.code_version is not None:
            groups[(r.corpus, r.model, r.code_version)].append(r)
    result = []
    for (corpus, model, version), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        names = [n for n in group[0].metrics if all(n in r.metrics for r in group)]
        values = {n: [v for r in group if (v := r.metrics[n].value) is not None] for n in names}
        values = {n: vs for n, vs in values.items() if vs}
        result.append(
            Spread(
                corpus=corpus,
                model=model,
                code_version=version,
                runs=len(group),
                low={n: min(vs) for n, vs in values.items()},
                high={n: max(vs) for n, vs in values.items()},
            )
        )
    return result
