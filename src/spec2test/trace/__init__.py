"""Trace: the requirement-to-item matrix with its gaps, and the diff of two spec versions."""

from spec2test.trace.diff import MATCH_SIMILARITY, diff
from spec2test.trace.matrix import trace
from spec2test.trace.models import (
    GAPS,
    Action,
    ChangeKind,
    Coverage,
    FieldChange,
    ItemImpact,
    Matrix,
    OrphanItem,
    RequirementChange,
    SpecDiff,
    TraceRow,
)
from spec2test.trace.report import diff_report, matrix_csv, matrix_report

__all__ = [
    "GAPS",
    "MATCH_SIMILARITY",
    "Action",
    "ChangeKind",
    "Coverage",
    "FieldChange",
    "ItemImpact",
    "Matrix",
    "OrphanItem",
    "RequirementChange",
    "SpecDiff",
    "TraceRow",
    "diff",
    "diff_report",
    "matrix_csv",
    "matrix_report",
    "trace",
]
