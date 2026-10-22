"""Calibrate the judge: grade a sample a person has graded, and report how often they agree.

A sample is a JSONL file, one `SampleLine` per line: the requirement(s), the generated
item, and the person's pass/fail grade for each rubric criterion (`docs/RUBRIC.md`).
`new_sample` draws one from a generation with the grades left empty, so the person grades
without seeing the judge's verdicts. An optional TOML file next to it (same name, `.toml`)
says who graded it:

    labeler = "Kenneth"

Agreement is reported per criterion and for the overall verdict (all four pass), as the
share of items the two agree on and as Cohen's kappa, which discounts the agreement two
graders would reach by chance at their pass rates.
"""

from __future__ import annotations

import json
import random
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from spec2test.generate import Generation
from spec2test.ingest import Document
from spec2test.judge.models import (
    Agreement,
    Calibration,
    Disagreement,
    JudgeFailure,
    Judgement,
    Sample,
    SampleLine,
)
from spec2test.judge.pipeline import judge_item, judge_warnings
from spec2test.judge.rubric import ACCEPTABLE, Criterion
from spec2test.llm import LLMClient, LLMError, model_name


class SampleError(ValueError):
    """The sample does not load. The message lists every problem, one per line, each with
    its file and line."""


class _Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labeler: str | None = None


def new_sample(generation: Generation, size: int | None = None, seed: int = 0) -> list[SampleLine]:
    """Up to `size` items drawn at random from `generation` (all of them by default), in
    generation order, ungraded."""
    items = generation.items
    if size is not None and size < len(items):
        picked = set(random.Random(seed).sample(range(len(items)), size))
        items = [item for i, item in enumerate(items) if i in picked]
    by_id = {r.id: r for r in generation.requirements}
    return [
        SampleLine(
            requirements=[by_id[i] for i in item.requirement_ids if i in by_id],
            item=item,
            generator=generation.model,
        )
        for item in items
        if all(i in by_id for i in item.requirement_ids)
    ]


def load_sample(path: str | Path) -> Sample:
    path = Path(path)
    errors: list[str] = []
    labeler = None
    toml = path.with_suffix(".toml")
    if toml.is_file():
        try:
            with toml.open("rb") as f:
                labeler = _Meta.model_validate(tomllib.load(f)).labeler
        except (tomllib.TOMLDecodeError, ValidationError) as exc:
            errors.append(f"{toml.name}: {exc}")
    lines: list[SampleLine] = []
    seen: dict[str, int] = {}
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        where = f"{path.name}:{n}"
        try:
            line = SampleLine.model_validate(json.loads(text))
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: not JSON: {exc}")
            continue
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'line'}: {e['msg']}" for e in exc.errors()
            )
            errors.append(f"{where}: {problems}")
            continue
        given = {r.id for r in line.requirements}
        if missing := [i for i in line.item.requirement_ids if i not in given]:
            errors.append(f"{where}: no record for requirement(s) {', '.join(missing)}")
        if line.item.id in seen:
            errors.append(
                f"{where}: duplicate item {line.item.id!r} (first on line {seen[line.item.id]})"
            )
        seen.setdefault(line.item.id, n)
        lines.append(line)
    if errors:
        raise SampleError("\n".join(errors))
    return Sample(path=str(path), labeler=labeler, lines=lines)


def calibrate(
    sample: Sample,
    llm: LLMClient | None,
    *,
    doc: Document | None = None,
    previous: Calibration | None = None,
) -> Calibration:
    """Judge every graded item in `sample` and compare with the person's grades.

    Items `previous` already judged are not judged again: after fixing a grade, rescore
    with `llm=None` and no model is called.
    """
    if llm is None and previous is None:
        raise ValueError("calibrate needs a judge model or a previous calibration to rescore")
    judge_model = model_name(llm) if llm is not None else previous.judge_model
    saved = {j.item_id: j for j in previous.judgements} if previous else {}
    result = Calibration(
        sample_path=sample.path,
        judge_model=judge_model,
        labeler=sample.labeler,
        warnings=judge_warnings(
            judge_model,
            generators=[line.generator for line in sample.lines],
            labeler=sample.labeler,
        ),
    )
    if sample.labeler is None:
        result.warnings.append(
            "the sample does not say who graded it (labeler in its .toml); agreement means "
            "judge-human agreement only if a person did"
        )

    graded: list[tuple[SampleLine, Judgement]] = []
    for line in sample.lines:
        if all(line.labels.label(c) is None for c in Criterion):
            result.unlabeled += 1
            continue
        judgement = saved.get(line.item.id)
        if judgement is None:
            if llm is None:
                error = "not judged in the saved calibration; run it with a model"
                result.failed.append(JudgeFailure(item_id=line.item.id, error=error))
                continue
            try:
                grades = judge_item(line.requirements, line.item, llm, doc=doc)
            except LLMError as exc:
                result.failed.append(JudgeFailure(item_id=line.item.id, error=str(exc)))
                continue
            judgement = Judgement(item_id=line.item.id, kind=line.item.kind, grades=grades)
        result.judgements.append(judgement)
        graded.append((line, judgement))
    if result.failed:
        result.warnings.append(
            f"the judge could not grade {len(result.failed)} item(s); they are left out"
        )

    for c in Criterion:
        pairs = [
            (j.grades.grade(c).passed, label, j.grades.grade(c).reason, line)
            for line, j in graded
            if (label := line.labels.label(c)) is not None
        ]
        result.agreements.append(_agreement(c.value, [(p[0], p[1]) for p in pairs]))
        result.disagreements += [
            Disagreement(
                item_id=line.item.id,
                criterion=c.value,
                judge=judged,
                person=label,
                judge_reason=reason,
                note=line.note,
            )
            for judged, label, reason, line in pairs
            if judged != label
        ]
    overall = [
        (j.grades.acceptable, line.labels.acceptable)
        for line, j in graded
        if line.labels.acceptable is not None
    ]
    result.agreements.append(_agreement(ACCEPTABLE, overall))
    return result


def _agreement(criterion: str, pairs: list[tuple[bool, bool]]) -> Agreement:
    return Agreement(
        criterion=criterion,
        both_pass=sum(j and p for j, p in pairs),
        both_fail=sum(not j and not p for j, p in pairs),
        judge_only_pass=sum(j and not p for j, p in pairs),
        person_only_pass=sum(p and not j for j, p in pairs),
    )
