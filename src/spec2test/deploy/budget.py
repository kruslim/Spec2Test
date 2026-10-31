"""The monthly LLM cost cap: a ledger of every hosted model call, and an `LLMClient` that
checks the month's spend before each call and records the call after it.

The ledger keeps token counts, the model, the workspace and the time; never a prompt, a
document or an answer. Deleting a workspace's data keeps its ledger rows (see
`spec2test.deploy.workspace`), so the month's spend, and with it the cap, stays correct.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from spec2test.deploy.config import Cap
from spec2test.llm import LLMClient, TokenUsage, model_name, price_of

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    workspace TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    dollars REAL,
    called_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_month ON calls (month, workspace);
"""


class BudgetExceededError(Exception):
    """The month's cap is reached. Not an `LLMError` on purpose: the stages record an
    `LLMError` against one section or item and carry on, but a spent budget ends the run."""


class Spend(BaseModel):
    month: str
    calls: int
    tokens: int
    dollars: float
    unpriced_calls: int


def month_of(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m")


class UsageLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, workspace: str, call: TokenUsage, *, at: datetime | None = None) -> None:
        when = at or datetime.now(UTC)
        price = price_of(call.model)
        with self._connect() as db:
            db.execute(
                "INSERT INTO calls (month, workspace, model, input_tokens, output_tokens, "
                "dollars, called_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    month_of(when),
                    workspace,
                    call.model,
                    call.input_tokens,
                    call.output_tokens,
                    None if price is None else price.dollars(call),
                    when.isoformat(),
                ),
            )

    def spend(self, month: str, workspace: str | None = None) -> Spend:
        """What `month` ('2026-10') cost, over every workspace or one."""
        query = (
            "SELECT COUNT(*), COALESCE(SUM(input_tokens + output_tokens), 0), "
            "COALESCE(SUM(dollars), 0), COUNT(*) - COUNT(dollars) FROM calls WHERE month = ?"
        )
        params: tuple[str, ...] = (month,)
        if workspace is not None:
            query += " AND workspace = ?"
            params += (workspace,)
        with self._connect() as db:
            calls, tokens, dollars, unpriced = db.execute(query, params).fetchone()
        return Spend(
            month=month, calls=calls, tokens=tokens, dollars=dollars, unpriced_calls=unpriced
        )


def over_cap(spend: Spend, cap: Cap) -> str | None:
    """Why `spend` has reached `cap`, or None while there is budget left."""
    if cap.monthly_tokens is not None and spend.tokens >= cap.monthly_tokens:
        return f"{spend.tokens:,} of {cap.monthly_tokens:,} tokens used in {spend.month}"
    if cap.monthly_usd is not None and spend.dollars >= cap.monthly_usd:
        return f"${spend.dollars:.2f} of ${cap.monthly_usd:.2f} spent in {spend.month}"
    return None


class CappedLLM(LLMClient):
    """Wraps the deployment's client: refuses a call once the month's cap is reached, and
    writes each call's tokens to the ledger under `workspace`, failed calls included (they
    are billed too)."""

    def __init__(self, inner: LLMClient, ledger: UsageLedger, cap: Cap, workspace: str) -> None:
        self.inner = inner
        self.ledger = ledger
        self.cap = cap
        self.workspace = workspace
        self.model = model_name(inner)

    def check(self) -> None:
        reason = over_cap(self.ledger.spend(month_of(datetime.now(UTC))), self.cap)
        if reason is not None:
            raise BudgetExceededError(f"the monthly model budget is used up: {reason}")

    def generate(self, prompt, schema, *, system=None):
        self.check()
        start = len(self.inner.usage)
        try:
            return self.inner.generate(prompt, schema, system=system)
        finally:
            for call in self.inner.usage[start:]:
                self.ledger.add(self.workspace, call)
                self.usage.append(call)
