"""The canonical records every spec is reduced to.

Every stage of the pipeline reads or writes these models. They are industry-agnostic:
nothing here should know which domain a requirement came from.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Record(BaseModel):
    # Unknown fields are an error, not silently dropped: a misspelled field from an LLM
    # or a fixture should fail loudly.
    model_config = ConfigDict(extra="forbid")


class RequirementType(StrEnum):
    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"
    SAFETY = "safety"
    INTERFACE = "interface"
    REGULATORY = "regulatory"


class Strength(StrEnum):
    SHALL = "shall"
    SHOULD = "should"
    MAY = "may"


class VerificationMethod(StrEnum):
    """Standard systems-engineering verification methods (IADT)."""

    INSPECTION = "inspection"
    TEST = "test"
    ANALYSIS = "analysis"
    DEMONSTRATION = "demonstration"


class TestabilityFlagKind(StrEnum):
    __test__ = False

    VAGUE_TERM = "vague_term"
    MISSING_UNIT = "missing_unit"
    MISSING_CONDITION = "missing_condition"
    COMPOUND = "compound"
    UNBOUNDED = "unbounded"


class Comparator(StrEnum):
    """How a measured value is compared to the criterion's value."""

    LT = "lt"
    LE = "le"
    EQ = "eq"
    GE = "ge"
    GT = "gt"
    BETWEEN = "between"


class VerificationItemKind(StrEnum):
    CHECKLIST = "checklist"
    TEST_CASE = "test_case"
    EVIDENCE = "evidence"


class SourceAnchor(_Record):
    """Where a requirement came from, down to the exact character span."""

    doc: str = Field(description="Document ID.")
    page: int = Field(ge=1, description="1-based page number.")
    section: str = Field(description="Section or clause number, e.g. '5.5.1'.")
    start: int = Field(ge=0, description="Start character offset in the document text.")
    end: int = Field(description="End character offset (exclusive) in the document text.")
    quote: str = Field(min_length=1, description="Exact source text at [start, end).")

    @model_validator(mode="after")
    def _span_is_ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be greater than start ({self.start})")
        return self


class Criterion(_Record):
    """One measurable criterion.

    "within 2.0 s of selecting reverse" is parameter='rear image display time',
    comparator='le', value='2.0', unit='s'. A range uses comparator='between', with the
    lower bound in `value` and the upper bound in `upper_value`.
    """

    parameter: str
    comparator: Comparator | None = Field(
        default=None, description="None when the source states no direction."
    )
    value: str = Field(description="Value as written in the source, e.g. '2.0'.")
    upper_value: str | None = Field(default=None, description="Upper bound; 'between' only.")
    unit: str | None = None
    tolerance: str | None = Field(default=None, description="As written, e.g. '±0.1'.")

    @model_validator(mode="after")
    def _upper_value_only_for_ranges(self) -> Self:
        is_range = self.comparator is Comparator.BETWEEN
        if is_range != (self.upper_value is not None):
            raise ValueError("upper_value is required for 'between' and only allowed there")
        return self


class TestabilityFlag(_Record):
    """A reason the requirement is hard to verify as written."""

    __test__ = False

    kind: TestabilityFlagKind
    reason: str
    suggested_rewrite: str | None = None


class Requirement(_Record):
    id: str
    source_anchor: SourceAnchor
    text: str
    type: RequirementType
    strength: Strength
    criteria: list[Criterion] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    verification_method: VerificationMethod | None = Field(
        default=None, description="None until the requirement has been classified."
    )
    testability_flags: list[TestabilityFlag] = Field(default_factory=list)


class ChecklistFields(_Record):
    """Inspection: what to look at and what passes."""

    inspect: str = Field(description="What to look at.")
    pass_condition: str


class TestCaseFields(_Record):
    """Test: a procedure with a measurable pass/fail criterion."""

    __test__ = False

    preconditions: list[str] = Field(default_factory=list)
    setup: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected_result: str
    pass_criterion: str
    equipment: list[str] = Field(default_factory=list)


class EvidenceFields(_Record):
    """Demonstration or analysis: the evidence that shows the requirement is met.

    A demonstration lists the procedure to perform; an analysis usually has none and
    names the calculation or report to attach.
    """

    description: str
    procedure: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(min_length=1, description="What to capture or attach.")
    acceptance_criterion: str


Fields = ChecklistFields | TestCaseFields | EvidenceFields

FIELDS_FOR_KIND: dict[VerificationItemKind, type[Fields]] = {
    VerificationItemKind.CHECKLIST: ChecklistFields,
    VerificationItemKind.TEST_CASE: TestCaseFields,
    VerificationItemKind.EVIDENCE: EvidenceFields,
}

KIND_FOR_METHOD: dict[VerificationMethod, VerificationItemKind] = {
    VerificationMethod.INSPECTION: VerificationItemKind.CHECKLIST,
    VerificationMethod.TEST: VerificationItemKind.TEST_CASE,
    VerificationMethod.DEMONSTRATION: VerificationItemKind.EVIDENCE,
    VerificationMethod.ANALYSIS: VerificationItemKind.EVIDENCE,
}
"""The output each verification method renders as."""


class VerificationItem(_Record):
    """A checklist item, test case or evidence item that verifies one or more requirements."""

    id: str
    requirement_ids: list[str] = Field(min_length=1)
    kind: VerificationItemKind
    fields: ChecklistFields | TestCaseFields | EvidenceFields

    @model_validator(mode="after")
    def _fields_match_kind(self) -> Self:
        expected = FIELDS_FOR_KIND[self.kind]
        if not isinstance(self.fields, expected):
            raise ValueError(f"kind {self.kind.value!r} needs {expected.__name__}")
        return self
