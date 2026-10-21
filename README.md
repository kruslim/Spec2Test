# Spec2Test

Turns a requirements document into verification items: inspection checklists, test cases
and evidence items. Every requirement is reduced to one canonical record first
(`spec2test.schema.Requirement`); each output is a rendering of that record.

See [docs/PLAN.md](docs/PLAN.md) for the build plan.

## Ingest

`spec2test.ingest.ingest(path)` reads a PDF, DOCX, XLSX or Markdown spec through
[Docling](https://github.com/docling-project/docling) and returns a `Document`: one
canonical text, its page spans, and one `Section` per numbered clause (`S5.5.1`, `5.1.1.`,
`4.2.1`, `T.1.2`, `Annex 3`, ...) with its page, parent clause and character offsets.
Every offset points into `Document.text`, so a later stage can check a quote verbatim.

```bash
uv run spec2test ingest path/to/spec.pdf          # outline: page, clause, title
uv run spec2test ingest path/to/spec.pdf --json   # the whole Document
```

PDFs must be born-digital (no OCR yet). The first PDF parse downloads Docling's layout
and table models (~500 MB) to the Hugging Face cache, and later runs are offline.

The gold-set source documents live in `corpora/`, git-ignored; see
[corpora/README.md](corpora/README.md).

## Development

Requires [uv](https://docs.astral.sh/uv/). One command installs everything, including the
pinned Python version:

```bash
uv sync
```

Run the checks CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests run offline against `FakeLLM`, which replays the fixtures in `tests/fixtures/llm/`.
To also run the live test against the Anthropic API (costs money; needs credentials):

```bash
SPEC2TEST_LIVE=1 uv run pytest -m live
```

To also parse the four gold-set documents (they must be in `corpora/` first):

```bash
SPEC2TEST_CORPORA=1 uv run pytest -m corpora
```
