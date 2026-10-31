"""Compare two judges on the same items: the independent check on the main judge.

The main judge (Claude Opus, in Claude Code sessions) grades every item. A second judge
from another family (Gemini, by API) grades only a small sample, such as the calibration
sample, and `compare` reports how often the two agree, per rubric criterion and for
`acceptable`, with both reasons for every disagreement. No model calls: both inputs are
saved judgements.

Agreement between two models is not accuracy: it only says where to look. Judge-human
agreement (`calibrate`) is what makes a judge's numbers count.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from spec2test.judge.models import Agreement, Judging
from spec2test.judge.rubric import RUBRIC
from spec2test.llm import model_family


class SplitVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    name: str = Field(description="A rubric criterion, or 'acceptable'.")
    first: bool
    second: bool
    first_reason: str = ""
    second_reason: str = ""


class JudgeComparison(BaseModel):
    """`Agreement.judge_only` counts verdicts only the first judge passed, `human_only`
    those only the second judge passed."""

    model_config = ConfigDict(extra="ignore")

    first_model: str
    second_model: str
    compared: int = Field(description="Items both judges graded.")
    first_only: int = Field(description="Items only the first judge graded.")
    second_only: int = Field(description="Items only the second judge graded.")
    warnings: list[str] = Field(default_factory=list)
    agreements: list[Agreement]
    disagreements: list[SplitVerdict]


def compare(first: Judging, second: Judging) -> JudgeComparison:
    """Agreement between two judges over the items both graded."""
    a = {j.item_id: j for j in first.judgements}
    b = {j.item_id: j for j in second.judgements}
    shared = [i for i in a if i in b]
    warnings = []
    if model_family(first.judge_model) == model_family(second.judge_model):
        warnings.append(
            f"both judges are from the {model_family(first.judge_model)!r} family: their "
            "agreement says little about either. Compare judges from different families."
        )
    if not shared:
        warnings.append("the two judgings share no item")

    agreements = {name: Agreement(name=name) for name in [*RUBRIC, "acceptable"]}
    disagreements = []
    for item_id in shared:
        x, y = a[item_id], b[item_id]
        pairs = {name: (x.verdicts[name].passed, y.verdicts[name].passed, name) for name in RUBRIC}
        pairs["acceptable"] = (x.acceptable, y.acceptable, None)
        for name, (by_first, by_second, criterion) in pairs.items():
            agreement = agreements[name]
            if by_first and by_second:
                agreement.both_pass += 1
            elif not by_first and not by_second:
                agreement.both_fail += 1
            elif by_first:
                agreement.judge_only += 1
            else:
                agreement.human_only += 1
            if by_first != by_second:
                disagreements.append(
                    SplitVerdict(
                        item_id=item_id,
                        name=name,
                        first=by_first,
                        second=by_second,
                        first_reason=x.verdicts[criterion].reason if criterion else "",
                        second_reason=y.verdicts[criterion].reason if criterion else "",
                    )
                )
    return JudgeComparison(
        first_model=first.judge_model,
        second_model=second.judge_model,
        compared=len(shared),
        first_only=len(a) - len(shared),
        second_only=len(b) - len(shared),
        warnings=warnings,
        agreements=list(agreements.values()),
        disagreements=disagreements,
    )


def comparison_report(cmp: JudgeComparison) -> str:
    first, second = cmp.first_model, cmp.second_model
    lines = [
        f"judge {first} vs. judge {second}: {cmp.compared} items graded by both "
        f"({cmp.first_only} by {first} only, {cmp.second_only} by {second} only)",
        *(f"warning: {w}" for w in cmp.warnings),
        "",
    ]
    width = max(len(a.name) for a in cmp.agreements)
    lines.append(
        f"{'criterion':{width}}  {'n':>4}  {'agree':>6}  {'kappa':>6}  "
        f"{'both pass':>9}  {'both fail':>9}  {'1st only':>8}  {'2nd only':>8}"
    )
    for a in cmp.agreements:
        agree = "-" if a.agreement is None else f"{a.agreement:.1%}"
        kappa = "-" if a.kappa is None else f"{a.kappa:.2f}"
        lines.append(
            f"{a.name:{width}}  {a.n:>4}  {agree:>6}  {kappa:>6}  {a.both_pass:>9}  "
            f"{a.both_fail:>9}  {a.judge_only:>8}  {a.human_only:>8}"
        )
    if cmp.disagreements:
        lines += ["", "disagreements:"]
        for d in cmp.disagreements:
            lines.append(
                f"  {d.item_id}  {d.name}: {first} {_word(d.first)}, {second} {_word(d.second)}"
            )
            if d.first_reason:
                lines.append(f"      {first}: {d.first_reason}")
            if d.second_reason:
                lines.append(f"      {second}: {d.second_reason}")
    return "\n".join(lines)


def _word(passed: bool) -> str:
    return "pass" if passed else "fail"
