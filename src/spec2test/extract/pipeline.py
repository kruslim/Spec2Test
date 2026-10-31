"""Turn each section of a `Document` into cited `Requirement` records.

Per section: one model call returns the requirements it states. Each one's quote is
located in the section and the resulting record is checked against the source. A draft
that fails goes back to the model once, with the error; if the answer still fails, the
draft is rejected and kept in `Extraction.rejected`, never in `requirements`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from spec2test.extract.citation import CitationError, check_citation, locate, section_label
from spec2test.extract.models import (
    ExtractedRequirement,
    ExtractedRequirements,
    Extraction,
    Rejection,
    SectionFailure,
)
from spec2test.extract.prompts import SYSTEM, extract_prompt, retry_prompt
from spec2test.ingest import Document, Section
from spec2test.llm import LLMClient, LLMError
from spec2test.schema import Requirement, SourceAnchor

# Broad on purpose: a section is skipped only when it cannot state a requirement at all.
_NORMATIVE = re.compile(r"\b(shall|should|must|may|will|requires?|required)\b", re.IGNORECASE)


def is_normative(section: Section) -> bool:
    """True when the section has wording that can state a requirement."""
    return _NORMATIVE.search(section.text) is not None


def extract(doc: Document, llm: LLMClient, *, sections: Iterable[int] | None = None) -> Extraction:
    """Extract cited requirements from `doc`.

    `sections` limits extraction to those `Section.index` values; by default every
    section with normative wording is sent (see `is_normative`).
    """
    result = Extraction(doc_id=doc.doc_id)
    start = len(llm.usage)
    wanted = None if sections is None else set(sections)
    ids = _Ids(doc.doc_id)
    for section in doc.sections:
        if wanted is not None and section.index not in wanted:
            continue
        if wanted is None and not is_normative(section):
            result.skipped_sections.append(section.index)
            continue
        _extract_section(doc, section, llm, ids, result)
    result.usage = llm.usage[start:]
    return result


def _extract_section(
    doc: Document, section: Section, llm: LLMClient, ids: _Ids, result: Extraction
) -> None:
    try:
        answer = llm.generate(extract_prompt(doc, section), ExtractedRequirements, system=SYSTEM)
    except LLMError as exc:
        result.failed_sections.append(SectionFailure(section=section.index, error=str(exc)))
        return

    seen: set[tuple[int, int, str]] = set()

    def accept(draft: ExtractedRequirement) -> list[str]:
        """Cite `draft` and keep it; return its errors instead if it does not check out."""
        try:
            start, end = locate(draft.quote, section, doc)
        except CitationError as exc:
            return [str(exc)]
        key = (start, end, draft.text)
        if key in seen:
            return []  # a split or a retry restated a record already kept
        req = _requirement(draft, doc, section, start, end)
        errors = check_citation(req, doc)
        if not errors:
            seen.add(key)
            result.requirements.append(req.model_copy(update={"id": ids.next(section)}))
        return errors

    failed = [(d, errors) for d in answer.requirements if (errors := accept(d))]
    for draft, errors in failed:
        result.retried += 1
        prompt = retry_prompt(doc, section, draft, errors)
        try:
            retry = llm.generate(prompt, ExtractedRequirements, system=SYSTEM).requirements
        except LLMError as exc:
            errors = [*errors, f"retry failed: {exc}"]
            result.rejected.append(Rejection(section=section.index, draft=draft, errors=errors))
            continue
        if not retry:
            errors = [*errors, "withdrawn on retry"]
            result.rejected.append(Rejection(section=section.index, draft=draft, errors=errors))
        for again in retry:
            if retry_errors := accept(again):
                result.rejected.append(
                    Rejection(section=section.index, draft=again, errors=retry_errors)
                )


def _requirement(
    draft: ExtractedRequirement, doc: Document, section: Section, start: int, end: int
) -> Requirement:
    """The cited record; its ID is assigned once it passes the check."""
    return Requirement(
        id="",
        source_anchor=SourceAnchor(
            doc=doc.doc_id,
            page=doc.page_at(start),
            section=section_label(section),
            start=start,
            end=end,
            quote=doc.text[start:end],
        ),
        text=draft.text,
        type=draft.type,
        strength=draft.strength,
        criteria=draft.criteria,
        conditions=draft.conditions,
        verification_method=draft.verification_method,
    )


class _Ids:
    """Requirement IDs '<doc>:<section>:<n>', numbered from 1 within each section."""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self.counts: Counter[str] = Counter()

    def next(self, section: Section) -> str:
        label = section.anchor or f"s{section.index}"
        self.counts[label] += 1
        return f"{self.doc_id}:{label}:{self.counts[label]}"
