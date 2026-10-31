"""Render a `Dashboard`: plain-text tables for the terminal and one self-contained HTML page."""

from __future__ import annotations

from collections import defaultdict
from html import escape

from spec2test.dashboard.models import (
    AgreementRun,
    CostSummary,
    Dashboard,
    ExtractionRun,
    JudgeRun,
    Rate,
    Spread,
)
from spec2test.evals.report import _PAGE
from spec2test.evals.score import (
    FIELD_ACCURACY,
    FLAG_PRECISION,
    FLAG_RECALL,
    METHOD_ACCURACY,
    PRECISION,
    RECALL,
    TYPE_ACCURACY,
)
from spec2test.review.models import Tally

COLUMNS = {
    RECALL: "recall",
    PRECISION: "precision",
    FIELD_ACCURACY: "field",
    TYPE_ACCURACY: "type",
    METHOD_ACCURACY: "method",
    FLAG_PRECISION: "flag P",
    FLAG_RECALL: "flag R",
}
"""The eval metrics the dashboard shows, with their short column names."""


def text_report(board: Dashboard) -> str:
    lines = [f"dashboard: {', '.join(board.sources) or '-'}"]
    if not (board.extraction or board.judging or board.agreement or board.reviews):
        lines.append("no saved runs found")
    for corpus, runs in _by_corpus(board.extraction).items():
        lines += ["", f"extraction: {corpus} ({len(runs)} run{'s' if len(runs) != 1 else ''})"]
        head = f"{'scored':20}  {'model':18}  {'code':20}"
        lines.append(head + "".join(f"  {c:>9}" for c in COLUMNS.values()) + f"  {'$/page':>8}")
        for r in runs:
            lines.append(
                f"{r.created[:19]:20}  {r.model[:18]:18}  {(r.code_version or '-')[:20]:20}"
                + "".join(f"  {_pct(r.metrics.get(n)):>9}" for n in COLUMNS)
                + f"  {_per_page(r.cost):>8}"
                + (f"  ({len(r.warnings)} warning(s))" if r.warnings else "")
            )
        for s in board.spreads:
            if s.corpus == corpus:
                lines.append(_spread_line(s))
    if board.judging:
        lines += ["", "judging"]
        lines.append(
            f"{'judged':20}  {'judge':22}  {'how':10}  {'generator':18}  {'items':>5}  "
            f"{'acceptable':>10}  {'criteria':>8}  by kind"
        )
        for j in board.judging:
            when = (j.created or f"~{j.file_time}")[:19]
            kinds = ", ".join(f"{k} {_pct(r)}" for k, r in j.by_kind.items())
            failed = f"  ({j.failed} failed)" if j.failed else ""
            lines.append(
                f"{when:20}  {j.judge_model[:22]:22}  {_how(j):10}  {j.generator_model[:18]:18}  "
                f"{j.judged:>5}  {_pct(j.acceptable):>10}  {_pct(j.criteria):>8}  {kinds}{failed}"
            )
    if board.agreement:
        lines += ["", "agreement on 'acceptable'"]
        for a in board.agreement:
            lines.append(_agreement_line(a))
    for review in board.reviews:
        t = review.stats.overall
        lines += [
            "",
            f"review: {review.db}: {len(review.stats.generations)} generation(s), {_tally(t)}",
        ]
    for s in board.skipped:
        lines.append(f"skipped {s.path}: {s.reason}")
    return "\n".join(lines)


def html_report(board: Dashboard) -> str:
    body = [
        "<h1>Spec2Test dashboard</h1>",
        f"<p class=sub>Built {escape(board.created)} from "
        f"{escape(', '.join(board.sources) or '-')}. Every number below comes from a saved "
        "run; nothing was recomputed with a model.</p>",
    ]
    if not (board.extraction or board.judging or board.agreement or board.reviews):
        body.append("<p>No saved runs found.</p>")
    for corpus, runs in _by_corpus(board.extraction).items():
        body += [
            f"<h2>Extraction: {escape(corpus)}</h2>",
            _extraction_table(runs),
            *(_spread_html(s) for s in board.spreads if s.corpus == corpus),
        ]
    if board.judging:
        body += [
            "<h2>Judging</h2>",
            "<p class=note>“Session” runs were answered in a Claude Code session, with no API "
            "spend; they are not API runs. Judge scores are uncalibrated until judge-human "
            "agreement is reported below.</p>",
            _judging_table(board.judging),
        ]
    if board.agreement:
        body += ["<h2>Agreement on “acceptable”</h2>", _agreement_table(board.agreement)]
    if board.reviews:
        body += [
            "<h2>Review acceptance</h2>",
            "<p class=note>Share of reviewed items accepted unedited.</p>",
            _reviews_table(board),
        ]
    if board.skipped:
        body.append("<h2>Skipped files</h2>")
        body += [f"<p class=warn>{escape(s.path)}: {escape(s.reason)}</p>" for s in board.skipped]
    return _PAGE.format(title="Spec2Test dashboard", body="\n".join(body))


def _by_corpus(runs: list[ExtractionRun]) -> dict[str, list[ExtractionRun]]:
    groups: dict[str, list[ExtractionRun]] = defaultdict(list)
    for r in runs:
        groups[r.corpus].append(r)
    return dict(sorted(groups.items()))


