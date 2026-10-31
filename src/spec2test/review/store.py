"""The review log: a SQLite file holding each Generation under review, its source Document
and every decision made on its items.

Decisions are only ever added, never changed, so the log keeps each item's history; the
latest decision for an item is the one that counts. A Generation is stored under the sha256
of its JSON, so a regenerated set of items starts a fresh review instead of inheriting
decisions made on different text.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from spec2test.generate import KIND_FOR_METHOD, Generation
from spec2test.ingest import Document
from spec2test.review.models import FIELDS, Decision, ReviewDecision, ReviewStats, Tally
from spec2test.schema import VerificationItem, VerificationMethod

DEFAULT_DB = Path("review.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    doc_id TEXT,
    model TEXT NOT NULL,
    added_at TEXT NOT NULL,
    generation TEXT NOT NULL,
    document TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES generations(id),
    item_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    method TEXT,
    decision TEXT NOT NULL,
    fields TEXT NOT NULL,
    edited TEXT,
    note TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_item ON decisions (generation_id, item_id, seq);
"""


class ReviewError(ValueError):
    """A decision the log cannot take: an unknown item, or an edit that is not one."""


def generation_id(generation: Generation) -> str:
    return hashlib.sha256(generation.model_dump_json().encode("utf-8")).hexdigest()


class StoredGeneration(BaseModel):
    id: str
    model: str
    doc_id: str | None
    added_at: datetime
    items: int


