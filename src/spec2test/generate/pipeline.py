"""Generate one verification item per requirement, rendered by its verification method.

Inspection becomes a checklist item, test a test case, demonstration and analysis an
evidence item. Per requirement: one model call returns the item's fields, which are then
checked (`check_item`). An item that fails goes back to the model once, with the errors;
if the answer still fails, it lands in `Generation.rejected`, never in `items`.
Requirements with no verification method are listed in `Generation.unclassified`.

Permissions ("may", "need not") and clauses that only say how something is measured or
where a reference point is ("shall be determined in accordance with SAE J964") oblige
nothing a verification item could check: they are listed in `Generation.skipped`, with
the reason, and never sent. See `not_verifiable`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from spec2test.generate.context import SourceContext, source_context
from spec2test.generate.models import (
    Generation,
    GenerationFailure,
    RejectedItem,
    SkippedRequirement,
)
from spec2test.generate.prompts import SYSTEM, generate_prompt, retry_prompt
from spec2test.ingest import Document
from spec2test.llm import LLMClient, LLMError, model_name
from spec2test.schema import (
    ChecklistFields,
    EvidenceFields,
    Requirement,
    Strength,
    TestCaseFields,
    VerificationItem,
    VerificationItemKind,
    VerificationMethod,
)

KIND_FOR_METHOD = {
    VerificationMethod.INSPECTION: VerificationItemKind.CHECKLIST,
    VerificationMethod.TEST: VerificationItemKind.TEST_CASE,
    VerificationMethod.DEMONSTRATION: VerificationItemKind.EVIDENCE,
    VerificationMethod.ANALYSIS: VerificationItemKind.EVIDENCE,
}

_FIELDS = {
    VerificationItemKind.CHECKLIST: ChecklistFields,
    VerificationItemKind.TEST_CASE: TestCaseFields,
    VerificationItemKind.EVIDENCE: EvidenceFields,
}

_ID_SUFFIX = {
    VerificationItemKind.CHECKLIST: "CL",
    VerificationItemKind.TEST_CASE: "TC",
    VerificationItemKind.EVIDENCE: "EV",
}

Fields = ChecklistFields | TestCaseFields | EvidenceFields


def generate(
    requirements: Iterable[Requirement], llm: LLMClient, *, doc: Document | None = None
) -> Generation:
    """Write a verification item for each classified requirement.

    With `doc`, each requirement is sent with its source context (`source_context`): its
    section headings, the definitions it uses and the clauses it references, so a test case
    can follow the procedure they state.
    """
    reqs = list(requirements)
    result = Generation(model=model_name(llm), requirements=reqs)
    for req in reqs:
        if req.verification_method is None:
            result.unclassified.append(req.id)
            continue
        if reason := not_verifiable(req):
            result.skipped.append(SkippedRequirement(requirement_id=req.id, reason=reason))
            continue
        ctx = None if doc is None else source_context(req, doc)
        _generate_one(req, llm, ctx, result)
    return result


def _generate_one(
    req: Requirement, llm: LLMClient, ctx: SourceContext | None, result: Generation
) -> None:
    assert req.verification_method is not None
    kind = KIND_FOR_METHOD[req.verification_method]
    schema = _FIELDS[kind]
    try:
        fields = llm.generate(generate_prompt(req, ctx), schema, system=SYSTEM)
    except LLMError as exc:
        result.failed.append(GenerationFailure(requirement_id=req.id, error=str(exc)))
        return
    errors = check_item(req, fields)
    if errors:
        result.retried += 1
        prompt = retry_prompt(req, fields.model_dump_json(indent=2), errors, ctx)
        try:
            fields = llm.generate(prompt, schema, system=SYSTEM)
            errors = check_item(req, fields)
        except LLMError as exc:
            errors = [*errors, f"retry failed: {exc}"]
    if errors:
        result.rejected.append(
            RejectedItem(requirement_id=req.id, kind=kind, draft=fields, errors=errors)
        )
        return
    result.items.append(
        VerificationItem(
            id=f"{req.id}:{_ID_SUFFIX[kind]}", requirement_ids=[req.id], kind=kind, fields=fields
        )
    )


_DEFINITIONAL = [
    (
        re.compile(
            r"\bshall be (?:determined|measured|calculated) (?:in accordance with|by|using)\b"
        ),
        "states how a quantity is determined, not a limit it must meet",
    ),
    (
        re.compile(r"\bshall be (?:those|as) (?:established|defined|specified|given) in\b"),
        "defines a reference the other requirements use, not an obligation of its own",
    ),
]


def not_verifiable(req: Requirement) -> str | None:
    """Why `req` obliges nothing a verification item could check, or None when it does.

    A permission ("may", "need not") only relaxes another requirement; a clause that only
    says how something is determined or where a reference point is defines terms for the
    others. Neither has a verdict of its own. A clause with a measurable criterion is
    always kept.
    """
    if req.strength is Strength.MAY:
        return "a permission ('may'): it relaxes another requirement and obliges nothing"
    text = " ".join(req.text.casefold().split())
    if re.search(r"\bneed not\b", text) and not re.search(r"\b(shall|must)\b", text):
        return "a permission ('need not'): it relaxes another requirement and obliges nothing"
    if req.criteria:
        return None
    for pattern, reason in _DEFINITIONAL:
        if pattern.search(text):
            return f"a definition: it {reason}"
    return None


def check_item(req: Requirement, fields: Fields) -> list[str]:
    """Why `fields` cannot stand as the item for `req`; empty when it can.

    The verdict (pass condition, pass criterion or acceptance criterion) must name every
    value the requirement's criteria state, and a demonstration must say what to do.
    """
    errors = []
    verdict = verdict_text(fields)
    for c in req.criteria:
        for value in (c.value, c.upper_value):
            if value is not None and not _mentions(verdict, value):
                errors.append(
                    f"the verdict does not state the criterion value {value!r} "
                    f"({c.parameter}); copy it exactly as the requirement writes it"
                )
    if (
        req.verification_method is VerificationMethod.DEMONSTRATION
        and isinstance(fields, EvidenceFields)
        and not fields.procedure
    ):
        errors.append("a demonstration needs a procedure: the actions to perform and observe")
    return errors


def verdict_text(fields: Fields) -> str:
    """The text that decides pass or fail."""
    if isinstance(fields, ChecklistFields):
        return fields.pass_condition
    if isinstance(fields, TestCaseFields):
        return f"{fields.pass_criterion}\n{fields.expected_result}"
    return fields.acceptance_criterion


def _mentions(text: str, value: str) -> bool:
    """`value` appears in `text` as a whole token: '2.0' is not in '12.05'."""
    value = " ".join(value.casefold().split())
    pattern = rf"(?<![\w.]){re.escape(value)}(?![\w]|\.\d)"
    return re.search(pattern, " ".join(text.casefold().split())) is not None
