"""Plain-text reports of a judging run and of a calibration, for the terminal."""

from __future__ import annotations

from spec2test.evals import Metric
from spec2test.judge.models import Agreement, Calibration, Judging
from spec2test.judge.pipeline import pass_rates
from spec2test.judge.rubric import Criterion


def judging_report(judging: Judging) -> str:
    headline = (
        f"judge: {judging.judge_model} · generator: {judging.generator_model or 'unknown'} · "
        f"{len(judging.judgements)} items judged, {len(judging.failed)} failed"
    )
    lines = [headline, *(f"warning: {w}" for w in judging.warnings), ""]
    rates = pass_rates(judging)
    names = [m.name for m in rates["all"]]
    width = max(len("criterion"), *(len(n) for n in names))
    cols = list(rates)
    col = max(14, *(len(c) for c in cols))
    lines.append(f"{'criterion':{width}}  " + "  ".join(f"{c:>{col}}" for c in cols))
    for i, name in enumerate(names):
        cells = [_rate(rates[c][i]) for c in cols]
        lines.append(f"{name:{width}}  " + "  ".join(f"{cell:>{col}}" for cell in cells))
    verdicts = []
    for j in judging.judgements:
        failed = [c for c in Criterion if not j.grades.grade(c).passed]
        verdicts.append(f"{j.item_id}  {'acceptable' if not failed else 'not acceptable'}")
        verdicts += [f"    {c.value}: {j.grades.grade(c).reason}" for c in failed]
    verdicts += [f"{f.item_id}  failed: {f.error}" for f in judging.failed]
    if verdicts:
        lines += ["", *verdicts]
    return "\n".join(lines)


def calibration_report(calibration: Calibration) -> str:
    graded = len(calibration.judgements)
    headline = (
        f"calibration: {calibration.sample_path} · judge: {calibration.judge_model} · "
        f"labeler: {calibration.labeler or 'unknown'} · {graded} items compared, "
        f"{calibration.unlabeled} ungraded, {len(calibration.failed)} failed"
    )
    lines = [headline, *(f"warning: {w}" for w in calibration.warnings), ""]
    width = max(len("criterion"), *(len(a.criterion) for a in calibration.agreements))
    lines.append(
        f"{'criterion':{width}}  {'n':>3}  {'agreement':>9}  {'kappa':>6}  {'both pass':>9}  "
        f"{'both fail':>9}  {'judge only':>10}  {'person only':>11}"
    )
    for a in calibration.agreements:
        lines.append(
            f"{a.criterion:{width}}  {a.n:>3}  {_share(a.agreement):>9}  {_kappa(a):>6}  "
            f"{a.both_pass:>9}  {a.both_fail:>9}  {a.judge_only_pass:>10}  "
            f"{a.person_only_pass:>11}"
        )
    lines.append("")
    lines.append("judge only: the judge passed what the person failed; person only: the reverse.")
    if calibration.disagreements:
        lines += ["", "disagreements:"]
        for d in calibration.disagreements:
            verdict = f"judge {_pf(d.judge)}, person {_pf(d.person)}"
            lines.append(f"{d.item_id}  {d.criterion}: {verdict}")
            lines.append(f"    judge: {d.judge_reason}")
            if d.note:
                lines.append(f"    note: {d.note}")
    for f in calibration.failed:
        lines.append(f"{f.item_id}  failed: {f.error}")
    return "\n".join(lines)


def _rate(m: Metric) -> str:
    return "n/a" if m.value is None else f"{m.value:.1%} {m.numerator}/{m.denominator}"


def _share(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _kappa(a: Agreement) -> str:
    return "n/a" if a.kappa is None else f"{a.kappa:.2f}"


def _pf(passed: bool) -> str:
    return "pass" if passed else "fail"
