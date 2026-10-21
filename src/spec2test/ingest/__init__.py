"""Ingest: read a spec (PDF, DOCX, XLSX or Markdown) into anchored clause sections."""

from __future__ import annotations

import re
from pathlib import Path

from spec2test.ingest.models import Document, PageSpan, Section
from spec2test.ingest.sections import Block, build_document

__all__ = [
    "SUFFIXES",
    "Block",
    "Document",
    "PageSpan",
    "Section",
    "UnsupportedFormatError",
    "build_document",
    "doc_id_for",
    "ingest",
]

SUFFIXES = (".pdf", ".docx", ".xlsx", ".md", ".markdown")


class UnsupportedFormatError(ValueError):
    """The file is not a format ingestion reads."""


def doc_id_for(path: str | Path) -> str:
    """Default document ID: the file name without its extension, as a lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")


def ingest(
    path: str | Path, doc_id: str | None = None, *, end_marker: str | None = None
) -> Document:
    """Read `path` and split it into clause sections anchored to page and offset.

    `end_marker` drops everything from the first block that starts with it; see
    `build_document`.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise UnsupportedFormatError(
            f"{path.name}: unsupported format {suffix or '(none)'!r}; "
            f"expected one of {', '.join(SUFFIXES)}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)

    # Docling (and torch under it) is slow to import; keep that off the CLI's fast paths.
    from spec2test.ingest import docling_blocks

    doc = docling_blocks.convert(path)
    blocks = docling_blocks.blocks(doc, rows_as_blocks=suffix == ".xlsx")
    return build_document(
        doc_id or doc_id_for(path),
        blocks,
        source=path.name,
        format=suffix.lstrip(".").replace("markdown", "md"),
        end_marker=end_marker,
    )
