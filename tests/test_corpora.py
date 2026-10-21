"""The corpora manifest, fetching, the CLI, and (opt-in) parsing the gold-set documents."""

import hashlib
import io
import os
import re
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from spec2test import corpora
from spec2test.cli import main
from spec2test.corpora import CorpusDocument, CorpusError, Status

REPO = Path(__file__).parent.parent
CORPORA = REPO / "corpora"
CONTENT = b"%PDF-1.7 invented corpus document"
SHA = hashlib.sha256(CONTENT).hexdigest()


def entry(**overrides):
    data = {
        "id": "doc",
        "title": "Invented document",
        "file": "doc.pdf",
        "url": "https://example.test/doc.pdf",
        "fetch": "auto",
        "sha256": SHA,
        "licence": "Invented for the tests.",
    }
    return CorpusDocument.model_validate({**data, **overrides})


def write_manifest(folder: Path, body: str) -> Path:
    (folder / corpora.MANIFEST).write_text(body, encoding="utf-8")
    return folder


def test_repo_manifest_lists_the_four_gold_set_documents():
    docs = corpora.load_manifest(CORPORA)
    assert [d.id for d in docs] == ["fmvss-111", "unece-r158", "nasa-std-5001", "fsae-rules"]
    assert all(d.sha256 for d in docs if d.fetch == "auto")


def test_corpus_documents_are_git_ignored():
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "corpora/*" in gitignore
    assert {"!corpora/manifest.toml", "!corpora/README.md"} <= set(gitignore)


def test_manifest_rejects_duplicates(tmp_path):
    doc = '[[documents]]\nid = "a"\ntitle = "A"\nfile = "a.pdf"\nurl = "u"\n'
    doc += 'fetch = "manual"\nlicence = "l"\n'
    write_manifest(tmp_path, doc + doc)
    with pytest.raises(CorpusError, match="duplicate"):
        corpora.load_manifest(tmp_path)


def test_manifest_needs_a_hash_for_automatic_fetches(tmp_path):
    write_manifest(
        tmp_path,
        '[[documents]]\nid = "a"\ntitle = "A"\nfile = "a.pdf"\nurl = "u"\n'
        'fetch = "auto"\nlicence = "l"\n',
    )
    with pytest.raises(CorpusError, match="needs a sha256"):
        corpora.load_manifest(tmp_path)


