"""Plugins: an input adapter and an exporter added from outside the core, through entry
points, and plugins that are broken or misbehave. The plugins live in tests/plugin_demo.py,
which `spec2test` never imports itself."""

from importlib.metadata import EntryPoint

import pytest

from spec2test import cli, plugins
from spec2test.export import EXPORTERS, prepare, run_exporter
from spec2test.ingest import ADAPTERS, TextAdapter, adapter_for, ingest, suffixes
from spec2test.plugins import ADAPTER_GROUP, EXPORTER_GROUP, PluginError

LAMP_SPEC = """1. SCOPE
1.1 Purpose. This document specifies an invented lamp.
---
2. REQUIREMENTS
2.1 Brightness. The lamp shall emit at least 400 lm.
"""


@pytest.fixture
def installed(monkeypatch):
    """Pretend a package declaring `entries` ({group: {name: "module:attr"}}) is installed."""

    def install(entries):
        found = [
            EntryPoint(name, value, group)
            for group, named in entries.items()
            for name, value in named.items()
        ]
        monkeypatch.setattr(
            plugins, "entry_points", lambda group: [e for e in found if e.group == group]
        )
        ADAPTERS.reload()
        EXPORTERS.reload()

    yield install
    monkeypatch.undo()
    ADAPTERS.reload()
    EXPORTERS.reload()


def test_an_adapter_from_an_entry_point_reads_its_format(installed, tmp_path):
    installed({ADAPTER_GROUP: {"lamp": "plugin_demo:LampAdapter"}})
    path = tmp_path / "spec.lamp"
    path.write_text(LAMP_SPEC, encoding="utf-8")
    doc = ingest(path)
    assert doc.format == "lamp"
    assert [s.anchor for s in doc.sections if s.anchor] == ["1", "1.1", "2", "2.1"]
    assert doc.section("2.1").page == 2
    assert ".lamp" in suffixes()


def test_an_exporter_from_an_entry_point_is_used_by_name(installed, generation):
    installed({EXPORTER_GROUP: {"jsonl": "plugin_demo:JsonLinesExporter"}})
    out = run_exporter(prepare(generation), "jsonl")
    assert out.count("\n") == len(generation.items)


def test_cli_lists_and_uses_plugins(installed, tmp_path, generation, capsys):
    installed(
        {
            ADAPTER_GROUP: {"lamp": "plugin_demo:LampAdapter"},
            EXPORTER_GROUP: {"jsonl": "plugin_demo:JsonLinesExporter"},
        }
    )
    assert cli.main(["plugins"]) == 0
    listing = capsys.readouterr().out
    assert "lamp" in listing and "plugin_demo" in listing
    assert "csv" in listing and "built-in" in listing

    path = tmp_path / "generation.json"
    path.write_text(generation.model_dump_json(), encoding="utf-8")
    assert cli.main(["export", str(path), "--format", "jsonl"]) == 0
    assert capsys.readouterr().out.count("\n") == len(generation.items)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("plugin_demo:Broken", "bug in the plugin"),
        ("plugin_demo:Missing", "Missing"),
        ("no_such_package:Thing", "no_such_package"),
        ("plugin_demo:NotAnExporter", "export"),
        ("plugin_demo:JsonLinesExporter", "must match"),  # named "jsonl", declared "broken"
    ],
)
def test_a_broken_plugin_is_skipped_and_says_why(installed, generation, value, reason):
    installed({EXPORTER_GROUP: {"broken": value}})
    with pytest.warns(UserWarning, match="skipped"):
        names = EXPORTERS.all()
    assert "broken" not in names
    assert "csv" in names  # the built-ins still work
    with pytest.raises(PluginError, match=reason):
        EXPORTERS.get("broken")
    run_exporter(prepare(generation), "csv")


def test_a_plugin_cannot_replace_a_built_in(installed, generation):
    installed({EXPORTER_GROUP: {"csv": "plugin_demo:ShadowCsv"}})
    with pytest.warns(UserWarning, match="already registered"):
        exporter = EXPORTERS.get("csv")
    assert EXPORTERS.is_builtin("csv")
    assert run_exporter(prepare(generation), exporter.name).startswith("ID,")


def test_unknown_plugins_name_the_known_ones(installed):
    installed({})
    with pytest.raises(PluginError, match="known: csv, markdown, xlsx, yaml"):
        EXPORTERS.get("docx")
    with pytest.raises(PluginError, match="unknown input adapter"):
        ingest("spec.pdf", adapter="nope")


def _adapter(**attrs):
    return type("Adapter", (), attrs)()


def _blocks(self, path):
    return []


@pytest.mark.parametrize(
    ("plugin", "error"),
    [
        (_adapter(suffixes=(".x",), blocks=_blocks), "name"),
        (_adapter(name="a b", suffixes=(".x",), blocks=_blocks), "name"),
        (_adapter(name="u", suffixes=(".X",), blocks=_blocks), "lowercase"),
        (_adapter(name="b", suffixes=".x", blocks=_blocks), "lowercase"),
        (_adapter(name="e", suffixes=(), blocks=_blocks), "lowercase"),
        (_adapter(name="n", suffixes=(".x",)), "blocks"),
    ],
)
def test_adapters_must_fit_the_interface(plugin, error):
    with pytest.raises(PluginError, match=error):
        ADAPTERS.register(plugin)


def test_registered_in_code_and_removed_again(tmp_path):
    from plugin_demo import LampAdapter

    ADAPTERS.register(LampAdapter())
    try:
        assert adapter_for(".LAMP").name == "lamp"
        with pytest.raises(PluginError, match="already registered"):
            ADAPTERS.register(LampAdapter())
    finally:
        ADAPTERS.unregister("lamp")
    assert adapter_for(".lamp") is None
    with pytest.raises(PluginError, match="built in"):
        ADAPTERS.unregister("docling")


def test_text_adapter_splits_paragraphs_and_pages(tmp_path):
    path = tmp_path / "Lamp Spec.txt"
    path.write_bytes(
        "﻿1. SCOPE\r\n\r\n1.1 Purpose. This spec is\r\ninvented.\r\n\f"
        "2.1 Brightness. The lamp shall emit\n  at least 400 lm.\n\n\n".encode()
    )
    doc = ingest(path)
    assert doc.doc_id == "lamp-spec" and doc.format == "txt"
    assert doc.section("1.1").text == "1.1 Purpose. This spec is invented."
    assert doc.section("2.1").page == 2
    assert doc.section("2.1").text == "2.1 Brightness. The lamp shall emit at least 400 lm."
    assert [b.page for b in TextAdapter().blocks(path)] == [1, 1, 2]


def test_an_empty_file_is_an_error(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("\n\n\f\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no text"):
        ingest(path)


def test_ingest_cli_takes_an_adapter(tmp_path, capsys):
    path = tmp_path / "spec.notes"
    path.write_text("2.1 Brightness. The lamp shall emit at least 400 lm.", encoding="utf-8")
    assert cli.main(["ingest", str(path)]) == 2
    assert "unsupported format '.notes'" in capsys.readouterr().err
    assert cli.main(["ingest", str(path), "--adapter", "text"]) == 0
    assert "2.1  Brightness" in capsys.readouterr().out
