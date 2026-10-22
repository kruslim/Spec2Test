"""The judge's rubric: four pass/fail criteria, the same for every kind of item.

People labeling a calibration sample use the same text, in `docs/RUBRIC.md`; a test keeps
the two copies identical. Pass/fail grades rather than a scale because agreement on a
yes/no question is what a person can label consistently and what kappa measures.
"""

from __future__ import annotations

from enum import StrEnum


class Criterion(StrEnum):
    COMPLETE = "complete"
    FAITHFUL = "faithful"
    EXECUTABLE = "executable"
    DECISIVE = "decisive"


RUBRIC: dict[Criterion, str] = {
    Criterion.COMPLETE: (
        "The item verifies the whole requirement: every criterion and condition it states, "
        "and every limit appears in the pass criterion. It adds no obligation the "
        "requirement does not state."
    ),
    Criterion.FAITHFUL: (
        "Every value, unit, tolerance, comparator, condition and procedure in the item "
        "matches the requirement or the source clauses given. Nothing is invented: what the "
        "source does not give is marked in [brackets] or cited by clause number."
    ),
    Criterion.EXECUTABLE: (
        "A competent tester could carry it out without guessing: the steps, what to "
        "inspect, or the evidence to produce are concrete and in order, and the "
        "preconditions, setup and equipment it needs are stated or bracketed."
    ),
    Criterion.DECISIVE: (
        "The pass criterion is objective: two testers with the same observations reach the "
        "same verdict. It compares a measured or observed result with the requirement's "
        "limit, in the requirement's direction (at least, at most, between). A [bracketed] "
        "limit is faithful but not yet decisive."
    ),
}

ACCEPTABLE = "acceptable"
"""The overall verdict: every criterion passes, so a reviewer could accept the item
unedited."""
