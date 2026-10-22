"""Render an `EvalRun`: a plain-text table for the terminal and a self-contained HTML page."""

from __future__ import annotations

from collections import Counter
from html import escape

from spec2test.evals.models import (
    DESCRIPTIONS,
    EvalRun,
    Failure,
    FailureCategory,
    FlagCounts,
    Metric,
)
from spec2test.schema import Requirement

_METHODS = ["inspection", "test", "demonstration", "analysis"]


def text_report(run: EvalRun) -> str:
    lines = [_headline(run), *(f"warning: {w}" for w in run.warnings), ""]
    width = max(len(m.name) for m in run.metrics)
    lines.append(f"{'metric':{width}}   {'value':>6}  {'n':>9}")
    for m in run.metrics:
        lines.append(f"{m.name:{width}}   {_percent(m):>6}  {_fraction(m):>9}")
    if run.flags:
        width = max(len("flag kind"), *(len(c.kind) for c in run.flags))
        lines += [
            "",
            f"{'flag kind':{width}}   {'caught':>6}  {'false':>5}  {'missed':>6}  "
            f"{'precision':>9}  {'recall':>6}",
        ]
        for c in run.flags:
            precision, recall = _flag_metrics(c)
            lines.append(
                f"{c.kind:{width}}   {c.true_positives:>6}  {c.false_positives:>5}  "
                f"{c.false_negatives:>6}  {_percent(precision):>9}  {_percent(recall):>6}"
            )
    counts = _category_counts(run.failures)
    if counts:
        width = max(len(c.value) for c in counts)
        lines += ["", f"{'failure':{width}}   {'count':>5}"]
        lines += [f"{c.value:{width}}   {n:>5}" for c, n in counts.items()]
    return "\n".join(lines)


def html_report(run: EvalRun) -> str:
    gold = {r.id: r for r in run.gold}
    pred = {r.id: r for r in run.predicted}
    body = [
        f"<h1>Eval: {escape(run.corpus)}</h1>",
        f"<p class=sub>{escape(_headline(run))}</p>",
        *(f"<p class=warn>Warning: {escape(w)}</p>" for w in run.warnings),
        _run_table(run),
        "<h2>Metrics</h2>",
        _metrics_table(run.metrics),
        "<h2>Failure taxonomy</h2>",
        _taxonomy_table(run.failures),
        "<h2>Verification method</h2>",
        "<p class=note>Rows: gold method. Columns: extracted method. Aligned pairs only.</p>",
        _confusion_table(run.method_confusion),
        "<h2>Testability flags</h2>",
        _flags_table(run),
        "<h2>Failures</h2>",
        _failures_table(run.failures, gold, pred),
        "<h2>Aligned pairs</h2>",
        _pairs_table(run, gold, pred),
    ]
    return _PAGE.format(title=escape(f"Eval {run.corpus}"), body="\n".join(body))


def _headline(run: EvalRun) -> str:
    seconds = "" if run.seconds is None else f", {run.seconds:.1f} s"
    return (
        f"{run.corpus}: {len(run.gold)} gold, {len(run.predicted)} extracted, "
        f"{len(run.matches)} aligned, {run.pages} pages{seconds}, model {run.model}"
    )


def _percent(m: Metric) -> str:
    return "n/a" if m.value is None else f"{m.value:.1%}"


def _fraction(m: Metric) -> str:
    return f"{m.numerator}/{m.denominator}"


def _category_counts(failures: list[Failure]) -> dict[FailureCategory, int]:
    counts = Counter(f.category for f in failures)
    return {c: counts[c] for c in FailureCategory if counts[c]}


def _run_table(run: EvalRun) -> str:
    x = run.extraction
    rows = [
        ("Document", run.doc_id),
        ("Gold set", run.gold_path),
        ("Scope", ", ".join(run.scope) if run.scope else "whole document"),
        ("Gold labeler", run.gold_labeler or "unknown"),
        ("Gold reviewed", "yes" if run.gold_reviewed else "no"),
        ("Model", run.model),
        ("Scored", run.created),
        ("Extraction time", "n/a" if run.seconds is None else f"{run.seconds:.1f} s"),
        (
            "Seconds per page",
            "n/a" if run.seconds is None or not run.pages else f"{run.seconds / run.pages:.1f}",
        ),
        ("Rejected drafts", str(len(x.rejected))),
        ("Failed sections", str(len(x.failed_sections))),
        ("Linter", _linter(run)),
    ]
    if run.lint is not None and run.lint.llm_pass:
        rows += [
            ("Rejected LLM flags", str(len(run.lint.rejected))),
            ("Failed lint batches", str(len(run.lint.failed))),
        ]
    cells = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in rows)
    return f"<table class=kv>{cells}</table>"


