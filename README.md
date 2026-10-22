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

## Extract

`spec2test.extract.extract(document, llm)` sends each section with normative wording
(shall, should, must, may, ...) to the model and returns an `Extraction`: cited
`Requirement` records, compound requirements split one obligation per record. The model
states each quote; the pipeline finds it in the section and fills in the offsets, page
and section. A quote that is not in the section verbatim goes back to the model once with
the error, then lands in `Extraction.rejected`, never in `requirements`.
`check_citation(requirement, document)` verifies any record's anchor against the source.

```bash
uv run spec2test extract path/to/spec.pdf                  # live, Kimi Code (KIMI_CODE_API_KEY)
uv run spec2test extract doc.json --provider anthropic     # live, Anthropic API
uv run spec2test extract doc.json --fake tests/fixtures/llm  # offline, FakeLLM fixtures
```

Kimi is the default extraction model. `--provider kimi-code` uses
`spec2test.llm.OpenAICompatLLM`, a client for any OpenAI-compatible chat-completions
endpoint. It sends the JSON Schema in the prompt and validates the reply with Pydantic.

`doc.json` is a Document saved with `spec2test ingest --json`.

The gold-set source documents live in `corpora/`, git-ignored; see
[corpora/README.md](corpora/README.md).

## Lint

`spec2test.lint.lint(requirements, llm)` flags what makes each requirement hard to
verify and returns a `Lint`: the records with their `testability_flags` filled in. Every
flag has a kind (`vague_term`, `missing_unit`, `missing_condition`, `compound`,
`unbounded`), a reason and a suggested rewrite that marks what the author must supply in
[brackets].

- Deterministic rules run first (`spec2test.lint.rules`): vague words ("adequate",
  "stable", "sharp"), escape clauses ("as appropriate", "if possible"), open-ended lists
  ("etc."), "and/or", unbounded wording ("minimise", "as low as possible", "never"),
  numbers with no unit, tested levels with no test conditions, and more than one "shall"
  in one record.
- An LLM pass then gets one section's records at a time, with the rule flags, and returns
  only what the rules missed. Each flag must quote words from its own requirement; one
  that does not lands in `Lint.rejected`.

```bash
uv run spec2test extract spec.pdf --json > extraction.json
uv run spec2test lint extraction.json                # rules and LLM pass, Kimi Code
uv run spec2test lint extraction.json --rules-only   # no model calls
```

## Generate

`spec2test.generate.generate(requirements, llm, doc=doc)` writes one verification item
per requirement, shaped by its verification method, and returns a `Generation`:

| Method | Item | Fields |
| --- | --- | --- |
| Inspection | checklist item | what to inspect, pass condition |
| Test | test case | preconditions, setup, steps, expected result, pass criterion, equipment |
| Demonstration | evidence item | description, procedure, evidence to capture, acceptance criterion |
| Analysis | evidence item | description, evidence to attach, acceptance criterion |

- With the document, the model also gets each requirement's clause and the clauses it
  refers to ("when tested in accordance with S14.1"), so a test follows the procedure
  the spec states. Anything the spec does not give is written in [brackets], never
  invented; testability flags from `lint` go to the model with their rewrites.
- The item check: every limit the requirement sets must be in the pass criterion, and a
  demonstration needs a procedure. An item that fails goes back to the model once with
  the error, then lands in `Generation.rejected`. A requirement with no verification
  method gets no item (`Generation.skipped`).

```bash
uv run spec2test lint extraction.json --json > lint.json
uv run spec2test generate lint.json --doc spec.pdf            # Markdown, Kimi Code
uv run spec2test generate lint.json --doc doc.json --json > generation.json
```

## Judge

`spec2test judge generation.json` grades each item against a written rubric
([docs/RUBRIC.md](docs/RUBRIC.md)): four pass/fail criteria (complete, faithful,
executable, decisive), each with a reason. An item is acceptable when it passes all four.
The report gives the pass rate of each criterion, overall and per kind of item, so
test-case quality is its own column.

A model never grades its own work: the judge defaults to `--provider anthropic` because
Kimi is the default generator, and every run warns when the judge is from the
generator's model family.

```bash
uv run spec2test judge generation.json --doc spec.pdf                  # Claude grades
uv run spec2test judge generation.json --kind test_case --json         # test cases only
```

`spec2test calibrate` measures the judge against a person. Draw an ungraded sample, grade
it by hand with the rubric, then compare: the report gives agreement and Cohen's kappa per
criterion and for the overall verdict, and lists every disagreement with the judge's
reason. Steps: [docs/RUBRIC.md](docs/RUBRIC.md#calibrating-the-judge).

```bash
uv run spec2test calibrate generation.json --new-sample --size 30 > sample.jsonl
uv run spec2test calibrate sample.jsonl --doc spec.pdf
```

## Eval

`spec2test eval <corpus>` extracts from a corpus document, lints the result, scores it
against its labeled gold set in `gold/<corpus>.jsonl` (format:
[gold/README.md](gold/README.md)), prints the metrics and writes `report.html` and
`results.json` to `eval-reports/<corpus>/<time>/`.

```bash
uv run spec2test eval fmvss-111                     # live, Kimi Code
uv run spec2test eval fmvss-111 --fake DIR          # offline, FakeLLM fixtures
uv run spec2test eval fmvss-111 --rescore RESULTS   # score a saved run again, no model calls
uv run spec2test eval fmvss-111 --lint rules        # lint with the rules only (or: off)
uv run spec2test eval widget-lamp --doc doc.json --gold g.jsonl   # any document and gold set
```

Extracted and gold records align by quote overlap: a pair needs to share at least half of
the shorter quote, and each record aligns at most once. The metrics:

| Metric | Over | Right when |
| --- | --- | --- |
| Extraction precision / recall | extracted / gold records in scope | aligned |
| Field accuracy | aligned pairs | criteria and conditions both right |
| Criteria accuracy | aligned pairs | same comparators, values, units and tolerances |
| Conditions accuracy | aligned pairs | each gold condition has a close match (text similarity ≥ 0.8) |
| Type / strength accuracy | aligned pairs | equal |
| Method accuracy | aligned pairs with a gold method | equal |
| Flag precision / recall | testability flag kinds on aligned pairs | same kind raised |

Flag precision and recall are also reported per kind. Rescoring runs the lint rules again,
so a rule change can be measured on a saved run for free; the saved run's LLM flags are
kept.

Every miss gets a failure category: why a gold record was missed (`section_failed`,
`section_skipped`, `merged`, `span_mismatch`, `citation_rejected`, `not_extracted`), why
an extracted one is extra (`over_split`, `span_mismatch`, `not_in_gold`), or which field
of an aligned pair is wrong (`wrong_type`, `criterion_unit`, `condition_missing`,
`wrong_method`, `flag_missed`, ...). The report counts them and lists each one with its
quote.

Model roles: the model being scored must not have labeled its own gold set, or its blind
spots count as correct. Kimi extracts; the gold sets are labeled by a person or by a
different model (FMVSS 111: Claude Opus 5.5, see `gold/fmvss-111.toml`). The report warns
when the extraction model is from the labeler's model family, and when no person has
reviewed the gold set yet.

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
