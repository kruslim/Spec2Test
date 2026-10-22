"""Prompts for the judge. The FakeLLM fixtures match on phrases from these, so a wording
change here may need the same change in `tests/fixtures/llm/judge_*.json`."""

from __future__ import annotations

import json

from spec2test.generate import SourceContext, render_context
from spec2test.judge.rubric import ACCEPTABLE, RUBRIC
from spec2test.schema import Requirement, VerificationItem

_CRITERIA = "\n".join(f"- {name}: {text}" for name, text in RUBRIC.items())

SYSTEM = f"""\
You grade verification items: the checklist item, test case or evidence item written to \
verify one requirement. Another model wrote the item; grade it strictly against the \
requirement and the source context given with it (its section headings, the definitions \
it uses and the clauses it references), not against what you would have written.

Grade each rubric criterion pass or fail, with a short reason that cites the item or the \
requirement. A criterion passes only if it holds completely.

{_CRITERIA}

Then the overall call:
- acceptable: {ACCEPTABLE}

Write the reason before deciding each verdict."""


def judge_prompt(req: Requirement, item: VerificationItem, ctx: SourceContext | None = None) -> str:
    lines = []
    if ctx is not None and ctx.headings:
        lines += [f"Within: {' > '.join(ctx.headings)}", ""]
    lines += [
        "<requirement>",
        json.dumps(_requirement(req), ensure_ascii=False, indent=2),
        "</requirement>",
    ]
    if ctx is not None and (rendered := render_context(ctx, headings=False)):
        lines += ["", rendered]
    lines += [
        "",
        f'<item kind="{item.kind.value}">',
        item.fields.model_dump_json(indent=2),
        "</item>",
        "",
        "Grade this item against the rubric.",
    ]
    return "\n".join(lines)


def _requirement(req: Requirement) -> dict:
    record: dict = {"id": req.id, "section": req.source_anchor.section, "text": req.text}
    if req.source_anchor.quote != req.text:
        record["source"] = req.source_anchor.quote
    if req.verification_method is not None:
        record["verification_method"] = req.verification_method.value
    record["criteria"] = [c.model_dump(mode="json", exclude_none=True) for c in req.criteria]
    record["conditions"] = req.conditions
    return record
