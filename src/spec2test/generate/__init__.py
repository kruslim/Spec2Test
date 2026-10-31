"""Generate: render each requirement as the verification item its method calls for."""

from spec2test.generate.context import (
    Clause,
    SourceContext,
    references,
    render_context,
    source_context,
)
from spec2test.generate.models import (
    Generation,
    GenerationFailure,
    RejectedItem,
    SkippedRequirement,
    pages_cited,
)
from spec2test.generate.pipeline import (
    KIND_FOR_METHOD,
    check_item,
    generate,
    not_verifiable,
    verdict_text,
)
from spec2test.generate.render import render

__all__ = [
    "KIND_FOR_METHOD",
    "Clause",
    "Generation",
    "GenerationFailure",
    "RejectedItem",
    "SkippedRequirement",
    "SourceContext",
    "check_item",
    "generate",
    "not_verifiable",
    "pages_cited",
    "references",
    "render",
    "render_context",
    "source_context",
    "verdict_text",
]
