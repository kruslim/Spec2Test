"""Extract: turn anchored sections into cited `Requirement` records."""

from spec2test.extract.citation import CitationError, check_citation, locate, section_at
from spec2test.extract.models import (
    ExtractedRequirement,
    ExtractedRequirements,
    Extraction,
    Rejection,
    SectionFailure,
)
from spec2test.extract.pipeline import extract, is_normative

__all__ = [
    "CitationError",
    "ExtractedRequirement",
    "ExtractedRequirements",
    "Extraction",
    "Rejection",
    "SectionFailure",
    "check_citation",
    "extract",
    "is_normative",
    "locate",
    "section_at",
]
