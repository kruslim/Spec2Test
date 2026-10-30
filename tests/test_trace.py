"""The traceability matrix and spec diffs. No model is called here: the widget lamp items
come from the FakeLLM generation fixtures, and its "revision 2" is invented below by
editing the extracted records."""

import csv
import io
import json

import pytest

from spec2test import cli
from spec2test.extract import Extraction, extract
from spec2test.generate import Generation, GenerationFailure, RejectedItem, SkippedRequirement
from spec2test.generate import generate as generate_items
from spec2test.lint import lint
from spec2test.schema import (
    ChecklistFields,
    Criterion,
    Requirement,
    SourceAnchor,
    VerificationItem,
)
from spec2test.trace import (
    Action,
    ChangeKind,
    Coverage,
    diff,
    diff_report,
    matrix_csv,
    matrix_report,
    trace,
)


def req(text, id, section="1", quote=None, **fields):
    quote = quote or text
    anchor = SourceAnchor(doc="spec", page=1, section=section, start=0, end=len(quote), quote=quote)
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=id, source_anchor=anchor, text=text, **fields)


def checklist(id, *requirement_ids):
    fields = ChecklistFields(inspect="The part.", pass_condition="It is there.")
    return VerificationItem(
        id=id, requirement_ids=list(requirement_ids), kind="checklist", fields=fields
    )


def revise(r, id=None, section=None, quote=None, **fields):
    """`r` as the next revision of the spec has it."""
    anchor = r.source_anchor.model_copy(
        update={k: v for k, v in {"section": section, "quote": quote}.items() if v is not None}
    )
    data = {**r.model_dump(), "id": id or r.id, "source_anchor": anchor.model_dump(), **fields}
    return Requirement.model_validate(data)


@pytest.fixture
def requirements(widget_lamp, fake_llm):
    return lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements


@pytest.fixture
def generation(requirements, fake_llm, widget_lamp):
    return generate_items(requirements, fake_llm, doc=widget_lamp)


@pytest.fixture
def revision_2(requirements):
    """Widget lamp revision 2 (invented): brightness raised to 450 lm, a CE mark added to
    2.2 ahead of the voltage label, the switch clause dropped, the mounting clause read as a
    demonstration, and a strap permitted in a new 2.7."""
    by_id = {r.id: r for r in requirements}
    brightness = by_id["widget-lamp:2.1:1"]
    criteria = [c.model_copy(update={"value": "450"}) if c.unit == "lm" else c
                for c in brightness.criteria]  # fmt: skip
    text = "The lamp shall emit at least 450 lm within 2.0 s of switch-on."
    return [
        revise(brightness, quote=text, text=text, criteria=criteria),
        by_id["widget-lamp:2.2:1"],
        revise(
            by_id["widget-lamp:2.2:1"],
            id="widget-lamp:2.2:2",
            quote="shall carry a CE mark",
            text="The housing shall carry a CE mark.",
            type="regulatory",
        ),
        revise(by_id["widget-lamp:2.2:2"], id="widget-lamp:2.2:3"),
        revise(by_id["widget-lamp:2.5:1"], verification_method="demonstration"),
        revise(
            by_id["widget-lamp:2.5:1"],
            id="widget-lamp:2.7:1",
            section="2.7",
            quote="The lamp may be supplied with a strap.",
            text="The lamp may be supplied with a strap.",
            strength="may",
        ),
    ]


# Traceability matrix


def test_every_widget_lamp_requirement_is_covered(generation):
    matrix = trace(generation)
    assert [(r.requirement_id, r.coverage, r.item_ids) for r in matrix.rows][:2] == [
        ("widget-lamp:2.1:1", Coverage.COVERED, ["widget-lamp:2.1:1:TC"]),
        ("widget-lamp:2.2:1", Coverage.COVERED, ["widget-lamp:2.2:1:CL"]),
    ]
    assert len(matrix.rows) == 5 and not matrix.gaps and not matrix.orphans
    assert all(not r.wrong_kind for r in matrix.rows)


