"""What the dashboard collects: one row per saved run, the spread between repeated runs,
and the acceptance rate from review logs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from spec2test.review.models import ReviewStats


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rate(_Record):
    """A share with its counts, so a reader sees how small the denominator is."""

    numerator: int
    denominator: int

    @computed_field
    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


class CostSummary(_Record):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    dollars: float | None = Field(default=None, description="None when a model is unpriced.")
    per_page: float | None = None
    basis: str = Field(description="What the pages count: 'scope' or 'cited'.")


class ExtractionRun(_Record):
    """One `spec2test eval` run (its `results.json`)."""

    path: str
    corpus: str
    model: str
    created: str
    code_version: str | None
    gold_labeler: str | None
    gold_reviewed: bool
    gold: int
    extracted: int
    metrics: dict[str, Rate] = Field(description="Metric name -> its counts, in run order.")
    failed_sections: int
    cost: CostSummary
    warnings: list[str]


class JudgeRun(_Record):
    """One `spec2test judge` run (its `--json` output)."""

    path: str
    judge_model: str
    generator_model: str
    provider: str | None = Field(description="'session', an API provider, or None if unrecorded.")
    created: str | None = Field(description="None when the judging did not record it.")
    file_time: str = Field(description="The file's modification time, the fallback order.")
    code_version: str | None
    judged: int
    failed: int
    acceptable: Rate
    criteria: Rate = Field(description="Rubric criteria passed over criteria graded.")
    by_kind: dict[str, Rate] = Field(description="Item kind -> acceptable over judged.")
    cost: CostSummary
    warnings: list[str]


class AgreementRun(_Record):
    """Agreement on `acceptable` between a judge and a person (`calibrate score`) or between
    two judges (`calibrate compare`)."""

    path: str
    kind: str = Field(description="'judge-human' or 'judge-judge'.")
    first: str = Field(description="The judge model.")
    second: str = Field(description="The labelers, or the second judge model.")
    items: int
    agreement: float | None
    kappa: float | None
    warnings: list[str]


class Spread(_Record):
    """Repeated extraction runs of one corpus, model and code version: how far apart their
    metrics land is the run-to-run noise, the size a real change has to beat."""

    corpus: str
    model: str
    code_version: str
    runs: int
    low: dict[str, float] = Field(description="Metric name -> lowest value over the runs.")
    high: dict[str, float] = Field(description="Metric name -> highest value over the runs.")


class ReviewSummary(_Record):
    db: str
    stats: ReviewStats


class Skipped(_Record):
    path: str
    reason: str


class Dashboard(_Record):
    created: str
    sources: list[str] = Field(description="The folders and files searched.")
    extraction: list[ExtractionRun] = Field(default_factory=list)
    spreads: list[Spread] = Field(default_factory=list)
    judging: list[JudgeRun] = Field(default_factory=list)
    agreement: list[AgreementRun] = Field(default_factory=list)
    reviews: list[ReviewSummary] = Field(default_factory=list)
    skipped: list[Skipped] = Field(
        default_factory=list, description="JSON files that look like runs but did not load."
    )
