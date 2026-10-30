"""What tracing returns: the requirement-to-item matrix, and the diff of two spec versions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from spec2test.schema import VerificationItemKind, VerificationMethod


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coverage(StrEnum):
    """Why a requirement has the verification items it has, or none."""

    COVERED = "covered"
    SKIPPED = "skipped"
    """A permission or a definition: it obliges nothing, so no item is owed."""
    UNCLASSIFIED = "unclassified"
    REJECTED = "rejected"
    FAILED = "failed"
    MISSING = "missing"
    """No item, and no record of why: the items come from another run or were removed."""


GAPS = frozenset({Coverage.UNCLASSIFIED, Coverage.REJECTED, Coverage.FAILED, Coverage.MISSING})
"""Coverage states where a requirement that obliges something has no item."""


class TraceRow(_Record):
    """One requirement and the items that verify it."""

    requirement_id: str
    section: str
    page: int
    text: str
    verification_method: VerificationMethod | None
    item_ids: list[str] = Field(default_factory=list)
    coverage: Coverage
    note: str | None = Field(
        default=None, description="Why it is skipped, rejected or failed, as generation said."
    )
    wrong_kind: list[str] = Field(
        default_factory=list,
        description="Items whose kind is not the one the requirement's method calls for.",
    )


class OrphanItem(_Record):
    """An item that verifies no requirement in the set traced."""

    item_id: str
    kind: VerificationItemKind
    requirement_ids: list[str] = Field(description="The ids it names; none is in the set.")


class Matrix(_Record):
    model: str = Field(description="The model that wrote the items.")
    rows: list[TraceRow]
    orphans: list[OrphanItem] = Field(default_factory=list)

    @property
    def gaps(self) -> list[TraceRow]:
        return [r for r in self.rows if r.coverage in GAPS]


class ChangeKind(StrEnum):
    """How a requirement differs between two versions of a spec.

    The source quote decides: it is the spec's own words, the same on every extraction run.
    The other fields are the extraction model's reading of them, so two runs over the same
    words can still differ there; that is a restatement, not a spec change.
    """

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    """The source wording changed."""
    RESTATED = "restated"
    """The same source wording, extracted differently (text, criteria, method, ...)."""
    UNCHANGED = "unchanged"
    """The same wording and reading; it may have moved to another section or id."""


class Action(StrEnum):
    """What a spec change asks of a verification item."""

    WRITE = "write"
    """A new requirement that obliges something: write its item."""
    RETIRE = "retire"
    """Its requirement is gone."""
    UPDATE = "update"
    """Its requirement's wording changed."""
    REVIEW = "review"
    """Its requirement was read differently; check the item still fits."""
    RELINK = "relink"
    """Its requirement is unchanged but has a new id: point the item at it."""


class FieldChange(_Record):
    field: str
    old: str
    new: str


class RequirementChange(_Record):
    kind: ChangeKind
    old_id: str | None = None
    new_id: str | None = None
    old_section: str | None = None
    new_section: str | None = None
    old_quote: str | None = None
    new_quote: str | None = None
    fields: list[FieldChange] = Field(
        default_factory=list, description="What differs, for a changed or restated pair."
    )
    similarity: float | None = Field(
        default=None, description="Quote similarity of a matched pair, 0 to 1."
    )


class ItemImpact(_Record):
    """One verification item (or, for WRITE, a missing one) a spec change touches."""

    action: Action
    item_id: str | None = Field(description="None for WRITE: the item does not exist yet.")
    old_requirement_id: str | None
    new_requirement_id: str | None
    reason: str


class SpecDiff(_Record):
    old_doc: str
    new_doc: str
    changes: list[RequirementChange] = Field(
        description="Every requirement of both versions, unchanged ones included."
    )
    impacts: list[ItemImpact] = Field(
        default_factory=list, description="Empty unless the old version's items were given."
    )
    items_given: bool = Field(default=False, description="Whether old items were traced.")

    def of_kind(self, kind: ChangeKind) -> list[RequirementChange]:
        return [c for c in self.changes if c.kind is kind]
