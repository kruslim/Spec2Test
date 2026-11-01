"""Input adapters: each one reads a file format into reading-order `Block`s.

The core turns blocks into the `Document` (clause sections, page spans, offsets), so an
adapter only has to say what text is on which page. Built in: Docling (PDF, DOCX, XLSX,
Markdown) and plain text. More come from the `spec2test.adapters` entry point; see
`spec2test.plugins` and docs/USAGE.md, "Writing an adapter".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from spec2test.ingest.sections import Block
from spec2test.plugins import ADAPTER_GROUP, PluginError, Registry, check_name


@runtime_checkable
class InputAdapter(Protocol):
    name: str
    """Short name, e.g. 'docling'; `ingest(..., adapter=name)` picks it explicitly."""
    suffixes: tuple[str, ...]
    """Lowercase file suffixes it reads, with the dot: ('.rtf',)."""

    def blocks(self, path: Path) -> Iterable[Block]:
        """The file's paragraphs, headings, list items and tables, in reading order, each
        with its 1-based page. Raise ValueError for a file it cannot read."""
        ...


def _check(plugin: object) -> InputAdapter:
    check_name(plugin, "input adapter")
    if not isinstance(plugin, InputAdapter):
        raise PluginError(f"input adapter {plugin.name!r} needs `suffixes` and `blocks(path)`")  # type: ignore[attr-defined]
    suffixes = plugin.suffixes
    if (
        isinstance(suffixes, str)
        or not suffixes
        or not all(
            isinstance(s, str) and s.startswith(".") and s == s.lower() and len(s) > 1
            for s in suffixes
        )
    ):
        raise PluginError(
            f"input adapter {plugin.name!r}: `suffixes` must be lowercase '.ext' strings, "
            f"got {suffixes!r}"
        )
    return plugin


ADAPTERS: Registry[InputAdapter] = Registry(ADAPTER_GROUP, "input adapter", _check)


def adapter_for(suffix: str) -> InputAdapter | None:
    """The adapter that reads `suffix`: a built-in first, then plugins in load order."""
    suffix = suffix.lower()
    adapters = ADAPTERS.all().values()
    return next((a for a in adapters if suffix in a.suffixes), None)


def suffixes() -> tuple[str, ...]:
    """Every suffix some adapter reads, in registration order."""
    seen: dict[str, None] = {}
    for a in ADAPTERS.all().values():
        seen.update(dict.fromkeys(a.suffixes))
    return tuple(seen)


class DoclingAdapter:
    """PDF, DOCX, XLSX and Markdown through Docling. A spreadsheet's rows become blocks of
    their own, so a sheet with one numbered requirement per row splits into clauses."""

    name = "docling"
    suffixes = (".pdf", ".docx", ".xlsx", ".md", ".markdown")

    def blocks(self, path: Path) -> list[Block]:
        # Docling (and torch under it) is slow to import; keep that off the CLI's fast paths.
        from spec2test.ingest import docling_blocks

        doc = docling_blocks.convert(path)
        return docling_blocks.blocks(doc, rows_as_blocks=path.suffix.lower() == ".xlsx")


class TextAdapter:
    """Plain UTF-8 text. Blank lines separate paragraphs, each one a block; the lines of a
    paragraph join with spaces. A form feed starts a new page. Write each clause as its own
    paragraph: '2.1 Brightness. The lamp shall ...'."""

    name = "text"
    suffixes = (".txt",)

    def blocks(self, path: Path) -> list[Block]:
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        out = []
        for page, page_text in enumerate(text.split("\f"), start=1):
            for para in re.split(r"\n[ \t]*\n", page_text):
                joined = " ".join(line.strip() for line in para.splitlines() if line.strip())
                if joined:
                    out.append(Block("text", joined, page))
        return out


ADAPTERS.register(DoclingAdapter(), builtin=True)
ADAPTERS.register(TextAdapter(), builtin=True)