class ReviewStore:
    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(path)
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

    def add(self, generation: Generation, document: Document | None = None) -> str:
        """Store `generation` (and its source, if given) for review; returns its id. Adding
        it again keeps its decisions, and stores `document` if it had none."""
        gid = generation_id(generation)
        doc_id = document.doc_id if document else _doc_id(generation)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO generations VALUES (?, ?, ?, ?, ?, NULL)",
                (gid, doc_id, generation.model, _now().isoformat(), generation.model_dump_json()),
            )
            if document is not None:
                db.execute(
                    "UPDATE generations SET document = ? WHERE id = ? AND document IS NULL",
                    (document.model_dump_json(), gid),
                )
        return gid

    def generations(self) -> list[StoredGeneration]:
        """Every stored Generation, newest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, model, doc_id, added_at, generation FROM generations "
                "ORDER BY added_at DESC, rowid DESC"
            ).fetchall()
        return [
            StoredGeneration(
                id=gid,
                model=model,
                doc_id=doc_id,
                added_at=added_at,
                items=len(json.loads(gen)["items"]),
            )
            for gid, model, doc_id, added_at, gen in rows
        ]

    def generation(self, gid: str) -> Generation:
        return Generation.model_validate_json(self._column(gid, "generation"))

    def document(self, gid: str) -> Document | None:
        doc = self._column(gid, "document")
        return None if doc is None else Document.model_validate_json(doc)

    def _column(self, gid: str, column: str) -> Any:
        with self._connect() as db:
            row = db.execute(f"SELECT {column} FROM generations WHERE id = ?", (gid,)).fetchone()
        if row is None:
            raise KeyError(gid)
        return row[0]

    def record(
        self,
        gid: str,
        item_id: str,
        decision: Decision,
        *,
        edited: dict[str, Any] | None = None,
        note: str = "",
        reviewer: str = "",
    ) -> ReviewDecision:
        """Log a decision on one item. An edit needs `edited`, the item's new fields, which
        must be valid for its kind and differ from the generated ones."""
        generation = self.generation(gid)
        item = next((i for i in generation.items if i.id == item_id), None)
        if item is None:
            raise ReviewError(f"{item_id!r} is not an item of generation {gid[:12]}")
        fields = item.fields.model_dump()
        decision = Decision(decision)
        if decision is Decision.EDIT:
            if edited is None:
                raise ReviewError("an edit needs the edited fields")
            try:
                edited = FIELDS[item.kind].model_validate(edited).model_dump()
            except ValidationError as exc:
                raise ReviewError(f"not a valid {item.kind.value}: {exc}") from exc
            if edited == fields:
                raise ReviewError("the edit changes nothing; accept the item instead")
        elif edited is not None:
            raise ReviewError(f"only an edit takes edited fields, not {decision.value!r}")
        logged = ReviewDecision(
            generation_id=gid,
            item_id=item_id,
            kind=item.kind,
            method=_method(generation, item),
            decision=decision,
            fields=fields,
            edited=edited,
            note=note,
            reviewer=reviewer,
            decided_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO decisions (generation_id, item_id, kind, method, decision, fields, "
                "edited, note, reviewer, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    gid,
                    item_id,
                    logged.kind.value,
                    logged.method.value if logged.method else None,
                    logged.decision.value,
                    json.dumps(fields),
                    None if edited is None else json.dumps(edited),
                    note,
                    reviewer,
                    logged.decided_at.isoformat(),
                ),
            )
        return logged

    def history(self, gid: str, item_id: str | None = None) -> list[ReviewDecision]:
        """Every decision logged on the generation (or on one of its items), oldest first."""
        query = "SELECT * FROM decisions WHERE generation_id = ?"
        params: tuple[str, ...] = (gid,)
        if item_id is not None:
            query += " AND item_id = ?"
            params += (item_id,)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query + " ORDER BY seq", params).fetchall()
        return [_decision(row) for row in rows]

    def latest(self, gid: str) -> dict[str, ReviewDecision]:
        """The decision that counts for each reviewed item."""
        return {d.item_id: d for d in self.history(gid)}

    def stats(self, gid: str | None = None) -> ReviewStats:
        """The acceptance rate of one generation, or of every stored one."""
        gids = [gid] if gid is not None else [g.id for g in self.generations()]
        overall = Tally(items=0)
        by_kind: dict[str, Tally] = {}
        by_method: dict[str, Tally] = {}
        for g in gids:
            generation = self.generation(g)
            latest = self.latest(g)
            for item in generation.items:
                method = _method(generation, item)
                tallies = [
                    overall,
                    by_kind.setdefault(item.kind.value, Tally(items=0)),
                    by_method.setdefault(
                        method.value if method else "unclassified", Tally(items=0)
                    ),
                ]
                decided = latest.get(item.id)
                for t in tallies:
                    t.items += 1
                    if decided is None:
                        continue
                    if decided.decision is Decision.ACCEPT:
                        t.accepted += 1
                    elif decided.decision is Decision.EDIT:
                        t.edited += 1
                    else:
                        t.rejected += 1
        return ReviewStats(
            generations=gids,
            overall=overall,
            by_kind=dict(sorted(by_kind.items())),
            by_method=dict(sorted(by_method.items())),
        )


def _method(generation: Generation, item: VerificationItem) -> VerificationMethod | None:
    req = next((r for r in generation.requirements if r.id == item.requirement_ids[0]), None)
    if req is not None and req.verification_method is not None:
        return req.verification_method
    # The requirement is not in the generation: fall back to the method its kind implies,
    # when only one does.
    methods = [m for m, k in KIND_FOR_METHOD.items() if k is item.kind]
    return methods[0] if len(methods) == 1 else None


def _doc_id(generation: Generation) -> str | None:
    docs = {r.source_anchor.doc for r in generation.requirements}
    return docs.pop() if len(docs) == 1 else None


def _decision(row: sqlite3.Row) -> ReviewDecision:
    return ReviewDecision(
        generation_id=row["generation_id"],
        item_id=row["item_id"],
        kind=row["kind"],
        method=row["method"],
        decision=row["decision"],
        fields=json.loads(row["fields"]),
        edited=None if row["edited"] is None else json.loads(row["edited"]),
        note=row["note"],
        reviewer=row["reviewer"],
        decided_at=row["decided_at"],
    )


def _now() -> datetime:
    return datetime.now(UTC)