def _metrics_table(metrics: list[Metric]) -> str:
    rows = []
    for m in metrics:
        bar = "" if m.value is None else f"<span class=bar style='width:{m.value * 100:.1f}%'>"
        rows.append(
            f"<tr><td>{escape(m.name)}</td><td class=num>{_percent(m)}</td>"
            f"<td class=num>{_fraction(m)}</td>"
            f"<td class=barcell>{bar}{'</span>' if bar else ''}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Metric</th><th>Value</th><th>n</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _taxonomy_table(failures: list[Failure]) -> str:
    counts = _category_counts(failures)
    if not counts:
        return "<p class=note>No failures.</p>"
    rows = "".join(
        f"<tr><td><a href='#{c.value}'><code>{c.value}</code></a></td><td class=num>{n}</td>"
        f"<td>{escape(DESCRIPTIONS[c])}</td></tr>"
        for c, n in counts.items()
    )
    return (
        "<table><thead><tr><th>Category</th><th>Count</th><th>Meaning</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _confusion_table(confusion: dict[str, dict[str, int]]) -> str:
    if not confusion:
        return "<p class=note>No aligned pair has a gold method.</p>"
    columns = _METHODS + sorted({k for row in confusion.values() for k in row} - set(_METHODS))
    head = "".join(f"<th>{escape(c)}</th>" for c in columns)
    rows = []
    for g in [m for m in _METHODS if m in confusion]:
        cells = []
        for c in columns:
            n = confusion[g].get(c, 0)
            cls = "num hit" if c == g and n else ("num miss" if n else "num")
            cells.append(f"<td class='{cls}'>{n or ''}</td>")
        rows.append(f"<tr><th>{escape(g)}</th>{''.join(cells)}</tr>")
    return f"<table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _flags_table(run: EvalRun) -> str:
    if not run.flags:
        return "<p class=note>No testability flags on the aligned pairs, gold or extracted.</p>"
    rows = "".join(
        f"<tr><td><code>{escape(c.kind)}</code></td><td class=num>{c.true_positives}</td>"
        f"<td class=num>{c.false_positives}</td><td class=num>{c.false_negatives}</td>"
        + "".join(f"<td class=num>{_percent(m)}</td>" for m in _flag_metrics(c))
        + "</tr>"
        for c in run.flags
    )
    return (
        "<table><thead><tr><th>Kind</th><th>Caught</th><th>False</th><th>Missed</th>"
        f"<th>Precision</th><th>Recall</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _flag_metrics(c: FlagCounts) -> tuple[Metric, Metric]:
    tp = c.true_positives
    return (
        Metric(name=f"{c.kind} precision", numerator=tp, denominator=tp + c.false_positives),
        Metric(name=f"{c.kind} recall", numerator=tp, denominator=tp + c.false_negatives),
    )


def _linter(run: EvalRun) -> str:
    if run.lint is None:
        return "off"
    return "rules and LLM pass" if run.lint.llm_pass else "rules only"


def _failures_table(
    failures: list[Failure], gold: dict[str, Requirement], pred: dict[str, Requirement]
) -> str:
    if not failures:
        return "<p class=note>No failures.</p>"
    rows = []
    for category in _category_counts(failures):
        group = [f for f in failures if f.category is category]
        for i, f in enumerate(group):
            anchor = f" id='{category.value}'" if i == 0 else ""
            record = gold.get(f.gold_id or "") or pred.get(f.pred_id or "")
            quote = record.source_anchor.quote if record else ""
            rows.append(
                f"<tr{anchor}><td><code>{category.value}</code></td>"
                f"<td>{escape(f.section)}</td><td>{_id(f.gold_id)}</td><td>{_id(f.pred_id)}</td>"
                f"<td>{escape(f.detail)}</td><td class=quote>{escape(quote)}</td></tr>"
            )
    return (
        "<table><thead><tr><th>Category</th><th>Section</th><th>Gold</th><th>Extracted</th>"
        f"<th>Detail</th><th>Quote</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _pairs_table(run: EvalRun, gold: dict[str, Requirement], pred: dict[str, Requirement]) -> str:
    if not run.matches:
        return "<p class=note>No aligned pairs.</p>"
    errors = Counter(f.gold_id for f in run.failures if f.gold_id and f.pred_id)
    rows = []
    for m in run.matches:
        g, p = gold[m.gold_id], pred[m.pred_id]
        n = errors[m.gold_id]
        status = "<span class=ok>ok</span>" if not n else f"<span class=bad>{n} field</span>"
        rows.append(
            f"<tr><td>{escape(g.source_anchor.section)}</td><td>{_id(g.id)}</td>"
            f"<td>{_id(p.id)}</td><td class=num>{m.coverage:.0%}</td><td>{status}</td>"
            f"<td>{escape(g.text)}</td><td>{escape(p.text)}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Section</th><th>Gold</th><th>Extracted</th><th>Overlap</th>"
        "<th>Fields</th><th>Gold text</th><th>Extracted text</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _id(value: str | None) -> str:
    return f"<code>{escape(value)}</code>" if value else ""


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #ffffff; --fg: #1d1d1f; --muted: #6b6b73; --line: #e2e2e6; --head: #f5f5f7;
  --bar: #3b6fd8; --ok: #1f7a3d; --bad: #b3261e; --hit: #e3f1e7; --miss: #fbe6e4;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #17171a; --fg: #ececf0; --muted: #a0a0aa; --line: #34343a; --head: #222227;
    --bar: #6f97ea; --ok: #6cc58a; --bad: #f08a80; --hit: #1e3a27; --miss: #47221f;
  }}
}}
body {{
  margin: 0 auto; padding: 24px 16px 64px; max-width: 1200px; background: var(--bg);
  color: var(--fg); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
h2 {{ font-size: 16px; margin: 32px 0 8px; }}
.sub, .note {{ color: var(--muted); margin: 0 0 12px; }}
table {{ border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 5px 10px; text-align: left;
  vertical-align: top; }}
thead th {{ background: var(--head); font-weight: 600; white-space: nowrap; }}
table.kv {{ width: auto; }}
table.kv th {{ color: var(--muted); font-weight: 500; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.barcell {{ width: 40%; min-width: 120px; }}
.bar {{ display: block; height: 10px; background: var(--bar); border-radius: 2px; }}
.hit {{ background: var(--hit); }}
.miss {{ background: var(--miss); }}
.ok {{ color: var(--ok); }}
.bad {{ color: var(--bad); }}
.quote {{ color: var(--muted); font-style: italic; }}
.warn {{ color: var(--bad); border-left: 3px solid var(--bad); padding: 4px 10px; }}
code {{ font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