def test_gaps_say_why_a_requirement_has_no_item():
    reqs = [
        req("The part shall be red.", "s:1:1", verification_method="inspection"),
        req("The part shall be blue.", "s:1:2", verification_method="inspection"),
        req("The part shall be green.", "s:1:3", verification_method="inspection"),
        req("The part shall be grey.", "s:1:4"),
        req("The part may be black.", "s:1:5", strength="may", verification_method="inspection"),
        req("The part shall be white.", "s:1:6", verification_method="inspection"),
    ]
    draft = ChecklistFields(inspect="The part.", pass_condition="It is fine.")
    generation = Generation(
        model="FakeLLM",
        requirements=reqs,
        items=[checklist("s:1:1:CL", "s:1:1")],
        rejected=[
            RejectedItem(
                requirement_id="s:1:2",
                kind="checklist",
                draft=draft,
                errors=["the verdict does not state 'blue'"],
            )
        ],
        failed=[GenerationFailure(requirement_id="s:1:3", error="refused")],
        unclassified=["s:1:4"],
        skipped=[SkippedRequirement(requirement_id="s:1:5", reason="a permission")],
    )
    matrix = trace(generation)
    assert [(r.coverage, r.note) for r in matrix.rows] == [
        (Coverage.COVERED, None),
        (Coverage.REJECTED, "the verdict does not state 'blue'"),
        (Coverage.FAILED, "refused"),
        (Coverage.UNCLASSIFIED, "no verification method"),
        (Coverage.SKIPPED, "a permission"),
        (Coverage.MISSING, None),
    ]
    assert [r.requirement_id for r in matrix.gaps] == ["s:1:2", "s:1:3", "s:1:4", "s:1:6"]
    report = matrix_report(matrix)
    assert "s:1:2  rejected: the verdict does not state 'blue'" in report
    assert "6 requirements: 1 covered, 1 skipped, 4 gaps" in report
    assert "coverage 20.0% of 5 that oblige something" in report


def test_items_traced_to_another_set_of_requirements_can_be_orphaned(generation, revision_2):
    matrix = trace(generation, revision_2)
    assert [o.item_id for o in matrix.orphans] == ["widget-lamp:2.3:1:EV"]
    rows = {r.requirement_id: r for r in matrix.rows}
    # The CE mark took the voltage label's id, so the label's item now points at it: a
    # matrix traces by id, which is why spec revisions go through `diff`.
    assert rows["widget-lamp:2.2:2"].item_ids == ["widget-lamp:2.2:2:CL"]
    assert rows["widget-lamp:2.2:3"].coverage is Coverage.MISSING
    assert rows["widget-lamp:2.7:1"].coverage is Coverage.SKIPPED
    assert rows["widget-lamp:2.5:1"].wrong_kind == ["widget-lamp:2.5:1:CL"]


def test_an_item_for_two_requirements_covers_both():
    reqs = [req("The cap shall be red.", "s:1:1", verification_method="inspection"),
            req("The cap shall be round.", "s:1:2", verification_method="inspection")]  # fmt: skip
    generation = Generation(
        model="FakeLLM", requirements=reqs, items=[checklist("cap", "s:1:1", "s:1:2")]
    )
    assert [r.item_ids for r in trace(generation).rows] == [["cap"], ["cap"]]


def test_matrix_csv_has_one_row_per_link_and_per_gap(generation, revision_2):
    rows = list(csv.DictReader(io.StringIO(matrix_csv(trace(generation, revision_2)))))
    assert rows[0]["requirement_id"] == "widget-lamp:2.1:1"
    assert rows[0]["item_id"] == "widget-lamp:2.1:1:TC"
    assert {r["coverage"] for r in rows if not r["item_id"]} == {"missing", "skipped"}
    assert rows[-1]["note"] == "orphan: verifies widget-lamp:2.3:1"


# Spec diffs


def test_identical_versions_have_no_changes(requirements, generation):
    d = diff(requirements, requirements, items=generation)
    assert {c.kind for c in d.changes} == {ChangeKind.UNCHANGED} and not d.impacts
    assert "no requirement changed" in diff_report(d)


def test_revision_lists_each_change(requirements, revision_2):
    d = diff(requirements, revision_2)
    kinds = [(c.kind, c.old_id, c.new_id) for c in d.changes]
    assert kinds == [
        (ChangeKind.CHANGED, "widget-lamp:2.1:1", "widget-lamp:2.1:1"),
        (ChangeKind.UNCHANGED, "widget-lamp:2.2:1", "widget-lamp:2.2:1"),
        (ChangeKind.ADDED, None, "widget-lamp:2.2:2"),
        (ChangeKind.UNCHANGED, "widget-lamp:2.2:2", "widget-lamp:2.2:3"),
        (ChangeKind.REMOVED, "widget-lamp:2.3:1", None),
        (ChangeKind.RESTATED, "widget-lamp:2.5:1", "widget-lamp:2.5:1"),
        (ChangeKind.ADDED, None, "widget-lamp:2.7:1"),
    ]
    changed = d.changes[0]
    assert {f.field for f in changed.fields} == {"quote", "text", "criteria"}
    criteria = next(f for f in changed.fields if f.field == "criteria")
    assert "ge 400 lm" in criteria.old and "ge 450 lm" in criteria.new
    assert [(f.field, f.old, f.new) for f in d.changes[5].fields] == [
        ("verification_method", "inspection", "demonstration")
    ]
    assert not d.impacts and not d.items_given


