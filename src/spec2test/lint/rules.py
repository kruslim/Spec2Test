"""Deterministic testability rules: the checks that need no model.

Each rule reads one `Requirement` and returns `TestabilityFlag`s, every one with a reason
and a suggested rewrite. Rewrites keep the requirement's wording and mark what the author
has to supply in [brackets]; a rule never invents a value.

The word lists follow common requirements-writing guidance (vague terms, escape clauses,
open-ended lists, unbounded wording). They are short on purpose: a rule flag should
usually be right, and the LLM pass is there for the subtler cases.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass

from spec2test.schema import (
    Criterion,
    Requirement,
    TestabilityFlag,
    TestabilityFlagKind,
    VerificationMethod,
)

_K = TestabilityFlagKind


@dataclass(frozen=True)
class _Term:
    pattern: str
    """Regex matched case-insensitively at word boundaries in `Requirement.text`."""
    kind: TestabilityFlagKind
    problem: str
    """Completes "'<phrase>' ...": why it cannot be verified."""
    fix: str
    """What the author has to state instead; goes in the rewrite's [brackets]."""
    replace: bool = False
    """Replace the phrase with the [fix] in the rewrite, instead of annotating it."""
    resolved_by_criteria: bool = False
    """The term only says "enough for its purpose": a stated criterion is the measure."""


def _vague(pattern: str, fix: str, **kw: bool) -> _Term:
    return _Term(pattern, _K.VAGUE_TERM, "is vague: it sets no measurable limit", fix, **kw)


# Longer phrases first: "as far as possible" is one escape clause, not also "possible".
_TERMS = [
    # Escape clauses: they let the requirement off without saying when.
    *(
        _Term(
            p,
            _K.VAGUE_TERM,
            "is an escape clause: it leaves open when the requirement applies",
            "state exactly when this applies, or delete",
            replace=True,
        )
        for p in [
            r"as appropriate",
            r"as (?:required|necessary|needed|applicable)",
            r"(?:if|where|when) (?:necessary|possible|practicable|practical|applicable|needed)",
            r"as far as (?:possible|practicable|practical)",
            r"to the (?:extent|degree) (?:possible|practicable|practical)",
        ]
    ),
    # Open-ended lists: what else is included cannot be checked.
    *(
        _Term(
            p,
            _K.VAGUE_TERM,
            "leaves the list open-ended",
            "list every item",
            replace=True,
        )
        for p in [r"etc\.?", r"and so on", r"(?:including|includes) but (?:is )?not limited to"]
    ),
    _Term(
        r"and/or",
        _K.VAGUE_TERM,
        "is ambiguous: it does not say whether both or either must hold",
        "'and' or 'or'",
        replace=True,
    ),
    # Unbounded: no target says when it is met, or no test can cover it.
    *(
        _Term(
            p,
            _K.UNBOUNDED,
            "is unbounded: no target value says when it is met",
            "state the target value and unit",
            replace=True,
        )
        for p in [
            r"minimi[sz](?:e|es|ed|ing)",
            r"maximi[sz](?:e|es|ed|ing)",
            r"optimi[sz](?:e|es|ed|ing)",
            r"optim(?:al|um)",
            r"as (?:low|small|short|fast|quick|quickly|high|much|little|few|soon|close|far|"
            r"large|long|great) as (?:possible|practicable|practical|achievable)",
        ]
    ),
    *(
        _Term(
            p,
            _K.UNBOUNDED,
            "is unbounded: no test can cover every case or all time",
            "state the conditions and duration over which it is verified",
        )
        for p in [
            r"always",
            r"never",
            r"at all times",
            r"(?:under|in) (?:all|any) (?:conditions|circumstances|environments)",
            r"unlimited",
            r"infinite(?:ly)?",
            r"indefinitely",
        ]
    ),
    # Vague qualities: each needs a number to be testable.
    _vague(r"(?:adequate|sufficient)(?:ly)?|enough", "state the minimum value and unit",
           resolved_by_criteria=True),
    _vague(r"(?:appropriate|suitable|proper|acceptable)(?:ly)?|suitably",
           "state the criterion that makes it acceptable"),
    _vague(r"reasonabl[ey]", "state the value and unit"),
    _vague(r"user[- ]friendly|easy|easily|intuitive(?:ly)?",
           "state the task, the user and a time or error limit"),
    _vague(r"fast|quick(?:ly)?|rapid(?:ly)?|promptly|timely|soon",
           "state the maximum time and unit"),
    _vague(r"stable|stably", "state the maximum deflection, drift or vibration"),
    _vague(r"robust(?:ly)?|reliabl[ey]|durable",
           "state the load, cycle count or failure rate it must withstand"),
    _vague(r"sharp", "state the minimum edge radius"),
    _vague(r"significant(?:ly)?|substantial(?:ly)?|negligibl[ey]|minimal",
           "state the threshold value and unit"),
    _vague(r"approximately|roughly|nearly|almost", "state the tolerance"),
    # Not "normal": in engineering text it is as often the geometric term.
    _vague(r"typical(?:ly)?|usual(?:ly)?", "state the operating conditions"),
    _vague(r"clearly|readily", "state how it is judged, with a measurable limit"),
    _vague(r"efficient(?:ly)?|effective(?:ly)?|flexible", "state the measurable target"),
    _vague(r"several|various|numerous", "state the number"),
]  # fmt: skip

