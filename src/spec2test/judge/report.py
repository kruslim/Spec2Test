"""Plain-text tables for the terminal: judge scores, and judge-human agreement."""

from __future__ import annotations

from spec2test.judge.models import Calibration, Judgement, Judging
from spec2test.judge.rubric import RUBRIC
from spec2test.llm import cost_line
from spec2test.schema import VerificationItemKind


def quality_report(judging: Judging) -> str:
    """Mean rubric score, acceptance rate and per-criterion pass rate, per item kind."""
    lines = [
        f"judge {judging.judge_model}, generator {judging.generator_model}: "
        f"{len(judging.judgements)} items judged, {len(judging.failed)} failed",
        cost_line(judging.usage, judging.pages),
        *(f"warning: {w}" for w in judging.warnings),
        "",
    ]
    groups = [
        (k.value, [j for j in judging.judgements if j.kind is k]) for k in VerificationItemKind
    ]
    groups = [(k, js) for k, js in groups if js]
    if not groups:
        return "\n".join(lines).rstrip()
    groups.append(("all", judging.judgements))
    names = ["score", "acceptable", *RUBRIC]
    width = max(len(n) for n in names)
    lines.append(f"{'':{width}}" + "".join(f"  {k:>10}" for k, _ in groups))
    lines.append(f"{'items':{width}}" + "".join(f"  {len(js):>10}" for _, js in groups))
    lines.append(f"{'score':{width}}" + "".join(f"  {_mean_score(js):>10}" for _, js in groups))
    lines.append(
        f"{'acceptable':{width}}"
        + "".join(f"  {_rate(js, lambda j: j.acceptable):>10}" for _, js in groups)
    )
    for name in RUBRIC:
        lines.append(
            f"{name:{width}}"
            + "".join(
                f"  {_rate(js, lambda j, n=name: j.verdicts[n].passed):>10}" for _, js in groups
            )
        )
    return "\n".join(lines)


def calibration_report(cal: Calibration) -> str:
    labelers = ", ".join(cal.labelers) or "-"
    lines = [
        f"judge {cal.judge_model} vs. {labelers}: {cal.labeled} labeled items, "
        f"{cal.unlabeled} unlabeled skipped",
        cost_line(cal.judging.usage, cal.judging.pages),
        *(f"warning: {w}" for w in cal.warnings),
        "",
    ]
    width = max(len(a.name) for a in cal.agreements)
    lines.append(
        f"{'criterion':{width}}  {'n':>4}  {'agree':>6}  {'kappa':>6}  "
        f"{'both pass':>9}  {'both fail':>9}  {'judge only':>10}  {'human only':>10}"
    )
    for a in cal.agreements:
        agree = "-" if a.agreement is None else f"{a.agreement:.1%}"
        kappa = "-" if a.kappa is None else f"{a.kappa:.2f}"
        lines.append(
            f"{a.name:{width}}  {a.n:>4}  {agree:>6}  {kappa:>6}  {a.both_pass:>9}  "
            f"{a.both_fail:>9}  {a.judge_only:>10}  {a.human_only:>10}"
        )
    if cal.disagreements:
        lines += ["", "disagreements:"]
        for d in cal.disagreements:
            verdict = "pass" if d.judge else "fail"
            lines.append(f"  {d.item_id}  {d.name}: judge {verdict}, human {_flip(verdict)}")
            if d.judge_reason:
                lines.append(f"      judge: {d.judge_reason}")
    return "\n".join(lines)


def _mean_score(judgements: list[Judgement]) -> str:
    return f"{sum(j.score for j in judgements) / len(judgements):.1%}"


def _rate(judgements: list[Judgement], passed) -> str:
    return f"{sum(map(passed, judgements)) / len(judgements):.1%}"


def _flip(verdict: str) -> str:
    return "fail" if verdict == "pass" else "pass"
