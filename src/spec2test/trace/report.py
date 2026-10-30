"""Render a traceability matrix and a spec diff: plain text for the terminal, and the matrix
as CSV for a spreadsheet."""

from __future__ import annotations

import csv
import io
from collections import Counter

from spec2test.trace.models import GAPS, Action, ChangeKind, Coverage, Matrix, SpecDiff


def matrix_report(matrix: Matrix) -> str:
    """One line per requirement with its items, then the gaps, orphans and a summary."""
    width = max((len(r.requirement_id) for r in matrix.rows), default=0)
    lines = [f"items by {matrix.model}", ""]
    for r in matrix.rows:
        method = r.verification_method.value if r.verification_method else "-"
        items = ", ".join(r.item_ids) if r.item_ids else r.coverage.value.upper()
        lines.append(f"{r.requirement_id:{width}}  {method:13}  {items}")
    gaps = matrix.gaps
    if gaps:
        lines += ["", "gaps (requirements with no item):"]
        lines += [f"  {r.requirement_id}  {r.coverage.value}: {r.note or '-'}" for r in gaps]
    wrong = [r for r in matrix.rows if r.wrong_kind]
    if wrong:
        lines += ["", "items of the wrong kind for their requirement's method:"]
        lines += [
            f"  {r.requirement_id}  ({r.verification_method})  {', '.join(r.wrong_kind)}"
            for r in wrong
        ]
    if matrix.orphans:
        lines += ["", "orphan items (no requirement in the set):"]
        lines += [f"  {o.item_id}  verifies {', '.join(o.requirement_ids)}" for o in matrix.orphans]
    counts = Counter(r.coverage for r in matrix.rows)
    needed = len(matrix.rows) - counts[Coverage.SKIPPED]
    covered = counts[Coverage.COVERED]
    share = f"{covered / needed:.1%}" if needed else "-"
    lines += [
        "",
        f"{len(matrix.rows)} requirements: {covered} covered, {counts[Coverage.SKIPPED]} "
        f"skipped, {sum(counts[c] for c in GAPS)} gaps "
        f"({', '.join(f'{counts[c]} {c.value}' for c in sorted(GAPS) if counts[c]) or 'none'}); "
        f"coverage {share} of {needed} that oblige something; {len(matrix.orphans)} orphan items",
    ]
    return "\n".join(lines)


def matrix_csv(matrix: Matrix) -> str:
    """The matrix as CSV: one row per requirement-item link, and one per requirement with
    no item. Orphan items follow, with an empty requirement."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(
        ["requirement_id", "section", "page", "method", "coverage", "item_id", "note", "text"]
    )
    for r in matrix.rows:
        method = r.verification_method.value if r.verification_method else ""
        base = [r.requirement_id, r.section, r.page, method, r.coverage.value]
        for item_id in r.item_ids or [""]:
            note = "wrong kind for the method" if item_id in r.wrong_kind else (r.note or "")
            w.writerow([*base, item_id, note, r.text])
    for o in matrix.orphans:
        note = f"orphan: verifies {', '.join(o.requirement_ids)}"
        w.writerow(["", "", "", "", "", o.item_id, note, ""])
    return out.getvalue()


_SIGN = {
    ChangeKind.ADDED: "+",
    ChangeKind.REMOVED: "-",
    ChangeKind.CHANGED: "~",
    ChangeKind.RESTATED: "?",
    ChangeKind.UNCHANGED: "=",
}


def diff_report(d: SpecDiff, *, unchanged: bool = False) -> str:
    """Each added, removed, changed and restated requirement (unchanged ones too with
    `unchanged`), then the items to act on, then the counts."""
    lines = [f"{d.old_doc} -> {d.new_doc}", ""]
    listed = [c for c in d.changes if unchanged or c.kind is not ChangeKind.UNCHANGED]
    if not listed:
        lines.append("no requirement changed")
    for c in listed:
        ids = c.new_id or c.old_id
        if c.old_id and c.new_id and c.old_id != c.new_id:
            ids = f"{c.old_id} -> {c.new_id}"
        lines.append(f"{_SIGN[c.kind]} {c.kind.value:9}  {ids}")
        if c.kind is ChangeKind.ADDED:
            lines.append(f"      new: {c.new_quote}")
        elif c.kind is ChangeKind.REMOVED:
            lines.append(f"      old: {c.old_quote}")
        for f in c.fields:
            lines.append(f"      {f.field}: {f.old}")
            lines.append(f"      {' ' * len(f.field)}  {f.new}")
    if d.items_given:
        lines += ["", "verification items to act on:"]
        if not d.impacts:
            lines.append("  none")
        for i in d.impacts:
            target = i.item_id or f"(new item for {i.new_requirement_id})"
            lines.append(f"  {i.action.value:7} {target}  {i.reason}")
    counts = Counter(c.kind for c in d.changes)
    summary = ", ".join(f"{counts[k]} {k.value}" for k in ChangeKind)
    lines += ["", summary]
    if d.items_given:
        actions = Counter(i.action for i in d.impacts)
        lines.append("items: " + ", ".join(f"{actions[a]} {a.value}" for a in Action))
    else:
        lines.append("pass --items with the old version's generation to list affected items")
    return "\n".join(lines)
