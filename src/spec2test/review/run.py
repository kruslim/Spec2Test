"""Run the pipeline on one document for review: extract, lint (rules and the model's
pass), then generate with the document as source context.

Every stage calls `llm`. With a real provider that costs money, so the app asks for an
explicit confirmation before it calls this; the tests pass a `FakeLLM`.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from spec2test.extract import Extraction, extract
from spec2test.generate import Generation, generate
from spec2test.ingest import Document
from spec2test.lint import Lint, lint
from spec2test.llm import LLMClient, TokenUsage, cost_line

STAGES = ("extract", "lint", "generate")


class PipelineRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction: Extraction
    lint: Lint
    generation: Generation
    usage: list[TokenUsage] = Field(
        default_factory=list, description="Every model call of the run, all three stages."
    )

    def cost(self) -> str:
        """The run's cost, spread over the pages its requirements cite."""
        return cost_line(self.usage, self.generation.pages)


def run_pipeline(
    doc: Document, llm: LLMClient, *, progress: Callable[[str], None] | None = None
) -> PipelineRun:
    """Extract, lint and generate from `doc`; `progress` is told each stage as it starts."""
    say = progress or (lambda stage: None)
    start = len(llm.usage)
    say("extract")
    extraction = extract(doc, llm)
    say("lint")
    linted = lint(extraction.requirements, llm)
    say("generate")
    generation = generate(linted.requirements, llm, doc=doc)
    return PipelineRun(
        extraction=extraction, lint=linted, generation=generation, usage=llm.usage[start:]
    )
