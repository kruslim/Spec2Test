"""Generate one verification item per requirement, shaped by its verification method.

Inspection becomes a checklist item, test a test case, demonstration and analysis an
evidence item (`schema.KIND_FOR_METHOD`). Per requirement: one model call returns the
item's fields, and the item check reads them against the requirement. An item that fails
goes back to the model once, with the errors; if the answer still fails, it is kept in
`Generation.rejected`, never in `items`. A requirement with no verification method gets
no item: it is listed in `Generation.skipped`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from spec2test.generate.context import source_context
from spec2test.generate.models import Generation, GenerationFailure, RejectedItem
from spec2test.generate.prompts import SYSTEM, item_prompt, retry_prompt
from spec2test.ingest import Document
from spec2test.llm import LLMClient, LLMError, model_name
from spec2test.schema import (
    FIELDS_FOR_KIND,
    KIND_FOR_METHOD,
    ChecklistFields,
    Fields,
    Requirement,
    TestCaseFields,
    VerificationItem,
    VerificationMethod,
)


def generate(
    requirements: Iterable[Requirement], llm: LLMClient, *, doc: Document | None = None
) -> Generation:
    """Write a verification item for each classified requirement.

    With `doc`, the model also gets each requirement's clause and the clauses it refers
    to, so a test can follow the procedure the spec states instead of a made-up one.
    """
    reqs = list(requirements)
    result = Generation(model=model_name(llm), requirements=reqs)
    for req in reqs:
        if req.verification_method is None:
            result.skipped.append(req.id)
            continue
        context = source_context(doc, req) if doc is not None else None
        _generate_one(req, context, llm, result)
    return result


def item_id(req: Requirement) -> str:
    """'<requirement id>:<kind>': one item per requirement and kind."""
    assert req.verification_method is not None
    return f"{req.id}:{KIND_FOR_METHOD[req.verification_method].value}"


def check_item(item: VerificationItem, req: Requirement) -> list[str]:
    """Why `item` does not verify `req` as stated; empty when it passes the check.

    Every limit the requirement sets must be in the pass criterion, and a demonstration
    needs a procedure to perform. What the check cannot read (whether the steps make
    sense) is the judge's job.
    """
    field, verdict = _verdict(item.fields)
    problems = []
    for c in req.criteria:
        for value in (c.value, c.upper_value):
            if value is not None and not _states(verdict, value):
                unit = f" {c.unit}" if c.unit else ""
                problems.append(
                    f"{field} does not state the requirement's {c.parameter} limit: {value}{unit}"
                )
    if req.verification_method is VerificationMethod.DEMONSTRATION and not getattr(
        item.fields, "procedure", None
    ):
        problems.append("a demonstration needs a procedure: the operations to perform and observe")
    return problems


def _generate_one(
    req: Requirement, context: str | None, llm: LLMClient, result: Generation
) -> None:
    assert req.verification_method is not None
    schema = FIELDS_FOR_KIND[KIND_FOR_METHOD[req.verification_method]]
    try:
        fields = llm.generate(item_prompt(req, context), schema, system=SYSTEM)
    except LLMError as exc:
        result.failed.append(GenerationFailure(requirement_id=req.id, error=str(exc)))
        return
    item = _item(req, fields)
    errors = check_item(item, req)
    if not errors:
        result.items.append(item)
        return

    result.retried += 1
    try:
        fields = llm.generate(retry_prompt(req, context, item, errors), schema, system=SYSTEM)
    except LLMError as exc:
        errors = [*errors, f"retry failed: {exc}"]
        result.rejected.append(RejectedItem(requirement_id=req.id, draft=item, errors=errors))
        return
    item = _item(req, fields)
    if errors := check_item(item, req):
        result.rejected.append(RejectedItem(requirement_id=req.id, draft=item, errors=errors))
    else:
        result.items.append(item)


def _item(req: Requirement, fields: Fields) -> VerificationItem:
    assert req.verification_method is not None
    return VerificationItem(
        id=item_id(req),
        requirement_ids=[req.id],
        kind=KIND_FOR_METHOD[req.verification_method],
        fields=fields,
    )


def _verdict(fields: Fields) -> tuple[str, str]:
    """The field that decides pass or fail, and its text."""
    if isinstance(fields, ChecklistFields):
        return "pass_condition", fields.pass_condition
    if isinstance(fields, TestCaseFields):
        return "pass_criterion", fields.pass_criterion
    return "acceptance_criterion", fields.acceptance_criterion


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")


def _states(text: str, value: str) -> bool:
    """True when `text` states `value`: the same number (2.0 = 2, 1,651 = 1651), or the
    same words when the value is not a number."""
    number = _number(value)
    if number is None:
        return _norm(value) in _norm(text)
    return any(_number(m.group(0)) == number for m in _NUMBER.finditer(text))


def _number(text: str) -> float | None:
    try:
        # Signs are dropped: a hyphen in the criterion and a minus sign (U+2212) in the
        # item are the same "-40".
        return float(text.strip().lstrip("+-\u2212±").replace(",", ""))
    except ValueError:
        return None


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())
