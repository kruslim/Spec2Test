"""The source a reviewer reads next to an item: the requirement's section, with its quote
highlighted where its anchor points."""

from __future__ import annotations

from html import escape

from pydantic import BaseModel, ConfigDict

from spec2test.ingest import Document
from spec2test.schema import Requirement


class SourceView(BaseModel):
    """The requirement's section split around its quote: `before + quote + after` is the
    section text, or just the quote when the source is not at hand."""

    model_config = ConfigDict(extra="forbid")

    heading: str
    page: int
    before: str = ""
    quote: str
    after: str = ""
    found: bool = True
    """False when the anchor does not point at the quote in this document."""

    def html(self) -> str:
        """The section as HTML, the quote in a <mark>; newlines kept."""
        parts = [escape(self.before), f"<mark>{escape(self.quote)}</mark>", escape(self.after)]
        return "".join(parts).replace("\n", "<br>")


def source_view(req: Requirement, doc: Document | None) -> SourceView:
    """Where `req` came from in `doc`. Without the document, or when the anchor does not
    point at the quote in it (another version, another document), the view is the quote
    alone, with `found` false."""
    anchor = req.source_anchor
    heading = f"{anchor.section} (p{anchor.page})"
    alone = SourceView(heading=heading, page=anchor.page, quote=anchor.quote, found=False)
    if doc is None or doc.doc_id != anchor.doc:
        return alone
    start, end = anchor.start, anchor.end
    if doc.text[start:end] != anchor.quote:
        return alone
    section = next((s for s in reversed(doc.sections) if s.start <= start < s.end), None)
    if section is None:
        return alone.model_copy(update={"found": True})
    if section.title:
        heading = f"{section.anchor or '-'} {section.title} (p{anchor.page})"
    return SourceView(
        heading=heading,
        page=anchor.page,
        before=doc.text[section.start : start],
        quote=anchor.quote,
        after=doc.text[end : max(end, section.end)],
    )
