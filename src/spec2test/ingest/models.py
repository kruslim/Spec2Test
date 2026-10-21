"""What ingestion produces: one canonical document text, split into clause sections.

Every offset points into `Document.text`, so a later stage can cite a quote by
`Document.text[start:end]` and check it verbatim.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageSpan(_Record):
    """The stretch of `Document.text` that came from one source page."""

    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(description="Exclusive.")


class Section(_Record):
    """One numbered clause (or the unnumbered front matter before the first clause)."""

    doc_id: str
    index: int = Field(ge=0, description="Position in `Document.sections`.")
    section_number: str | None = Field(
        description="Clause number as printed, without a trailing dot: 'S5.5.1', '5.1.1', "
        "'T.1.2', 'Annex 3'. None for front matter."
    )
    title: str | None = None
    level: int = Field(ge=0, description="Depth of the clause number; 0 for front matter.")
    parent: int | None = Field(default=None, description="`index` of the enclosing section.")
    annex: str | None = Field(
        default=None,
        description="Enclosing annex or appendix, e.g. 'Annex 3'. Annexes restart their "
        "numbering, so '2.1' inside 'Annex 3' is not the body's clause 2.1.",
    )
    page: int = Field(ge=1, description="Page the section starts on.")
    page_end: int = Field(ge=1, description="Page the section ends on.")
    start: int = Field(ge=0, description="Start offset in `Document.text`.")
    end: int = Field(description="End offset (exclusive) in `Document.text`.")
    text: str = Field(min_length=1, description="Exactly `Document.text[start:end]`.")

    @property
    def anchor(self) -> str | None:
        """The section reference to cite: '5.1.1', or 'Annex 3 2.1' inside an annex."""
        if self.annex is None or self.section_number == self.annex:
            return self.section_number
        return f"{self.annex} {self.section_number}"

    @model_validator(mode="after")
    def _spans_are_ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be greater than start ({self.start})")
        if self.page_end < self.page:
            raise ValueError(f"page_end ({self.page_end}) is before page ({self.page})")
        return self


class Document(_Record):
    doc_id: str
    source: str = Field(description="File name the document was read from.")
    format: str = Field(description="Input format, e.g. 'pdf'.")
    text: str
    pages: list[PageSpan]
    sections: list[Section]

    @model_validator(mode="after")
    def _offsets_point_into_text(self) -> Self:
        pages = {p.page for p in self.pages}
        for i, s in enumerate(self.sections):
            if s.index != i:
                raise ValueError(f"section {i} has index {s.index}")
            if s.doc_id != self.doc_id:
                raise ValueError(f"section {i} belongs to {s.doc_id!r}, not {self.doc_id!r}")
            if self.text[s.start : s.end] != s.text:
                raise ValueError(f"section {i} text does not match text[{s.start}:{s.end}]")
            if s.page not in pages or s.page_end not in pages:
                raise ValueError(f"section {i} is on a page the document does not have")
            if s.parent is not None and not 0 <= s.parent < i:
                raise ValueError(f"section {i} has parent {s.parent}, which does not precede it")
        return self

    def page_at(self, offset: int) -> int:
        """The source page `text[offset]` came from."""
        if not 0 <= offset < len(self.text):
            raise IndexError(f"offset {offset} is outside the document text")
        starts = [p.start for p in self.pages]
        return self.pages[bisect_right(starts, offset) - 1].page

    def section(self, anchor: str) -> Section:
        """The first section whose `anchor` is `anchor`."""
        for s in self.sections:
            if s.anchor == anchor:
                return s
        raise KeyError(anchor)
