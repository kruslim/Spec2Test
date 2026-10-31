"""What an exporter receives: the items to write, with their values already worked out for
the profile's columns, so every output format shows the same thing."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from spec2test.export.profile import Profile, ReviewStatus
from spec2test.schema import Requirement, VerificationItem

Value = str | list[str]
"""A cell: text, or a list (steps, equipment, requirement IDs) the format lays out."""

ORDERED = frozenset({"actions", "steps", "procedure", "setup"})
"""Columns whose lists are sequences, numbered where a format numbers them."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportRow(_Record):
    id: str = Field(description="The ID the profile's scheme gives the item.")
    item: VerificationItem = Field(description="The item, with the reviewer's edit applied.")
    requirements: list[Requirement] = Field(
        description="The requirements it verifies that the generation holds."
    )
    status: ReviewStatus
    values: dict[str, Value] = Field(description="Every column of the profile, in order.")


class Export(_Record):
    profile: Profile
    model: str = Field(description="The model that wrote the items.")
    title: str
    columns: list[str]
    labels: dict[str, str] = Field(description="Column to the label people read.")
    rows: list[ExportRow]
    missing: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Required column to the IDs of the rows written with a placeholder.",
    )
    left_out: dict[ReviewStatus, int] = Field(
        default_factory=dict, description="Items not exported, by review status."
    )

    @property
    def options(self) -> dict[str, Any]:
        return self.profile.output.options
