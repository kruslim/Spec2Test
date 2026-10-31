"""Profiles and the built-in exporters, on the widget lamp items from the FakeLLM fixtures.
No model calls."""

import csv
import io
import json
import tomllib
from pathlib import Path

import openpyxl
import pytest
import yaml

from spec2test import cli
from spec2test.export import (
    COLUMNS,
    Export,
    ExportError,
    Profile,
    ReviewStatus,
    load_profile,
    prepare,
    run_exporter,
)
from spec2test.review import Decision, ReviewStore
from spec2test.schema import TestCaseFields, VerificationItemKind

EXAMPLE = Path(__file__).parents[1] / "examples" / "profiles" / "widget-co.toml"


def profile(**data) -> Profile:
    return Profile.model_validate(data)


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def item_of(generation, kind):
    return next(i for i in generation.items if i.kind.value == kind)


def test_default_export_is_one_csv_row_per_item(generation):
    export = prepare(generation)
    rows = read_csv(run_exporter(export))
    assert len(rows) == len(generation.items)
    assert list(rows[0]) == [COLUMNS[c] for c in export.columns]
    test_case = item_of(generation, "test_case")
    row = next(r for r in rows if r["ID"] == test_case.id)
    assert row["Kind"] == "Test case"
    assert row["Requirements"] == test_case.requirement_ids[0]
    assert row["Section"] == "2.1" and row["Page"] == "2" and row["Method"] == "test"
    assert row["Procedure"].splitlines() == [
        f"{n}. {s}" for n, s in enumerate(test_case.fields.steps, start=1)
    ]
    assert row["Acceptance criterion"] == test_case.fields.pass_criterion
    assert {r["Review status"] for r in rows} == {"unreviewed"}


def test_actions_and_acceptance_cover_every_kind(generation):
    export = prepare(generation, profile(output={"columns": ["id", "actions", "acceptance"]}))
    by_id = {r.item.id: r.values for r in export.rows}
    checklist, evidence = item_of(generation, "checklist"), item_of(generation, "evidence")
    assert by_id[checklist.id]["actions"] == [checklist.fields.inspect]
    assert by_id[checklist.id]["acceptance"] == checklist.fields.pass_condition
    assert by_id[evidence.id]["actions"] == evidence.fields.procedure
    assert by_id[evidence.id]["acceptance"] == evidence.fields.acceptance_criterion


def test_id_scheme(generation):
    p = profile(
        ids={"pattern": "{doc}-{kind}-{n:03}", "doc": "WL", "kind_codes": {"test_case": "TP"}}
    )
    ids = [r.id for r in prepare(generation, p).rows]
    assert ids == ["WL-TP-001", "WL-CL-002", "WL-CL-003", "WL-EV-004", "WL-CL-005"]
    p = profile(ids={"pattern": "{section}/{kind}{n_kind}", "start": 0})
    assert [r.id for r in prepare(generation, p).rows] == [
        "2.1/TC0", "2.2/CL0", "2.2/CL1", "2.3/EV0", "2.5/CL2"
    ]  # fmt: skip


def test_rejecting_an_item_does_not_renumber_the_rest(tmp_path, generation):
    store = ReviewStore(tmp_path / "review.sqlite")
    gid = store.add(generation)
    store.record(gid, generation.items[1].id, Decision.REJECT, note="duplicate")
    p = profile(ids={"pattern": "WL-{n:02}"})
    export = prepare(generation, p, store.latest(gid))
    assert [r.id for r in export.rows] == ["WL-01", "WL-03", "WL-04", "WL-05"]
    assert export.left_out == {ReviewStatus.REJECTED: 1}


