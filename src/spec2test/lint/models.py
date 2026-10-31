"""What the LLM lint pass asks the model for, and what linting returns."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from spec2test.llm.usage import TokenUsage
from spec2test.schema import Requirement, TestabilityFlag, TestabilityFlagKind


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlagDraft(_Record):
    """One testability problem as the model states it, before it is checked."""

    requirement_id: str = Field(description="The id of the requirement it applies to.")
    kind: TestabilityFlagKind
    phrase: str = Field(
        min_length=1,
        description="The words in the requirement's text that cause the problem, copied exactly.",
    )
    reason: str = Field(description="One sentence: why this cannot be verified as written.")
    suggested_rewrite: str = Field(
        description="The whole requirement rewritten so it can be verified, keeping its "
        "wording; anything the source does not give goes in [brackets], never invented."
    )


class FlagDrafts(_Record):
    """The model's answer for one batch. Empty when every requirement is testable."""

    flags: list[FlagDraft]


class RejectedFlag(_Record):
    """A flag the model raised that does not point at its requirement's own words."""

    draft: FlagDraft
    error: str


class LintFailure(_Record):
    """A batch whose LLM call failed outright (refusal, truncation, bad output)."""

    section: str
    requirement_ids: list[str]
    error: str


class Lint(_Record):
    requirements: list[Requirement] = Field(
        default_factory=list,
        description="The linted records; their testability_flags are the rule flags, then "
        "the LLM pass's.",
    )
    llm_pass: bool = Field(default=False, description="Whether the LLM pass ran.")
    llm_flags: dict[str, list[TestabilityFlag]] = Field(
        default_factory=dict,
        description="Requirement id -> flags the LLM pass added. Kept apart so the rules "
        "can be re-run on a saved run without calling the model again.",
    )
    rejected: list[RejectedFlag] = Field(default_factory=list)
    failed: list[LintFailure] = Field(default_factory=list)
    usage: list[TokenUsage] = Field(
        default_factory=list, description="Tokens per model call, for pricing the run."
    )
