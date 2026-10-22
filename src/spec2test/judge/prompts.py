"""Prompts for the judge. The FakeLLM fixtures match on phrases from these, so a wording
change here may need the same change in `tests/fixtures/llm/judge_*.json`."""

from __future__ import annotations

import json

from spec2test.generate.prompts import requirement_record
from spec2test.judge.rubric import RUBRIC
from spec2test.schema import Requirement, VerificationItem

_CRITERIA = "\n".join(f"- {c.value}: {text}" for c, text in RUBRIC.items())

SYSTEM = f"""\
You grade verification items (checklist items, test cases and evidence items) against \
the requirement each one verifies, for engineers deciding whether to accept an item \
unedited.

Grade every criterion of the rubric pass or fail, each on its own. For each, give the \
reason first, in one sentence that names the defect (or why it passes when that is not \
obvious), then the verdict.

Rubric:
{_CRITERIA}

How to grade:
- Fail a criterion only for a defect you can point to in the item. Wording, style and \
length are not defects.
- Judge against the requirement record and the source clauses given, not against what \
you know about the subject. A procedure the item cites by clause number, where that \
clause is not given, is not a defect.
- A checklist item has no steps: grade executable on whether what to inspect, where, and \
with what is specific. An evidence item: on whether the procedure and the evidence to \
capture or attach are specific."""


def judge_prompt(
    requirements: list[Requirement], item: VerificationItem, context: str | None
) -> str:
    records = "\n".join(
        json.dumps(requirement_record(r), ensure_ascii=False, indent=2) for r in requirements
    )
    parts = [f"Item: {item.id}", "", "<requirement>", records, "</requirement>"]
    if context:
        parts += ["", "<source>", context, "</source>"]
    kind = item.kind.value.replace("_", " ")
    parts += [
        "",
        f'<item kind="{item.kind.value}">',
        item.fields.model_dump_json(indent=2),
        "</item>",
        "",
        f"Grade this {kind} against the rubric.",
    ]
    return "\n".join(parts)
