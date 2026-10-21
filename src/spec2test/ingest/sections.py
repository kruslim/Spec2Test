"""Turn reading-order text blocks into one document text split into clause sections.

Pure: nothing here knows about Docling, so the clause rules are tested on plain blocks.

A clause number opening a block starts a new section:

    S5.5.1 Field of view. ...        FMVSS (US federal)
    5.1.1. The device shall ...      UNECE
    4.2.1 Factors of Safety          NASA
    GR - General Regulations         FSAE chapter, then GR.1, GR.1.1, ...
    T.1.2.3 ... / EV.5.1 ...         FSAE rules
    Part I Devices for ...           a division; the numbering carries on through it
    Annex 3 / Appendix A             restarts the numbering inside it

Lettered sub-items such as "(a)" or "a." stay inside their clause.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from spec2test.ingest.models import Document, PageSpan, Section

BlockKind = Literal["heading", "text", "list", "table"]


@dataclass(frozen=True)
class Block:
    """One paragraph, heading, list item or table, in reading order."""

    kind: BlockKind
    text: str
    page: int = 1
    margin: bool = False
    """True when the block sits in the top or bottom margin of its page."""


_CLAUSE = re.compile(
    r"""
    (?P<number>
        (?P<prefix>S(?=\d)|[A-Z]{1,3}\.)?   # FMVSS 'S5', FSAE 'T.1' or 'EV.5'
        \d{1,3}(?:\.\d{1,3})*               # 1-3 digits per part, so years never match
        (?:\([a-z0-9]{1,3}\))*              # 'S5.5.1(a)'
    )
    \.?                                     # UNECE writes '5.1.1.'
    (?:[ \t]+(?=[A-Z"'(\[\u201c\u2018])|[ \t]*$)  # then a capital, quote, bracket or the end
    """,
    re.VERBOSE,
)
_ANNEX = re.compile(r"(?P<word>(?i:annex|appendix))[ \t]+(?P<id>[A-Z]{1,2}|\d{1,2})\b")
_DIVISION = re.compile(r"(?i:part)[ \t]+(?P<id>[IVX]{1,4}|\d{1,2})\b")
_CHAPTER = re.compile(r"(?P<id>[A-Z]{1,3})[ \t]+[-\u2013\u2014][ \t]+(?P<title>[A-Z].*)")
_APPENDIX_OF_ANNEX = re.compile(r"(?i:appendix)(?:[ \t]+(?P<id>[A-Z]{1,2}|\d{1,2}))?")
_LABEL = re.compile(r"(?i:figure|fig\.|table)[ \t]+\w")
_FORM_FIELD = re.compile(r"(\.{4,}|\u2026+)\s*$")
_PART = re.compile(r"\d+|\([a-z0-9]+\)")
_DOT_LEADER = re.compile(r"(\.\s?){4,}\s*\d+\s*$")
_LEADING_PHRASE = re.compile(r"(?P<title>[A-Z][^.\n]{0,80}?)\.(?:\s|$)")
_ENUMERATOR = re.compile(r"[a-z]{1,4}[.)]\s")  # 'a. ', 'iv) '
_MODAL = re.compile(r"\b(shall|should|may|must|will)\b", re.IGNORECASE)
_MAX_ANNEX_HEADING = 100
_MAX_TITLE_WORDS = 10
_MAX_HEADING_CHARS = 100
_FURNITURE_PAGES = 3
"""A line repeated on this many pages is a running head or footer, not content."""


@dataclass(frozen=True)
class _Clause:
    number: str
    key: tuple[str | int, ...]
    title: str | None
    letter: str | None = None
    """Letter prefix of a lettered clause: 'T' for 'T.1.2', 'A' for appendix clause 'A.1'."""
    annex_id: str | None = None
    """Set only for an annex or appendix heading: '3' for 'Annex 3'."""
    division: bool = False
    """A 'Part I' heading: a section of its own, without restarting the numbering."""


def build_document(
    doc_id: str,
    blocks: Iterable[Block],
    *,
    source: str = "",
    format: str = "",
    end_marker: str | None = None,
) -> Document:
    """Join `blocks` into `Document.text` and split it into clause sections.

    `end_marker` drops everything from the first block that starts with it, for files
    that carry the start of the next document (e.g. a CFR section PDF whose last page
    begins the next section).
    """
    kept = _until(_drop_furniture(_clean(blocks)), end_marker)
    if not kept:
        raise ValueError(f"{doc_id!r} has no text")
    text, spans, continues = _assemble(kept)

    sections: list[Section] = []
    seen: dict[tuple[str | int, ...], int] = {}  # clause key -> section index
    annex: tuple[str, int, str] | None = None  # (number, section index, id)
    annex_root: tuple[str, int] | None = None  # the last 'Annex N', which owns appendices
    division: int | None = None  # section index of the current 'Part'

    def open_section(i: int, clause: _Clause | None, title: str | None = None) -> None:
        nonlocal annex, annex_root, division
        number, level, parent, scope = None, 0, None, None
        if clause is not None and clause.annex_id is not None:
            seen.clear()
            number, title, level = clause.number, clause.title, 1
            # UNECE annexes carry their own appendices: 'Appendix 1' to Annex 8.
            if annex_root and number.startswith("Appendix"):
                number = f"{annex_root[0]} {number}"
            if annex_root and number.startswith(annex_root[0] + " "):
                parent, level = annex_root[1], 2
            elif number.startswith("Annex"):
                annex_root = (number, len(sections))
            annex, division, scope = (number, len(sections), clause.annex_id), None, number
        elif clause is not None and clause.division:
            division = len(sections)
            number, title = clause.number, clause.title
            scope = annex[0] if annex else None
        elif clause is not None:
            number, title = clause.number, clause.title
            level = sum(1 for part in clause.key if isinstance(part, int) or "(" in part)
            level += clause.letter is not None  # 'GR' is a level of its own above 'GR.1'
            # 'B.1' belongs to Appendix B even when it is read before the 'APPENDIX B' heading.
            in_annex = annex is not None and clause.letter in (None, annex[2])
            scope = annex[0] if annex and in_annex else None
            parent = next(
                (
                    seen[clause.key[:k]]
                    for k in range(len(clause.key) - 1, 0, -1)
                    if clause.key[:k] in seen
                ),
                annex[1] if annex and in_annex else division,
            )
            seen[clause.key] = len(sections)
        start, end, page = spans[i]
        sections.append(
            Section(
                doc_id=doc_id,
                index=len(sections),
                section_number=number,
                title=title,
                level=level,
                parent=parent,
                annex=scope,
                page=page,
                page_end=page,
                start=start,
                end=end,
                text=text[start:end],
            )
        )

    numbered = False
    for i, block in enumerate(kept):
        clause = None if continues[i] else _clause(block)
        if clause is not None:
            numbered = True
            open_section(i, clause)
        elif not sections or (block.kind == "heading" and not numbered and not continues[i]):
            # Front matter (title page, foreword) gets a section per heading; after the
            # first clause, unnumbered headings such as table titles stay in their clause.
            open_section(i, None, _first_line(block.text) if block.kind == "heading" else None)
        else:
            last = sections[-1]
            _, end, page = spans[i]
            update = {"end": end, "page_end": page, "text": text[last.start : end]}
            if block.kind == "heading" and last.title is None and last.anchor == last.annex:
                # "Annex 2" on one line, its title on the next.
                update["title"] = _first_line(block.text)
            sections[-1] = last.model_copy(update=update)

    return Document(
        doc_id=doc_id,
        source=source,
        format=format,
        text=text,
        pages=_page_spans(spans, len(text)),
        sections=sections,
    )


def _clean(blocks: Iterable[Block]) -> list[Block]:
    out = []
    for b in blocks:
        text = b.text.strip()
        if not text or (b.kind != "table" and _DOT_LEADER.search(text)):
            continue  # empty, or a table-of-contents line
        out.append(Block(b.kind, text, b.page, b.margin))
    return out


def _drop_furniture(blocks: list[Block]) -> list[Block]:
    """Drop running heads, footers and print slugs that repeat across pages.

    Docling labels most furniture itself; this catches what it reads as body text, such
    as a CFR page's 'VerDate ...' slug or a running head placed between two columns.
    """
    margin_pages: dict[str, set[int]] = defaultdict(set)
    short_pages: dict[str, set[int]] = defaultdict(set)

    def margin_key(b: Block) -> str:
        # Digits are masked so 'Page 6 of 145' matches 'Page 7 of 145', but never for a
        # heading or a label: 'Annex 2' and 'Annex 3', or 'Figure 1' and 'Figure 2', differ.
        if _clause(b) is not None or _LABEL.match(b.text):
            return b.text
        return re.sub(r"\d+", "#", b.text)

    for b in blocks:
        if b.kind == "table":
            continue
        if b.margin:
            margin_pages[margin_key(b)].add(b.page)
        if 8 <= len(b.text) <= 40:
            short_pages[b.text].add(b.page)

    def is_furniture(b: Block) -> bool:
        if b.kind == "table":
            return False
        # Once a text repeats in the margins, every copy goes, including copies Docling
        # read as mid-page.
        repeats = len(margin_pages.get(margin_key(b), ()))
        return max(repeats, len(short_pages.get(b.text, ()))) >= _FURNITURE_PAGES

    return [b for b in blocks if not is_furniture(b)]


def _until(blocks: list[Block], end_marker: str | None) -> list[Block]:
    if end_marker is None:
        return blocks
    for i, b in enumerate(blocks):
        if b.text.startswith(end_marker):
            return blocks[:i]
    raise ValueError(f"end marker {end_marker!r} not found")


def _assemble(blocks: Sequence[Block]) -> tuple[str, list[tuple[int, int, int]], list[bool]]:
    """Join blocks with blank lines, rejoining sentences that a page or column broke.

    Returns the text, each block's (start, end, page) and whether it continues the
    previous block. Pages never go backwards, so each page is one contiguous span.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    continues: list[bool] = []
    length = 0
    page = 1
    prev: Block | None = None
    for b in blocks:
        page = max(page, b.page)
        joined = prev is not None and _is_continuation(prev, b)
        if joined and parts[-1].endswith("-") and parts[-1][-2:-1].islower():
            # "in-" + "stalled": a word hyphenated across the break.
            parts[-1] = parts[-1][:-1]
            length -= 1
            start, end, p = spans[-1]
            spans[-1] = (start, end - 1, p)
            sep = ""
        elif joined:
            sep = " "
        else:
            sep = "\n\n" if parts else ""
        parts.append(sep + b.text)
        start = length + len(sep)
        length = start + len(b.text)
        spans.append((start, length, page))
        continues.append(joined)
        prev = b
    return "".join(parts), spans, continues


def _is_continuation(prev: Block, b: Block) -> bool:
    """True when `b` finishes the sentence `prev` started before a page or column break."""
    if "heading" in (prev.kind, b.kind) or "table" in (prev.kind, b.kind):
        return False
    if _ENUMERATOR.match(b.text):
        return False
    return b.text[0].islower() and prev.text[-1] not in ".:;!?"


def _page_spans(spans: list[tuple[int, int, int]], length: int) -> list[PageSpan]:
    starts: list[tuple[int, int]] = []  # (page, start)
    for start, _, page in spans:
        if not starts:
            starts.append((page, 0))
        elif page != starts[-1][0]:
            starts.append((page, start))
    ends = [s for _, s in starts[1:]] + [length]
    return [PageSpan(page=p, start=s, end=e) for (p, s), e in zip(starts, ends, strict=True)]


def _clause(block: Block) -> _Clause | None:
    if block.kind == "table":
        return None
    line = _first_line(block.text)
    annex = _ANNEX.match(line)
    if annex and (block.kind == "heading" or len(line) <= _MAX_ANNEX_HEADING):
        annex_id = annex["id"]
        number = f"{annex['word'].capitalize()} {annex_id}"
        rest = line[annex.end() :].strip(" \t-\u2013\u2014:.")
        appendix = _APPENDIX_OF_ANNEX.fullmatch(rest)
        if appendix:  # "Annex 4 - Appendix": the appendix to Annex 4
            number, annex_id = f"{number} {rest.title()}", appendix["id"] or annex_id
            rest = ""
        return _Clause(number, (number,), rest or None, annex_id=annex_id)

    heading = block.kind == "heading"
    division = _DIVISION.match(line)
    if division:
        rest = line[division.end() :].strip(" \t-\u2013\u2014:.")
        if heading or (rest and _looks_like_heading(rest)):
            return _Clause(f"Part {division['id']}", (), rest or None, division=True)
    chapter = _CHAPTER.fullmatch(line) if heading else None
    if chapter:
        letter = chapter["id"]
        return _Clause(letter, (letter,), chapter["title"].strip(), letter=letter)

    m = _CLAUSE.match(line)
    if m is None:
        return None
    number, prefix = m["number"], m["prefix"]
    rest = line[m.end() :].strip()
    if _FORM_FIELD.search(rest):
        return None  # "3.1. Location of that marking: ......" is a blank to fill in
    parts = _PART.findall(number)
    numbers = [int(p) for p in parts if p.isdigit()]
    if 0 in numbers[1:]:
        return None  # "1.0" is a version, "2.0 s" a value; clause parts start at 1
    headingish = heading or _looks_like_heading(rest)
    if prefix is None and len(parts) == 1 and not heading:
        # A bare number in running text is usually a list item or a value ("240 MPa").
        # UNECE writes its top-level clauses "1. Scope"; a spreadsheet row "1  SCOPE".
        dotted = line[len(number) : len(number) + 1] == "."
        if not (_is_caps(rest) or (dotted and headingish)):
            return None
    # FMVSS writes 'S5.1' (just a prefix); FSAE's static events chapter is 'S.1' (a letter).
    letter = prefix.rstrip(".") if prefix and prefix.endswith(".") else None
    key = ((prefix.rstrip("."),) if prefix else ()) + tuple(
        int(p) if p.isdigit() else p for p in parts
    )
    return _Clause(number, key, _title(rest, heading=headingish), letter=letter)


def _looks_like_heading(rest: str) -> bool:
    """'Scope' or 'SPECIFICATIONS' after a clause number, rather than a sentence."""
    if _is_caps(rest):
        return True
    return (
        0 < len(rest) <= _MAX_HEADING_CHARS
        and rest[0].isupper()
        and rest[-1] not in ".:;,"
        and ":" not in rest
        and not _MODAL.search(rest)
    )


def _title(rest: str, *, heading: bool) -> str | None:
    if not rest:
        return None
    if heading:
        return rest.rstrip(" .:") or None
    m = _LEADING_PHRASE.match(rest)
    if m is None:
        return None
    title = m["title"].strip()
    if len(title.split()) > _MAX_TITLE_WORDS or _MODAL.search(title):
        return None
    return title


def _is_caps(text: str) -> bool:
    """An all-caps heading such as 'SCOPE', not a unit expression such as 'V DC < U'."""
    words = re.findall(r"[^\W\d_]+", text)
    return any(len(w) >= 3 for w in words) and all(w.isupper() for w in words)


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()
