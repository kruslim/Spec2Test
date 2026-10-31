"""What generation returns: the verification items, and what could not be generated."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from spec2test.llm import TokenUsage
from spec2test.schema import (
    ChecklistFields,
    EvidenceFields,
    Requirement,
    TestCaseFields,
    VerificationItem,
    VerificationItemKind,
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RejectedItem(_Record):
    """An item the model wrote that failed the check, even after a retry."""

    requirement_id: str
    kind: VerificationItemKind
    draft: ChecklistFields | TestCaseFields | EvidenceFields = Field(
        description="The model's last attempt."
    )
    errors: list[str] = Field(min_length=1)


class GenerationFailure(_Record):
    """A requirement whose generation call failed outright (refusal, truncation, bad output)."""

    requirement_id: str
    error: str


class SkippedRequirement(_Record):
    """A requirement that obliges nothing a verification item could check."""

    requirement_id: str
    reason: str


class Generation(_Record):
    model: str = Field(description="The model that wrote the items; the judge must differ.")
    requirements: list[Requirement] = Field(
        description="The requirements generated from, so a saved generation can be judged "
        "on its own."
    )
    items: list[VerificationItem] = Field(
        default_factory=list, description="Every one has passed the check."
    )
    rejected: list[RejectedItem] = Field(default_factory=list)
    failed: list[GenerationFailure] = Field(default_factory=list)
    unclassified: list[str] = Field(
        default_factory=list,
        description="Ids of requirements with no verification method; never sent.",
    )
    skipped: list[SkippedRequirement] = Field(
        default_factory=list,
        description="Permissions and definitions: they oblige nothing; never sent.",
    )
    retried: int = Field(default=0, description="Items sent back once with their errors.")
    usage: list[TokenUsage] = Field(
        default_factory=list, description="Tokens per model call, for pricing the run."
    )

    @property
    def pages(self) -> int:
        return pages_cited(self.requirements)


def pages_cited(requirements: Iterable[Requirement]) -> int:
    """How many source pages the requirements cite: the pages a run's cost is spread over."""
    return len({r.source_anchor.page for r in requirements})
