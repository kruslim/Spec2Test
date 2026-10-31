"""Run extraction and the linter on a labeled document, score it, and write the report."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Literal

from spec2test import lint as linter
from spec2test.evals.gold import GoldSet, in_scope
from spec2test.evals.models import EvalRun
from spec2test.evals.report import html_report
from spec2test.evals.score import score, text_changed
from spec2test.extract import Extraction, extract, is_normative
from spec2test.ingest import Document
from spec2test.lint import Lint
from spec2test.llm import LLMClient, model_name

LintMode = Literal["full", "rules", "off"]
"""How an eval lints the extraction: the rules and the LLM pass, the rules only, or not
at all. When rescoring, "full" keeps the LLM flags the saved run has."""


def evaluate(
    doc: Document,
    gold: GoldSet,
    llm: LLMClient,
    *,
    corpus: str,
    model: str | None = None,
    lint: LintMode = "full",
) -> EvalRun:
    """Extract from the sections the gold set covers, lint the result, then score it."""
    sections = None
    if gold.scope is not None:
        sections = [
            s.index for s in doc.sections if in_scope(doc, s, gold.scope) and is_normative(s)
        ]
    start = len(llm.usage)
    started = perf_counter()
    extraction = extract(doc, llm, sections=sections)
    seconds = perf_counter() - started
    linted = None
    if lint != "off":
        linted = linter.lint(extraction.requirements, llm if lint == "full" else None)
    run = score(
        gold,
        extraction,
        doc,
        corpus=corpus,
        model=model or model_name(llm),
        seconds=seconds,
        lint=linted,
    )
    run.usage = llm.usage[start:]
    return run


def rescore(
    doc: Document,
    gold: GoldSet,
    previous: EvalRun | Extraction,
    *,
    corpus: str,
    lint: LintMode = "full",
) -> EvalRun:
    """Score a saved extraction again, without calling a model: after relabeling, say.

    The rules run again, so a rule change shows up too; "full" keeps the LLM flags the
    saved run has, if any. A saved run keeps its token counts, so its cost is still shown.
    A warning says so when the document text differs from the one the run extracted from.
    """
    if isinstance(previous, EvalRun):
        model, seconds, extraction = previous.model, previous.seconds, previous.extraction
        saved = previous.lint
    else:
        model, seconds, extraction, saved = "saved extraction", None, previous, None
    linted = None
    if lint != "off":
        keep = saved if lint == "full" and saved is not None else Lint()
        linted = linter.relint(extraction.requirements, keep)
    run = score(gold, extraction, doc, corpus=corpus, model=model, seconds=seconds, lint=linted)
    if isinstance(previous, EvalRun):
        run.usage = previous.usage
        if changed := text_changed(previous, run):
            run.warnings.append(changed)
    return run


def write_report(run: EvalRun, out_dir: str | Path) -> tuple[Path, Path]:
    """Write `report.html` and `results.json` to `out_dir`; return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html, results = out / "report.html", out / "results.json"
    html.write_text(html_report(run), encoding="utf-8")
    results.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return html, results
