"""What generation returns: the verification items, and what could not be generated."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from spec2test.schema import Requirement, VerificationItem


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RejectedItem(_Record):
    """An item that failed the item check, even after a retry."""

    requirement_id: str
    draft: VerificationItem = Field(description="The model's last attempt.")
    errors: list[str] = Field(min_length=1)


class GenerationFailure(_Record):
    """A requirement whose generation call failed outright (refusal, truncation, bad output)."""

    requirement_id: str
    error: str


class Generation(_Record):
    model: str = Field(description="The model that wrote the items; the judge must differ.")
    requirements: list[Requirement] = Field(
        default_factory=list,
        description="The records generated from, so the judge can read each item's requirement.",
    )
    items: list[VerificationItem] = Field(
        default_factory=list, description="Every one has passed the item check."
    )
    rejected: list[RejectedItem] = Field(default_factory=list)
    failed: list[GenerationFailure] = Field(default_factory=list)
    skipped: list[str] = Field(
        default_factory=list,
        description="Ids of requirements with no verification method; nothing was generated.",
    )
    retried: int = Field(default=0, description="Items sent back once with their check errors.")