def test_review_edits_are_exported_and_statuses_filter(tmp_path, generation):
    store = ReviewStore(tmp_path / "review.sqlite")
    gid = store.add(generation)
    test_case = item_of(generation, "test_case")
    edited = test_case.fields.model_dump() | {"pass_criterion": "At least 400 lm by 2.0 s."}
    store.record(gid, test_case.id, Decision.EDIT, edited=edited, reviewer="ana", note="tidy")
    store.record(gid, generation.items[1].id, Decision.ACCEPT)
    store.record(gid, generation.items[2].id, Decision.REJECT)

    p = profile(output={"columns": [*COLUMNS], "statuses": ["accepted", "edited"]})
    export = prepare(generation, p, store.latest(gid))
    assert [r.item.id for r in export.rows] == [test_case.id, generation.items[1].id]
    row = export.rows[0]
    assert isinstance(row.item.fields, TestCaseFields)
    assert row.values["acceptance"] == row.values["pass_criterion"] == edited["pass_criterion"]
    assert (row.values["status"], row.values["reviewer"], row.values["note"]) == (
        "edited",
        "ana",
        "tidy",
    )
    assert export.left_out == {ReviewStatus.REJECTED: 1, ReviewStatus.UNREVIEWED: 2}


def test_reviewed_only_needs_decisions(generation):
    with pytest.raises(ExportError, match="no review decisions"):
        prepare(generation, profile(output={"statuses": ["accepted"]}))


def test_decisions_must_belong_to_the_generation(tmp_path, generation, widget_lamp, fake_llm):
    store = ReviewStore(tmp_path / "review.sqlite")
    gid = store.add(generation)
    store.record(gid, generation.items[0].id, Decision.ACCEPT)
    other = generation.model_copy(update={"items": generation.items[1:]})
    with pytest.raises(ExportError, match="not in the generation"):
        prepare(other, None, store.latest(gid))


def test_duplicate_ids_are_refused(generation):
    twin = generation.items[1].model_copy(update={"id": "twin"})
    doubled = generation.model_copy(update={"items": [*generation.items, twin]})
    with pytest.raises(ExportError, match="more than one item"):
        prepare(doubled, profile(ids={"pattern": "{req}"}))


def test_required_fields_get_a_placeholder_never_a_value(generation):
    p = profile(
        required=["equipment", "owner"],
        values={"program": "Lamp rev A"},
        output={"columns": ["id", "equipment", "owner", "program"]},
        vocabulary={"columns": {"owner": "Test owner"}},
    )
    export = prepare(generation, p)
    test_case = item_of(generation, "test_case")
    for row in export.rows:
        assert row.values["owner"] == "[Test owner]"
        assert row.values["program"] == "Lamp rev A"
        if row.item.id == test_case.id:
            assert row.values["equipment"] == test_case.fields.equipment
        else:
            assert row.values["equipment"] == "[Equipment]"
    assert export.missing["owner"] == [r.id for r in export.rows]
    assert test_case.id not in export.missing["equipment"]


def test_vocabulary_renames_headers_kinds_and_statuses(generation):
    p = profile(
        vocabulary={
            "columns": {"id": "Ref", "kind": "Type"},
            "kinds": {"test_case": "Test procedure", "evidence": "Proof"},
            "statuses": {"unreviewed": "Draft"},
        },
        output={"columns": ["id", "kind", "status", "evidence"]},
    )
    rows = read_csv(run_exporter(prepare(generation, p)))
    assert list(rows[0]) == ["Ref", "Type", "Review status", "Evidence"]
    assert {r["Type"] for r in rows} == {"Test procedure", "Checklist item", "Proof"}
    assert {r["Review status"] for r in rows} == {"Draft"}


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"output": {"columns": ["id", "stpes"]}}, "unknown column"),
        ({"output": {"columns": ["kind"]}}, "must include 'id'"),
        ({"output": {"columns": ["id", "id"]}}, "more than once"),
        ({"output": {"statuses": []}}, "at least 1"),
        ({"ids": {"pattern": "{doc}-{number}"}}, "unknown placeholder"),
        ({"ids": {"pattern": "{doc}-{kind}"}}, "the same ID"),
        ({"ids": {"pattern": "{n:x"}}, "pattern"),
        ({"ids": {"pattern": "{n:s}"}}, "pattern"),
        ({"ids": {"kind_codes": {"test": "T"}}}, "kind_codes"),
        ({"values": {"kind": "x"}, "output": {"columns": ["id", "kind"]}}, "filled by the core"),
        ({"required": ["owner"]}, "not in output.columns"),
        ({"vocabulary": {"columns": {"stpes": "Steps"}}}, "unknown column"),
        ({"vocabulary": {"kinds": {"test": "Test"}}}, "kinds"),
        ({"colums": ["id"]}, "colums"),
    ],
)
def test_profile_mistakes_fail_on_load(data, error):
    with pytest.raises(ValueError, match=error):
        Profile.model_validate(data)


