"""Render a verification item as plain text: the checklist line, test case or evidence item
a person reads. Each kind renders its own fields, in the order they are used."""

from __future__ import annotations

from spec2test.schema import ChecklistFields, EvidenceFields, TestCaseFields, VerificationItem

_TITLES = {"checklist": "Checklist item", "test_case": "Test case", "evidence": "Evidence item"}


def render(item: VerificationItem) -> str:
    lines = [f"{_TITLES[item.kind.value]} {item.id}  (verifies {', '.join(item.requirement_ids)})"]
    f = item.fields
    if isinstance(f, ChecklistFields):
        lines += [f"  Inspect: {f.inspect}", f"  Pass if: {f.pass_condition}"]
    elif isinstance(f, TestCaseFields):
        lines += _list("Preconditions", f.preconditions)
        lines += _list("Setup", f.setup)
        lines += _list("Equipment", f.equipment)
        lines += _list("Steps", f.steps, numbered=True)
        lines += [f"  Expected result: {f.expected_result}", f"  Pass if: {f.pass_criterion}"]
    elif isinstance(f, EvidenceFields):
        lines.append(f"  {f.description}")
        lines += _list("Procedure", f.procedure, numbered=True)
        lines += _list("Evidence", f.evidence)
        lines.append(f"  Accept if: {f.acceptance_criterion}")
    return "\n".join(lines)


def _list(title: str, entries: list[str], *, numbered: bool = False) -> list[str]:
    if not entries:
        return []
    marks = [f"{n}." if numbered else "-" for n in range(1, len(entries) + 1)]
    return [f"  {title}:", *(f"    {m} {e}" for m, e in zip(marks, entries, strict=True))]
