"""Gold-set source documents: a committed manifest, with the files kept out of git.

See `corpora/README.md`. Only public documents are ever fetched, and nothing fetched is
committed.
"""

from __future__ import annotations

import hashlib
import tomllib
import urllib.request
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spec2test import __version__
from spec2test.ingest import Document, ingest

DEFAULT_DIR = Path("corpora")
MANIFEST = "manifest.toml"
_TIMEOUT_S = 60


class CorpusError(Exception):
    """A corpus document is missing, changed, or could not be fetched."""


class Status(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNPINNED = "unpinned"
    """Present, but the manifest has no sha256 to check it against."""
    MISMATCH = "mismatch"


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    file: str
    url: str
    fetch: Literal["auto", "manual"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    licence: str
    end_marker: str | None = Field(
        default=None, description="Passed to `ingest`: where this document's own text ends."
    )
    notes: str | None = None


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[CorpusDocument]


def load_manifest(directory: str | Path = DEFAULT_DIR) -> list[CorpusDocument]:
    path = Path(directory) / MANIFEST
    with path.open("rb") as f:
        docs = _Manifest.model_validate(tomllib.load(f)).documents
    for field in ("id", "file"):
        values = [getattr(d, field) for d in docs]
        if len(values) != len(set(values)):
            raise CorpusError(f"{path}: duplicate document {field}")
    for d in docs:
        if d.fetch == "auto" and d.sha256 is None:
            raise CorpusError(f"{path}: {d.id} is fetched automatically, so it needs a sha256")
    return docs


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def status(doc: CorpusDocument, directory: str | Path = DEFAULT_DIR) -> Status:
    path = Path(directory) / doc.file
    if not path.is_file():
        return Status.MISSING
    if doc.sha256 is None:
        return Status.UNPINNED
    return Status.OK if sha256_of(path) == doc.sha256 else Status.MISMATCH


def fetch(doc: CorpusDocument, directory: str | Path = DEFAULT_DIR) -> Path:
    """Download `doc` unless a matching copy is already there."""
    path = Path(directory) / doc.file
    if status(doc, directory) is Status.OK:
        return path
    if doc.fetch == "manual":
        raise CorpusError(f"{doc.id} has to be downloaded by hand: {doc.notes or doc.url}")
    partial = path.with_name(path.name + ".part")
    request = urllib.request.Request(doc.url, headers={"User-Agent": f"spec2test/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            partial.write_bytes(response.read())
        actual = sha256_of(partial)
        if actual != doc.sha256:
            raise CorpusError(
                f"{doc.id}: downloaded file has sha256 {actual}, manifest says {doc.sha256}"
            )
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def parse(doc: CorpusDocument, directory: str | Path = DEFAULT_DIR) -> Document:
    """Check `doc` against the manifest, then ingest it."""
    state = status(doc, directory)
    if state is Status.MISSING:
        raise CorpusError(f"{doc.id}: {Path(directory) / doc.file} is missing")
    if state is Status.MISMATCH:
        raise CorpusError(f"{doc.id}: {doc.file} does not match the manifest's sha256")
    return ingest(Path(directory) / doc.file, doc.id, end_marker=doc.end_marker)
