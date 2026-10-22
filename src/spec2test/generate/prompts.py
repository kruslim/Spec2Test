"""Prompts for generation. The FakeLLM fixtures match on phrases from these, so a wording
change here may need the same change in `tests/fixtures/llm/generate_*.json`."""

from __future__ import annotations

import json

from spec2test.generate.context import SourceContext, render_context
from spec2test.schema import Requirement, VerificationMethod

SYSTEM = """\
You write verification items for requirements: the checklist item, test case or evidence \
item an engineer uses to reach a clear pass or fail verdict on one requirement.

Rules for every item:
- Verify exactly this requirement: every obligation, criterion and condition it states, \
and no pass or fail condition it does not state.
- Copy every value, unit, tolerance and comparator the requirement states, exactly as it \
states them. The verdict must name each criterion value.
- Never invent a value, limit, tolerance or instrument accuracy the source does not give. \
Write what is missing as a [bracketed placeholder], such as [maximum force in N], and say \
in the item what has to be decided.
- Where the requirement names a procedure or clause ("when tested in accordance with \
S14.1"), follow the referenced text given with it, and cite the clause in the step. Where \
the source gives no procedure, write one: the setup, steps and equipment are yours to \
specify, as long as they add no pass or fail limit.
- The section headings say what the requirement is about ("Outside rearview mirror - \
driver's side"); the source section is the clause it comes from, whose other sentences \
may say how or from where it is measured; the definitions say what its terms mean. Use \
all three.
- A testability flag on the requirement is a known gap: write the item around it with a \
placeholder; do not paper over it.
- Plain, imperative, specific steps a competent technician can follow without guessing."""

_KIND_INSTRUCTIONS = {
    VerificationMethod.INSPECTION: """\
Write a checklist item (verification method: inspection).
- inspect: what to look at or which document to check, and where.
- pass_condition: what has to be seen for a pass, naming every criterion value.""",
    VerificationMethod.TEST: """\
Write a test case (verification method: test).
- preconditions: the state the item under test must be in before the test.
- setup: how to arrange the item, the environment and the instruments.
- steps: the ordered actions, each one action, including what to measure and record.
- expected_result: what a compliant item shows.
- pass_criterion: the measurable verdict: the parameter, comparator, value and unit; \
the test fails otherwise.
- equipment: the instruments and fixtures needed.""",
    VerificationMethod.DEMONSTRATION: """\
Write an evidence item for a demonstration (verification method: demonstration).
- description: what is demonstrated and to whom.
- procedure: the ordered actions to perform while observing; at least one step.
- evidence: what to capture while doing it (video, photographs, signed observation \
record, ...).
- acceptance_criterion: what has to be observed for a pass, naming every criterion value.""",
    VerificationMethod.ANALYSIS: """\
Write an evidence item for an analysis (verification method: analysis).
- description: what the analysis shows and from which inputs.
- procedure: the calculation or modelling steps, if the requirement implies them; it may \
be empty.
- evidence: the calculation, model, report or drawing to attach.
- acceptance_criterion: the result that passes, naming every criterion value.""",
}


def generate_prompt(req: Requirement, ctx: SourceContext | None = None) -> str:
    assert req.verification_method is not None
    return f"""\
{_context(req, ctx)}

{_KIND_INSTRUCTIONS[req.verification_method]}"""


def retry_prompt(
    req: Requirement, draft_json: str, errors: list[str], ctx: SourceContext | None = None
) -> str:
    assert req.verification_method is not None
    problems = "\n".join(f"- {e}" for e in errors)
    return f"""\
{_context(req, ctx)}

{_KIND_INSTRUCTIONS[req.verification_method]}

The item you wrote for this requirement failed the check.

<item>
{draft_json}
</item>

Problem:
{problems}

Return the corrected item."""


def _context(req: Requirement, ctx: SourceContext | None) -> str:
    lines = [
        f"Document: {req.source_anchor.doc}",
        f"Section: {req.source_anchor.section or '(front matter)'}",
    ]
    if ctx is not None and ctx.headings:
        lines.append(f"Within: {' > '.join(ctx.headings)}")
    rendered = render_context(ctx, headings=False) if ctx is not None else ""
    lines += [
        "",
        "<requirement>",
        json.dumps(_record(req), ensure_ascii=False, indent=2),
        "</requirement>",
    ]
    if rendered:
        lines += ["", rendered]
    return "\n".join(lines)


def _record(req: Requirement) -> dict:
    record: dict = {"id": req.id, "text": req.text}
    if req.source_anchor.quote != req.text:
        record["source"] = req.source_anchor.quote
    record["strength"] = req.strength.value
    record["type"] = req.type.value
    record["criteria"] = [c.model_dump(mode="json", exclude_none=True) for c in req.criteria]
    record["conditions"] = req.conditions
    record["testability_flags"] = [
        f.model_dump(mode="json", exclude_none=True) for f in req.testability_flags
    ]
    return record