def test_status(tmp_path):
    doc = entry()
    assert corpora.status(doc, tmp_path) is Status.MISSING
    (tmp_path / "doc.pdf").write_bytes(CONTENT)
    assert corpora.status(doc, tmp_path) is Status.OK
    assert corpora.status(entry(sha256=None, fetch="manual"), tmp_path) is Status.UNPINNED
    (tmp_path / "doc.pdf").write_bytes(b"changed")
    assert corpora.status(doc, tmp_path) is Status.MISMATCH


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_downloads_and_checks_the_hash(tmp_path, monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return FakeResponse(CONTENT)

    monkeypatch.setattr(corpora.urllib.request, "urlopen", urlopen)
    path = corpora.fetch(entry(), tmp_path)
    assert path.read_bytes() == CONTENT
    assert requests[0].full_url == "https://example.test/doc.pdf"
    assert requests[0].get_header("User-agent").startswith("spec2test/")
    corpora.fetch(entry(), tmp_path)
    assert len(requests) == 1  # already there and matching


def test_fetch_rejects_a_changed_download(tmp_path, monkeypatch):
    monkeypatch.setattr(
        corpora.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"other")
    )
    with pytest.raises(CorpusError, match="sha256"):
        corpora.fetch(entry(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_fetch_never_downloads_a_manual_document(tmp_path):
    with pytest.raises(CorpusError, match="by hand"):
        corpora.fetch(entry(fetch="manual", notes="Save it from a browser."), tmp_path)


def test_parse_refuses_a_missing_or_changed_file(tmp_path):
    with pytest.raises(CorpusError, match="missing"):
        corpora.parse(entry(), tmp_path)
    (tmp_path / "doc.pdf").write_bytes(b"changed")
    with pytest.raises(CorpusError, match="does not match"):
        corpora.parse(entry(), tmp_path)


def test_cli_ingest(tmp_path, capsys):
    path = tmp_path / "spec.md"
    path.write_text("# Spec\n\n## 1. SCOPE\n\n1.1 Purpose. Text.\n", encoding="utf-8")
    assert main(["ingest", str(path)]) == 0
    out = capsys.readouterr().out
    assert "p1    1  SCOPE" in out
    assert "p1      1.1  Purpose" in out
    assert "3 sections (2 numbered)" in out

    assert main(["ingest", str(path), "--json"]) == 0
    assert '"doc_id": "spec"' in capsys.readouterr().out


def test_cli_ingest_reports_bad_input(tmp_path, capsys):
    assert main(["ingest", str(tmp_path / "notes.txt")]) == 2
    assert "unsupported format" in capsys.readouterr().err


def test_cli_corpora_status(tmp_path, capsys):
    write_manifest(
        tmp_path,
        f'[[documents]]\nid = "doc"\ntitle = "D"\nfile = "doc.pdf"\nurl = "u"\n'
        f'fetch = "auto"\nsha256 = "{SHA}"\nlicence = "l"\n'
        '[[documents]]\nid = "hand"\ntitle = "H"\nfile = "hand.pdf"\nurl = "u"\n'
        'fetch = "manual"\nlicence = "l"\nnotes = "Save it from a browser."\n',
    )
    (tmp_path / "doc.pdf").write_bytes(CONTENT)
    assert main(["corpora", "status", "--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "doc              ok" in out
    assert "hand             missing  (Save it from a browser.)" in out

    (tmp_path / "hand.pdf").write_bytes(b"x")
    assert main(["corpora", "status", "hand", "--dir", str(tmp_path)]) == 0
    assert f"unpinned  sha256 = {hashlib.sha256(b'x').hexdigest()}" in capsys.readouterr().out
    assert main(["corpora", "status", "nope", "--dir", str(tmp_path)]) == 2


# The Phase 1 "done when": every gold-set document parses into anchored sections. The
# documents are not in git, so this runs only on request (see corpora/README.md).

# Clauses checked by hand against each document: (anchor, page, title).
SPOT_CHECKS = {
    "fmvss-111": [
        ("S5.5.3", 3, "Response time"),
        ("S6.2.1", 3, "Field of view"),
        ("S14.1.2.5.1", 11, None),
        ("S15.7", 16, "Calculation of complying vehicles"),
    ],
    "unece-r158": [
        ("Part II", 11, "Installation of devices for reversing motion"),
        ("6.1.2.2.2.1", 9, None),
        ("15.2.1", 14, "Requirements"),
        ("16.1.2.1", 17, "Response time"),
        # The trailing "1" is the source's footnote marker.
        (
            "Annex 8 Appendix 1",
            34,
            'Description of the three dimensional "H" point machine (3-D H machine) 1',
        ),
        ("Annex 9 1.3.1", 36, "Lighting"),
        ("Annex 10", 40, "Test methods for detection systems"),
    ],
    "nasa-std-5001": [
        ("1.1", 6, "Purpose"),
        ("4.2.1", 17, "Metallic Structures"),
        ("4.5.1", 25, None),
    ],
    "fsae-rules": [
        ("GR.2.2.1", 6, None),
        ("F.5", 31, "CHASSIS REQUIREMENTS"),
        ("F.5.1.1", 31, None),
        ("T.1", 55, "COCKPIT"),
        ("EV", 91, "ELECTRIC VEHICLES"),
        ("EV.5.1", 95, "Tractive Battery"),
    ],
}
MIN_NUMBERED = {"fmvss-111": 95, "unece-r158": 230, "nasa-std-5001": 65, "fsae-rules": 1700}
FURNITURE = {
    "fmvss-111": ["VerDate", "DSKJY6RXB4PROD", "49 CFR Ch. V", "Standard No. 113"],
    "unece-r158": ["E/ECE/TRANS/505/Rev.3/Add.157"],
    "nasa-std-5001": ["APPROVED FOR PUBLIC RELEASE", "NASA-STD-5001B W/CHANGE 3"],
    "fsae-rules": ["Page 6 of 145", "SAE International Page"],
}
# Each document's own table of contents: (its PDF pages, a regex for one entry, the least
# number of entries it must yield). Every entry must be a section on the page it states.
TABLES_OF_CONTENTS = {
    "unece-r158": ((3, 4), r"^(?P<num>\d{1,2}\.|Annex \d+)\s.*?\.{3,}\s*(?P<page>\d+)\s*$", 20),
    "nasa-std-5001": (
        (4, 5),
        r"^(?P<num>\d+\.(?:\d+(?:\.\d+)*)?)\s.*?\.{3,}\s*(?P<page>\d+)\s*$",
        25,
    ),
    "fsae-rules": ((2, 4), r"^(?P<num>[A-Z]{1,3}(?:\.\d+)?)\s.*?\.{3,}\s*(?P<page>\d+)\s*$", 110),
}


def corpus_doc(doc_id):
    [doc] = [d for d in corpora.load_manifest(CORPORA) if d.id == doc_id]
    return doc


@pytest.fixture(scope="module")
def parsed():
    cache = {}

    def get(doc):
        if doc.id not in cache:
            cache[doc.id] = corpora.parse(doc, CORPORA)
        return cache[doc.id]

    return get


@pytest.mark.corpora
@pytest.mark.skipif(os.environ.get("SPEC2TEST_CORPORA") != "1", reason="set SPEC2TEST_CORPORA=1")
@pytest.mark.parametrize("doc", corpora.load_manifest(CORPORA), ids=lambda d: d.id)
def test_gold_set_document_parses(doc, parsed):
    state = corpora.status(doc, CORPORA)
    assert state is not Status.MISSING, f"put {doc.file} in corpora/ ({doc.notes or doc.url})"
    assert state is not Status.MISMATCH
    result = parsed(doc)

    numbered = [s for s in result.sections if s.section_number is not None]
    assert len(numbered) >= MIN_NUMBERED[doc.id]
    pages = {p.page for p in result.pages}
    for s in result.sections:
        assert result.text[s.start : s.end] == s.text
        assert {s.page, s.page_end} <= pages
    for anchor, page, title in SPOT_CHECKS.get(doc.id, []):
        section = result.section(anchor)
        assert (section.page, section.title) == (page, title), anchor
    for text in FURNITURE.get(doc.id, []):
        assert text not in result.text


@pytest.mark.corpora
@pytest.mark.skipif(os.environ.get("SPEC2TEST_CORPORA") != "1", reason="set SPEC2TEST_CORPORA=1")
def test_fmvss_page_break_is_rejoined(parsed):
    s53 = parsed(corpus_doc("fmvss-111")).section("S5.3")
    assert "a convex mirror installed on the passenger's side." in s53.text
    assert (s53.page, s53.page_end) == (2, 2)


@pytest.mark.corpora
@pytest.mark.skipif(os.environ.get("SPEC2TEST_CORPORA") != "1", reason="set SPEC2TEST_CORPORA=1")
@pytest.mark.parametrize("doc_id", TABLES_OF_CONTENTS)
def test_every_contents_entry_is_a_section_on_its_page(doc_id, parsed):
    (first, last), pattern, at_least = TABLES_OF_CONTENTS[doc_id]
    doc = corpus_doc(doc_id)
    pdf = pdfium.PdfDocument(CORPORA / doc.file)
    try:
        toc = "\n".join(pdf[i].get_textpage().get_text_range() for i in range(first - 1, last))
    finally:
        pdf.close()
    entries = [(m["num"].rstrip("."), int(m["page"])) for m in re.finditer(pattern, toc, re.M)]
    assert len(entries) >= at_least

    result = parsed(doc)
    first_page = {}
    for s in result.sections:
        first_page.setdefault(s.anchor, s.page)
    misplaced = [
        (num, page, first_page.get(num)) for num, page in entries if first_page.get(num) != page
    ]
    assert misplaced == []
