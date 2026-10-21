"""Gold sets: labeled requirements to score extraction against.

A gold set is a JSONL file, one labeled `Requirement` per line. Labeling offsets by hand
is impractical, so a line may leave `source_anchor.page`, `start` and `end` out: the
loader finds the quote in the cited section and fills them in. Every record is then put
through the same citation check extraction uses, so a gold quote is always the source
text at its offsets.

An optional TOML file next to it (same name, `.toml`) states the scope and who labeled it:

    scope = ["S5", "S6"]          # these sections and their subsections, labeled completely
    labeler = "claude-opus-5-5"   # a person's name, or the model that labeled it
    reviewed = false              # true once a person has checked every line

Extraction is scored inside the scope only. Without the file the scope is the whole
document. See `gold/README.md`.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spec2test.extract import CitationError, check_citation, locate, section_at
from spec2test.ingest import Document, Section
from spec2test.schema import Requirement

DEFAULT_DIR = Path("gold")


class GoldError(ValueError):
    """The gold set does not load or does not check out against its document. The message
    lists every problem, one per line, each with its file and line."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Meta(_Record):
    scope: list[str] | None = Field(default=None, min_length=1)
    labeler: str | None = None
    reviewed: bool = False


class GoldSet(_Record):
    path: str
    doc_id: str
    scope: list[str] | None = Field(
        description="Section anchors labeled completely, with their subsections; None for "
        "the whole document."
    )
    labeler: str | None = Field(
        default=None, description="Who labeled it: a person, or a model ID."
    )
    reviewed: bool = Field(default=False, description="A person has checked every line.")
    requirements: list[Requirement]


def gold_path(corpus: str, directory: str | Path = DEFAULT_DIR) -> Path:
    return Path(directory) / f"{corpus}.jsonl"


def load_gold(path: str | Path, doc: Document) -> GoldSet:
    """Read the gold set at `path`, resolve its anchors in `doc` and check every record."""
    path = Path(path)
    errors: list[str] = []
    meta = _load_meta(path, doc, errors)
    scope = meta.scope
    requirements: list[Requirement] = []
    ids: dict[str, int] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{n}"
        try:
            req = _resolve(json.loads(line), doc)
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: not JSON: {exc}")
            continue
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['msg']}" for e in exc.errors()
            )
            errors.append(f"{where}: {problems}")
            continue
        except (CitationError, KeyError, ValueError) as exc:
            errors.append(f"{where}: {exc}")
            continue
        problems = check_citation(req, doc)
        if req.id in ids:
            problems.append(f"duplicate id {req.id!r} (first on line {ids[req.id]})")
        section = section_at(doc, req.source_anchor.start)
        if scope is not None and section is not None and not in_scope(doc, section, scope):
            problems.append(f"section {req.source_anchor.section!r} is outside the scope")
        errors.extend(f"{where}: {p}" for p in problems)
        ids.setdefault(req.id, n)
        requirements.append(req)
    if errors:
        raise GoldError("\n".join(errors))
    return GoldSet(
        path=str(path),
        doc_id=doc.doc_id,
        scope=scope,
        labeler=meta.labeler,
        reviewed=meta.reviewed,
        requirements=requirements,
    )


def in_scope(doc: Document, section: Section, scope: list[str] | None) -> bool:
    """True when `section` or one of its enclosing sections is in `scope`."""
    if scope is None:
        return True
    current: Section | None = section
    while current is not None:
        if current.anchor in scope:
            return True
        current = None if current.parent is None else doc.sections[current.parent]
    return False


def _load_meta(path: Path, doc: Document, errors: list[str]) -> _Meta:
    toml = path.with_suffix(".toml")
    if not toml.is_file():
        return _Meta()
    try:
        with toml.open("rb") as f:
            meta = _Meta.model_validate(tomllib.load(f))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        errors.append(f"{toml.name}: {exc}")
        return _Meta()
    anchors = {s.anchor for s in doc.sections}
    errors.extend(
        f"{toml.name}: no section {a!r} in {doc.doc_id}"
        for a in meta.scope or []
        if a not in anchors
    )
    return meta


def _resolve(data: dict, doc: Document) -> Requirement:
    """The record on one gold line, with any missing page and offsets found from its quote."""
    anchor = data.get("source_anchor")
    if isinstance(anchor, dict) and not {"start", "end"} <= anchor.keys():
        if "section" not in anchor or "quote" not in anchor:
            raise ValueError("source_anchor needs section and quote to find the offsets")
        try:
            section = doc.section(anchor["section"])
        except KeyError:
            raise ValueError(f"no section {anchor['section']!r} in {doc.doc_id}") from None
        start, end = locate(anchor["quote"], section, doc)
        anchor = {
            **anchor,
            "page": anchor.get("page", doc.page_at(start)),
            "start": start,
            "end": end,
            "quote": doc.text[start:end],
        }
        data = {**data, "source_anchor": anchor}
    return Requirement.model_validate(data)
