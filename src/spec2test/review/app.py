"""The review app: open a Generation (or upload a spec and run the pipeline), read each item
next to its highlighted source, and accept, edit or reject it.

Run it with `spec2test review open`, which sets the environment this reads:

- SPEC2TEST_REVIEW_DB: the review log (default `review.sqlite`).
- SPEC2TEST_REVIEW_FAKE: a FakeLLM fixture folder; when set, runs replay it by default.
- SPEC2TEST_REVIEW_OPEN: the id of the generation to open first.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st

from spec2test.generate import Generation, render
from spec2test.ingest import Document, doc_id_for, ingest, suffixes
from spec2test.llm import (
    PRESETS,
    AnthropicLLM,
    FakeLLM,
    LLMClient,
    OpenAICompatLLM,
    model_name,
)
from spec2test.review.forms import form_fields, to_form
from spec2test.review.forms import from_form as fields_from_form
from spec2test.review.models import Decision, ReviewDecision
from spec2test.review.run import run_pipeline
from spec2test.review.source import source_view
from spec2test.review.store import DEFAULT_DB, ReviewError, ReviewStore
from spec2test.schema import Comparator, Criterion, Requirement, VerificationItem

FAKE = "fake"
_MARK = {None: "○", Decision.ACCEPT: "✓", Decision.EDIT: "✎", Decision.REJECT: "✗"}
_FILTERS = {
    "All": None,
    "To review": "todo",
    "Accepted": Decision.ACCEPT,
    "Edited": Decision.EDIT,
    "Rejected": Decision.REJECT,
}


def main() -> None:
    st.set_page_config(page_title="Spec2Test review", layout="wide")
    store = ReviewStore(os.environ.get("SPEC2TEST_REVIEW_DB") or DEFAULT_DB)
    fake_dir = os.environ.get("SPEC2TEST_REVIEW_FAKE") or None
    if "generation" not in st.session_state:
        st.session_state.generation = os.environ.get("SPEC2TEST_REVIEW_OPEN") or None

    with st.sidebar:
        st.text_input("Reviewer", key="reviewer", help="Logged with every decision.")
        _open_panel(store)
        _upload_panel(store, fake_dir)

    gid = st.session_state.generation
    if gid is None:
        st.title("Spec2Test review")
        st.info("Open a saved review, or upload a spec in the sidebar.")
        return
    _review(store, gid)


def _open_panel(store: ReviewStore) -> None:
    saved = store.generations()
    st.header("Open")
    if not saved:
        st.caption("No saved reviews yet.")
        return
    ids = [g.id for g in saved]
    labels = {
        g.id: f"{g.doc_id or 'mixed'} · {g.model} · {g.items} items · {g.added_at:%Y-%m-%d %H:%M}"
        for g in saved
    }
    current = st.session_state.generation
    choice = st.selectbox(
        "Saved review",
        ids,
        index=ids.index(current) if current in ids else None,
        format_func=labels.__getitem__,
        placeholder="Choose one",
    )
    if choice is not None and choice != current:
        st.session_state.generation = choice
        st.session_state.pop("item", None)
        st.rerun()


def _upload_panel(store: ReviewStore, fake_dir: str | None) -> None:
    st.header("New review")
    spec = st.file_uploader(
        "Spec", type=[s.lstrip(".") for s in suffixes()] + ["json"],
        help="A PDF, DOCX, XLSX, Markdown or text spec (or a format a plugin adds), or a "
        "Document saved by `ingest --json`.",
    )  # fmt: skip
    saved = st.file_uploader(
        "Generation (optional)",
        type=["json"],
        help="Items saved by `generate --json`; without one, run the pipeline below.",
    )
    doc = _uploaded_doc(spec) if spec is not None else None
    if saved is not None:
        if st.button("Open for review", type="primary"):
            generation = Generation.model_validate_json(saved.getvalue())
            _opened(store.add(generation, doc))
        return
    if doc is None:
        return
    st.caption(f"{doc.doc_id}: {len(doc.sections)} sections, {len(doc.pages)} pages")
    _run_panel(store, doc, fake_dir)


@st.cache_data(show_spinner="Reading the spec (the first PDF downloads Docling's models)")
def _ingest_upload(name: str, data: bytes) -> Document:
    if name.lower().endswith(".json"):
        return Document.model_validate_json(data)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        return ingest(path, doc_id_for(name))


def _uploaded_doc(spec) -> Document | None:
    try:
        return _ingest_upload(spec.name, spec.getvalue())
    except ValueError as exc:
        st.error(f"Could not read {spec.name}: {exc}")
        return None


def _run_panel(store: ReviewStore, doc: Document, fake_dir: str | None) -> None:
    providers = ([FAKE] if fake_dir else []) + [*PRESETS, "anthropic"]
    provider = st.selectbox("Model provider", providers)
    model = st.text_input("Model", placeholder="the provider's default")
    llm = _llm(provider, model or None, fake_dir)
    paid = provider != FAKE
    confirmed = not paid or st.checkbox(
        f"Run {model_name(llm)} on every section and requirement. This calls a paid API."
    )
    if st.button("Run the pipeline", type="primary", disabled=not confirmed):
        with st.status("Running the pipeline", expanded=True) as status:
            result = run_pipeline(doc, llm, progress=lambda stage: st.write(f"{stage}…"))
            status.update(label="Pipeline done", state="complete")
        st.session_state.last_run = f"{model_name(llm)}: {result.cost()}"
        _opened(store.add(result.generation, doc))


def _llm(provider: str, model: str | None, fake_dir: str | None) -> LLMClient:
    if provider == FAKE:
        return FakeLLM.from_dir(fake_dir, model=model)
    if provider == "anthropic":
        return AnthropicLLM(model) if model else AnthropicLLM()
    return OpenAICompatLLM.preset(provider, **({"model": model} if model else {}))


def _opened(gid: str) -> None:
    st.session_state.generation = gid
    st.session_state.pop("item", None)
    st.rerun()


def _review(store: ReviewStore, gid: str) -> None:
    generation = store.generation(gid)
    doc = store.document(gid)
    latest = store.latest(gid)
    stats = store.stats(gid).overall
    doc_ids = sorted({r.source_anchor.doc for r in generation.requirements})
    doc_name = (doc.source or doc.doc_id) if doc else ", ".join(doc_ids) or "no requirements"
    st.title(f"Review: {doc_name}")
    st.caption(f"Items by {generation.model} · review id {gid[:12]}")
    if "last_run" in st.session_state:
        st.caption(f"Last run, {st.session_state.pop('last_run')}")
    cols = st.columns(5)
    cols[0].metric("Items", stats.items)
    cols[1].metric("Reviewed", stats.reviewed)
    cols[2].metric("Accepted unedited", stats.accepted)
    cols[3].metric("Edited · rejected", f"{stats.edited} · {stats.rejected}")
    rate = stats.acceptance_rate
    cols[4].metric("Acceptance rate", "-" if rate is None else f"{rate:.0%}")
    if doc is None:
        st.warning("No source document stored with this review: quotes show without context.")

    if not generation.items:
        st.info("This generation has no items to review.")
        _not_generated(generation)
        return
    shown = st.radio("Show", list(_FILTERS), horizontal=True, key="filter")
    items = [i for i in generation.items if _matches(latest.get(i.id), _FILTERS[shown])]
    if not items:
        st.success("Nothing left in this view.")
        _not_generated(generation)
        return
    ids = [i.id for i in items]
    current = st.session_state.get("item")
    reqs = {r.id: r for r in generation.requirements}
    item_id = st.selectbox(
        "Item",
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=lambda i: _label(i, latest.get(i), generation, reqs),
    )
    st.session_state.item = item_id
    item = next(i for i in items if i.id == item_id)

    left, right = st.columns(2)
    with left:
        for req_id in item.requirement_ids:
            _requirement(reqs.get(req_id), req_id, doc)
    with right:
        _item(store, gid, item, latest.get(item.id), ids)
    _not_generated(generation)


def _matches(decided: ReviewDecision | None, wanted: Decision | str | None) -> bool:
    if wanted is None:
        return True
    if wanted == "todo":
        return decided is None
    return decided is not None and decided.decision is wanted


def _label(item_id: str, decided, generation: Generation, reqs: dict[str, Requirement]) -> str:
    item = next(i for i in generation.items if i.id == item_id)
    req = reqs.get(item.requirement_ids[0])
    text = req.text if req else ""
    mark = _MARK[decided.decision if decided else None]
    return f"{mark} {item_id} · {text[:70]}{'…' if len(text) > 70 else ''}"


def _requirement(req: Requirement | None, req_id: str, doc: Document | None) -> None:
    st.subheader(f"Requirement {req_id}")
    if req is None:
        st.warning("This requirement is not in the generation.")
        return
    st.write(req.text)
    method = req.verification_method.value if req.verification_method else "unclassified"
    st.caption(f"{req.type.value} · {req.strength.value} · {method}")
    for c in req.criteria:
        st.markdown(f"- {_criterion(c)}")
    for cond in req.conditions:
        st.markdown(f"- when: {cond}")
    for flag in req.testability_flags:
        rewrite = f" Rewrite: {flag.suggested_rewrite}" if flag.suggested_rewrite else ""
        st.warning(f"{flag.kind.value}: {flag.reason}{rewrite}")
    view = source_view(req, doc)
    with st.container(border=True):
        st.caption(f"Source: {view.heading}")
        st.markdown(view.html(), unsafe_allow_html=True)
    if not view.found and doc is not None:
        st.caption("The anchor does not point at this quote in the stored document.")


_SYMBOL = {"lt": "<", "le": "≤", "eq": "=", "ge": "≥", "gt": ">"}


def _criterion(c: Criterion) -> str:
    if c.comparator is Comparator.BETWEEN:
        value = f"{c.value} to {c.upper_value}"
    else:
        value = f"{_SYMBOL.get(c.comparator.value, '') if c.comparator else ''} {c.value}"
    parts = [f"{c.parameter}:", value.strip(), c.unit or "", c.tolerance or ""]
    return " ".join(p for p in parts if p)


def _item(
    store: ReviewStore,
    gid: str,
    item: VerificationItem,
    decided: ReviewDecision | None,
    order: list[str],
) -> None:
    st.subheader(f"Item {item.id}")
    shown = item
    if decided is not None and decided.decision is Decision.EDIT:
        shown = item.model_copy(update={"fields": type(item.fields).model_validate(decided.edited)})
        st.caption("Showing your edit.")
    st.code(render(shown), language=None, wrap_lines=True)
    if decided is not None:
        who = f" by {decided.reviewer}" if decided.reviewer else ""
        st.caption(
            f"{_MARK[decided.decision]} {decided.decision.value}{who}, "
            f"{decided.decided_at:%Y-%m-%d %H:%M} UTC"
            + (f" · {decided.note}" if decided.note else "")
        )

    note = st.text_input("Note (optional)", key=f"note-{item.id}")
    accept, reject = st.columns(2)
    if accept.button("Accept", type="primary", width="stretch", key=f"acc-{item.id}"):
        _decide(store, gid, item.id, Decision.ACCEPT, order, note=note)
    if reject.button("Reject", width="stretch", key=f"rej-{item.id}"):
        _decide(store, gid, item.id, Decision.REJECT, order, note=note)

    with st.expander("Edit", expanded=False), st.form(f"edit-{item.id}"):
        values = to_form(item.kind, shown.fields.model_dump())
        boxes = {}
        for name, is_list in form_fields(item.kind):
            label = name.replace("_", " ").capitalize() + (" (one per line)" if is_list else "")
            boxes[name] = st.text_area(label, values[name], key=f"{item.id}-{name}")
        if st.form_submit_button("Save edit"):
            edited = fields_from_form(item.kind, boxes)
            _decide(store, gid, item.id, Decision.EDIT, order, note=note, edited=edited)


def _decide(
    store: ReviewStore,
    gid: str,
    item_id: str,
    decision: Decision,
    order: list[str],
    *,
    note: str = "",
    edited: dict | None = None,
) -> None:
    try:
        store.record(
            gid,
            item_id,
            decision,
            edited=edited,
            note=note,
            reviewer=st.session_state.get("reviewer", ""),
        )
    except ReviewError as exc:
        st.error(str(exc))
        return
    # Move on to the next item still to review, after this one in the list.
    latest = store.latest(gid)
    at = order.index(item_id)
    rest = order[at + 1 :] + order[:at]
    st.session_state.item = next((i for i in rest if i not in latest), item_id)
    st.rerun()


def _not_generated(generation: Generation) -> None:
    rows = (
        [f"rejected · {r.requirement_id} · {'; '.join(r.errors)}" for r in generation.rejected]
        + [f"failed · {f.requirement_id} · {f.error}" for f in generation.failed]
        + [f"unclassified · {i} · no verification method" for i in generation.unclassified]
        + [f"skipped · {s.requirement_id} · {s.reason}" for s in generation.skipped]
    )
    if not rows:
        return
    with st.expander(f"Requirements without an item ({len(rows)})"):
        for row in rows:
            st.markdown(f"- {row}")


main()