_TERM_RE = [(t, re.compile(rf"\b(?:{t.pattern})(?![\w/-])", re.IGNORECASE)) for t in _TERMS]

_MODAL = re.compile(r"\b(?:shall|must|should|(?:is|are) required to)\b", re.IGNORECASE)

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_COMPARATIVE = re.compile(
    r"\b(?:at (?:least|most)|(?:not|no) (?:less|more|greater|fewer|lower|higher) than|"
    r"(?:less|more|greater|fewer|lower|higher) than|within|up to|(?:maximum|minimum) of|"
    r"exceed(?:s|ing)?)\s+"
    rf"({_NUMBER})(?![.,]?\d)"
    # A bare number: nothing but punctuation, the end, or the start of a clause follows.
    r"(?=\s*(?:[.;,:)]|$|(?:after|before|when|while|during|unless|if)\b))",
    re.IGNORECASE,
)
_COUNT = re.compile(r"\b(?:number|count|quantity|ratio|factor|times|cycles|occurrences)\b", re.I)

_CONDITION = re.compile(
    r"\b(?:when|whenever|while|during|under|if|unless|after|before|upon|following|"
    r"in accordance with|measured|tested|at (?:an? )?(?:ambient|temperature|speed|load|"
    r"voltage|pressure)|at -?\d)",
    re.IGNORECASE,
)


def lint_rules(req: Requirement) -> list[TestabilityFlag]:
    """Every flag the deterministic rules raise for `req`, in rule order."""
    return [flag for rule in RULES for flag in rule(req)]


