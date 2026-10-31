"""Review: a person accepts, edits or rejects each verification item next to its source,
and every decision is logged to SQLite; the acceptance rate is what they add up to.

The Streamlit app (`spec2test.review.app`) needs the `ui` extra; nothing here imports it.
"""

from pathlib import Path

from spec2test.review.models import FIELDS, Decision, ReviewDecision, ReviewStats, Tally
from spec2test.review.report import stats_report
from spec2test.review.run import STAGES, PipelineRun, run_pipeline
from spec2test.review.source import SourceView, source_view
from spec2test.review.store import (
    DEFAULT_DB,
    ReviewError,
    ReviewStore,
    StoredGeneration,
    generation_id,
)

APP = Path(__file__).with_name("app.py")
"""Path of the Streamlit app, for `streamlit run`."""

__all__ = [
    "APP",
    "DEFAULT_DB",
    "FIELDS",
    "STAGES",
    "Decision",
    "PipelineRun",
    "ReviewDecision",
    "ReviewError",
    "ReviewStats",
    "ReviewStore",
    "SourceView",
    "StoredGeneration",
    "Tally",
    "generation_id",
    "run_pipeline",
    "source_view",
    "stats_report",
]
