"""Read the JSON and JSONL files the CLI writes, whatever saved them.

`spec2test ... --json > file` in Windows PowerShell 5.1 saves UTF-16 with a byte order
mark, and some editors save UTF-8 with one. `read_text` accepts both; any other text must
be UTF-8. `--out FILE` avoids the shell entirely and is the safe way to save.
"""

from __future__ import annotations

import codecs
from pathlib import Path


def read_text(path: str | Path) -> str:
    """The file's text: UTF-16 or UTF-8 with a byte order mark, otherwise strict UTF-8."""
    data = Path(path).read_bytes()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")
