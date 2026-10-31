"""Grade generated verification items with an LLM judge against the written rubric.

The judge must be a different model from the generator: a model grading its own work
counts its blind spots as correct. Judging with the generator's own model is refused;
judging with another model of its family runs, with a warning.
"""

from __future__ import annotations

from datetime import UTC, datetime

from spec2test.generate import Generation, SourceContext, pages_cited, source_context
from spec2test.ingest import Document
from spec2test.judge.models import JudgeAnswer, JudgeFailure, Judgement, Judging, graded_hash
from spec2test.judge.prompts import SYSTEM, judge_prompt
from spec2test.llm import LLMClient, LLMError, model_family, model_name, provider_name
from spec2test.schema import Requirement, VerificationItem
from spec2test.version import code_version


class SameModelError(ValueError):
    """The judge is the model that generated the items."""


def judge(generation: Generation, llm: LLMClient, *, doc: Document | None = None) -> Judging:
    """Grade every item in `generation`.

    With `doc`, each requirement's source context (headings, definitions, referenced
    clauses) is shown to the judge too, as it was to the generator.
    """
    result = Judging(
        judge_model=model_name(llm),
        generator_model=generation.model,
        warnings=judge_warnings(model_name(llm), generation.model),
        **run_record(llm),
    )
    requirements = {r.id: r for r in generation.requirements}
    result.pages = pages_cited(requirements[i.requirement_ids[0]] for i in generation.items)
    start = len(llm.usage)
    for item in generation.items:
        req = requirements[item.requirement_ids[0]]
        ctx = None if doc is None else source_context(req, doc)
        try:
            result.judgements.append(judge_item(req, item, llm, ctx))
        except LLMError as exc:
            result.failed.append(JudgeFailure(item_id=item.id, error=str(exc)))
    result.usage = llm.usage[start:]
    return result


def run_record(llm: LLMClient) -> dict[str, str]:
    """The `Judging` fields that say how, when and by which code a judging ran."""
    return {
        "provider": provider_name(llm),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_version": code_version(),
    }


def judge_item(
    req: Requirement,
    item: VerificationItem,
    llm: LLMClient,
    ctx: SourceContext | None = None,
) -> Judgement:
    answer = llm.generate(judge_prompt(req, item, ctx), JudgeAnswer, system=SYSTEM)
    return Judgement(
        item_id=item.id,
        requirement_id=req.id,
        kind=item.kind,
        verdicts=answer.verdicts(),
        acceptable=answer.acceptable,
        graded=graded_hash(req, item),
    )


def judge_warnings(judge_model: str, generator_model: str) -> list[str]:
    """Refuse a judge that is the generator; warn about one from the generator's family."""
    if judge_model.casefold() == generator_model.casefold():
        raise SameModelError(
            f"the judge {judge_model!r} is the model that generated the items; judge with "
            "a different model"
        )
    if model_family(judge_model) == model_family(generator_model):
        return [
            f"the judge {judge_model!r} is from the same family as the generator "
            f"{generator_model!r}: shared blind spots count as correct, so these scores "
            "overstate quality. Judge with another model family."
        ]
    return []
