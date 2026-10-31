"""The review log, the source view, the edit form and the review app. No model is called:
the widget lamp items come from the FakeLLM fixtures, and the app is driven offline with
Streamlit's AppTest."""

import json
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from spec2test import cli
from spec2test.llm import FakeLLM
from spec2test.review import (
    APP,
    Decision,
    ReviewError,
    ReviewStats,
    ReviewStore,
    generation_id,
    run_pipeline,
    source_view,
    stats_report,
)
from spec2test.review.forms import form_fields, from_form, to_form

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / "review.sqlite")


def item_of(generation, kind):
    return next(i for i in generation.items if i.kind.value == kind)


def test_add_stores_the_generation_and_its_document_once(store, generation, widget_lamp):
    gid = store.add(generation)
    assert gid == generation_id(generation)
    assert store.document(gid) is None
    assert store.add(generation, widget_lamp) == gid
    assert store.document(gid) == widget_lamp
    assert store.generation(gid) == generation
    [saved] = store.generations()
    assert (saved.id, saved.doc_id, saved.items) == (gid, "widget-lamp", len(generation.items))
    with pytest.raises(KeyError):
        store.generation("nope")


def test_every_decision_is_logged_and_the_latest_counts(store, generation):
    gid = store.add(generation)
    item = generation.items[0]
    store.record(gid, item.id, Decision.REJECT, note="wrong value", reviewer="me")
    store.record(gid, item.id, Decision.ACCEPT, reviewer="me")
    history = store.history(gid, item.id)
    assert [d.decision for d in history] == [Decision.REJECT, Decision.ACCEPT]
    assert history[0].note == "wrong value"
    assert history[0].fields == item.fields.model_dump()
    assert store.latest(gid)[item.id].decision is Decision.ACCEPT


def test_an_edit_keeps_both_versions(store, generation):
    gid = store.add(generation)
    item = item_of(generation, "test_case")
    edited = {**item.fields.model_dump(), "equipment": ["Lux meter, calibrated"]}
    logged = store.record(gid, item.id, "edit", edited=edited)
    assert logged.edited["equipment"] == ["Lux meter, calibrated"]
    assert logged.fields == item.fields.model_dump()
    with sqlite3.connect(store.path) as db:
        row = db.execute("SELECT fields, edited FROM decisions").fetchone()
    assert json.loads(row[0]) != json.loads(row[1])


@pytest.mark.parametrize(
    ("decision", "edited", "error"),
    [
        ("edit", None, "needs the edited fields"),
        ("edit", {}, "not a valid test_case"),
        ("edit", "same", "changes nothing"),
        ("accept", "same", "only an edit"),
    ],
)
def test_bad_decisions_are_refused(store, generation, decision, edited, error):
    gid = store.add(generation)
    item = item_of(generation, "test_case")
    if edited == "same":
        edited = item.fields.model_dump()
    with pytest.raises(ReviewError, match=error):
        store.record(gid, item.id, decision, edited=edited)
    assert store.history(gid) == []


def test_unknown_items_are_refused(store, generation):
    gid = store.add(generation)
    with pytest.raises(ReviewError, match="not an item"):
        store.record(gid, "widget-lamp:9.9:1-TC", Decision.ACCEPT)


def test_stats_count_accepted_unedited_over_reviewed(store, generation):
    gid = store.add(generation)
    items = generation.items
    assert len(items) >= 3
    store.record(gid, items[0].id, Decision.ACCEPT)
    store.record(gid, items[1].id, Decision.REJECT)
    edited = {**items[2].fields.model_dump()}
    first = next(iter(edited))
    edited[first] = (
        [*edited[first], "Note the ambient temperature."]
        if isinstance(edited[first], list)
        else edited[first] + " (edited)"
    )
    store.record(gid, items[2].id, Decision.EDIT, edited=edited)

    stats = store.stats(gid)
    o = stats.overall
    assert (o.items, o.reviewed, o.accepted, o.edited, o.rejected) == (len(items), 3, 1, 1, 1)
    assert o.acceptance_rate == pytest.approx(1 / 3)
    assert sum(t.items for t in stats.by_kind.values()) == len(items)
    assert sum(t.reviewed for t in stats.by_method.values()) == 3
    assert ReviewStats.model_validate_json(stats.model_dump_json()) == stats
    assert "overall" in stats_report(stats)
    assert store.stats(gid) == store.stats()


