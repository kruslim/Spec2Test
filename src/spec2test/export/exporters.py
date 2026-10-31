"""Exporters: each one writes an `Export` in one output format.

Built in: CSV, XLSX, a Markdown checklist and YAML. More come from the
`spec2test.exporters` entry point; see `spec2test.plugins` and the README's "Writing an
exporter".
"""

from __future__ import annotations

import csv
import io
import re
from typing import ClassVar, Protocol, runtime_checkable

from spec2test.export.models import ORDERED, Export, Value
from spec2test.plugins import EXPORTER_GROUP, PluginError, Registry, check_name


@runtime_checkable
class Exporter(Protocol):
    name: str
    """Short name a profile's `output.format` uses, e.g. 'csv'."""
    suffix: str
    """File suffix of what it writes, with the dot: '.csv'."""

    def export(self, export: Export) -> str | bytes:
        """The file's content: text (written as UTF-8) or bytes (written as is)."""
        ...


def _check(plugin: object) -> Exporter:
    check_name(plugin, "exporter")
    if not isinstance(plugin, Exporter):
        raise PluginError(f"exporter {plugin.name!r} needs `suffix` and `export(export)`")  # type: ignore[attr-defined]
    if not (isinstance(plugin.suffix, str) and plugin.suffix.startswith(".")):
        raise PluginError(f"exporter {plugin.name!r}: `suffix` must look like '.ext'")
    return plugin


EXPORTERS: Registry[Exporter] = Registry(EXPORTER_GROUP, "exporter", _check)


def run_exporter(export: Export, name: str | None = None) -> str | bytes:
    """Write `export` with the exporter `name`, or the one its profile names."""
    exporter = EXPORTERS.get(name or export.profile.output.format)
    out = exporter.export(export)
    if not isinstance(out, str | bytes):
        raise PluginError(f"exporter {exporter.name!r} returned {type(out).__name__}")
    return out


def cell(value: Value, column: str) -> str:
    """A value as one spreadsheet cell: lists one entry per line, numbered when ordered."""
    if isinstance(value, str):
        return value
    if column in ORDERED and len(value) > 1:
        return "\n".join(f"{n}. {v}" for n, v in enumerate(value, start=1))
    return "\n".join(value)


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[-+]?\d[\d.,]*(\s[^=+\-@(]*)?")


def _defuse(text: str) -> str:
    """Keep a spreadsheet from running a cell as a formula: '=cmd()' becomes "'=cmd()".
    A signed number, alone or followed by plain words ('-40 °C', '+5 V'), stays as it is."""
    if text.startswith(_FORMULA_START) and not _NUMBER.fullmatch(text):
        return "'" + text
    return text


class CsvExporter:
    """One row per item, the profile's labels as the header. Written with a byte-order
    mark so spreadsheet programs read it as UTF-8 ('§', '±', '°')."""

    name = "csv"
    suffix = ".csv"
    encoding: ClassVar[str] = "utf-8-sig"

    def export(self, export: Export) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow([_defuse(export.labels[c]) for c in export.columns])
        for row in export.rows:
            writer.writerow([_defuse(cell(row.values[c], c)) for c in export.columns])
        return out.getvalue()


class XlsxExporter:
    """One sheet, one row per item, the header frozen. Cells are always text, so nothing
    an item says is run as a formula."""

    name = "xlsx"
    suffix = ".xlsx"

    def export(self, export: Export) -> bytes:
        from openpyxl import Workbook
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Alignment, Font

        wb = Workbook()
        ws = wb.active
        ws.title = _sheet_name(export.title)
        wb.properties.creator = "spec2test"
        wrap = Alignment(wrap_text=True, vertical="top")
        rows = [[export.labels[c] for c in export.columns]]
        rows += [[cell(r.values[c], c) for c in export.columns] for r in export.rows]
        for r, values in enumerate(rows, start=1):
            for c, value in enumerate(values, start=1):
                target = ws.cell(row=r, column=c, value=ILLEGAL_CHARACTERS_RE.sub("", value))
                target.data_type = "s"
                target.alignment = wrap
                if r == 1:
                    target.font = Font(bold=True)
        for c, column in enumerate(export.columns, start=1):
            longest = max(
                (len(line) for values in rows for line in values[c - 1].splitlines()),
                default=0,
            )
            letter = ws.cell(row=1, column=c).column_letter
            ws.column_dimensions[letter].width = min(max(longest, len(column)) + 2, 60)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()


def _sheet_name(title: str) -> str:
    # Excel: at most 31 characters, none of []:*?/\ and not blank.
    name = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:31].strip()
    return name or "Items"


class MarkdownExporter:
    """A checklist to tick off: one box per item, grouped by the section it verifies,
    with the profile's other columns under it."""

    name = "markdown"
    suffix = ".md"

    def export(self, export: Export) -> str:
        lines = [f"# {_md(export.title)}", ""]
        lines.append(
            f"{len(export.rows)} items, generated by {_md(export.model)}; "
            f"profile {_md(export.profile.name)}."
        )
        head = [c for c in ("id", "kind") if c in export.columns]
        rest = [c for c in export.columns if c not in head]
        section = None
        for row in export.rows:
            current = row.requirements[0].source_anchor.section if row.requirements else ""
            if current != section or section is None:
                section = current
                lines += ["", f"## {_md(section) if section else 'No section'}", ""]
            title = " ".join(
                f"**{_md(row.values[c])}**" if c == "id" else _md(cell(row.values[c], c))
                for c in head
            )
            lines.append(f"- [ ] {title}".rstrip())
            for c in rest:
                lines += _md_field(export.labels[c], row.values[c], c)
        return "\n".join(lines) + "\n"


def _md_field(label: str, value: Value, column: str) -> list[str]:
    if isinstance(value, str):
        if not value.strip():
            return []
        text = _md(value).replace("\n", "\n    ")
        return [f"  - {_md(label)}: {text}"]
    entries = [v for v in value if v.strip()]
    if not entries:
        return []
    if len(entries) == 1 or column == "requirements":
        return [f"  - {_md(label)}: {', '.join(_md(v) for v in entries)}"]
    marks = [f"{n}." if column in ORDERED else "-" for n in range(1, len(entries) + 1)]
    out = [f"  - {_md(label)}:"]
    out += [f"    {m} {_md(v)}" for m, v in zip(marks, entries, strict=True)]
    return out


def _md(text: str) -> str:
    """Escape what Markdown would read as markup inside running text: emphasis, code, table
    pipes, HTML tags, links and a '#' starting a line. Placeholders such as '[Owner]' stay
    readable."""
    text = re.sub(r"([\\`*|])", r"\\\1", text)
    text = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)
    if "](" in text:
        text = re.sub(r"([\[\]])", r"\\\1", text)
    return re.sub(r"(?m)^(\s*)#", r"\1\\#", text)


class YamlExporter:
    """A YAML document: the title, the model and profile, then one mapping per item keyed
    by column name, with lists kept as lists."""

    name = "yaml"
    suffix = ".yaml"

    def export(self, export: Export) -> str:
        import yaml

        data = {
            "title": export.title,
            "generated_by": export.model,
            "profile": export.profile.name,
            "items": [{c: row.values[c] for c in export.columns} for row in export.rows],
        }
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


for _exporter in (CsvExporter(), XlsxExporter(), MarkdownExporter(), YamlExporter()):
    EXPORTERS.register(_exporter, builtin=True)
