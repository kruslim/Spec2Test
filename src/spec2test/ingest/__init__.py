"""Ingest: read a spec (PDF, DOCX, XLSX, Markdown, plain text or a plugin's format) into
anchored clause sections."""

from __future__ import annotations

import re
from pathlib import Path

from spec2test.ingest.adapters import (
    ADAPTERS,
    DoclingAdapter,
    InputAdapter,
    TextAdapter,
    adapter_for,
    suffixes,
)
from spec2test.ingest.models import Document, PageSpan, Section
from spec2test.ingest.sections import Block, build_document

__all__ = [
    "ADAPTERS",
    "Block",
    "DoclingAdapter",
    "Document",
    "InputAdapter",
    "PageSpan",
    "Section",
    "TextAdapter",
    "UnsupportedFormatError",
    "adapter_for",
    "build_document",
    "doc_id_for",
    "ingest",
    "suffixes",
]


class UnsupportedFormatError(ValueError):
    """The file is not a format any input adapter reads."""


def doc_id_for(path: str | Path) -> str:
    """Default document ID: the file name without its extension, as a lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")


def ingest(
    path: str | Path,
    doc_id: str | None = None,
    *,
    end_marker: str | None = None,
    adapter: str | None = None,
) -> Document:
    """Read `path` and split it into clause sections anchored to page and offset.

    The input adapter is the one registered for the file's suffix, or the one named
    `adapter`. `end_marker` drops everything from the first block that starts with it; see
    `build_document`.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    reader = ADAPTERS.get(adapter) if adapter is not None else adapter_for(suffix)
    if reader is None:
        raise UnsupportedFormatError(
            f"{path.name}: unsupported format {suffix or '(none)'!r}; "
            f"expected one of {', '.join(suffixes())}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)

    blocks = list(reader.blocks(path))
    if not blocks:
        raise ValueError(f"{path.name}: the {reader.name} adapter found no text")
    return build_document(
        doc_id or doc_id_for(path),
        blocks,
        source=path.name,
        format=suffix.lstrip(".").replace("markdown", "md") or reader.name,
        end_marker=end_marker,
    )
