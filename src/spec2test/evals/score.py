"""Score an extraction against a gold set.

Alignment is by quote overlap. A gold and an extracted record can align when their source
spans share at least `MIN_COVERAGE` of the shorter span. Pairs are then taken greedily,
best first, each record at most once: more coverage wins, then a tighter fit (overlap
over union), then the more similar requirement text. The last key is what tells apart the
records of a split compound requirement, which may share one quote.

Every gold record left unaligned is a miss, every extracted record left unaligned is an
extra, and every wrong field of an aligned pair is a field error; each gets a
`FailureCategory`.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher

from spec2test.evals.gold import GoldSet, in_scope
from spec2test.evals.models import (
    EvalRun,
    Failure,
    FailureCategory,
    FlagCounts,
    Match,
    Metric,
)
from spec2test.extract import Extraction, is_normative, section_at
from spec2test.ingest import Document
from spec2test.lint import Lint
from spec2test.schema import Criterion, Requirement, SourceAnchor

MIN_COVERAGE = 0.5
"""Least share of the shorter quote two records must share to align."""
CONDITION_SIMILARITY = 0.8
"""Least text similarity (difflib ratio, after normalizing case and spaces) for an
extracted condition to count as the gold one. Conditions are free text, so exact
equality would fail nearly every paraphrase. A condition whose words all appear in the
other one also counts: 'during the backing event' is the same condition as 'during the
backing event, until the driver modifies the view'."""

_UNITS = {
    alias: unit
    for unit, aliases in {
        "s": ["sec", "secs", "second", "seconds"],
        "ms": ["msec", "millisecond", "milliseconds"],
        "h": ["hr", "hrs", "hour", "hours"],
        "m": ["metre", "metres", "meter", "meters"],
        "mm": ["millimetre", "millimetres", "millimeter", "millimeters"],
        "mm2": ["mm 2", "mm²", "mm^2", "sq mm", "square millimetres", "square millimeters"],
        "cm2": ["cm 2", "cm²", "cm^2", "sq cm", "square centimetres", "square centimeters"],
        "kg": ["kilogram", "kilograms"],
        "n": ["newton", "newtons"],
        "%": ["percent", "per cent", "pct"],
        "°": ["deg", "degree", "degrees"],
    }.items()
    for alias in [unit, *aliases]
}
"""Spellings of one unit. A unit is right when it is the same unit, however written:
'2.0 seconds' extracted as '2.0 s' is not a wrong unit."""
_STOPWORDS = {"a", "an", "the", "of", "in", "on", "to", "and", "or", "is", "be", "with"}

PRECISION = "extraction precision"
RECALL = "extraction recall"
FIELD_ACCURACY = "field accuracy"
CRITERIA_ACCURACY = "criteria accuracy"
CONDITIONS_ACCURACY = "conditions accuracy"
TYPE_ACCURACY = "type accuracy"
STRENGTH_ACCURACY = "strength accuracy"
METHOD_ACCURACY = "method accuracy"
FLAG_PRECISION = "flag precision"
FLAG_RECALL = "flag recall"

_CRITERIA = {
    FailureCategory.CRITERION_MISSING,
    FailureCategory.CRITERION_EXTRA,
    FailureCategory.CRITERION_COMPARATOR,
    FailureCategory.CRITERION_UNIT,
    FailureCategory.CRITERION_TOLERANCE,
}
_CONDITIONS = {FailureCategory.CONDITION_MISSING, FailureCategory.CONDITION_EXTRA}