def vague_terms(req: Requirement) -> list[TestabilityFlag]:
    """Vague, open-ended, escape-clause and unbounded wording, one flag per phrase."""
    found: list[tuple[int, TestabilityFlag]] = []
    taken: list[tuple[int, int]] = []
    seen: set[str] = set()
    for term, regex in _TERM_RE:
        if term.resolved_by_criteria and req.criteria:
            continue
        for m in regex.finditer(req.text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append(m.span())
            phrase = m.group(0)
            if phrase.casefold() in seen:
                continue
            seen.add(phrase.casefold())
            if term.replace:
                rewrite = _splice(req.text, m.start(), m.end(), f"[{term.fix}]")
            else:
                rewrite = _splice(req.text, m.end(), m.end(), f" [{term.fix}]")
            if req.text.rstrip().endswith(".") and not rewrite.rstrip().endswith("."):
                rewrite = rewrite.rstrip() + "."  # the phrase took the full stop ("etc.")
            flag = TestabilityFlag(
                kind=term.kind,
                reason=f"'{phrase}' {term.problem}",
                suggested_rewrite=_tidy(rewrite),
            )
            found.append((m.start(), flag))
    # Reading order, whatever order the word lists are in.
    return [flag for _, flag in sorted(found, key=lambda f: f[0])]


def missing_units(req: Requirement) -> list[TestabilityFlag]:
    """A numeric criterion with no unit, or a bare number after "at least", "within", ...."""
    flags = []
    flagged: set[str] = set()
    for c in req.criteria:
        if c.unit or not _is_number(c.value) or _COUNT.search(c.parameter):
            continue
        flagged.add(_digits(c.value))
        flags.append(
            TestabilityFlag(
                kind=_K.MISSING_UNIT,
                reason=f"criterion '{c.parameter}' has the value {c.value} but no unit",
                suggested_rewrite=_with_unit(req.text, c.value, c.parameter),
            )
        )
    for m in _COMPARATIVE.finditer(req.text):
        value = m.group(1)
        if _digits(value) in flagged:
            continue
        flagged.add(_digits(value))
        flags.append(
            TestabilityFlag(
                kind=_K.MISSING_UNIT,
                reason=f"'{m.group(0).strip()}' gives a number with no unit",
                suggested_rewrite=_tidy(_splice(req.text, m.end(1), m.end(1), " [unit]")),
            )
        )
    return flags


def missing_conditions(req: Requirement) -> list[TestabilityFlag]:
    """A tested, measurable requirement that states no conditions to test it under.

    Only for tests (or unclassified performance requirements): a measured result depends
    on the state and environment it is measured in, where an inspected dimension does not.
    """
    tested = req.verification_method is VerificationMethod.TEST or (
        req.verification_method is None and req.type.value == "performance"
    )
    if not tested or not req.criteria or req.conditions or _CONDITION.search(req.text):
        return []
    levels = "; ".join(_describe(c) for c in req.criteria)
    return [
        TestabilityFlag(
            kind=_K.MISSING_CONDITION,
            reason=f"sets a measurable level ({levels}) but states no conditions to test it under",
            suggested_rewrite=_tidy(
                f"{req.text.rstrip().rstrip('.')} [when tested under: state the operating "
                "state, environment and procedure]."
            ),
        )
    ]


def compound(req: Requirement) -> list[TestabilityFlag]:
    """More than one obligation in one record: 'shall be red and shall carry a label'."""
    modals = list(_MODAL.finditer(req.text))
    if len(modals) < 2:
        return []
    text = req.text
    subject = text[: modals[0].start()].strip()
    # Each obligation runs from its own subject (or its modal) to the clause break before
    # the next one: "X shall A, and shall B; it must C" -> "X shall A", "X shall B", "It must C".
    starts, ends, subjects = [modals[0].start()], [], [subject]
    for prev, m in itertools.pairwise(modals):
        between = text[prev.end() : m.start()]
        breaks = list(re.finditer(r"[;,]|\b(?:and|or|but)\b", between))
        if not breaks:
            ends.append(m.start())
            starts.append(m.start())
            subjects.append(subject)
            continue
        cut = prev.end() + breaks[-1].start()
        own = text[prev.end() + breaks[-1].end() : m.start()].strip(" ,;")
        ends.append(cut)
        starts.append(m.start())
        subjects.append(own[:1].upper() + own[1:] if own else subject)
    ends.append(len(text))
    parts = []
    for start, end, who in zip(starts, ends, subjects, strict=True):
        body = re.sub(r"[\s,;]*(?:\b(?:and|or|but)\b)?[\s,;.]*$", "", text[start:end])
        parts.append(_tidy(f"{who} {body}.") if who else f"{body}.")
    words = ", ".join(f"'{m.group(0)}'" for m in modals)
    return [
        TestabilityFlag(
            kind=_K.COMPOUND,
            reason=f"states {len(modals)} obligations ({words}); each needs its own verdict",
            suggested_rewrite=" ".join(parts),
        )
    ]


RULES: list[Callable[[Requirement], list[TestabilityFlag]]] = [
    vague_terms,
    missing_units,
    missing_conditions,
    compound,
]


def _splice(text: str, start: int, end: int, insert: str) -> str:
    return text[:start] + insert + text[end:]


def _tidy(text: str) -> str:
    """Collapse the doubled spaces and space-before-punctuation a splice leaves."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:])", r"\1", text)


def _with_unit(text: str, value: str, parameter: str) -> str:
    m = re.search(rf"(?<![\w.]){re.escape(value)}(?![\w.]*\d)", text)
    if m is None:
        return _tidy(f"{text.rstrip().rstrip('.')} [state the unit of {parameter}].")
    return _tidy(_splice(text, m.end(), m.end(), " [unit]"))


def _is_number(value: str) -> bool:
    return re.fullmatch(rf"[-+±]?{_NUMBER}", value.strip()) is not None


def _digits(value: str) -> str:
    return value.replace(",", "").strip()


def _describe(c: Criterion) -> str:
    value = c.value if c.upper_value is None else f"{c.value} to {c.upper_value}"
    parts = [c.parameter, c.comparator.value if c.comparator else None, value, c.unit]
    return " ".join(p for p in parts if p)
