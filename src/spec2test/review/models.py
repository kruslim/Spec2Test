"""What review records: a person's decision on each verification item, and the acceptance
rate those decisions add up to."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from spec2test.schema import (
    ChecklistFields,
    EvidenceFields,
    TestCaseFields,
    VerificationItemKind,
    VerificationMethod,
)

FIELDS: dict[VerificationItemKind, type[BaseModel]] = {
    VerificationItemKind.CHECKLIST: ChecklistFields,
    VerificationItemKind.TEST_CASE: TestCaseFields,
    VerificationItemKind.EVIDENCE: EvidenceFields,
}
"""The fields model of each item kind: what an edit must validate against."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Decision(StrEnum):
    ACCEPT = "accept"
    """Accepted as generated, unedited."""
    EDIT = "edit"
    """Accepted after the reviewer changed its fields."""
    REJECT = "reject"


class ReviewDecision(_Record):
    """One decision on one item, as logged. Every decision is kept; the latest one for an
    item is the one that counts."""

    generation_id: str = Field(description="sha256 of the Generation JSON reviewed.")
    item_id: str
    kind: VerificationItemKind
    method: VerificationMethod | None = Field(
        description="The verification method of the first requirement the item verifies."
    )
    decision: Decision
    fields: dict[str, Any] = Field(description="The item's fields as generated.")
    edited: dict[str, Any] | None = Field(
        default=None, description="The reviewer's fields; set only for an edit."
    )
    note: str = ""
    reviewer: str = ""
    decided_at: datetime


class Tally(BaseModel):
    """Latest decisions, counted for a set of items."""

    # The computed fields are written out; ignore them when reading one back.
    model_config = ConfigDict(extra="ignore")

    items: int = Field(description="Items in the set, reviewed or not.")
    accepted: int = 0
    edited: int = 0
    rejected: int = 0

    @computed_field
    @property
    def reviewed(self) -> int:
        return self.accepted + self.edited + self.rejected

    @computed_field
    @property
    def acceptance_rate(self) -> float | None:
        """Share of reviewed items accepted unedited; None before any review."""
        return self.accepted / self.reviewed if self.reviewed else None


class ReviewStats(_Record):
    """The acceptance rate of one Generation, or of every Generation in a store."""

    generations: list[str] = Field(description="The generation ids counted.")
    overall: Tally
    by_kind: dict[str, Tally] = Field(default_factory=dict)
    by_method: dict[str, Tally] = Field(
        default_factory=dict, description="Keyed by method; 'unclassified' when it has none."
    )