def score(
    gold: GoldSet,
    extraction: Extraction,
    doc: Document,
    *,
    corpus: str,
    model: str,
    seconds: float | None = None,
    lint: Lint | None = None,
) -> EvalRun:
    """Align `extraction` with `gold` inside the gold set's scope and compute the metrics.

    With `lint`, the extracted records are scored as linted, testability flags included;
    without it they carry no flags, and flag precision and recall measure nothing.
    """
    if extraction.doc_id != doc.doc_id or gold.doc_id != doc.doc_id:
        raise ValueError(
            f"document mismatch: gold {gold.doc_id!r}, extraction {extraction.doc_id!r}, "
            f"document {doc.doc_id!r}"
        )
    records = lint.requirements if lint is not None else extraction.requirements
    predicted = [r for r in records if _in_scope(doc, r, gold.scope)]
    matches = align(gold.requirements, predicted)
    gold_by_id = {g.id: g for g in gold.requirements}
    pred_by_id = {p.id: p for p in predicted}

    failures: list[Failure] = []
    pair_failures: dict[str, list[Failure]] = {}
    for m in matches:
        found = compare(gold_by_id[m.gold_id], pred_by_id[m.pred_id])
        pair_failures[m.gold_id] = found
        failures.extend(found)
    failures.extend(_missed(gold.requirements, predicted, matches, extraction, doc))
    failures.extend(_extra(gold.requirements, predicted, matches))

    confusion: dict[str, dict[str, int]] = defaultdict(dict)
    flags: dict[str, FlagCounts] = {}
    for m in matches:
        g, p = gold_by_id[m.gold_id], pred_by_id[m.pred_id]
        if g.verification_method is not None:
            row = confusion[g.verification_method.value]
            got = p.verification_method.value if p.verification_method else "none"
            row[got] = row.get(got, 0) + 1
        gold_kinds = {f.kind.value for f in g.testability_flags}
        pred_kinds = {f.kind.value for f in p.testability_flags}
        for kind in gold_kinds | pred_kinds:
            counts = flags.setdefault(kind, FlagCounts(kind=kind))
            if kind in gold_kinds and kind in pred_kinds:
                counts.true_positives += 1
            elif kind in gold_kinds:
                counts.false_negatives += 1
            else:
                counts.false_positives += 1

    def pairs_without(categories: set[FailureCategory]) -> int:
        return sum(
            not any(f.category in categories for f in found) for found in pair_failures.values()
        )

    n = len(matches)
    fields_right = pairs_without(_CRITERIA | _CONDITIONS)
    labeled = [m for m in matches if gold_by_id[m.gold_id].verification_method is not None]
    tp = sum(c.true_positives for c in flags.values())
    fp = sum(c.false_positives for c in flags.values())
    fn = sum(c.false_negatives for c in flags.values())
    method_errors = {FailureCategory.WRONG_METHOD, FailureCategory.METHOD_MISSING}
    metrics = [
        Metric(name=PRECISION, numerator=n, denominator=len(predicted)),
        Metric(name=RECALL, numerator=n, denominator=len(gold.requirements)),
        Metric(name=FIELD_ACCURACY, numerator=fields_right, denominator=n),
        Metric(name=CRITERIA_ACCURACY, numerator=pairs_without(_CRITERIA), denominator=n),
        Metric(name=CONDITIONS_ACCURACY, numerator=pairs_without(_CONDITIONS), denominator=n),
        Metric(
            name=TYPE_ACCURACY,
            numerator=pairs_without({FailureCategory.WRONG_TYPE}),
            denominator=n,
        ),
        Metric(
            name=STRENGTH_ACCURACY,
            numerator=pairs_without({FailureCategory.WRONG_STRENGTH}),
            denominator=n,
        ),
        Metric(
            name=METHOD_ACCURACY,
            numerator=sum(
                not any(f.category in method_errors for f in pair_failures[m.gold_id])
                for m in labeled
            ),
            denominator=len(labeled),
        ),
        Metric(name=FLAG_PRECISION, numerator=tp, denominator=tp + fp),
        Metric(name=FLAG_RECALL, numerator=tp, denominator=tp + fn),
    ]

    return EvalRun(
        corpus=corpus,
        doc_id=doc.doc_id,
        gold_path=gold.path,
        model=model,
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        seconds=seconds,
        pages=len(scope_pages(doc, gold.scope)),
        scope=gold.scope,
        gold_labeler=gold.labeler,
        gold_reviewed=gold.reviewed,
        warnings=warnings(gold, model, lint),
        gold=gold.requirements,
        predicted=predicted,
        extraction=extraction,
        lint=lint,
        matches=matches,
        failures=failures,
        metrics=metrics,
        method_confusion=dict(confusion),
        flags=sorted(flags.values(), key=lambda c: c.kind),
    )