def test_stats_before_any_review_have_no_rate(store, generation):
    store.add(generation)
    assert store.stats().overall.acceptance_rate is None


def test_source_view_highlights_the_quote_in_its_section(generation, widget_lamp):
    req = next(r for r in generation.requirements if r.id == "widget-lamp:2.2:1")
    view = source_view(req, widget_lamp)
    assert view.found
    assert view.before + view.quote + view.after == widget_lamp.section("2.2").text
    assert view.heading.startswith("2.2 Housing")
    assert f"<mark>{view.quote}</mark>" in view.html()


def test_source_view_without_its_document_shows_the_quote_alone(generation, widget_lamp):
    req = generation.requirements[0]
    assert not source_view(req, None).found
    other = widget_lamp.model_copy(update={"doc_id": "other"})
    view = source_view(req, other)
    assert (view.found, view.before, view.quote) == (False, "", req.source_anchor.quote)


def test_source_view_escapes_html():
    from spec2test.review import SourceView

    view = SourceView(heading="1", page=1, before="a < b\n", quote="<x>", after="&")
    assert view.html() == "a &lt; b<br><mark>&lt;x&gt;</mark>&amp;"


def test_forms_round_trip_each_kind(generation):
    for kind in ("checklist", "test_case", "evidence"):
        item = next((i for i in generation.items if i.kind.value == kind), None)
        if item is None:
            continue
        fields = item.fields.model_dump()
        assert from_form(kind, to_form(kind, fields)) == fields
    assert ("steps", True) in form_fields("test_case")
    assert from_form("checklist", {"inspect": " The label ", "pass_condition": "Legible"}) == {
        "inspect": "The label",
        "pass_condition": "Legible",
    }
    assert from_form("evidence", {"evidence": "photo\n\n  video  \n"})["evidence"] == [
        "photo",
        "video",
    ]


def test_run_pipeline_extracts_lints_and_generates(widget_lamp, fake_llm, generation):
    stages = []
    result = run_pipeline(widget_lamp, fake_llm, progress=stages.append)
    assert stages == ["extract", "lint", "generate"]
    assert result.generation.items == generation.items
    assert result.usage == fake_llm.usage[len(fake_llm.usage) // 2 :]  # this run's calls only
    assert result.cost().startswith("cost")


def test_cli_stats(tmp_path, store, generation, capsys):
    gid = store.add(generation)
    store.record(gid, generation.items[0].id, Decision.ACCEPT)
    assert cli.main(["review", "stats", "--db", str(store.path)]) == 0
    assert "overall" in capsys.readouterr().out
    assert cli.main(["review", "stats", "--db", str(store.path), "--json",
                     "--generation", gid[:8]]) == 0  # fmt: skip
    stats = ReviewStats.model_validate_json(capsys.readouterr().out)
    assert stats.overall.accepted == 1
    assert cli.main(["review", "stats", "--db", str(tmp_path / "none.sqlite")]) == 2
    assert cli.main(["review", "stats", "--db", str(store.path), "--generation", "zz"]) == 2


def test_cli_open_adds_the_generation_and_starts_streamlit(
    tmp_path, generation, widget_lamp, monkeypatch
):
    gen_path = tmp_path / "generation.json"
    gen_path.write_text(generation.model_dump_json(), encoding="utf-8")
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(widget_lamp.model_dump_json(), encoding="utf-8")
    db = tmp_path / "review.sqlite"
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, env: calls.append((cmd, env)) or 0)
    args = ["review", "open", str(gen_path), "--doc", str(doc_path), "--db", str(db)]
    assert cli.main([*args, "--fake", str(FIXTURES / "llm"), "--port", "8600"]) == 0
    [(command, env)] = calls
    assert command[1:5] == ["-m", "streamlit", "run", str(APP)]
    assert command[-2:] == ["--server.port", "8600"]
    at = command.index("--server.address")
    assert command[at + 1] == "localhost"  # not reachable from the network
    gid = env["SPEC2TEST_REVIEW_OPEN"]
    assert ReviewStore(db).document(gid) == widget_lamp
    assert env["SPEC2TEST_REVIEW_FAKE"] == str((FIXTURES / "llm").resolve())


