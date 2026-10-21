"""The citation check: every requirement's quote must be the source text at its offsets.

`locate` turns a quote the model stated into offsets in `Document.text`; `check_citation`
verifies any `Requirement` against its document, whoever produced it.
"""

from __future__ import annotations

import re
from bisect import bisect_right

from spec2test.ingest import Document, Section
from spec2test.schema import Requirement


class CitationError(ValueError):
    """The quote cannot be pinned to one place in its section. The message is written for
    the model, which gets it back on the retry."""


def section_label(section: Section) -> str:
    """What `SourceAnchor.section` holds: the clause anchor, or '' for front matter."""
    return section.anchor or ""


def locate(quote: str, section: Section, doc: Document) -> tuple[int, int]:
    """The `(start, end)` offsets in `doc.text` of the one place `quote` appears in `section`.

    An exact match wins. Failing that, the quote may differ from the source in whitespace
    only (models collapse the blank line between paragraphs into a space); the returned
    span is then the source's own text, so `doc.text[start:end]` is still what gets cited.
    """
    hits = _find(quote, section.text)
    if len(hits) == 1:
        start, end = hits[0]
        return section.start + start, section.start + end
    where = f"section {section_label(section) or '(front matter)'}"
    if len(hits) > 1:
        raise CitationError(
            f"the quote appears {len(hits)} times in {where}; quote more of the sentence so "
            "it matches exactly one place"
        )
    elsewhere = _find(quote, doc.text)
    if elsewhere:
        other = section_at(doc, elsewhere[0][0])
        if other is not None and other.index != section.index:
            raise CitationError(
                f"the quote is from section {section_label(other) or '(front matter)'}, not "
                f"{where}; extract only requirements this section states"
            )
    raise CitationError(
        f"the quote is not in {where} verbatim; copy the source text character for "
        "character instead of paraphrasing, correcting or joining separate passages"
    )


def check_citation(req: Requirement, doc: Document) -> list[str]:
    """Everything wrong with `req.source_anchor` against `doc`; empty when it checks out."""
    a = req.source_anchor
    if a.doc != doc.doc_id:
        return [f"anchor cites document {a.doc!r}, not {doc.doc_id!r}"]
    if a.end > len(doc.text):
        return [f"span [{a.start}, {a.end}) runs past the end of the text ({len(doc.text)})"]
    actual = doc.text[a.start : a.end]
    if actual != a.quote:
        return [f"quote is not verbatim at [{a.start}, {a.end}); the source there is {actual!r}"]

    errors = []
    page = doc.page_at(a.start)
    if a.page != page:
        errors.append(f"anchor says page {a.page}, but the quote starts on page {page}")
    section = section_at(doc, a.start)
    if section is None or a.end > section.end:
        errors.append("quote is not inside a single section")
    elif a.section != section_label(section):
        errors.append(f"anchor says section {a.section!r}, but the quote is in {section.anchor!r}")
    return errors


def section_at(doc: Document, offset: int) -> Section | None:
    """The section whose text contains `doc.text[offset]`, if any."""
    i = bisect_right([s.start for s in doc.sections], offset) - 1
    if i >= 0 and offset < doc.sections[i].end:
        return doc.sections[i]
    return None


def _find(quote: str, text: str) -> list[tuple[int, int]]:
    """Every `(start, end)` where `quote` occurs: exactly, else up to whitespace."""
    quote = quote.strip()
    if not quote:
        return []
    hits = []
    i = text.find(quote)
    while i != -1:
        hits.append((i, i + len(quote)))
        i = text.find(quote, i + 1)
    if hits:
        return hits
    pattern = r"\s+".join(re.escape(word) for word in quote.split())
    return [m.span() for m in re.finditer(pattern, text)]