def warnings(gold: GoldSet, model: str, lint: Lint | None = None) -> list[str]:
    """What makes a score untrustworthy: the scored model grading itself, labels no person
    has checked, or flags the linter did not raise because it was off or failed."""
    out = []
    if gold.labeler is not None and _family(gold.labeler) == _family(model):
        out.append(
            f"the extraction model {model!r} is from the same family as the gold set's "
            f"labeler {gold.labeler!r}: shared blind spots count as correct, so these scores "
            "overstate accuracy. Extract with another model."
        )
    if not gold.reviewed:
        who = f"labeled by {gold.labeler}" if gold.labeler else "labeler unknown"
        out.append(f"the gold set ({who}) has not been reviewed by a person")
    if lint is None and any(g.testability_flags for g in gold.requirements):
        out.append("the extraction was not linted: flag precision and recall measure nothing")
    elif lint is not None and lint.failed:
        out.append(
            f"the LLM lint pass failed for {len(lint.failed)} batch(es); flag recall is understated"
        )
    return out


def align(gold: list[Requirement], predicted: list[Requirement]) -> list[Match]:
    """One-to-one pairs of gold and extracted records, in gold order."""
    candidates = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(predicted):
            coverage, iou = _overlap(g.source_anchor, p.source_anchor)
            if coverage >= MIN_COVERAGE:
                candidates.append(((coverage, iou, _similarity(g.text, p.text)), gi, pi))
    # A stable sort, so ties go to the earlier gold record, then the earlier extracted one.
    candidates.sort(key=lambda c: c[0], reverse=True)
    taken_gold: dict[int, Match] = {}
    taken_pred: set[int] = set()
    for (coverage, _, _), gi, pi in candidates:
        if gi in taken_gold or pi in taken_pred:
            continue
        taken_gold[gi] = Match(
            gold_id=gold[gi].id, pred_id=predicted[pi].id, coverage=round(coverage, 3)
        )
        taken_pred.add(pi)
    return [taken_gold[gi] for gi in sorted(taken_gold)]


def compare(g: Requirement, p: Requirement) -> list[Failure]:
    """The field errors of an aligned pair; empty when every labeled field is right."""
    found: list[tuple[FailureCategory, str]] = []
    if g.type != p.type:
        found.append((FailureCategory.WRONG_TYPE, f"gold {g.type.value}, extracted {p.type.value}"))
    if g.strength != p.strength:
        found.append(
            (
                FailureCategory.WRONG_STRENGTH,
                f"gold {g.strength.value}, extracted {p.strength.value}",
            )
        )
    found.extend(_compare_criteria(g.criteria, p.criteria))
    found.extend(_compare_conditions(g.conditions, p.conditions))
    if g.verification_method is not None and g.verification_method != p.verification_method:
        if p.verification_method is None:
            found.append((FailureCategory.METHOD_MISSING, f"gold {g.verification_method.value}"))
        else:
            found.append(
                (
                    FailureCategory.WRONG_METHOD,
                    f"gold {g.verification_method.value}, extracted {p.verification_method.value}",
                )
            )
    gold_flags = {f.kind: f for f in g.testability_flags}
    pred_flags = {f.kind: f for f in p.testability_flags}
    for kind in sorted(gold_flags.keys() - pred_flags.keys()):
        found.append((FailureCategory.FLAG_MISSED, f"{kind.value}: {gold_flags[kind].reason}"))
    for kind in sorted(pred_flags.keys() - gold_flags.keys()):
        found.append((FailureCategory.FLAG_FALSE, f"{kind.value}: {pred_flags[kind].reason}"))
    return [
        Failure(
            category=category,
            gold_id=g.id,
            pred_id=p.id,
            section=g.source_anchor.section,
            detail=detail,
        )
        for category, detail in found
    ]


