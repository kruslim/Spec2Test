"""Render verification items as Markdown, the way a reviewer reads them."""

from __future__ import annotations

from collections.abc import Iterable

from spec2test.generate.models import Generation
from spec2test.schema import (
    ChecklistFields,
    EvidenceFields,
    Requirement,
    TestCaseFields,
    VerificationItem,
    VerificationMethod,
)

_EVIDENCE_TITLE = {
    VerificationMethod.DEMONSTRATION: "Evidence item (demonstration)",
    VerificationMethod.ANALYSIS: "Evidence item (analysis)",
}


def render(item: VerificationItem, requirements: Iterable[Requirement] = ()) -> str:
    """One item as Markdown, under the requirements it verifies when they are given."""
    reqs = {r.id: r for r in requirements}
    lines = [f"### {_title(item, reqs)}: {item.id}", ""]
    for rid in item.requirement_ids:
        r = reqs.get(rid)
        if r is None:
            lines.append(f"Verifies {rid}.")
        else:
            where = f"{r.source_anchor.doc} {r.source_anchor.section}, p{r.source_anchor.page}"
            lines.append(f"Verifies {rid} ({where}): {r.text}")
    lines.append("")
    f = item.fields
    if isinstance(f, ChecklistFields):
        lines += [f"- [ ] **Inspect:** {f.inspect}", f"- **Pass if:** {f.pass_condition}"]
    elif isinstance(f, TestCaseFields):
        lines += _bullets("Preconditions", f.preconditions)
        lines += _bullets("Setup", f.setup)
        lines += _bullets("Equipment", f.equipment)
        lines += _numbered("Steps", f.steps)
        lines += [
            f"**Expected result:** {f.expected_result}",
            "",
            f"**Pass criterion:** {f.pass_criterion}",
        ]
    else:
        lines += [f.description, ""]
        lines += _numbered("Procedure", f.procedure)
        lines += _bullets("Evidence", f.evidence)
        lines.append(f"**Acceptance criterion:** {f.acceptance_criterion}")
    return "\n".join(lines).rstrip() + "\n"


def render_generation(generation: Generation) -> str:
    """Every item, then what was rejected, failed or skipped, as one Markdown document."""
    reqs = {r.id: r for r in generation.requirements}
    parts = [render(item, generation.requirements) for item in generation.items]
    gaps = [
        *(f"- {r.requirement_id}: rejected: {'; '.join(r.errors)}" for r in generation.rejected),
        *(f"- {f.requirement_id}: failed: {f.error}" for f in generation.failed),
        *(f"- {rid}: skipped: no verification method" for rid in generation.skipped),
    ]
    if gaps:
        parts.append("\n".join(["### No item", "", *gaps]) + "\n")
    if not parts:
        return f"No requirements ({len(reqs)} given).\n"
    return "\n".join(parts)


def _title(item: VerificationItem, reqs: dict[str, Requirement]) -> str:
    if isinstance(item.fields, ChecklistFields):
        return "Checklist item"
    if isinstance(item.fields, TestCaseFields):
        return "Test case"
    assert isinstance(item.fields, EvidenceFields)
    methods = {reqs[i].verification_method for i in item.requirement_ids if i in reqs}
    if len(methods) == 1 and (method := methods.pop()) in _EVIDENCE_TITLE:
        return _EVIDENCE_TITLE[method]
    return "Evidence item"


def _bullets(title: str, items: list[str]) -> list[str]:
    return [f"**{title}**", "", *(f"- {i}" for i in items), ""] if items else []


def _numbered(title: str, items: list[str]) -> list[str]:
    return [f"**{title}**", "", *(f"{n}. {i}" for n, i in enumerate(items, 1)), ""] if items else []
