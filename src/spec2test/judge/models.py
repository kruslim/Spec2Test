"""What the judge asks the model for, and what judging and calibration return."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from spec2test.judge.rubric import Criterion
from spec2test.schema import Requirement, VerificationItem, VerificationItemKind


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Grade(_Record):
    """One rubric criterion. The reason comes first so the model reasons before it rules."""

    reason: str = Field(description="One sentence: the defect, or why it passes.")
    passed: bool


class Grades(_Record):
    """The judge's answer for one item: every rubric criterion, pass or fail."""

    complete: Grade
    faithful: Grade
    executable: Grade
    decisive: Grade

    def grade(self, criterion: Criterion) -> Grade:
        return getattr(self, criterion.value)

    @property
    def acceptable(self) -> bool:
        return all(self.grade(c).passed for c in Criterion)


class Judgement(_Record):
    item_id: str
    kind: VerificationItemKind
    grades: Grades


class JudgeFailure(_Record):
    """An item the judge could not grade: the call failed, or its requirement is missing."""

    item_id: str
    error: str


class Judging(_Record):
    judge_model: str
    generator_model: str | None = None
    warnings: list[str] = Field(
        default_factory=list, description="Why these grades may not mean what they seem."
    )
    judgements: list[Judgement] = Field(default_factory=list)
    failed: list[JudgeFailure] = Field(default_factory=list)


class Labels(_Record):
    """A person's grades for one item; None where they have not graded yet."""

    complete: bool | None = None
    faithful: bool | None = None
    executable: bool | None = None
    decisive: bool | None = None

    def label(self, criterion: Criterion) -> bool | None:
        return getattr(self, criterion.value)

    @property
    def acceptable(self) -> bool | None:
        """All four pass; None until all four are graded."""
        labels = [self.label(c) for c in Criterion]
        return None if None in labels else all(labels)


class SampleLine(_Record):
    """One line of a calibration sample: an item, what it verifies, and a person's grades."""

    requirements: list[Requirement] = Field(min_length=1)
    item: VerificationItem
    generator: str | None = Field(default=None, description="The model that wrote the item.")
    labels: Labels = Field(default_factory=Labels)
    note: str = ""


class Sample(_Record):
    path: str
    labeler: str | None = Field(default=None, description="The person who graded it.")
    lines: list[SampleLine]


class Agreement(BaseModel):
    """Judge against person on one criterion, over the items both graded.

    `agreement` and `kappa` are computed, but written to the results; ignore them when
    reading one back.
    """

    model_config = ConfigDict(extra="ignore")

    criterion: str
    both_pass: int = 0
    both_fail: int = 0
    judge_only_pass: int = Field(default=0, description="Judge pass, person fail.")
    person_only_pass: int = Field(default=0, description="Judge fail, person pass.")

    @property
    def n(self) -> int:
        return self.both_pass + self.both_fail + self.judge_only_pass + self.person_only_pass

    @computed_field
    @property
    def agreement(self) -> float | None:
        return (self.both_pass + self.both_fail) / self.n if self.n else None

    @computed_field
    @property
    def kappa(self) -> float | None:
        """Cohen's kappa: agreement beyond what chance gives at these pass rates. None when
        it is undefined (no items, or both graders gave one verdict throughout)."""
        if not self.n:
            return None
        judge_pass = (self.both_pass + self.judge_only_pass) / self.n
        person_pass = (self.both_pass + self.person_only_pass) / self.n
        chance = judge_pass * person_pass + (1 - judge_pass) * (1 - person_pass)
        if chance == 1:
            return None
        return (self.agreement - chance) / (1 - chance)


class Disagreement(_Record):
    item_id: str
    criterion: str
    judge: bool
    person: bool
    judge_reason: str
    note: str = ""


class Calibration(_Record):
    sample_path: str
    judge_model: str
    labeler: str | None = None
    warnings: list[str] = Field(default_factory=list)
    unlabeled: int = Field(default=0, description="Sample lines with no grades; not judged.")
    judgements: list[Judgement] = Field(
        default_factory=list, description="The judge's grades, for rescoring."
    )
    failed: list[JudgeFailure] = Field(default_factory=list)
    agreements: list[Agreement] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
