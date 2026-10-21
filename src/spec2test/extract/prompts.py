"""Prompts for extraction. The FakeLLM fixtures match on phrases from these, so a wording
change here may need the same change in `tests/fixtures/llm/extract_*.json`."""

from __future__ import annotations

from spec2test.extract.models import ExtractedRequirement
from spec2test.ingest import Document, Section

SYSTEM = """\
You extract requirements from one section of a specification, for engineers who will \
write a checklist item or test case for each one.

What counts:
- A requirement obliges, recommends or permits something: "shall", "must", "is required \
to", "should", "may". Skip headings, definitions ("X means ..."), scope and purpose \
statements, notes, examples, and cross-references that add no obligation of their own.
- Extract only what this section states. The enclosing clauses are given for context only.

For each requirement:
- quote: copy the source text that states it, character for character: same spelling, \
punctuation, symbols, capitals and numbering. Never paraphrase, fix typos, expand \
abbreviations or splice separate passages together. Quote enough to match one place in \
the section.
- One record per obligation. Split compound requirements: a sentence that obliges several \
things ("shall be red and shall carry a label", a list of (a), (b), (c) items that must \
all hold) becomes one record each. Each record quotes the passage that states its own \
obligation; records may share a quote when one sentence states several. Do not split \
alternatives ("either X or Y", "one of the following"): meeting any one option satisfies \
the requirement, so it is one record.
- text: that one obligation as a self-contained sentence, keeping the source's wording; \
restore the subject when the quote leaves it implicit.
- strength: "shall" for shall, must, is required to; "should" for should, is recommended; \
"may" for may, is permitted.
- type: performance when it sets a measurable level; safety when it protects people; \
interface when it concerns a connection to another system; regulatory when it requires \
compliance, marking or approval; functional otherwise.
- criteria: each measurable value the source states. comparator is lt, le, eq, ge, gt or \
between (lower bound in value, upper bound in upper_value); write value, unit and \
tolerance as the source writes them. Leave criteria empty rather than invent a value.
- conditions: the circumstances or states under which it applies, if the source states any.
- verification_method: inspection (look at it or check a document), test (measure under \
controlled conditions), demonstration (operate it and observe), or analysis (calculation \
or modelling).

Return an empty list if the section states no requirement."""


def extract_prompt(doc: Document, section: Section) -> str:
    return f"{_context(doc, section)}\n\nExtract every requirement this section states."


def retry_prompt(
    doc: Document, section: Section, draft: ExtractedRequirement, errors: list[str]
) -> str:
    problems = "\n".join(f"- {e}" for e in errors)
    return f"""\
{_context(doc, section)}

A requirement you extracted from this section failed the citation check.

<record>
{draft.model_dump_json(indent=2)}
</record>

Problem:
{problems}

Return this requirement again with a quote copied verbatim from the section above. \
Return one record per obligation if it is compound, or an empty list if this section \
does not state it."""


def _context(doc: Document, section: Section) -> str:
    lines = [f"Document: {doc.doc_id}"]
    ancestors = []
    parent = section.parent
    while parent is not None:
        ancestors.append(_heading(doc.sections[parent]))
        parent = doc.sections[parent].parent
    if ancestors:
        lines.append(f"Within: {' > '.join(reversed(ancestors))}")
    lines.append(f"Section: {_heading(section)}")
    return "\n".join(lines) + f"\n\n<section>\n{section.text}\n</section>"


def _heading(section: Section) -> str:
    parts = [section.anchor or "(front matter)", section.title or ""]
    return " ".join(p for p in parts if p)
