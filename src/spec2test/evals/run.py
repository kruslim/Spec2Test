"""Run extraction on a labeled document, score it, and write the report."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from spec2test.evals.gold import GoldSet, in_scope
from spec2test.evals.models import EvalRun
from spec2test.evals.report import html_report
from spec2test.evals.score import score
from spec2test.extract import Extraction, extract, is_normative
from spec2test.ingest import Document
from spec2test.llm import LLMClient


def evaluate(
    doc: Document,
    gold: GoldSet,
    llm: LLMClient,
    *,
    corpus: str,
    model: str | None = None,
) -> EvalRun:
    """Extract from the sections the gold set covers, then score against it."""
    sections = None
    if gold.scope is not None:
        sections = [
            s.index for s in doc.sections if in_scope(doc, s, gold.scope) and is_normative(s)
        ]
    started = perf_counter()
    extraction = extract(doc, llm, sections=sections)
    seconds = perf_counter() - started
    return score(
        gold, extraction, doc, corpus=corpus, model=model or model_name(llm), seconds=seconds
    )


def rescore(
    doc: Document, gold: GoldSet, previous: EvalRun | Extraction, *, corpus: str
) -> EvalRun:
    """Score a saved extraction again, without calling a model: after relabeling, say."""
    if isinstance(previous, EvalRun):
        model, seconds, extraction = previous.model, previous.seconds, previous.extraction
    else:
        model, seconds, extraction = "saved extraction", None, previous
    return score(gold, extraction, doc, corpus=corpus, model=model, seconds=seconds)


def write_report(run: EvalRun, out_dir: str | Path) -> tuple[Path, Path]:
    """Write `report.html` and `results.json` to `out_dir`; return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html, results = out / "report.html", out / "results.json"
    html.write_text(html_report(run), encoding="utf-8")
    results.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return html, results


def model_name(llm: LLMClient) -> str:
    return getattr(llm, "model", None) or type(llm).__name__
