"""Prompts for generation. The FakeLLM fixtures match on phrases from these, so a wording
change here may need the same change in `tests/fixtures/llm/generate_*.json`."""

from __future__ import annotations

import json

from spec2test.schema import Requirement, VerificationItem, VerificationMethod

SYSTEM = """\
You write verification items: the checklist item, test case or evidence item an engineer \
uses to show that one requirement is met, ending in a clear pass or fail.

Rules for every item:
- Verify exactly the requirement given: every criterion and condition it states, and \
nothing it does not.
- Use the requirement's own values, units, tolerances and comparators, written as the \
requirement writes them. The pass criterion states every limit the requirement sets.
- Never invent a value, limit or procedure. When the requirement and its source clauses \
do not give something a tester needs, write what is needed in [brackets], such as \
[supply voltage in V].
- When the requirement points to a procedure in another clause ("as specified in \
S14.1"), follow that clause's text if it is given and cite the clause in the step; if it \
is not given, cite the clause instead of making up its steps.
- A testability flag means the requirement is hard to verify as written. Keep its \
wording, and bracket what the flag says is missing; its suggested rewrite shows how.
- Write for a competent tester: concrete, one action per step, in order."""

_INSTRUCTIONS = {
    VerificationMethod.INSPECTION: """\
Write a checklist item for this requirement; it is verified by inspection.
- inspect: what to look at, where, and with what (a rule or gauge when a dimension is \
checked, or the document to check).
- pass_condition: the observable condition that passes, with the requirement's values.""",
    VerificationMethod.TEST: """\
Write a test case for this requirement; it is verified by test.
- preconditions: the state the item under test is in before the test starts.
- setup: the configuration, instrumentation and environment, including the \
requirement's conditions.
- steps: the actions and measurements, one per step, in order.
- expected_result: what a compliant item shows.
- pass_criterion: the measured quantity compared with the requirement's limit, \
e.g. "display time ≤ 2.0 s".
- equipment: the instruments and fixtures needed.""",
    VerificationMethod.DEMONSTRATION: """\
Write an evidence item for this requirement; it is verified by demonstration.
- description: what is demonstrated.
- procedure: the operations to perform and observe, one per step, in order.
- evidence: what to capture (video, photographs, a signed observation record).
- acceptance_criterion: what the observer must see for it to pass.""",
    VerificationMethod.ANALYSIS: """\
Write an evidence item for this requirement; it is verified by analysis.
- description: the calculation, simulation or report that shows compliance.
- procedure: the analysis steps; leave it empty when the evidence is an existing report.
- evidence: the documents to attach and what each must show.
- acceptance_criterion: the result the analysis must reach, with the requirement's \
values.""",
}


def item_prompt(req: Requirement, context: str | None) -> str:
    return f"{_inputs(req, context)}\n\n{_instructions(req)}"


def retry_prompt(
    req: Requirement, context: str | None, draft: VerificationItem, errors: list[str]
) -> str:
    problems = "\n".join(f"- {e}" for e in errors)
    return f"""\
{_inputs(req, context)}

The {draft.kind.value.replace("_", " ")} you wrote for this requirement failed the check.

<item>
{draft.fields.model_dump_json(indent=2)}
</item>

Problem:
{problems}

{_instructions(req)}
Fix the problem; keep what was right."""


def requirement_record(req: Requirement) -> dict:
    """The requirement as the generator and the judge are shown it."""
    record: dict = {"id": req.id, "section": req.source_anchor.section, "text": req.text}
    if req.source_anchor.quote != req.text:
        record["source"] = req.source_anchor.quote
    record["type"] = req.type.value
    record["strength"] = req.strength.value
    if req.verification_method is not None:
        record["verification_method"] = req.verification_method.value
    record["criteria"] = [c.model_dump(mode="json", exclude_none=True) for c in req.criteria]
    record["conditions"] = req.conditions
    record["testability_flags"] = [
        f.model_dump(mode="json", exclude_none=True) for f in req.testability_flags
    ]
    return record


def _inputs(req: Requirement, context: str | None) -> str:
    parts = [
        f"Document: {req.source_anchor.doc}",
        f"Requirement: {req.id}",
        "",
        "<requirement>",
        json.dumps(requirement_record(req), ensure_ascii=False, indent=2),
        "</requirement>",
    ]
    if context:
        parts += ["", "<source>", context, "</source>"]
    return "\n".join(parts)


def _instructions(req: Requirement) -> str:
    assert req.verification_method is not None
    return _INSTRUCTIONS[req.verification_method]
