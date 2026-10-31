"""The source context a requirement needs to be verified, beyond its own sentence.

`source_context` collects, from the requirement's document:

- headings: the titles of its section and the sections enclosing it. "S5.2 Outside
  rearview mirror-driver's side" is what says which mirror "the mirror" is.
- section: the whole text of its own section, when it says more than the requirement's
  quote ("The average reflectance ... shall be determined in accordance with SAE J964").
- definitions: the entries of the document's definitions section whose term the
  requirement uses ("Backing event means ...").
- references: the clauses it points to ("tested in accordance with S14.1", "S5.5.1
  through S5.5.7"), each with its subclauses: the procedure or limits a test case has to
  follow.

The generator and the judge see the same context, and a calibration sample stores it with
each item so the person labeling sees it too.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from spec2test.ingest import Document, Section
from spec2test.schema import Requirement

MAX_REFERENCES = 12
"""Most referenced clauses sent with one requirement."""
MAX_REFERENCE_CHARS = 16_000
"""Longest text sent for one referenced clause, its subclauses included."""
MAX_CONTEXT_CHARS = 40_000
"""Most referenced-clause text sent with one requirement, all clauses together."""

_RANGE = re.compile(r"(\S+)\s+(?:through|thru|to)\s+(\S+)", re.IGNORECASE)
_CLAUSE = re.compile(r"[A-Z]+\.?\d+(?:\.\d+)*|\d+(?:\.\d+)+", re.IGNORECASE)
"""A token that reads as a clause number on its own: 'S5', 'T.1.2', '5.1.1', '2.1'. A bare
'2' does not ("shall have 2 bulbs"); it counts only after a word that names a clause."""
_CUES = {"paragraph", "paragraphs", "para", "paras", "clause", "clauses", "section"}
_CUES |= {"sections", "§", "§§", "item", "items", "point", "points"}
_ANNEX = {"annex", "appendix"}
_DEFINITIONS_TITLE = re.compile(r"\bdefinitions?\b", re.IGNORECASE)
_DEFINES = re.compile(r"^(?P<term>[^\n]{1,80}?)\s+(?:means|is defined as|refers to)\b")


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Clause(_Record):
    anchor: str = Field(description="Section anchor, or the defined term for a definition.")
    text: str


class SourceContext(_Record):
    headings: list[str] = Field(
        default_factory=list, description="Enclosing section headings, outermost first."
    )
    section: Clause | None = Field(
        default=None,
        description="The requirement's whole section, when it says more than the quote: a "
        "sibling sentence often fixes how or from where it is measured.",
    )
    definitions: list[Clause] = Field(default_factory=list)
    references: list[Clause] = Field(default_factory=list)


def source_context(req: Requirement, doc: Document) -> SourceContext:
    section = _own_section(req, doc)
    own = None
    if section is not None and " ".join(section.text.split()) != " ".join(
        req.source_anchor.quote.split()
    ):
        text = section.text
        if len(text) > MAX_REFERENCE_CHARS:
            text = text[:MAX_REFERENCE_CHARS] + " [...]"
        own = Clause(anchor=section.anchor or "", text=text)
    return SourceContext(
        headings=[] if section is None else _headings(section, doc),
        section=own,
        definitions=definitions(req, doc),
        references=references(req, doc),
    )


def render_context(ctx: SourceContext, *, headings: bool = True) -> str:
    """The context as prompt text; empty when there is none."""
    lines = []
    if headings and ctx.headings:
        lines.append(f"Within: {' > '.join(ctx.headings)}")
    if ctx.section is not None:
        lines += [
            "",
            f'<source_section anchor="{ctx.section.anchor}">',
            ctx.section.text,
            "</source_section>",
        ]
    for d in ctx.definitions:
        lines += ["", f'<definition term="{d.anchor}">', d.text, "</definition>"]
    for r in ctx.references:
        lines += ["", f'<referenced_clause anchor="{r.anchor}">', r.text, "</referenced_clause>"]
    return "\n".join(lines).strip("\n")


def references(req: Requirement, doc: Document) -> list[Clause]:
    """The clauses `req` points to, with their subclauses, in the order it names them.

    "S5.5.1 through S5.5.7" names every clause from S5.5.1 to S5.5.7 at that level. A
    clause inside another one already sent is not sent again; the requirement's own
    section is never sent.
    """
    anchors = {s.anchor: s for s in doc.sections if s.anchor}
    own = req.source_anchor.section
    own_section = _own_section(req, doc)
    annex = None if own_section is None else own_section.annex
    named: list[Section] = []
    for anchor in _named_anchors(_words(req), anchors, doc, annex):
        section = anchors[anchor]
        if anchor != own and section not in named:
            named.append(section)
    named = [s for s in named if not any(_descends(s, other, doc) for other in named)]

    out: list[Clause] = []
    budget = MAX_CONTEXT_CHARS
    for s in named[:MAX_REFERENCES]:
        text = _with_subclauses(s, doc)
        if len(text) > MAX_REFERENCE_CHARS:
            text = text[:MAX_REFERENCE_CHARS] + " [...]"
        if budget <= 0:
            break
        if len(text) > budget:
            text = text[:budget] + " [...]"
        budget -= len(text)
        out.append(Clause(anchor=s.anchor or "", text=text))
    return out


def definitions(req: Requirement, doc: Document) -> list[Clause]:
    """Entries of the document's definitions sections whose term `req` uses."""
    words = " ".join(_words(req).casefold().split())
    out = []
    for term, text in _glossary(doc):
        if re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", words):
            out.append(Clause(anchor=term, text=text))
    return out