def scope_pages(doc: Document, scope: list[str] | None) -> set[int]:
    pages: set[int] = set()
    for s in doc.sections:
        if in_scope(doc, s, scope):
            pages.update(range(s.page, s.page_end + 1))
    return pages


def _missed(
    gold: list[Requirement],
    predicted: list[Requirement],
    matches: list[Match],
    extraction: Extraction,
    doc: Document,
) -> list[Failure]:
    matched_gold = {m.gold_id for m in matches}
    matched_pred = {m.pred_id: m.gold_id for m in matches}
    failed = {f.section for f in extraction.failed_sections}
    skipped = set(extraction.skipped_sections)
    rejected = Counter(r.section for r in extraction.rejected)
    out = []
    for g in gold:
        if g.id in matched_gold:
            continue
        section = section_at(doc, g.source_anchor.start)
        index = section.index if section is not None else -1
        overlapping = [p for p in predicted if _overlap(g.source_anchor, p.source_anchor)[0] > 0]
        merged = [p for p in overlapping if p.id in matched_pred]
        loose = [p for p in overlapping if p.id not in matched_pred]
        if index in failed:
            category = FailureCategory.SECTION_FAILED
            detail = next(f.error for f in extraction.failed_sections if f.section == index)
        elif index in skipped or (section is not None and not is_normative(section)):
            category = FailureCategory.SECTION_SKIPPED
            detail = "section has no normative wording, so it was never sent"
        elif merged:
            category = FailureCategory.MERGED
            detail = f"covered by {merged[0].id}, which aligned with {matched_pred[merged[0].id]}"
        elif loose:
            category = FailureCategory.SPAN_MISMATCH
            detail = f"overlaps {loose[0].id}: {loose[0].source_anchor.quote!r}"
        elif rejected[index]:
            category = FailureCategory.CITATION_REJECTED
            detail = f"{rejected[index]} draft(s) from this section failed the citation check"
        else:
            category = FailureCategory.NOT_EXTRACTED
            detail = "nothing extracted for it"
        out.append(
            Failure(category=category, gold_id=g.id, section=g.source_anchor.section, detail=detail)
        )
    return out


def _extra(
    gold: list[Requirement], predicted: list[Requirement], matches: list[Match]
) -> list[Failure]:
    matched_gold = {m.gold_id: m.pred_id for m in matches}
    matched_pred = {m.pred_id for m in matches}
    out = []
    for p in predicted:
        if p.id in matched_pred:
            continue
        overlapping = [g for g in gold if _overlap(g.source_anchor, p.source_anchor)[0] > 0]
        loose = [g for g in overlapping if g.id not in matched_gold]
        if loose:
            category = FailureCategory.SPAN_MISMATCH
            detail = f"overlaps {loose[0].id}: {loose[0].source_anchor.quote!r}"
        elif overlapping:
            category = FailureCategory.OVER_SPLIT
            detail = f"{overlapping[0].id} already aligned with {matched_gold[overlapping[0].id]}"
        else:
            category = FailureCategory.NOT_IN_GOLD
            detail = "not a requirement, or missing from the gold set"
        out.append(
            Failure(category=category, pred_id=p.id, section=p.source_anchor.section, detail=detail)
        )
    return out


