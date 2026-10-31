"""The hosted app: sign in, then review in your own workspace, see its usage, delete its
data, or read how data is handled.

`spec2test deploy serve` starts it with SPEC2TEST_DEPLOY_CONFIG set. Every run calls the
one model the config names, through `CappedLLM`, so the monthly cap holds for every
workspace together. A signed-in email that no workspace lists sees nothing but the
data-handling page.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import streamlit as st

from spec2test.deploy import auth
from spec2test.deploy.budget import BudgetExceededError, CappedLLM, month_of, over_cap
from spec2test.deploy.config import (
    CONFIG_ENV,
    DeployConfig,
    DeployConfigError,
    data_handling,
    load_config,
)
from spec2test.deploy.workspace import Workspace, ledger, metrics
from spec2test.ingest import Document
from spec2test.review import ReviewStore, run_pipeline
from spec2test.review.app import _ingest_upload, _open_panel, _opened, _review, _upload_panel

PAGES = ("Review", "Your data", "Data handling")


def main() -> None:
    st.set_page_config(page_title="Spec2Test", layout="wide")
    try:
        config = load_config(os.environ[CONFIG_ENV])
    except DeployConfigError as exc:
        # Shown while the operator fixes the file; the app reads it again on every load.
        st.title("Spec2Test")
        st.error(f"The service is not configured correctly: {exc}")
        return
    email = auth.current_email()
    if email is None:
        _signed_out(config)
        return
    wid = config.workspace_for(email)
    if wid is None:
        st.title("Spec2Test")
        st.warning(f"{email} is not invited to a workspace. Ask the person who invited you.")
        if st.button("Sign out"):
            auth.sign_out()
        st.markdown(data_handling(config))
        return
    ws = Workspace(config, wid)
    # Decisions are logged under the signed-in email; there is no name to type.
    st.session_state.reviewer = email
    with st.sidebar:
        st.caption(f"{email} · {ws.name}")
        if st.button("Sign out"):
            auth.sign_out()
        page = st.radio("Page", PAGES, key="page")
    if page == "Review":
        _review_page(config, ws)
    elif page == "Your data":
        _data_page(config, ws)
    else:
        st.markdown(data_handling(config))


def _signed_out(config: DeployConfig) -> None:
    st.title("Spec2Test")
    st.write("Turn a requirements document into checklists, test cases and evidence items.")
    if st.button("Sign in", type="primary"):
        auth.sign_in()
    st.markdown(data_handling(config))


def _review_page(config: DeployConfig, ws: Workspace) -> None:
    store = ws.store()
    with st.sidebar:
        _open_panel(store)
        _upload_panel(store, None, run=lambda s, doc: _run_panel(config, ws, s, doc))
    gid = st.session_state.get("generation")
    if gid is None or gid not in {g.id for g in store.generations()}:
        st.title(f"Spec2Test · {ws.name}")
        st.info("Open a saved review, or upload a spec in the sidebar.")
        return
    _review(store, gid)


def _run_panel(config: DeployConfig, ws: Workspace, store: ReviewStore, doc: Document) -> None:
    spend = ledger(config).spend(month_of(datetime.now(UTC)))
    spent = over_cap(spend, config.cap)
    if spent is not None:
        st.error(f"This month's model budget is used up ({spent}). Runs start again next month.")
        return
    st.caption(f"Runs {config.llm.model_id} on every section and requirement.")
    if st.button("Run the pipeline", type="primary"):
        llm = CappedLLM(config.llm.client(), ledger(config), config.cap, ws.id)
        try:
            with st.status("Running the pipeline", expanded=True) as status:
                result = run_pipeline(doc, llm, progress=lambda stage: st.write(f"{stage}…"))
                status.update(label="Pipeline done", state="complete")
        except BudgetExceededError as exc:
            st.error(f"The run stopped: {exc}. Nothing from it was saved.")
            return
        st.session_state.last_run = f"{llm.model}: {result.cost()}"
        _opened(store.add(result.generation, doc))


def _data_page(config: DeployConfig, ws: Workspace) -> None:
    st.title(f"Your data · {ws.name}")
    m = metrics(config, ws.id)
    cols = st.columns(4)
    cols[0].metric("Documents processed", m.documents)
    cols[1].metric("Items reviewed", f"{m.reviewed} of {m.items}")
    rate = m.acceptance_rate
    cols[2].metric("Accepted unedited", "-" if rate is None else f"{rate:.0%}")
    cols[3].metric(f"Model calls in {m.month}", m.calls)

    st.subheader("Delete this workspace's data")
    st.write(
        "This removes every document, generated item and review decision in this "
        "workspace, for every member. It cannot be undone."
    )
    if st.session_state.pop("deleted", False):
        st.success("Deleted.")
    if not ws.has_data():
        st.caption("There is nothing stored for this workspace.")
        return
    typed = st.text_input(f"Type {ws.id} to confirm", key="confirm-delete")
    if st.button("Delete this workspace's data", disabled=typed.strip() != ws.id):
        ws.delete()
        # The parsed uploads are cached in memory too; drop them with the files.
        _ingest_upload.clear()
        for key in ("generation", "item", "last_run", "confirm-delete"):
            st.session_state.pop(key, None)
        st.session_state.deleted = True
        st.rerun()


main()
