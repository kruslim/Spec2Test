"""Read a file with Docling and flatten it into reading-order `Block`s."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    ListItem,
    NodeItem,
    TableItem,
    TextItem,
)

from spec2test.ingest.sections import Block, BlockKind

FORMATS: dict[str, InputFormat] = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".xlsx": InputFormat.XLSX,
    ".md": InputFormat.MD,
    ".markdown": InputFormat.MD,
}

_KINDS: dict[DocItemLabel, BlockKind] = {
    DocItemLabel.TITLE: "heading",
    DocItemLabel.SECTION_HEADER: "heading",
    DocItemLabel.LIST_ITEM: "list",
    DocItemLabel.TABLE: "table",
}
# Never body text: the table of contents, figures, and furniture Docling recognised.
_SKIP = {
    DocItemLabel.DOCUMENT_INDEX,
    DocItemLabel.PICTURE,
    DocItemLabel.CHART,
    DocItemLabel.PAGE_HEADER,
    DocItemLabel.PAGE_FOOTER,
}
_MARGIN = 0.15
"""Share of the page height at the top and bottom counted as margin. Generous, because
margin text only counts as furniture when it also repeats across pages."""


@cache
def _converter() -> DocumentConverter:
    # Born-digital documents only for now: OCR would add models and silently guess at text.
    # Table structure stays on so table cells come through as text.
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    return DocumentConverter(
        allowed_formats=list(set(FORMATS.values())),
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def convert(path: Path) -> DoclingDocument:
    return _converter().convert(path).document


def blocks(doc: DoclingDocument, *, rows_as_blocks: bool = False) -> list[Block]:
    """Flatten `doc` into blocks.

    With `rows_as_blocks` (spreadsheets), each table row becomes its own text block, so
    a sheet listing one numbered requirement per row splits into clauses.
    """
    out: list[Block] = []
    for item, _ in doc.iterate_items():
        if item.label in _SKIP:
            continue
        page, margin = _position(doc, item)
        if isinstance(item, TableItem):
            rows = [
                "\t".join(cell.text.strip() for cell in row)
                for row in item.data.grid
                if any(cell.text.strip() for cell in row)
            ]
            if rows_as_blocks:
                out.extend(Block("text", row, page, margin) for row in rows)
            elif rows:
                out.append(Block("table", "\n".join(rows), page, margin))
        elif isinstance(item, TextItem):
            text = item.text
            # Docling may keep a list item's number apart from its text ("5.1." + "The ...").
            marker = item.marker if isinstance(item, ListItem) else ""
            if any(c.isalnum() for c in marker or "") and not text.startswith(marker):
                text = f"{marker} {text}"
            out.append(Block(_KINDS.get(item.label, "text"), text, page, margin))
    return out


def _position(doc: DoclingDocument, item: NodeItem) -> tuple[int, bool]:
    prov = getattr(item, "prov", None)
    if not prov:
        return 1, False
    page_no = prov[0].page_no
    page = doc.pages.get(page_no)
    if page is None or page.size is None or not page.size.height:
        return page_no, False
    height = page.size.height
    box = prov[0].bbox.to_top_left_origin(page_height=height)
    margin = box.b <= height * _MARGIN or box.t >= height * (1 - _MARGIN)
    return page_no, margin
