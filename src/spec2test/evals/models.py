"""What an eval run produces: aligned pairs, categorized failures and the metrics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from spec2test.extract import Extraction
from spec2test.schema import Requirement


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureCategory(StrEnum):
    """Why a gold requirement was missed, an extracted one is extra, or a field is wrong.
    `DESCRIPTIONS` says what each one means."""

    # A gold requirement no extracted one aligns with.
    SECTION_FAILED = "section_failed"
    SECTION_SKIPPED = "section_skipped"
    MERGED = "merged"
    SPAN_MISMATCH = "span_mismatch"  # also an extracted record
    CITATION_REJECTED = "citation_rejected"
    NOT_EXTRACTED = "not_extracted"
    # An extracted requirement no gold one aligns with.
    OVER_SPLIT = "over_split"
    NOT_IN_GOLD = "not_in_gold"
    # A wrong field on an aligned pair.
    WRONG_TYPE = "wrong_type"
    WRONG_STRENGTH = "wrong_strength"
    CRITERION_MISSING = "criterion_missing"
    CRITERION_EXTRA = "criterion_extra"
    CRITERION_COMPARATOR = "criterion_comparator"
    CRITERION_UNIT = "criterion_unit"
    CRITERION_TOLERANCE = "criterion_tolerance"
    CONDITION_MISSING = "condition_missing"
    CONDITION_EXTRA = "condition_extra"
    WRONG_METHOD = "wrong_method"
    METHOD_MISSING = "method_missing"
    FLAG_MISSED = "flag_missed"
    FLAG_FALSE = "flag_false"


_F = FailureCategory
DESCRIPTIONS = {
    _F.SECTION_FAILED: "Missed: the model call for its section failed outright.",
    _F.SECTION_SKIPPED: "Missed: its section has no normative wording, so it was never sent.",
    _F.MERGED: "Missed: an extracted record covers it but aligned with another gold record; "
    "the compound requirement was not split.",
    _F.SPAN_MISMATCH: "The quotes overlap too little to align: their boundaries differ.",
    _F.CITATION_REJECTED: "Missed: a draft from its section failed the citation check twice.",
    _F.NOT_EXTRACTED: "Missed: the model returned nothing for it.",
    _F.OVER_SPLIT: "Extra: overlaps a gold record that aligned with another extracted record; "
    "split too finely, or extracted twice.",
    _F.NOT_IN_GOLD: "Extra: overlaps no gold record; not a requirement, or missing from the "
    "gold set.",
    _F.WRONG_TYPE: "Wrong requirement type.",
    _F.WRONG_STRENGTH: "Wrong strength (shall / should / may).",
    _F.CRITERION_MISSING: "A gold criterion has no extracted counterpart.",
    _F.CRITERION_EXTRA: "An extracted criterion is not in the gold record.",
    _F.CRITERION_COMPARATOR: "Same value, wrong comparator.",
    _F.CRITERION_UNIT: "Same value, wrong unit.",
    _F.CRITERION_TOLERANCE: "Same value, wrong tolerance.",
    _F.CONDITION_MISSING: "A gold condition has no extracted counterpart.",
    _F.CONDITION_EXTRA: "An extracted condition is not in the gold record.",
    _F.WRONG_METHOD: "Wrong verification method.",
    _F.METHOD_MISSING: "The gold record has a verification method; the extracted one has none.",
    _F.FLAG_MISSED: "A gold testability flag was not raised.",
    _F.FLAG_FALSE: "A testability flag was raised that the gold record does not have.",
}


class Failure(_Record):
    """One miss, with its category. Missed gold records carry `gold_id` only, extra
    extracted records `pred_id` only, field errors both."""

    category: FailureCategory
    gold_id: str | None = None
    pred_id: str | None = None
    section: str
    detail: str


class Match(_Record):
    """A gold record and the extracted record aligned with it."""

    gold_id: str
    pred_id: str
    coverage: float = Field(description="Shared characters over the shorter quote's length.")


class Metric(BaseModel):
    # `value` is computed, but written to results.json; ignore it when reading one back.
    model_config = ConfigDict(extra="ignore")

    name: str
    numerator: int
    denominator: int

    @computed_field
    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


class FlagCounts(_Record):
    """Testability flags of one kind over the aligned pairs."""

    kind: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0


class EvalRun(_Record):
    """One scored run: inputs, alignment, failures and metrics. Saved as `results.json`."""

    corpus: str
    doc_id: str
    gold_path: str
    model: str
    created: str = Field(description="ISO 8601 time the run was scored.")
    seconds: float | None = Field(default=None, description="Wall time of the extraction.")
    pages: int = Field(description="Pages the scope covers.")
    scope: list[str] | None
    gold_labeler: str | None = None
    gold_reviewed: bool = False
    warnings: list[str] = Field(
        default_factory=list, description="Why these numbers may not mean what they seem."
    )
    gold: list[Requirement]
    predicted: list[Requirement] = Field(description="Extracted records inside the scope.")
    extraction: Extraction = Field(description="The whole extraction, for rescoring.")
    matches: list[Match]
    failures: list[Failure]
    metrics: list[Metric]
    method_confusion: dict[str, dict[str, int]] = Field(
        description="gold method -> extracted method ('none' if unclassified) -> pairs."
    )
    flags: list[FlagCounts]

    def metric(self, name: str) -> Metric:
        return next(m for m in self.metrics if m.name == name)
