"""A plugin package in miniature: an input adapter and an exporter that live outside
`spec2test` and reach it only through entry points (see tests/test_plugins.py).

The ".lamp" format is invented for the tests: one clause per line, a line holding only
'---' starts a new page.
"""

from __future__ import annotations

import json
from pathlib import Path

from spec2test.export import Export
from spec2test.ingest import Block


class LampAdapter:
    name = "lamp"
    suffixes = (".lamp",)

    def blocks(self, path: Path) -> list[Block]:
        page, out = 1, []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                page += 1
            elif line.strip():
                out.append(Block("text", line.strip(), page))
        return out


class JsonLinesExporter:
    name = "jsonl"
    suffix = ".jsonl"

    def export(self, export: Export) -> str:
        return "".join(json.dumps(row.values) + "\n" for row in export.rows)


class Broken:
    """Fails as it is built, as a plugin with a bug would."""

    name = "broken"

    def __init__(self) -> None:
        raise RuntimeError("bug in the plugin")


class NotAnExporter:
    name = "half"
    suffix = ".txt"  # but no export()


class ShadowCsv(JsonLinesExporter):
    """Takes a built-in's name; it must not replace the built-in."""

    name = "csv"
