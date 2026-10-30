"""Diff two versions of a spec: which requirements were added, removed or changed, and which
verification items each change affects.

The input is the requirements extracted from each version, so no model is called. Records
pair across versions by source quote: quote similarity first, then the same section, then
the more similar requirement text (which tells apart the records of a split compound
requirement, since they share one quote), then the same id. Pairs are taken greedily, best
first, each record at most once; a pair needs `MATCH_SIMILARITY`. Requirement ids are not
used to pair, because inserting a requirement shifts the ids after it in its section.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from difflib import SequenceMatcher

from spec2test.generate import Generation, not_verifiable
from spec2test.schema import Criterion, Requirement
from spec2test.trace.models import (
    Action,
    ChangeKind,
    FieldChange,
    ItemImpact,
    RequirementChange,
    SpecDiff,
)

MATCH_SIMILARITY = 0.6
"""Least quote similarity (difflib ratio, after normalizing case and spaces) for an old and
a new record to count as one requirement. Below it, the old one is removed and the new one
added."""

_READING = ["text", "type", "strength", "criteria", "conditions", "verification_method"]
"""The fields that are the extraction model's reading of the quote."""


def diff(
    old: Iterable[Requirement], new: Iterable[Requirement], *, items: Generation | None = None
) -> SpecDiff:
    """Compare the requirements of two versions of a spec.

    With `items`, the old version's generation, every change also lists the items it
    affects: retire, update, review or relink each one, and write an item for each new
    requirement that obliges something.
    """
    old, new = list(old), list(new)
    paired = {n.id: _change(o, n, sim) for o, n, sim in _pair(old, new)}
    changes = [
        paired.get(n.id)
        or RequirementChange(
            kind=ChangeKind.ADDED,
            new_id=n.id,
            new_section=n.source_anchor.section,
            new_quote=n.source_anchor.quote,
        )
        for n in new
    ]
    # A removed record goes right after the change of the old record before it.
    paired_old = {c.old_id: c for c in paired.values()}
    after: RequirementChange | None = None
    for o in old:
        if o.id in paired_old:
            after = paired_old[o.id]
            continue
        removed = RequirementChange(
            kind=ChangeKind.REMOVED,
            old_id=o.id,
            old_section=o.source_anchor.section,
            old_quote=o.source_anchor.quote,
        )
        at = 0 if after is None else next(i for i, c in enumerate(changes) if c is after) + 1
        changes.insert(at, removed)
        after = removed
    result = SpecDiff(
        old_doc=_doc(old), new_doc=_doc(new), changes=changes, items_given=items is not None
    )
    if items is not None:
        result.impacts = _impacts(changes, {r.id: r for r in new}, items)
    return result


def _pair(
    old: list[Requirement], new: list[Requirement]
) -> list[tuple[Requirement, Requirement, float]]:
    candidates = []
    for o in old:
        for n in new:
            sim = _similarity(o.source_anchor.quote, n.source_anchor.quote)
            if sim < MATCH_SIMILARITY:
                continue
            same_section = o.source_anchor.section == n.source_anchor.section
            key = (sim, same_section, _similarity(o.text, n.text, floor=0.0), o.id == n.id)
            candidates.append((key, o, n))
    candidates.sort(key=lambda c: c[0], reverse=True)
    pairs, used_old, used_new = [], set(), set()
    for key, o, n in candidates:
        if o.id in used_old or n.id in used_new:
            continue
        used_old.add(o.id)
        used_new.add(n.id)
        pairs.append((o, n, key[0]))
    return pairs


def _change(o: Requirement, n: Requirement, sim: float) -> RequirementChange:
    fields = []
    if _norm(o.source_anchor.quote) != _norm(n.source_anchor.quote):
        fields.append(
            FieldChange(field="quote", old=o.source_anchor.quote, new=n.source_anchor.quote)
        )
    if o.source_anchor.section != n.source_anchor.section:
        fields.append(
            FieldChange(field="section", old=o.source_anchor.section, new=n.source_anchor.section)
        )
    for name in _READING:
        a, b = _show(name, getattr(o, name)), _show(name, getattr(n, name))
        if _norm(a) != _norm(b):
            fields.append(FieldChange(field=name, old=a, new=b))
    names = {f.field for f in fields}
    if "quote" in names:
        kind = ChangeKind.CHANGED
    elif names - {"section"}:
        kind = ChangeKind.RESTATED
    else:
        kind = ChangeKind.UNCHANGED
    return RequirementChange(
        kind=kind,
        old_id=o.id,
        new_id=n.id,
        old_section=o.source_anchor.section,
        new_section=n.source_anchor.section,
        old_quote=o.source_anchor.quote,
        new_quote=n.source_anchor.quote,
        fields=fields,
        similarity=round(sim, 3),
    )


