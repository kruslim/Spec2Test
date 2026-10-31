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

### Cost

Every `LLMClient` records the tokens of each call it makes in `llm.usage` (output tokens
include thinking tokens; calls that fail after the model answered are counted, since they
are billed). `eval`, `generate` and `judge` save their own calls with their output
(`EvalRun.usage`, `Generation.usage`, `Judging.usage`) and print what the run cost:

```text
cost $0.60 ($0.1500 per page over 4 pages); 12 calls, 100,000 in / 10,000 out tokens
```

The HTML report shows the calls, tokens, cost and cost per page. The eval spreads the cost
over the pages its gold set's scope covers and counts extraction and linting both;
`generate` and `judge` spread it over the source pages their requirements cite. A rescored
run keeps the saved run's token counts.

Prices are in `spec2test.llm.usage.PRICES`, in USD per million tokens, copied from each
provider's own pricing page (with the date checked). A model with no entry is not priced:
the cost is reported as unknown, with the token counts, never guessed. Kimi Code
(`kimi-for-coding`) has none, since it is billed by subscription plan. FakeLLM fixtures can
give a call's tokens with `"usage": {"input_tokens": ..., "output_tokens": ...}`.

## Generate

`spec2test.generate.generate(requirements, llm, doc=...)` writes one `VerificationItem`
per requirement, rendered by its verification method:

| Method | Item | Fields |
| --- | --- | --- |
| Inspection | checklist item | what to inspect, pass condition |
| Test | test case | preconditions, setup, steps, expected result, pass criterion, equipment |
| Demonstration | evidence item | description, procedure (required), evidence to capture, acceptance criterion |
| Analysis | evidence item | description, optional procedure, evidence to attach, acceptance criterion |

The model gets each requirement with its criteria, conditions and testability flags. When
a document is given it also gets the requirement's source context
(`spec2test.generate.source_context`):

