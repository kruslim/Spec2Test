"""What the judge asks the model for, what judging returns, and the calibration records."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from spec2test.generate.context import SourceContext
from spec2test.judge.rubric import RUBRIC
from spec2test.schema import Requirement, VerificationItem, VerificationItemKind


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verdict(_Record):
    reason: str = Field(description="One or two sentences: the evidence for the verdict.")
    passed: bool


class JudgeAnswer(_Record):
    """The model's grade for one item: one verdict per rubric criterion, then the overall
    call. The field names are the `RUBRIC` keys."""

    covers: Verdict
    faithful: Verdict
    objective_verdict: Verdict
    executable: Verdict
    method_fit: Verdict
    acceptable: bool

    def verdicts(self) -> dict[str, Verdict]:
        return {name: getattr(self, name) for name in RUBRIC}


class Judgement(BaseModel):
    # `score` is computed, but written out; ignore it when reading one back.
    model_config = ConfigDict(extra="ignore")

    item_id: str
    requirement_id: str
    kind: VerificationItemKind
    verdicts: dict[str, Verdict]
    acceptable: bool

    @computed_field
    @property
    def score(self) -> float:
        """Share of rubric criteria passed."""
        return sum(v.passed for v in self.verdicts.values()) / len(self.verdicts)


class JudgeFailure(_Record):
    item_id: str
    error: str


class Judging(_Record):
    judge_model: str
    generator_model: str
    warnings: list[str] = Field(default_factory=list)
    judgements: list[Judgement] = Field(default_factory=list)
    failed: list[JudgeFailure] = Field(default_factory=list)


class HumanLabel(_Record):
    """A person's grade for one item against the same rubric. `None` means not labeled yet."""

    labeler: str | None = None
    verdicts: dict[str, bool | None] = Field(
        default_factory=lambda: dict.fromkeys(RUBRIC),
        description="Rubric criterion -> passed.",
    )
    acceptable: bool | None = None
    notes: str = ""

    @property
    def complete(self) -> bool:
        return (
            self.labeler is not None
            and self.acceptable is not None
            and set(self.verdicts) == set(RUBRIC)
            and all(v is not None for v in self.verdicts.values())
        )


class CalibrationItem(_Record):
    """One line of a calibration sample: an item, what it verifies, and a person's grade."""

    requirement: Requirement
    item: VerificationItem
    context: SourceContext = Field(
        default_factory=SourceContext,
        description="The headings, definitions and referenced clauses shown with it.",
    )
    generator_model: str
    human: HumanLabel = Field(default_factory=HumanLabel)


class Agreement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="A rubric criterion, or 'acceptable'.")
    both_pass: int = 0
    both_fail: int = 0
    judge_only: int = Field(default=0, description="The judge passed it, the person did not.")
    human_only: int = Field(default=0, description="The person passed it, the judge did not.")

    @computed_field
    @property
    def n(self) -> int:
        return self.both_pass + self.both_fail + self.judge_only + self.human_only

    @computed_field
    @property
    def agreement(self) -> float | None:
        return (self.both_pass + self.both_fail) / self.n if self.n else None

    @computed_field
    @property
    def kappa(self) -> float | None:
        """Cohen's kappa: agreement corrected for chance. None when undefined (n = 0, or
        both raters gave one answer throughout)."""
        if not self.n:
            return None
        judge_pass = (self.both_pass + self.judge_only) / self.n
        human_pass = (self.both_pass + self.human_only) / self.n
        chance = judge_pass * human_pass + (1 - judge_pass) * (1 - human_pass)
        if chance == 1:
            return None
        return (self.agreement - chance) / (1 - chance)


class Disagreement(_Record):
    item_id: str
    name: str
    judge: bool
    human: bool
    judge_reason: str


class Calibration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    judge_model: str
    labelers: list[str]
    labeled: int
    unlabeled: int = Field(description="Sample lines skipped: no complete human label.")
    warnings: list[str] = Field(default_factory=list)
    agreements: list[Agreement]
    disagreements: list[Disagreement]
    judging: Judging
