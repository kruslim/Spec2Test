"""The written rubric the judge grades every verification item against.

Each criterion is pass/fail, not a scale: binary verdicts are what a person can label
consistently, so the judge's agreement with them can be measured. An item's score is the
share of criteria it passes; `acceptable` is the overall call a reviewer would make.
The calibration sample (`spec2test calibrate`) is labeled against this same text.

What the source owns and what the item owns: the source owns every value, limit,
tolerance, condition and procedure it states; the item owns how to check them when the
source does not say. A first judging run graded "invents a procedure" both ways, so
`covers` and `faithful` spell this out.
"""

from __future__ import annotations

RUBRIC: dict[str, str] = {
    "covers": (
        "Covers the requirement: the item verifies every obligation, criterion and condition "
        "the requirement states, and adds no pass or fail condition the requirement does not "
        "state. The setup, steps, measurements and equipment needed to check it are not "
        "added obligations."
    ),
    "faithful": (
        "Faithful to the source: every value, unit, tolerance, comparator and condition "
        "matches the requirement and its source context, and where the source states a "
        "procedure the item follows it. No limit is invented: a value, limit or tolerance "
        "the source does not give is written as a [bracketed placeholder]. Where the source "
        "gives no procedure, writing one (setup, steps, equipment) is the item's job, not "
        "invention, as long as it adds no pass or fail limit."
    ),
    "objective_verdict": (
        "Objective verdict: the pass condition, pass criterion or acceptance criterion is "
        "measurable or plainly observable, so two competent testers would reach the same "
        "verdict on the same item under test."
    ),
    "executable": (
        "Executable: a competent technician could carry it out without guessing. The "
        "preconditions, setup, steps and equipment of a test case, what to inspect for a "
        "checklist item, or the procedure and evidence of an evidence item are concrete, "
        "complete and in a workable order."
    ),
    "method_fit": (
        "Fits its verification method: a test case measures under controlled conditions, "
        "a checklist item looks at the item or checks a document, a demonstration operates "
        "the item and observes, an analysis calculates or models."
    ),
}

ACCEPTABLE = (
    "Acceptable: an engineer would put this item into the verification plan unedited. "
    "Placeholders for values the source does not give do not make an item unacceptable on "
    "their own; a wrong, missing or invented value does."
)
