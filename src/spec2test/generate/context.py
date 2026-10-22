"""The source text a requirement's item is written from: its own clause and the clauses it
points to ("when tested in accordance with S14.1").

Generation and the judge both read it, so the judge checks an item against what its
writer was given.
"""

from __future__ import annotations

import re

from spec2test.extract import section_at
from spec2test.ingest import Document, Section
from spec2test.schema import Requirement

MAX_REFERENCES = 5
"""Most referenced clauses included, in the order the requirement mentions them."""
MAX_CLAUSE_CHARS = 4000
"""A longer referenced clause, with its subclauses, is cut off here."""

# A bare number such as "2.1" is a clause reference only after a word that says so;
# "S14.1" or "T.1.2" is one on its own.
_REFERENCE_WORD = r"(?:paragraphs?|clauses?|sections?|subsections?|§)\s*"


def source_context(doc: Document, req: Requirement) -> str | None:
    """The requirement's clause and up to `MAX_REFERENCES` clauses it references, as prompt
    text; None when `req` is not from `doc`."""
    if req.source_anchor.doc != doc.doc_id:
        return None
    own = section_at(doc, req.source_anchor.start)
    if own is None:
        return None
    parts = [_clause("clause", own, own.text)]
    for ref in references(doc, req, own)[:MAX_REFERENCES]:
        text = doc.text[ref.start : _subtree_end(doc, ref)]
        if len(text) > MAX_CLAUSE_CHARS:
            text = text[:MAX_CLAUSE_CHARS].rstrip() + " [...]"
        parts.append(_clause("referenced_clause", ref, text))
    return "\n".join(parts)


def references(doc: Document, req: Requirement, own: Section | None = None) -> list[Section]:
    """Sections of `doc` that `req` refers to by number, in the order it first mentions
    them; never its own clause or a clause that encloses it."""
    if own is None:
        own = section_at(doc, req.source_anchor.start)
    enclosing = set()
    current = own
    while current is not None:
        enclosing.add(current.index)
        current = None if current.parent is None else doc.sections[current.parent]
    text = " ".join([req.text, req.source_anchor.quote, *req.conditions])
    found: dict[int, int] = {}
    for s in doc.sections:
        if s.anchor is None or s.index in enclosing or s.index in found:
            continue
        if m := _mention(s.anchor).search(text):
            found[s.index] = m.start()
    return [doc.sections[i] for i in sorted(found, key=found.__getitem__)]


def _mention(anchor: str) -> re.Pattern[str]:
    body = re.escape(anchor).replace(r"\ ", r"\s+")
    prefix = _REFERENCE_WORD if re.fullmatch(r"[\d.]+", anchor) else ""
    # Not part of a longer number or clause: "S14.1" is not in "S14.12" or "S14.1.2".
    return re.compile(rf"(?<![\w.]){prefix}{body}(?!\w|\.\d)", re.IGNORECASE)


def _subtree_end(doc: Document, section: Section) -> int:
    """Where `section` and its subclauses end: its subclauses follow it directly."""
    end = section.end
    inside = {section.index}
    for s in doc.sections[section.index + 1 :]:
        if s.parent not in inside:
            break
        inside.add(s.index)
        end = s.end
    return end


def _clause(tag: str, section: Section, text: str) -> str:
    heading = " ".join(p for p in [section.anchor or "(front matter)", section.title] if p)
    return f'<{tag} heading="{heading}">\n{text.strip()}\n</{tag}>'
