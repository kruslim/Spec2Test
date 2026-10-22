"""Grade generated items against the rubric with a model other than the one that wrote them.

A model grading its own items shares their blind spots and passes them, so every judging
run records both models and warns when they are from the same family.
"""

from __future__ import annotations

from collections.abc import Iterable

from spec2test.evals import Metric
from spec2test.generate import Generation, source_context
from spec2test.ingest import Document
from spec2test.judge.models import Grades, JudgeFailure, Judgement, Judging
from spec2test.judge.prompts import SYSTEM, judge_prompt
from spec2test.judge.rubric import ACCEPTABLE, Criterion
from spec2test.llm import LLMClient, LLMError, model_name, same_family
from spec2test.schema import Requirement, VerificationItem, VerificationItemKind


def judge(
    generation: Generation,
    llm: LLMClient,
    *,
    doc: Document | None = None,
    kinds: Iterable[VerificationItemKind] | None = None,
) -> Judging:
    """Grade each item in `generation` (only those of `kinds`, if given).

    With `doc`, the judge sees the same source clauses the generator was given.
    """
    wanted = None if kinds is None else set(kinds)
    judge_model = model_name(llm)
    result = Judging(
        judge_model=judge_model,
        generator_model=generation.model,
        warnings=judge_warnings(judge_model, generators=[generation.model]),
    )
    by_id = {r.id: r for r in generation.requirements}
    for item in generation.items:
        if wanted is not None and item.kind not in wanted:
            continue
        missing = [i for i in item.requirement_ids if i not in by_id]
        if missing:
            error = f"requirement(s) {', '.join(missing)} not in the generation"
            result.failed.append(JudgeFailure(item_id=item.id, error=error))
            continue
        reqs = [by_id[i] for i in item.requirement_ids]
        try:
            grades = judge_item(reqs, item, llm, doc=doc)
        except LLMError as exc:
            result.failed.append(JudgeFailure(item_id=item.id, error=str(exc)))
            continue
        result.judgements.append(Judgement(item_id=item.id, kind=item.kind, grades=grades))
    return result


def judge_item(
    requirements: list[Requirement],
    item: VerificationItem,
    llm: LLMClient,
    *,
    doc: Document | None = None,
) -> Grades:
    """The judge's grades for one item. Raises `LLMError` when the call fails."""
    context = None
    if doc is not None:
        clauses = [c for r in requirements if (c := source_context(doc, r))]
        context = "\n".join(dict.fromkeys(clauses)) or None
    return llm.generate(judge_prompt(requirements, item, context), Grades, system=SYSTEM)


def judge_warnings(
    judge_model: str, *, generators: Iterable[str | None] = (), labeler: str | None = None
) -> list[str]:
    """What makes the judge's grades untrustworthy: grading items its own model family
    wrote, or being calibrated against labels its own family made."""
    out = []
    for generator in sorted({g for g in generators if g}):
        if same_family(judge_model, generator):
            out.append(
                f"the judge {judge_model!r} is from the same family as the generator "
                f"{generator!r}: it shares the generator's blind spots, so these grades "
                "overstate quality. Judge with another model."
            )
    if labeler is not None and same_family(judge_model, labeler):
        out.append(
            f"the judge {judge_model!r} is from the same family as the labeler {labeler!r}: "
            "agreement measures the model agreeing with itself, not with a person"
        )
    return out


def pass_rates(judging: Judging) -> dict[str, list[Metric]]:
    """Per kind of item, and for 'all': the share of items passing each criterion, and
    the share acceptable (passing all four)."""
    groups: dict[str, list[Judgement]] = {"all": judging.judgements}
    for kind in VerificationItemKind:
        if group := [j for j in judging.judgements if j.kind is kind]:
            groups[kind.value] = group
    return {
        name: [
            *(
                Metric(
                    name=c.value,
                    numerator=sum(j.grades.grade(c).passed for j in group),
                    denominator=len(group),
                )
                for c in Criterion
            ),
            Metric(
                name=ACCEPTABLE,
                numerator=sum(j.grades.acceptable for j in group),
                denominator=len(group),
            ),
        ]
        for name, group in groups.items()
    }