def _compare_criteria(
    gold: list[Criterion], pred: list[Criterion]
) -> list[tuple[FailureCategory, str]]:
    """Criteria compare as written, ignoring the free-text parameter name. Unequal ones
    are paired by value first, so a wrong unit reads as a wrong unit, not a miss."""
    gold, pred = list(gold), list(pred)
    for c in list(gold):
        twin = next((d for d in pred if _criterion_key(d) == _criterion_key(c)), None)
        if twin is not None:
            gold.remove(c)
            pred.remove(twin)
    found = []
    for c in list(gold):
        same_value = next((d for d in pred if _value_key(d) == _value_key(c)), None)
        if same_value is None:
            continue
        gold.remove(c)
        pred.remove(same_value)
        for category, attr in (
            (FailureCategory.CRITERION_COMPARATOR, "comparator"),
            (FailureCategory.CRITERION_UNIT, "unit"),
            (FailureCategory.CRITERION_TOLERANCE, "tolerance"),
        ):
            want, got = getattr(c, attr), getattr(same_value, attr)
            same = _unit(want) == _unit(got) if attr == "unit" else _norm(want) == _norm(got)
            if not same:
                found.append((category, f"{_describe(c)}: gold {attr} {want}, extracted {got}"))
    found.extend((FailureCategory.CRITERION_MISSING, _describe(c)) for c in gold)
    found.extend((FailureCategory.CRITERION_EXTRA, _describe(c)) for c in pred)
    return found


def _compare_conditions(gold: list[str], pred: list[str]) -> list[tuple[FailureCategory, str]]:
    pred = list(pred)
    found = []
    for c in gold:
        best = max(pred, key=lambda d: _condition_match(c, d), default=None)
        if best is not None and _condition_match(c, best) >= CONDITION_SIMILARITY:
            pred.remove(best)
        else:
            found.append((FailureCategory.CONDITION_MISSING, c))
    found.extend((FailureCategory.CONDITION_EXTRA, c) for c in pred)
    return found


def _condition_match(a: str, b: str) -> float:
    """1.0 when every word of one condition is in the other; 0.0 when they state different
    numbers ('at 20 °C', 'at 25 °C'); else their text similarity."""
    words_a, words_b = _words(a), _words(b)
    if words_a and words_b and (words_a <= words_b or words_b <= words_a):
        return 1.0
    if _numbers(words_a) != _numbers(words_b):
        return 0.0
    return _similarity(a, b)


def _numbers(words: set[str]) -> set[str]:
    return {w for w in words if any(ch.isdigit() for ch in w)}


def _words(text: str) -> set[str]:
    """Words and numbers, so '4,536 kg' and 'S12.' read as '4536', 'kg' and 's12'."""
    return set(re.findall(r"\w+(?:\.\w+)*", _norm(text).replace(",", ""))) - _STOPWORDS


def _criterion_key(c: Criterion) -> tuple[str, ...]:
    return (*_value_key(c), _norm(c.comparator), _unit(c.unit), _norm(c.tolerance))


def _value_key(c: Criterion) -> tuple[str, str]:
    return _norm(c.value), _norm(c.upper_value)


def _describe(c: Criterion) -> str:
    value = c.value if c.upper_value is None else f"{c.value} to {c.upper_value}"
    parts = [c.comparator.value if c.comparator else None, value, c.unit, c.tolerance]
    return f"{c.parameter} " + " ".join(p for p in parts if p)


def _unit(unit: str | None) -> str:
    text = _norm(unit)
    return _UNITS.get(text, _UNITS.get(text.replace(" ", ""), text))


def _norm(text: object) -> str:
    return " ".join(str(text).casefold().split()) if text is not None else ""


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _overlap(a: SourceAnchor, b: SourceAnchor) -> tuple[float, float]:
    """(shared characters over the shorter span, shared over the union) of two spans."""
    if a.doc != b.doc:
        return 0.0, 0.0
    shared = min(a.end, b.end) - max(a.start, b.start)
    if shared <= 0:
        return 0.0, 0.0
    shorter = min(a.end - a.start, b.end - b.start)
    union = max(a.end, b.end) - min(a.start, b.start)
    return shared / shorter, shared / union


def _family(model: str) -> str:
    """'claude-opus-5-5' -> 'claude', 'kimi-for-coding' -> 'kimi'."""
    return model.casefold().split("/")[-1].split("-")[0]


def _in_scope(doc: Document, req: Requirement, scope: list[str] | None) -> bool:
    section = section_at(doc, req.source_anchor.start)
    return section is not None and in_scope(doc, section, scope)