def test_the_example_profile_loads_and_exports(generation):
    p = load_profile(EXAMPLE)
    export = prepare(generation, p)
    assert export.rows[0].id == "WL-TP-001"
    assert run_exporter(export).startswith("#")  # its format is the Markdown checklist


def test_profile_errors_name_the_file(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[output]\ncolumns = ["id", "nope"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.toml.*nope"):
        load_profile(path)
    path.write_text("name = ", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_profile(path)


@pytest.fixture
def hostile(generation):
    """A generation whose test case says things a spreadsheet would run as formulas."""
    test_case = item_of(generation, "test_case")
    fields = test_case.fields.model_copy(
        update={
            "steps": ['=HYPERLINK("http://example.invalid","x")', "Cool to -40 °C"],
            "pass_criterion": "+5 V at the terminal",
            "expected_result": "@SUM(A1)",
            "equipment": ["-2+3", "Bench\x07 supply"],
        }
    )
    items = [
        i.model_copy(update={"fields": fields}) if i is test_case else i for i in generation.items
    ]
    return generation.model_copy(update={"items": items})  # fmt: skip


def test_csv_cells_never_run_as_formulas(hostile):
    columns = ["id", "steps", "pass_criterion", "expected_result", "equipment"]
    p = profile(output={"columns": columns})
    row = read_csv(run_exporter(prepare(hostile, p)))[0]
    assert row["Test steps"].startswith("1. =HYPERLINK")  # numbered, so not at the start
    assert row["Pass criterion"] == "+5 V at the terminal"
    assert row["Expected result"] == "'@SUM(A1)"
    single = profile(output={"columns": ["id", "steps"]})
    one_step = hostile.model_copy(deep=True)
    one_step.items[0].fields.steps = ['=HYPERLINK("x")']
    assert read_csv(run_exporter(prepare(one_step, single)))[0]["Test steps"] == '\'=HYPERLINK("x")'
    assert "'-2+3" in run_exporter(
        prepare(hostile, profile(output={"columns": ["id", "equipment"]}))
    )


def test_xlsx_is_one_sheet_of_text_cells(hostile):
    columns = ["id", "kind", "steps", "expected_result", "equipment"]
    p = profile(output={"format": "xlsx", "title": "Lamp: tests [rev A]?", "columns": columns})
    data = run_exporter(prepare(hostile, p))
    assert isinstance(data, bytes)
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    assert ws.title == "Lamp  tests  rev A"
    assert [c.value for c in ws[1]] == ["ID", "Kind", "Test steps", "Expected result", "Equipment"]
    assert ws.max_row == len(hostile.items) + 1
    assert ws.freeze_panes == "A2"
    assert ws["D2"].value == "@SUM(A1)" and ws["D2"].data_type == "s"
    assert ws["E2"].value == "-2+3\nBench supply"  # control character dropped
    assert ws["C2"].value.startswith('1. =HYPERLINK("http://example.invalid","x")')


def test_xlsx_cli_needs_a_file(tmp_path, generation, capsys):
    path = tmp_path / "generation.json"
    path.write_text(generation.model_dump_json(), encoding="utf-8")
    assert cli.main(["export", str(path), "--format", "xlsx"]) == 2
    assert "pass --out" in capsys.readouterr().err
    out = tmp_path / "items.xlsx"
    assert cli.main(["export", str(path), "--format", "xlsx", "--out", str(out)]) == 0
    assert openpyxl.load_workbook(out).active.max_row == len(generation.items) + 1


def test_markdown_checklist_groups_by_section(generation):
    p = profile(output={"format": "markdown", "title": "Lamp checks"})
    text = run_exporter(prepare(generation, p))
    lines = text.splitlines()
    assert lines[0] == "# Lamp checks"
    assert [line for line in lines if line.startswith("## ")] == [
        "## 2.1", "## 2.2", "## 2.3", "## 2.5"
    ]  # fmt: skip
    assert sum(line.startswith("- [ ] **") for line in lines) == len(generation.items)
    assert "  - Procedure:" in lines and "    1. Start the photometer recording" in lines
    assert "[maximum force in N]" in text  # placeholders stay readable


def test_markdown_escapes_markup():
    from spec2test.export.exporters import _md

    assert _md("a*b* `c` | <script> x <= 2") == r"a\*b\* \`c\` \| \<script> x <= 2"
    assert _md("[click](http://x)") == r"\[click\](http://x)"
    assert _md("[Owner]") == "[Owner]"
    assert _md("line\n# heading") == "line\n\\# heading"


def test_yaml_keeps_lists_and_column_keys(generation):
    p = profile(output={"format": "yaml", "columns": ["id", "kind", "steps", "equipment"]})
    data = yaml.safe_load(run_exporter(prepare(generation, p)))
    assert data["generated_by"] == generation.model and data["profile"] == "default"
    test_case = item_of(generation, "test_case")
    first = data["items"][0]
    assert first == {
        "id": test_case.id,
        "kind": "Test case",
        "steps": test_case.fields.steps,
        "equipment": test_case.fields.equipment,
    }
    assert len(data["items"]) == len(generation.items)


def test_every_exporter_writes_an_empty_export(generation):
    empty = generation.model_copy(update={"items": []})
    for name in ("csv", "xlsx", "markdown", "yaml"):
        assert run_exporter(prepare(empty), name)


def test_cli_export_from_the_review_log(tmp_path, generation, capsys):
    db = tmp_path / "review.sqlite"
    store = ReviewStore(db)
    gid = store.add(generation)
    store.record(gid, generation.items[0].id, Decision.REJECT)
    path = tmp_path / "generation.json"
    path.write_text(generation.model_dump_json(), encoding="utf-8")
    out = tmp_path / "items.csv"

    assert cli.main(["export", str(path), "--db", str(db), "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert "4 items written" in err and "left out: 1 rejected" in err
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 byte-order mark, for spreadsheets
    assert b"\r\r\n" not in raw
    assert len(read_csv(raw.decode("utf-8-sig"))) == 4

    assert cli.main(["export", "--db", str(db), "--generation", gid[:8], "--format", "yaml"]) == 0
    assert len(yaml.safe_load(capsys.readouterr().out)["items"]) == 4


def test_cli_export_errors(tmp_path, generation, capsys):
    path = tmp_path / "generation.json"
    path.write_text(generation.model_dump_json(), encoding="utf-8")
    db = tmp_path / "review.sqlite"
    cases = [
        ([], "not both or neither"),
        ([str(path), "--generation", "abc"], "not both or neither"),
        (["--generation", "abc"], "needs --db"),
        ([str(path), "--db", str(db)], "no review log"),
        ([str(path), "--format", "docx"], "unknown exporter 'docx'"),
        ([str(path), "--profile", str(tmp_path / "none.toml")], "none.toml"),
    ]
    for args, error in cases:
        assert cli.main(["export", *args]) == 2, args
        assert error in capsys.readouterr().err
    ReviewStore(db)
    assert cli.main(["export", str(path), "--db", str(db)]) == 2
    assert "review it first" in capsys.readouterr().err
    assert not (tmp_path / "items.csv").exists()


def test_cli_export_reports_required_fields(tmp_path, generation, capsys):
    path = tmp_path / "generation.json"
    path.write_text(generation.model_dump_json(), encoding="utf-8")
    p = tmp_path / "p.toml"
    p.write_text('required = ["owner"]\n[output]\ncolumns = ["id", "owner"]\n', encoding="utf-8")
    assert cli.main(["export", str(path), "--profile", str(p)]) == 1
    captured = capsys.readouterr()
    assert "required 'owner' is empty for 5 items, written as [Owner]" in captured.err
    assert captured.out.count("[Owner]") == 5


def test_export_is_a_plain_record(generation):
    export = prepare(generation)
    again = Export.model_validate_json(export.model_dump_json())
    assert again == export
    assert json.loads(export.model_dump_json())["rows"][0]["item"]["kind"] in set(
        VerificationItemKind
    )
