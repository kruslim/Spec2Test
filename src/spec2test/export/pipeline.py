"""Turn a Generation, and the review decisions on it, into the rows a profile exports.

No model calls. Reviewer edits replace the generated fields; the profile's statuses pick
which items go out (rejected ones stay out by default). IDs are numbered over every item
in the generation, before any is left out, so rejecting one does not renumber the rest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from spec2test.export.models import Export, ExportRow, Value
from spec2test.export.profile import Profile, ReviewStatus
from spec2test.generate import Generation
from spec2test.review import FIELDS, Decision, ReviewDecision
from spec2test.schema import (
    ChecklistFields,
    EvidenceFields,
    Requirement,
    TestCaseFields,
    VerificationItem,
)


class ExportError(ValueError):
    """The items cannot be exported under this profile."""


_STATUS = {
    Decision.ACCEPT: ReviewStatus.ACCEPTED,
    Decision.EDIT: ReviewStatus.EDITED,
    Decision.REJECT: ReviewStatus.REJECTED,
}


def prepare(
    generation: Generation,
    profile: Profile | None = None,
    decisions: Mapping[str, ReviewDecision] | None = None,
) -> Export:
    """The rows `profile` exports from `generation`. `decisions` is the latest review
    decision per item ID (`ReviewStore.latest`); without it every item is unreviewed.

    With `decisions`, a profile that does not list its statuses exports only accepted and
    edited items: unreviewed model output never goes out next to reviewed items unasked."""
    profile = profile or Profile()
    wanted = set(profile.output.statuses)
    if decisions is not None and "statuses" not in profile.output.model_fields_set:
        wanted = {ReviewStatus.ACCEPTED, ReviewStatus.EDITED}
    decisions = decisions or {}
    if not decisions and not wanted & {ReviewStatus.UNREVIEWED, ReviewStatus.REJECTED}:
        raise ExportError(
            f"profile {profile.name!r} exports only reviewed items, and no review decisions "
            "were given; pass the review log"
        )
    unknown = sorted(set(decisions) - {i.id for i in generation.items})
    if unknown:
        raise ExportError(f"decisions on items not in the generation: {', '.join(unknown)}")

    reqs = {r.id: r for r in generation.requirements}
    ids = _ids(generation, profile)
    dupes = sorted(i for i, n in Counter(ids.values()).items() if n > 1)
    if dupes:
        raise ExportError(
            f"ids.pattern {profile.ids.pattern!r} gives more than one item the ID "
            f"{', '.join(dupes)}; add {{n}} or {{item}} to it"
        )

    rows: list[ExportRow] = []
    left_out: Counter[ReviewStatus] = Counter()
    missing: dict[str, list[str]] = {}
    for item in generation.items:
        decided = decisions.get(item.id)
        status = ReviewStatus.UNREVIEWED if decided is None else _STATUS[decided.decision]
        if status not in wanted:
            left_out[status] += 1
            continue
        if decided is not None and decided.edited is not None:
            fields = FIELDS[item.kind].model_validate(decided.edited)
            item = item.model_copy(update={"fields": fields})
        verified = [reqs[r] for r in item.requirement_ids if r in reqs]
        values = _values(item, verified, status, decided, generation.model, ids[item.id])
        values["kind"], values["status"] = (
            profile.kind_label(item.kind),
            profile.status_label(status),
        )
        row_values: dict[str, Value] = {}
        for column in profile.output.columns:
            value = profile.values.get(column, values.get(column, ""))
            if column in profile.required and not _filled(value):
                value = f"[{profile.label(column)}]"
                missing.setdefault(column, []).append(ids[item.id])
            row_values[column] = value
        rows.append(
            ExportRow(
                id=ids[item.id], item=item, requirements=verified, status=status, values=row_values
            )
        )
    labels = {c: profile.label(c) for c in profile.output.columns}
    return Export(
        profile=profile,
        model=generation.model,
        title=profile.output.title or _title(generation),
        columns=list(profile.output.columns),
        labels=labels,
        rows=rows,
        missing=missing,
        left_out=dict(left_out),
    )


def _ids(generation: Generation, profile: Profile) -> dict[str, str]:
    scheme = profile.ids
    reqs = {r.id: r for r in generation.requirements}
    per_kind: Counter[str] = Counter()
    out: dict[str, str] = {}
    for n, item in enumerate(generation.items, start=scheme.start):
        first = reqs.get(item.requirement_ids[0])
        doc = scheme.doc or (first.source_anchor.doc if first else _doc(generation))
        out[item.id] = scheme.pattern.format(
            item=item.id,
            doc=doc,
            kind=scheme.kind_codes[item.kind],
            section=first.source_anchor.section if first else "",
            req=item.requirement_ids[0],
            n=n,
            n_kind=scheme.start + per_kind[item.kind],
        )
        per_kind[item.kind] += 1
    return out


def _values(
    item: VerificationItem,
    reqs: list[Requirement],
    status: ReviewStatus,
    decided: ReviewDecision | None,
    model: str,
    item_id: str,
) -> dict[str, Value]:
    f = item.fields
    values: dict[str, Value] = {
        "id": item_id,
        "item_id": item.id,
        "kind": item.kind.value,
        "requirements": list(item.requirement_ids),
        "section": _distinct(r.source_anchor.section for r in reqs),
        "page": _distinct(str(r.source_anchor.page) for r in reqs),
        "method": _distinct(r.verification_method.value for r in reqs if r.verification_method),
        "strength": _distinct(r.strength.value for r in reqs),
        "requirement": "\n".join(r.text for r in reqs),
        "quote": "\n".join(r.source_anchor.quote for r in reqs),
        "status": status.value,
        "reviewer": decided.reviewer if decided else "",
        "note": decided.note if decided else "",
        "model": model,
    }
    values.update({k: v for k, v in f.model_dump().items() if k in _FIELD_COLUMNS})
    if isinstance(f, ChecklistFields):
        values["actions"], values["acceptance"] = [f.inspect], f.pass_condition
    elif isinstance(f, TestCaseFields):
        values["actions"], values["acceptance"] = list(f.steps), f.pass_criterion
    elif isinstance(f, EvidenceFields):
        values["actions"] = list(f.procedure) or [f.description]
        values["acceptance"] = f.acceptance_criterion
    return values


_FIELD_COLUMNS = {
    name
    for fields in (ChecklistFields, TestCaseFields, EvidenceFields)
    for name in fields.model_fields
}


def _distinct(values) -> str:
    return ", ".join(dict.fromkeys(values))


def _filled(value: Value) -> bool:
    if isinstance(value, list):
        return any(v.strip() for v in value)
    return bool(value.strip())


def _doc(generation: Generation) -> str:
    docs = {r.source_anchor.doc for r in generation.requirements}
    return docs.pop() if len(docs) == 1 else "doc"


def _title(generation: Generation) -> str:
    docs = sorted({r.source_anchor.doc for r in generation.requirements})
    return f"Verification items: {', '.join(docs)}" if docs else "Verification items"