def test_cli_open_refuses_a_document_without_a_generation(tmp_path, widget_lamp, capsys):
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(widget_lamp.model_dump_json(), encoding="utf-8")
    assert cli.main(["review", "open", "--doc", str(doc_path), "--db", str(tmp_path / "r")]) == 2
    assert "go with a Generation" in capsys.readouterr().err


# The app, driven offline.


@pytest.fixture
def app(tmp_path, monkeypatch):
    def start(**env):
        monkeypatch.setenv("SPEC2TEST_REVIEW_DB", str(tmp_path / "review.sqlite"))
        for k, v in env.items():
            monkeypatch.setenv(f"SPEC2TEST_REVIEW_{k.upper()}", v)
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        assert not at.exception
        return at

    return start


def test_app_starts_empty(app):
    at = app()
    assert at.title[0].value == "Spec2Test review"


def test_app_uploads_a_spec_runs_the_pipeline_and_logs_reviews(app, tmp_path, widget_lamp):
    at = app(fake=str(FIXTURES / "llm"))
    data = widget_lamp.model_dump_json().encode("utf-8")
    at.sidebar.file_uploader[0].set_value(("widget-lamp.json", data, "application/json"))
    at.run()
    assert at.sidebar.selectbox[0].value == "fake"
    at.sidebar.button[0].click().run()  # Run the pipeline: the fake needs no confirmation
    assert not at.exception
    assert at.title[0].value == "Review: widget-lamp"

    store = ReviewStore(tmp_path / "review.sqlite")
    [saved] = store.generations()
    gid = saved.id
    item_id = at.main.selectbox[0].value
    source = [m.value for m in at.main.markdown if "<mark>" in m.value]
    assert source, "the source quote is highlighted"

    at.main.text_input[0].set_value("looks right")
    at.main.button[0].click().run()  # Accept
    decided = store.latest(gid)[item_id]
    assert (decided.decision, decided.note) == (Decision.ACCEPT, "looks right")
    assert at.main.selectbox[0].value != item_id  # moved on to the next item to review

    next_id = at.main.selectbox[0].value
    at.main.button[1].click().run()  # Reject
    assert store.latest(gid)[next_id].decision is Decision.REJECT
    assert at.main.metric[1].value == "2"


def test_app_saves_an_edit(app, tmp_path, generation, widget_lamp):
    store = ReviewStore(tmp_path / "review.sqlite")
    gid = store.add(generation, widget_lamp)
    item = item_of(generation, "checklist")
    at = app(open=gid)
    at.main.selectbox[0].set_value(item.id).run()
    at.text_area(key=f"{item.id}-pass_condition").set_value("The label reads 12 V.")
    submit = next(b for b in at.main.button if b.label == "Save edit")
    submit.click().run()
    assert not at.exception
    decided = store.latest(gid)[item.id]
    assert decided.decision is Decision.EDIT
    assert decided.edited["pass_condition"] == "The label reads 12 V."
    assert decided.fields == item.fields.model_dump()


def test_app_asks_before_a_paid_run(app, widget_lamp):
    at = app()
    data = widget_lamp.model_dump_json().encode("utf-8")
    at.sidebar.file_uploader[0].set_value(("widget-lamp.json", data, "application/json"))
    at.run()
    assert at.sidebar.selectbox[0].value == "kimi-code"
    run = next(b for b in at.sidebar.button if b.label == "Run the pipeline")
    assert run.disabled
    assert "paid API" in at.sidebar.checkbox[0].label


def test_app_clears_the_paid_run_tick_after_a_run(app, widget_lamp, monkeypatch):
    """A second paid run needs a second tick. The paid model is swapped for the fake."""
    from spec2test.review import run

    real = run.run_pipeline
    fake = FakeLLM.from_dir(FIXTURES / "llm")
    monkeypatch.setattr(run, "run_pipeline", lambda doc, llm, **kw: real(doc, fake, **kw))
    at = app()
    data = widget_lamp.model_dump_json().encode("utf-8")
    at.sidebar.file_uploader[0].set_value(("widget-lamp.json", data, "application/json"))
    at.run()
    at.sidebar.checkbox[0].check().run()
    run_button = next(b for b in at.sidebar.button if b.label == "Run the pipeline")
    assert not run_button.disabled
    run_button.click().run()
    assert not at.exception
    assert at.title[0].value == "Review: widget-lamp"
    assert not at.sidebar.checkbox[0].value
    assert next(b for b in at.sidebar.button if b.label == "Run the pipeline").disabled