def _glossary(doc: Document) -> list[tuple[str, str]]:
    """(term, definition) pairs: each paragraph of a definitions section that starts
    "<term> means ...", with the paragraphs that follow it until the next one."""
    entries: list[tuple[str, list[str]]] = []
    for s in doc.sections:
        if not s.title or not _DEFINITIONS_TITLE.search(s.title):
            continue
        for para in (p.strip() for p in s.text.split("\n\n")):
            if m := _DEFINES.match(para):
                entries.append((m["term"].strip(), [para]))
            elif entries and para:
                entries[-1][1].append(para)
    return [(term, "\n\n".join(paras)) for term, paras in entries]


def _named_anchors(
    words: str, anchors: dict[str, Section], doc: Document, annex: str | None = None
) -> list[str]:
    """The anchors `words` names, in order. A clause number resolves inside the annex it
    is said to be in ("paragraph 2.1 of Annex 3", "Annex 3, paragraph 2.1"), else inside
    the requirement's own `annex`, else in the body: annexes restart their numbering."""
    tokens = [_strip(t) for t in words.split()]
    out: list[str] = []
    for i, token in enumerate(tokens):
        if token.casefold() in _ANNEX and i + 1 < len(tokens):
            if (name := _annex(token, tokens[i + 1], anchors)) is not None and not _locates(
                tokens, i
            ):
                out.append(name)
            continue
        cued = i > 0 and tokens[i - 1].casefold() in _CUES
        if not (_CLAUSE.fullmatch(token) or (cued and token.isdigit())):
            continue
        if i > 0 and tokens[i - 1].casefold() in _ANNEX:
            continue  # the number of "Annex 3", named above
        if (anchor := _resolve(token, _annex_near(tokens, i, anchors) or annex, anchors)) is None:
            continue
        out.append(anchor)
        out += _range_from(words, token, anchor, anchors, doc)
    return out


def _locates(tokens: list[str], i: int) -> bool:
    """Whether "Annex 3" at `tokens[i]` only says where a clause is, as in "2.1 of Annex 3"
    or "Annex 3, paragraph 2.1": then the clause is the reference, not the whole annex."""
    before = tokens[max(i - 2, 0) : i]
    if len(before) == 2 and before[1].casefold() in ("of", "in") and _CLAUSE.fullmatch(before[0]):
        return True
    return any(_CLAUSE.fullmatch(t) for t in tokens[i + 2 : i + 4])


def _annex(word: str, number: str, anchors: dict[str, Section]) -> str | None:
    """The anchor of "Annex 3" or "appendix 3" as the document spells it, if it has one."""
    wanted = f"{word} {number}".casefold()
    return next((a for a in anchors if a.casefold() == wanted), None)


def _annex_near(tokens: list[str], i: int, anchors: dict[str, Section]) -> str | None:
    """The annex a clause number at `tokens[i]` is said to be in: "2.1 of Annex 3" just
    after it, or "Annex 3, paragraph 2.1" just before it."""
    after = tokens[i + 1 : i + 4]
    if len(after) >= 3 and after[0].casefold() in ("of", "in") and after[1].casefold() in _ANNEX:
        return _annex(after[1], after[2], anchors)
    for j in range(max(i - 3, 0), i - 1):
        if tokens[j].casefold() in _ANNEX:
            return _annex(tokens[j], tokens[j + 1], anchors)
    return None


def _resolve(number: str, annex: str | None, anchors: dict[str, Section]) -> str | None:
    if annex is not None and f"{annex} {number}" in anchors:
        return f"{annex} {number}"
    return number if number in anchors else None


def _range_from(
    words: str, token: str, first: str, anchors: dict[str, Section], doc: Document
) -> list[str]:
    """The clauses after `first` in "S5.5.1 through S5.5.7", when `token` starts a range."""
    for m in _RANGE.finditer(words):
        if _strip(m[1]) != token:
            continue
        last = _resolve(_strip(m[2]), anchors[first].annex, anchors)
        if last is not None:
            return [s.anchor for s in _span(anchors[first], anchors[last], doc) if s.anchor]
    return []


def _span(first: Section, last: Section, doc: Document) -> list[Section]:
    """`first`, `last` and the clauses between them at the same level."""
    if last.index < first.index:
        return []
    return [s for s in doc.sections[first.index : last.index + 1] if s.parent == first.parent]


def _own_section(req: Requirement, doc: Document) -> Section | None:
    for s in doc.sections:
        if s.start <= req.source_anchor.start < s.end:
            return s
    return None


def _headings(section: Section, doc: Document) -> list[str]:
    chain = []
    current: Section | None = section
    while current is not None:
        chain.append(" ".join(p for p in (current.anchor, current.title) if p))
        current = None if current.parent is None else doc.sections[current.parent]
    return [h for h in reversed(chain) if h]


def _words(req: Requirement) -> str:
    return " ".join([req.text, req.source_anchor.quote, *req.conditions])


def _strip(token: str) -> str:
    return token.strip(".,;:()")


def _with_subclauses(section: Section, doc: Document) -> str:
    end = section.end
    for s in doc.sections[section.index + 1 :]:
        if not _descends(s, section, doc):
            break
        end = s.end
    return doc.text[section.start : end]


def _descends(s: Section, ancestor: Section, doc: Document) -> bool:
    parent = s.parent
    while parent is not None:
        if parent == ancestor.index:
            return True
        parent = doc.sections[parent].parent
    return False
