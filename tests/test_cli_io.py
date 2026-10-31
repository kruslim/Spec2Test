"""Saving and loading the CLI's JSON on any platform: `--out`, ASCII-safe printing and
loaders that accept a byte order mark or UTF-16. Offline, on the invented widget lamp."""

import json

import pytest

from spec2test import cli
from spec2test.extract import Extraction
from spec2test.textio import read_text

TEXT = '{"quote": "at least 400 lm, § 2.1, ≥ 2.0 s"}'


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"])
def test_read_text_accepts_a_bom_and_utf16(tmp_path, encoding):
    path = tmp_path / "x.json"
    data = TEXT.encode(encoding)
    if encoding in ("utf-16-le", "utf-16-be"):  # PowerShell writes the BOM itself
        data = ("﻿" + TEXT).encode(encoding)
    path.write_bytes(data)
    assert read_text(path) == TEXT


def test_read_text_rejects_other_encodings(tmp_path):
    path = tmp_path / "x.json"
    path.write_bytes('{"quote": "§ 2.1"}'.encode("cp1252"))
    with pytest.raises(UnicodeDecodeError):  # a ValueError: the CLI reports it, exit 2
        read_text(path)


@pytest.fixture
def doc_json(widget_lamp, tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(widget_lamp.model_dump_json(), encoding="utf-8")
    return path


def test_out_writes_utf8_json(doc_json, tmp_path, capsys):
    out = tmp_path / "extraction.json"
    fake = str(cli.Path(__file__).parent / "fixtures" / "llm")
    cli.main(["extract", str(doc_json), "--fake", fake, "--out", str(out)])
    assert capsys.readouterr().out == ""  # --out implies --json, and prints nothing
    extraction = Extraction.model_validate_json(out.read_text(encoding="utf-8"))
    assert extraction.requirements


def test_printed_json_is_ascii_and_loads_after_a_utf16_redirect(tmp_path, capsys):
    """What `spec2test ingest --json > doc.json` leaves in Windows PowerShell 5.1."""
    spec = tmp_path / "lamp.txt"
    spec.write_text("1. LAMP\n\n1.1 The lamp shall emit ≥ 400 lm (see § 2).\n", encoding="utf-8")
    assert cli.main(["ingest", str(spec), "--json"]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii() and "\\u2265" in printed
    saved = tmp_path / "doc.json"
    saved.write_text(printed, encoding="utf-16")
    no_fixtures = tmp_path / "llm"
    no_fixtures.mkdir()
    assert cli.main(["extract", str(saved), "--fake", str(no_fixtures)]) in (0, 1)
    assert "error" not in capsys.readouterr().err  # the UTF-16 Document loaded
    assert "≥ 400 lm" in json.loads(read_text(saved))["text"]
