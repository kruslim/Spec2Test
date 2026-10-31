"""Per-workspace data: each workspace gets its own folder under the deployment's data
directory, holding its own review log (documents, generations and decisions). Nothing is
shared between workspaces, so one partner's app can only ever open its own log.

Deleting a workspace's data removes that folder. The usage ledger keeps the workspace's
rows (token counts, model and time; no content), so the month's spend stays correct.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from spec2test.deploy.budget import UsageLedger, month_of
from spec2test.deploy.config import DeployConfig
from spec2test.review import ReviewStore, Tally


class Workspace:
    def __init__(self, config: DeployConfig, wid: str) -> None:
        if wid not in config.workspaces:
            raise KeyError(f"no workspace {wid!r} in the deployment config")
        self.id = wid
        self.name = config.workspaces[wid].name
        self.dir = workspaces_dir(config) / wid

    @property
    def review_db(self) -> Path:
        return self.dir / "review.sqlite"

    def store(self) -> ReviewStore:
        self.dir.mkdir(parents=True, exist_ok=True)
        return ReviewStore(self.review_db)

    def has_data(self) -> bool:
        return self.dir.exists() and any(self.dir.iterdir())

    def delete(self) -> bool:
        """Remove every document, generation and decision of this workspace; True if there
        was anything to remove."""
        if not self.dir.exists():
            return False
        shutil.rmtree(self.dir)
        return True


def workspaces_dir(config: DeployConfig) -> Path:
    return config.data_dir / "workspaces"


def ledger(config: DeployConfig) -> UsageLedger:
    return UsageLedger(config.data_dir / "usage.sqlite")


class WorkspaceMetrics(BaseModel):
    workspace: str
    name: str
    documents: int
    generations: int
    items: int
    reviewed: int
    accepted: int
    edited: int
    rejected: int
    acceptance_rate: float | None
    month: str
    calls: int
    tokens: int
    dollars: float
    unpriced_calls: int


def metrics(config: DeployConfig, wid: str, month: str | None = None) -> WorkspaceMetrics:
    """Documents processed, items reviewed and the acceptance rate of one workspace, read
    from its review log, plus its model use in `month` (default: this month)."""
    ws = Workspace(config, wid)
    month = month or month_of(datetime.now(UTC))
    spend = ledger(config).spend(month, wid)
    saved, tally = [], Tally(items=0)
    if ws.review_db.is_file():
        store = ReviewStore(ws.review_db)
        saved, tally = store.generations(), store.stats().overall
    return WorkspaceMetrics(
        workspace=wid,
        name=ws.name,
        documents=len({g.doc_id or g.id for g in saved}),
        generations=len(saved),
        items=tally.items,
        reviewed=tally.reviewed,
        accepted=tally.accepted,
        edited=tally.edited,
        rejected=tally.rejected,
        acceptance_rate=tally.acceptance_rate,
        month=month,
        calls=spend.calls,
        tokens=spend.tokens,
        dollars=spend.dollars,
        unpriced_calls=spend.unpriced_calls,
    )


def metrics_report(rows: list[WorkspaceMetrics]) -> str:
    """One line per workspace, then the total; dollars cover priced calls only."""
    lines = []
    for m in rows:
        rate = "-" if m.acceptance_rate is None else f"{m.acceptance_rate:.1%}"
        unpriced = f" (+{m.unpriced_calls} unpriced calls)" if m.unpriced_calls else ""
        lines.append(
            f"{m.workspace}: {m.documents} documents, {m.items} items, {m.reviewed} reviewed, "
            f"acceptance {rate}; {m.month}: {m.calls} calls, {m.tokens:,} tokens, "
            f"${m.dollars:.2f}{unpriced}"
        )
    reviewed = sum(m.reviewed for m in rows)
    accepted = sum(m.accepted for m in rows)
    rate = f"{accepted / reviewed:.1%}" if reviewed else "-"
    lines.append(
        f"total: {sum(m.documents for m in rows)} documents, {reviewed} items reviewed, "
        f"acceptance {rate}"
    )
    return "\n".join(lines)