def _impacts(
    changes: list[RequirementChange], new: dict[str, Requirement], items: Generation
) -> list[ItemImpact]:
    items_for: dict[str, list[str]] = defaultdict(list)
    for item in items.items:
        for req_id in item.requirement_ids:
            items_for[req_id].append(item.id)
    return [
        ItemImpact(
            action=action,
            item_id=item_id,
            old_requirement_id=c.old_id,
            new_requirement_id=c.new_id,
            reason=reason,
        )
        for c in changes
        for action, item_id, reason in _actions(
            c, items_for.get(c.old_id or "", []), new.get(c.new_id or "")
        )
    ]


def _actions(
    c: RequirementChange, old_items: list[str], req: Requirement | None
) -> list[tuple[Action, str | None, str]]:
    """What change `c` asks of each of its old items, or that an item be written."""
    if c.kind is ChangeKind.REMOVED:
        return [(Action.RETIRE, i, "its requirement was removed") for i in old_items]
    assert req is not None
    why_not = _obliges_nothing(req)
    if not old_items:
        # An unchanged requirement without an item is an old gap, not this change's.
        if c.kind is ChangeKind.UNCHANGED or why_not is not None:
            return []
        reason = "new requirement" if c.old_id is None else f"{c.kind}, and it has no item"
        if req.verification_method is None:
            reason += "; classify its verification method first"
        return [(Action.WRITE, None, reason)]
    what = ", ".join(f.field for f in c.fields if f.field != "section")
    renamed = f"; requirement {c.old_id} is now {c.new_id}" if c.old_id != c.new_id else ""
    if why_not is not None:
        action, reason = Action.RETIRE, f"its requirement now {why_not}"
    elif c.kind is ChangeKind.CHANGED:
        action, reason = Action.UPDATE, f"source wording changed ({what}){renamed}"
    elif c.kind is ChangeKind.RESTATED:
        action, reason = Action.REVIEW, f"same wording, extracted differently ({what}){renamed}"
    elif renamed:
        action, reason = Action.RELINK, renamed.removeprefix("; ")
    else:
        return []
    return [(action, i, reason) for i in old_items]


def _obliges_nothing(req: Requirement) -> str | None:
    if reason := not_verifiable(req):
        return f"obliges nothing: {reason}"
    return None


def _show(name: str, value: object) -> str:
    """A field as one comparable line. Criteria and conditions compare as sets; a
    criterion's free-text parameter name is ignored, as the eval ignores it."""
    if name == "criteria":
        return "; ".join(sorted(_criterion(c) for c in value))  # type: ignore[attr-defined]
    if name == "conditions":
        return "; ".join(sorted(_norm(c) for c in value))  # type: ignore[attr-defined]
    if value is None:
        return "-"
    return str(getattr(value, "value", value))


def _criterion(c: Criterion) -> str:
    parts = [c.comparator.value if c.comparator else "?", c.value]
    if c.upper_value is not None:
        parts.append(f"to {c.upper_value}")
    if c.unit:
        parts.append(c.unit)
    if c.tolerance:
        parts.append(c.tolerance)
    return " ".join(parts)


def _doc(reqs: list[Requirement]) -> str:
    docs = sorted({r.source_anchor.doc for r in reqs})
    return ", ".join(docs) or "-"


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _similarity(a: str, b: str, *, floor: float = MATCH_SIMILARITY) -> float:
    """difflib ratio of the normalized texts; the cheap upper bounds skip pairs that cannot
    reach `floor`."""
    m = SequenceMatcher(None, _norm(a), _norm(b), autojunk=False)
    if m.real_quick_ratio() < floor or m.quick_ratio() < floor:
        return 0.0
    return m.ratio()
