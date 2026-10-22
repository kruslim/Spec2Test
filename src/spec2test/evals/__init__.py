"""Evals: score extraction and linting against labeled gold sets."""

from spec2test.evals.gold import DEFAULT_DIR, GoldError, GoldSet, gold_path, in_scope, load_gold
from spec2test.evals.models import (
    DESCRIPTIONS,
    EvalRun,
    Failure,
    FailureCategory,
    FlagCounts,
    Match,
    Metric,
)
from spec2test.evals.report import html_report, text_report
from spec2test.evals.run import LintMode, evaluate, model_name, rescore, write_report
from spec2test.evals.score import align, compare, score

__all__ = [
    "DEFAULT_DIR",
    "DESCRIPTIONS",
    "EvalRun",
    "Failure",
    "FailureCategory",
    "FlagCounts",
    "GoldError",
    "GoldSet",
    "LintMode",
    "Match",
    "Metric",
    "align",
    "compare",
    "evaluate",
    "gold_path",
    "html_report",
    "in_scope",
    "load_gold",
    "model_name",
    "rescore",
    "score",
    "text_report",
    "write_report",
]
