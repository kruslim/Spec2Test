"""The traceability matrix: each requirement, the items that verify it, and the gaps."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from spec2test.generate import KIND_FOR_METHOD, Generation, not_verifiable
from spec2test.schema import Requirement
from spec2test.trace.models import Coverage, Matrix, OrphanItem, TraceRow


def trace(generation: Generation, requirements: Iterable[Requirement] | None = None) -> Matrix:
    """Trace `generation`'s items to its requirements, or to `requirements` when given.

    A requirement generation has no record of (one from `requirements`) is unclassified or
    skipped by the rules generation itself applies, and missing otherwise.

    Pass another set of requirements, such as a newer extraction, to see which of these items
    still point at a requirement and which are left orphaned. An item can verify several
    requirements and a requirement can have several items; each pair is one link.
    """
    reqs = generation.requirements if requirements is None else list(requirements)
    ids = {r.id for r in reqs}
    items_for: dict[str, list[str]] = defaultdict(list)
    kinds = {i.id: i.kind for i in generation.items}
    orphans = []
    for item in generation.items:
        for req_id in item.requirement_ids:
            items_for[req_id].append(item.id)
        if not ids.intersection(item.requirement_ids):
            orphans.append(
                OrphanItem(item_id=item.id, kind=item.kind, requirement_ids=item.requirement_ids)
            )

    notes: dict[str, tuple[Coverage, str | None]] = {}
    for req_id in generation.unclassified:
        notes[req_id] = (Coverage.UNCLASSIFIED, "no verification method")
    for s in generation.skipped:
        notes[s.requirement_id] = (Coverage.SKIPPED, s.reason)
    for f in generation.failed:
        notes[f.requirement_id] = (Coverage.FAILED, f.error)
    for rej in generation.rejected:
        notes[rej.requirement_id] = (Coverage.REJECTED, "; ".join(rej.errors))

    rows = []
    for r in reqs:
        item_ids = items_for.get(r.id, [])
        coverage, note = (Coverage.COVERED, None) if item_ids else notes.get(r.id, _why_none(r))
        method = r.verification_method
        wrong = (
            [] if method is None else [i for i in item_ids if kinds[i] != KIND_FOR_METHOD[method]]
        )
        rows.append(
            TraceRow(
                requirement_id=r.id,
                section=r.source_anchor.section,
                page=r.source_anchor.page,
                text=r.text,
                verification_method=method,
                item_ids=item_ids,
                coverage=coverage,
                note=note,
                wrong_kind=wrong,
            )
        )
    return Matrix(model=generation.model, rows=rows, orphans=orphans)


def _why_none(req: Requirement) -> tuple[Coverage, str | None]:
    if req.verification_method is None:
        return Coverage.UNCLASSIFIED, "no verification method"
    if reason := not_verifiable(req):
        return Coverage.SKIPPED, reason
    return Coverage.MISSING, None
