"""Calibrate the judge: how often it agrees with a person grading the same items.

1. `make_sample` draws items from a saved `Generation`, spread across item kinds, into a
   JSONL file with an empty `human` label on every line.
2. A person fills in `human` on each line against the rubric (`spec2test.judge.rubric`).
3. `calibrate` judges the labeled lines (or reads saved judgements) and reports, per
   rubric criterion and for the overall `acceptable` call, the judge-human agreement and
   Cohen's kappa, with every disagreement listed.

The labels must come from a person. A model's labels would measure agreement between two
models, which says nothing about whether the judge is right.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from pathlib import Path

from spec2test.generate import Generation, SourceContext, source_context
from spec2test.ingest import Document
from spec2test.judge.models import (
    Agreement,
    Calibration,
    CalibrationItem,
    Disagreement,
    JudgeFailure,
    Judgement,
    Judging,
)
from spec2test.judge.pipeline import judge_item, judge_warnings
from spec2test.judge.rubric import RUBRIC
from spec2test.llm import LLMClient, LLMError, model_name


def make_sample(
    generation: Generation, size: int, *, doc: Document | None = None, seed: int = 0
) -> list[CalibrationItem]:
    """Up to `size` items, drawn in turn from each item kind so every kind is represented.

    The same `seed` draws the same sample.
    """
    rng = random.Random(seed)
    by_kind: dict[str, list] = {}
    for item in generation.items:
        by_kind.setdefault(item.kind.value, []).append(item)
    for items in by_kind.values():
        rng.shuffle(items)
    picked = []
    queues = [by_kind[k] for k in sorted(by_kind)]
    while len(picked) < size and any(queues):
        for queue in queues:
            if queue and len(picked) < size:
                picked.append(queue.pop())
    requirements = {r.id: r for r in generation.requirements}
    out = []
    for item in picked:
        req = requirements[item.requirement_ids[0]]
        out.append(
            CalibrationItem(
                requirement=req,
                item=item,
                context=SourceContext() if doc is None else source_context(req, doc),
                generator_model=generation.model,
            )
        )
    return out


def write_sample(sample: Iterable[CalibrationItem], path: str | Path) -> None:
    lines = [s.model_dump_json() for s in sample]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_sample(path: str | Path) -> list[CalibrationItem]:
    text = Path(path).read_text(encoding="utf-8")
    return [CalibrationItem.model_validate_json(line) for line in text.splitlines() if line.strip()]


def calibrate(
    sample: list[CalibrationItem],
    llm: LLMClient | None = None,
    *,
    saved: Judging | None = None,
) -> Calibration:
    """Judge the labeled lines of `sample` with `llm`, or take the judgements from `saved`
    (a previous calibration's, or `spec2test judge` output), and compare with the labels.

    Lines without a complete human label are skipped and counted.
    """
    if (llm is None) == (saved is None):
        raise ValueError("pass either a judge model or saved judgements")
    labeled = [s for s in sample if s.human.complete]
    judge = model_name(llm) if llm is not None else saved.judge_model
    generators = sorted({s.generator_model for s in labeled})
    warnings = [w for g in generators for w in judge_warnings(judge, g)]
    judging = Judging(judge_model=judge, generator_model=", ".join(generators), warnings=warnings)

    found = {} if saved is None else {j.item_id: j for j in saved.judgements}
    graded: list[tuple[CalibrationItem, Judgement]] = []
    for s in labeled:
        if llm is not None:
            try:
                j = judge_item(s.requirement, s.item, llm, s.context)
            except LLMError as exc:
                judging.failed.append(JudgeFailure(item_id=s.item.id, error=str(exc)))
                continue
        elif (j := found.get(s.item.id)) is None:
            judging.failed.append(JudgeFailure(item_id=s.item.id, error="no saved judgement"))
            continue
        judging.judgements.append(j)
        graded.append((s, j))

    if judging.failed:
        warnings.append(f"{len(judging.failed)} labeled item(s) could not be judged")
    if not labeled:
        warnings.append("no line has a complete human label yet: fill in `human` first")
    elif len(graded) < 30:
        warnings.append(
            f"only {len(graded)} labeled item(s): agreement and kappa are rough estimates"
        )

    agreements = {name: Agreement(name=name) for name in [*RUBRIC, "acceptable"]}
    disagreements = []
    for s, j in graded:
        pairs = {name: (j.verdicts[name].passed, s.human.verdicts[name]) for name in RUBRIC}
        pairs["acceptable"] = (j.acceptable, s.human.acceptable)
        for name, (by_judge, by_human) in pairs.items():
            a = agreements[name]
            if by_judge and by_human:
                a.both_pass += 1
            elif not by_judge and not by_human:
                a.both_fail += 1
            elif by_judge:
                a.judge_only += 1
            else:
                a.human_only += 1
            if by_judge != by_human:
                reason = j.verdicts[name].reason if name in j.verdicts else ""
                disagreements.append(
                    Disagreement(
                        item_id=s.item.id,
                        name=name,
                        judge=by_judge,
                        human=bool(by_human),
                        judge_reason=reason,
                    )
                )
    return Calibration(
        judge_model=judge,
        labelers=sorted({s.human.labeler for s in labeled if s.human.labeler}),
        labeled=len(labeled),
        unlabeled=len(sample) - len(labeled),
        warnings=warnings,
        agreements=list(agreements.values()),
        disagreements=disagreements,
        judging=judging,
    )