def _pct(rate: Rate | None) -> str:
    return "-" if rate is None or rate.value is None else f"{rate.value:.1%}"


def _per_page(c: CostSummary) -> str:
    if not c.calls:
        return "-"
    return "unknown" if c.per_page is None else f"${c.per_page:.4f}"


def _how(j: JudgeRun) -> str:
    return j.provider or "[unknown]"


def _spread_line(s: Spread) -> str:
    parts = [f"{COLUMNS[n]} {s.low[n]:.1%}-{s.high[n]:.1%}" for n in COLUMNS if n in s.low]
    return f"  spread over {s.runs} runs ({s.model}, {s.code_version}): " + ", ".join(parts)


def _agreement_line(a: AgreementRun) -> str:
    agree = "-" if a.agreement is None else f"{a.agreement:.1%}"
    kappa = "-" if a.kappa is None else f"{a.kappa:.2f}"
    return f"  {a.kind}: {a.first} vs {a.second}: {a.items} items, agree {agree}, kappa {kappa}"


def _tally(t: Tally) -> str:
    rate = "-" if t.acceptance_rate is None else f"{t.acceptance_rate:.1%}"
    return (
        f"{t.reviewed}/{t.items} items reviewed, {t.accepted} accepted, {t.edited} edited, "
        f"{t.rejected} rejected, acceptance {rate}"
    )


def _cell(text: str, num: bool = False, title: str = "") -> str:
    cls = " class=num" if num else ""
    tip = f' title="{escape(title)}"' if title else ""
    return f"<td{cls}{tip}>{escape(text)}</td>"


def _rate_cell(rate: Rate | None) -> str:
    title = "" if rate is None else f"{rate.numerator}/{rate.denominator}"
    return _cell(_pct(rate), num=True, title=title)


def _head(*names: str) -> str:
    return "<thead><tr>" + "".join(f"<th>{escape(n)}</th>" for n in names) + "</tr></thead>"


def _extraction_table(runs: list[ExtractionRun]) -> str:
    rows = []
    for r in runs:
        reviewed = "reviewed" if r.gold_reviewed else "unreviewed"
        cells = [
            _cell(r.created),
            _cell(r.model),
            _cell(r.code_version or "-"),
            _cell(f"{r.gold} ({reviewed})"),
            *(_rate_cell(r.metrics.get(n)) for n in COLUMNS),
            _cell(_per_page(r.cost), num=True),
            _cell("; ".join(r.warnings) or "-"),
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = _head("Scored", "Model", "Code", "Gold", *COLUMNS.values(), "$/page", "Warnings")
    return f"<table>{head}<tbody>{''.join(rows)}</tbody></table>"


def _spread_html(s: Spread) -> str:
    return (
        f"<p class=note>{escape(_spread_line(s).strip())}. Differences inside this range are "
        "run-to-run noise.</p>"
    )


def _judging_table(runs: list[JudgeRun]) -> str:
    rows = []
    for j in runs:
        when = j.created or f"~{j.file_time} (file time)"
        kinds = ", ".join(f"{k} {_pct(r)} of {r.denominator}" for k, r in j.by_kind.items())
        cells = [
            _cell(when),
            _cell(j.judge_model),
            _cell("session" if j.provider == "session" else _how(j)),
            _cell(j.generator_model),
            _cell(str(j.judged), num=True),
            _cell(str(j.failed), num=True),
            _rate_cell(j.acceptable),
            _rate_cell(j.criteria),
            _cell(kinds or "-"),
            _cell(_per_page(j.cost), num=True),
            _cell("; ".join(j.warnings) or "-"),
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = _head(
        "Judged", "Judge", "How", "Generator", "Items", "Failed", "Acceptable", "Criteria",
        "By kind", "$/page", "Warnings",
    )  # fmt: skip
    return f"<table>{head}<tbody>{''.join(rows)}</tbody></table>"


def _agreement_table(runs: list[AgreementRun]) -> str:
    rows = []
    for a in runs:
        cells = [
            _cell(a.kind),
            _cell(a.first),
            _cell(a.second),
            _cell(str(a.items), num=True),
            _cell("-" if a.agreement is None else f"{a.agreement:.1%}", num=True),
            _cell("-" if a.kappa is None else f"{a.kappa:.2f}", num=True),
            _cell(a.path),
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = _head("Kind", "Judge", "Against", "Items", "Agreement", "Kappa", "File")
    return f"<table>{head}<tbody>{''.join(rows)}</tbody></table>"


def _reviews_table(board: Dashboard) -> str:
    rows = []
    for review in board.reviews:
        t = review.stats.overall
        rate = "-" if t.acceptance_rate is None else f"{t.acceptance_rate:.1%}"
        cells = [
            _cell(review.db),
            _cell(str(len(review.stats.generations)), num=True),
            _cell(str(t.items), num=True),
            _cell(str(t.reviewed), num=True),
            _cell(str(t.accepted), num=True),
            _cell(str(t.edited), num=True),
            _cell(str(t.rejected), num=True),
            _cell(rate, num=True),
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = _head(
        "Review log", "Generations", "Items", "Reviewed", "Accepted", "Edited", "Rejected",
        "Acceptance",
    )  # fmt: skip
    return f"<table>{head}<tbody>{''.join(rows)}</tbody></table>"