def test_revision_lists_the_items_each_change_affects(requirements, revision_2, generation):
    d = diff(requirements, revision_2, items=generation)
    assert [(i.action, i.item_id) for i in d.impacts] == [
        (Action.UPDATE, "widget-lamp:2.1:1:TC"),
        (Action.WRITE, None),
        (Action.RELINK, "widget-lamp:2.2:2:CL"),
        (Action.RETIRE, "widget-lamp:2.3:1:EV"),
        (Action.REVIEW, "widget-lamp:2.5:1:CL"),
    ]
    assert d.impacts[1].new_requirement_id == "widget-lamp:2.2:2"
    assert d.impacts[2].reason == "requirement widget-lamp:2.2:2 is now widget-lamp:2.2:3"
    report = diff_report(d)
    assert "update  widget-lamp:2.1:1:TC  source wording changed (quote, text, criteria)" in report
    assert "write   (new item for widget-lamp:2.2:2)  new requirement" in report
    assert "2 added, 1 removed, 1 changed, 1 restated, 2 unchanged" in report
    assert "items: 1 write, 1 retire, 1 update, 1 review, 1 relink" in report


def test_split_records_sharing_a_quote_pair_by_text():
    quote = "The cap shall be red and round."
    old = [req("The cap shall be red.", "a:1:1", quote=quote),
           req("The cap shall be round.", "a:1:2", quote=quote)]  # fmt: skip
    new = [old[1].model_copy(update={"id": "b:1:1"}), old[0].model_copy(update={"id": "b:1:2"})]
    d = diff(old, new)
    assert [(c.kind, c.old_id, c.new_id) for c in d.changes] == [
        (ChangeKind.UNCHANGED, "a:1:2", "b:1:1"),
        (ChangeKind.UNCHANGED, "a:1:1", "b:1:2"),
    ]


def test_a_moved_requirement_is_unchanged_and_its_item_relinked():
    old = [req("The cap shall be red.", "s:4:1", section="4", verification_method="inspection")]
    new = [revise(old[0], id="s:5:1", section="5")]
    items = Generation(model="FakeLLM", requirements=old, items=[checklist("cap", "s:4:1")])
    d = diff(old, new, items=items)
    [change] = d.changes
    assert change.kind is ChangeKind.UNCHANGED
    assert [(f.field, f.old, f.new) for f in change.fields] == [("section", "4", "5")]
    assert [(i.action, i.item_id) for i in d.impacts] == [(Action.RELINK, "cap")]


def test_dissimilar_wording_is_a_removal_and_an_addition():
    old = [req("The cap shall be red.", "s:1:1")]
    new = [req("The hinge shall open to 90 degrees.", "s:1:1")]
    assert [c.kind for c in diff(old, new).changes] == [ChangeKind.REMOVED, ChangeKind.ADDED]


def test_a_requirement_turned_permission_retires_its_item():
    old = [req("The cap shall be red.", "s:1:1", verification_method="inspection")]
    new = [revise(old[0], quote="The cap may be red.", text="The cap may be red.", strength="may")]
    items = Generation(model="FakeLLM", requirements=old, items=[checklist("cap", "s:1:1")])
    [impact] = diff(old, new, items=items).impacts
    assert impact.action is Action.RETIRE and "obliges nothing" in impact.reason


def test_a_criterion_parameter_rename_is_not_a_change():
    c = Criterion(parameter="display time", comparator="le", value="2.0", unit="s")
    old = [req("Shown within 2.0 s.", "s:1:1", criteria=[c])]
    new = [revise(old[0], criteria=[c.model_copy(update={"parameter": "time to display"})])]
    assert diff(old, new).changes[0].kind is ChangeKind.UNCHANGED


# CLI


def test_cli_trace_and_diff(requirements, revision_2, generation, tmp_path, capsys):
    old, new, gen = tmp_path / "old.json", tmp_path / "new.json", tmp_path / "gen.json"
    old.write_text(
        Extraction(doc_id="widget-lamp", requirements=requirements).model_dump_json(),
        encoding="utf-8",
    )
    new.write_text(
        Extraction(doc_id="widget-lamp", requirements=revision_2).model_dump_json(),
        encoding="utf-8",
    )
    gen.write_text(generation.model_dump_json(), encoding="utf-8")

    assert cli.main(["trace", str(gen)]) == 0
    assert "5 covered, 0 skipped, 0 gaps" in capsys.readouterr().out
    assert cli.main(["trace", str(gen), "--requirements", str(new), "--csv"]) == 1
    assert "orphan: verifies widget-lamp:2.3:1" in capsys.readouterr().out
    assert cli.main(["trace", str(gen), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["rows"]) == 5

    assert cli.main(["diff", str(old), str(new)]) == 0
    out = capsys.readouterr().out
    assert "~ changed    widget-lamp:2.1:1" in out and "pass --items" in out
    assert cli.main(["diff", str(old), str(new), "--items", str(gen), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["impacts"]) == 5
    assert cli.main(["diff", str(old), str(new), "--items", str(tmp_path / "nope.json")]) == 2