- the headings of its section and the sections around it ("S5.2 Outside rearview
  mirror-driver's side" says which mirror "the mirror" is);
- its whole source section, whose other sentences often say how or from where it is
  measured;
- the definitions-section entries for the terms it uses ("Backing event means ...");
- the clauses it references, each with its subclauses ("tested in accordance with
  S14.1"; "S5.5.1 through S5.5.7" sends all seven). Up to 12 clauses, 16,000 characters
  each and 40,000 in all; a clause inside one already sent is not sent twice.

Values and limits the source does not give go in [brackets], never invented; the
procedure is the model's to write when the source gives none. Every item is checked: its
verdict (pass condition, pass criterion or acceptance criterion) must state each criterion
value the requirement has, and a demonstration needs a procedure. An item that fails goes
back to the model once with the errors, then lands in `Generation.rejected`.

Two kinds of record are never sent. Requirements with no verification method are listed
in `Generation.unclassified`. Permissions ("may", "need not") and clauses that only say how
something is determined or where a reference point is ("shall be determined in accordance
with SAE J964", "shall be those established in Standard No. 104") oblige nothing, so they
are listed in `Generation.skipped` with the reason (`not_verifiable`). A clause with a
measurable criterion is always kept.

```bash
uv run spec2test generate lint.json --corpus fmvss-111          # live, Kimi Code
uv run spec2test generate eval-reports/fmvss-111/<run>/results.json --corpus fmvss-111 --json > generation.json
```

The input is an Extraction, a Lint or an eval run's `results.json` (its in-scope records,
with their testability flags). `--corpus ID` or `--doc PATH` supplies the source for the
referenced clauses.

## Judge

`spec2test.judge.judge(generation, llm)` grades every item against the written rubric in
`spec2test.judge.rubric`, one pass/fail verdict with a reason per criterion:

| Criterion | Passes when |
| --- | --- |
| `covers` | every obligation, criterion and condition is verified, and no pass/fail condition is added |
| `faithful` | values, units, tolerances, conditions and stated procedures match the source; no limit invented |
| `objective_verdict` | two competent testers would reach the same verdict |
| `executable` | a technician could carry it out without guessing |
| `method_fit` | it verifies the way its method says (measure, look, operate, calculate) |

plus the overall call, `acceptable`: an engineer would take the item unedited. An item's
score is the share of criteria it passes; `spec2test judge` prints the mean score, the
acceptance rate and each criterion's pass rate, per item kind.

The judge must not be the generator: judging with the generator's own model is refused,
and a judge from the generator's model family gets a warning, as the eval does for
gold-set labelers. Kimi generates; Gemini (`--provider gemini`, `GEMINI_API_KEY`) is the
default judge.

```bash
uv run spec2test judge generation.json --corpus fmvss-111         # live, Gemini
```

### Calibration

The judge's numbers mean something only once it agrees with a person. To measure that:

```bash
uv run spec2test calibrate sample generation.json --corpus fmvss-111 --out gold/judge/fmvss-111.jsonl
# fill in "human" on every line: labeler, a true/false per rubric criterion, acceptable
uv run spec2test calibrate score gold/judge/fmvss-111.jsonl       # live, Gemini
uv run spec2test calibrate score gold/judge/fmvss-111.jsonl --judgements calibration.json   # no model calls
```

`sample` draws items in turn from each item kind (`--size`, `--seed`) and stores each with
its requirement and source context, so the person and the judge see the same thing.
`score` judges the labeled lines (lines without a complete label are skipped and counted)
and reports, per criterion and for `acceptable`, the judge-human agreement, Cohen's kappa
and every disagreement with the judge's reason. The labels must come from a person: a
model's labels would only measure two models agreeing.

## Trace

`spec2test.trace.trace(generation)` builds the traceability matrix: one row per
requirement with the items that verify it, and why it has none. No model is called.

| Coverage | Meaning | A gap? |
| --- | --- | --- |
| `covered` | at least one item verifies it | no |
| `skipped` | a permission or definition; it obliges nothing | no |
| `unclassified` | no verification method, so never sent | yes |
| `rejected` | its item failed the check, even after a retry | yes |
| `failed` | the generation call failed | yes |
| `missing` | no item, and generation has no record of why | yes |

The matrix also lists items of the wrong kind for their requirement's method (a checklist
item for a demonstration) and orphan items, which verify no requirement in the set. Pass
`--requirements` to trace a generation's items to another set of records, such as a newer
extraction, and see which items are left orphaned.

```bash
uv run spec2test trace generation.json                     # matrix, gaps, orphans, coverage
uv run spec2test trace generation.json --csv > matrix.csv  # one row per requirement-item link
uv run spec2test trace generation.json --requirements new-extraction.json
```

Coverage is items over the requirements that oblige something (skipped ones are left
out). `trace` exits with 1 when there is a gap or an orphan, so a script can stop on it.

### Spec diffs

`spec2test.trace.diff(old, new, items=generation)` compares the requirements extracted from
two versions of a spec and lists, for each change, the old items it affects. Records pair
across versions by source quote (difflib similarity of at least 0.6), then same section,
then the more similar requirement text, which tells apart the records of a split compound
requirement; ids are not used, because inserting a requirement shifts the ids after it.

| Change | When | What it asks of the old items |
| --- | --- | --- |
| `added` | no old record pairs with it | `write` one, if it obliges something |
| `removed` | no new record pairs with it | `retire` them |
| `changed` | the source quote differs | `update` them |
| `restated` | same quote, but the extraction reads it differently (text, type, strength, criteria, conditions or method) | `review` them |
| `unchanged` | same quote and reading; it may have moved section or id | `relink` them when its id changed |

The quote decides between `changed` and `restated` because it is the spec's own words,
the same on every run; the other fields are a model's reading of them, so two extraction
runs over the same words can differ there. A requirement that turns into a permission
retires its items. Criteria compare by comparator, value, unit and tolerance, ignoring
the free-text parameter name, as the eval does.

```bash
uv run spec2test diff rev-a/extraction.json rev-b/extraction.json                # changes only
uv run spec2test diff rev-a/extraction.json rev-b/extraction.json --items rev-a/generation.json
uv run spec2test diff A.json B.json --all --json                                  # everything
```

Both inputs are an Extraction, a Lint or an eval run's `results.json`. Extract both
versions with the same `--doc-id` so unchanged requirements keep their ids.

## Review

`spec2test review open` starts a Streamlit app where a person reads each verification item
next to its source and accepts, edits or rejects it. It needs the `ui` extra
(`uv sync --extra ui`; the dev group already has it).

- **Open** a Generation saved by `generate --json`, with its source so each quote shows in
  its section, highlighted; or pick a review saved earlier.
- **New review**: upload a PDF, DOCX, XLSX or Markdown spec (or a Document saved by
  `ingest --json`). Ingestion runs locally and costs nothing. Upload the Generation made
  from it, or run the pipeline (extract, lint with the model pass, generate) from the app.
  A run on a real provider needs a confirming tick first, since it calls a paid API;
  `--fake DIR` adds a FakeLLM provider that replays fixtures. The run's cost shows above
  the items.
- **Review**: each item shows its requirement (criteria, conditions, testability flags and
  rewrites) and the source section with the quote highlighted. Accept, reject (with an
  optional note) or edit its fields; the app moves on to the next item still to review.

```bash
uv sync --extra ui
uv run spec2test review open generation.json --doc spec.pdf     # add a Generation and open it
uv run spec2test review open --fake tests/fixtures/llm          # upload widget-lamp offline
uv run spec2test review stats                                  # acceptance rate
uv run spec2test review stats --generation 258ba75d --json
```

Every decision goes to a SQLite log (`review.sqlite` by default, `--db` to change it; it
is git-ignored, since it stores the whole source document). Decisions are only added, so
an item's history stays; the latest decision is the one that counts. An edit keeps both
the generated and the edited fields, and must be a valid item of its kind that changes
something. A Generation is stored under the sha256 of its JSON, so regenerated items start
a fresh review.

`review stats` reports the acceptance rate, the plan's production metric: the share of
reviewed items accepted unedited, overall, per item kind and per verification method.
Edited items count as reviewed but not accepted. Review decisions are not judge labels:
nothing here writes to `gold/judge/`.

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
To also run the live tests (costs money; needs `ANTHROPIC_API_KEY`, `KIMI_CODE_API_KEY` and
`GEMINI_API_KEY`):

```bash
SPEC2TEST_LIVE=1 uv run pytest -m live
```

To also parse the four gold-set documents (they must be in `corpora/` first):

```bash
SPEC2TEST_CORPORA=1 uv run pytest -m corpora
```
