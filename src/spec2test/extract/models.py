"""What extraction asks the model for, and what it returns to the rest of the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from spec2test.llm.usage import TokenUsage
from spec2test.schema import Criterion, Requirement, RequirementType, Strength, VerificationMethod


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedRequirement(_Record):
    """One atomic requirement as the model states it, before it is cited.

    The model states the quote; the pipeline finds it in the source and states the
    offsets, page and section. Character offsets are the one thing a model cannot count
    reliably, and a quote it cannot find verbatim is exactly what the citation check is
    there to catch.
    """

    quote: str = Field(
        min_length=1,
        description="The source text that states this requirement, copied character for "
        "character from the section: same spelling, punctuation, symbols and capitals.",
    )
    text: str = Field(
        description="The requirement as one self-contained sentence stating one obligation. "
        "Equal to the quote unless the quote is compound or needs its subject restored."
    )
    type: RequirementType
    strength: Strength
    criteria: list[Criterion] = Field(default_factory=list)
    conditions: list[str] = Field(
        default_factory=list, description="When or under which state the requirement applies."
    )
    verification_method: VerificationMethod | None = None


class ExtractedRequirements(_Record):
    """The model's answer for one section. Empty when the section states no requirement."""

    requirements: list[ExtractedRequirement]


class Rejection(_Record):
    """A requirement the model stated but could not cite verbatim, even after a retry."""

    section: int = Field(description="`Section.index` the requirement was extracted from.")
    draft: ExtractedRequirement = Field(description="The model's last attempt.")
    errors: list[str] = Field(min_length=1)


class SectionFailure(_Record):
    """A section whose extraction call failed outright (refusal, truncation, bad output)."""

    section: int
    error: str


class Extraction(_Record):
    doc_id: str
    requirements: list[Requirement] = Field(
        default_factory=list, description="Every one has passed the citation check."
    )
    rejected: list[Rejection] = Field(default_factory=list)
    failed_sections: list[SectionFailure] = Field(default_factory=list)
    skipped_sections: list[int] = Field(
        default_factory=list, description="Sections with no normative wording; never sent."
    )
    retried: int = Field(default=0, description="Drafts sent back once with their citation error.")
    usage: list[TokenUsage] = Field(
        default_factory=list, description="Tokens per model call, for pricing the run."
    )
