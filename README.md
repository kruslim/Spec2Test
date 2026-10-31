# Spec2Test

Turns a requirements document into verification items: inspection checklists, test cases
and evidence items. Every requirement is reduced to one canonical record first
(`spec2test.schema.Requirement`); each output is a rendering of that record.

See [docs/PLAN.md](docs/PLAN.md) for the build plan.

## Ingest

`spec2test.ingest.ingest(path)` reads a PDF, DOCX, XLSX or Markdown spec through
[Docling](https://github.com/docling-project/docling), or a plain-text spec (`.txt`: one
paragraph per clause, a form feed between pages), and returns a `Document`: one
canonical text, its page spans, and one `Section` per numbered clause (`S5.5.1`, `5.1.1.`,
`4.2.1`, `T.1.2`, `Annex 3`, ...) with its page, parent clause and character offsets.
Every offset points into `Document.text`, so a later stage can check a quote verbatim.

```bash
uv run spec2test ingest path/to/spec.pdf          # outline: page, clause, title
uv run spec2test ingest path/to/spec.pdf --json   # the whole Document
uv run spec2test ingest path/to/spec.pdf --out doc.json   # saved as UTF-8
```

Every command with `--json` also takes `--out FILE`, which writes the same JSON to FILE
as UTF-8. Use it to save output: on Windows, `>` in PowerShell 5.1 writes UTF-16 and
PowerShell 7 re-encodes through the console code page. Printed JSON escapes non-ASCII
characters (`§` for `§`), so a redirect cannot mangle it, and every loader also
accepts a byte order mark and UTF-16.

Each format is read by an input adapter (`spec2test plugins` lists them); `--adapter NAME`
reads a file with a given one whatever its suffix. A plugin can add more formats; see
[Plugins](#plugins).

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

The prompt follows the gold-set labeling policy (`gold/fmvss-111.toml`): a permission that
only relaxes another requirement ("may", "need not") is not a requirement, nor is a clause
that only says how a measurement is made or only points at its subclauses. Conditions are
applicability, exceptions, the state the requirement holds in and the test procedure the
clause itself names, taken from that section only.

```bash
uv run spec2test extract path/to/spec.pdf                  # live, Kimi Code (KIMI_CODE_API_KEY)
uv run spec2test extract doc.json --provider anthropic     # live, Anthropic API
uv run spec2test extract doc.json --fake tests/fixtures/llm  # offline, FakeLLM fixtures
```

Kimi is the default extraction model. `--provider kimi-code` uses
`spec2test.llm.OpenAICompatLLM`, a client for any OpenAI-compatible chat-completions
endpoint. It sends the JSON Schema in the prompt and validates the reply with Pydantic.
A rate limit (HTTP 429), a server error (5xx) or a refused connection is retried up to
three times, 2, 4 and 8 s apart; a timeout is not, since the call may already have been
billed. Every failure, a malformed reply included, fails only its own section or item.

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
uv run spec2test extract spec.pdf --out extraction.json
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

Flag precision and recall are also reported per kind. They count aligned pairs only, so
they measure the linter on the records extraction got, apart from extraction recall: a
gold flag on a missed requirement and a flag on an extra record are not scored, and the
run warns how many gold flags went unscored that way. Rescoring runs the lint rules again,
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
when the extraction model is from the labeler's model family, when no person has
reviewed the gold set yet, and when an extraction call failed for a section: an outage
lowers recall without saying anything about the model, so run again before quoting it.

### Cost

Every `LLMClient` records the tokens of each call it makes in `llm.usage` (output tokens
include thinking tokens; calls that fail after the model answered are counted, since they
are billed). Every stage saves its own calls with its output (`Extraction.usage`,
`Lint.usage`, `EvalRun.usage`, `Generation.usage`, `Judging.usage`) and prints what the run
cost:

```text
cost $0.60 ($0.1500 per page over 4 cited pages); 12 calls, 100,000 in / 10,000 out tokens
```

The HTML report shows the calls, tokens, cost and cost per page. The page counts differ by
command, and each line names its own: the eval spreads the cost over the pages its gold
set's scope covers ("scope pages") and counts extraction and linting both; `extract`,
`lint`, `generate` and `judge` spread it over the source pages their requirements cite
("cited pages"). Per-page costs on the two bases do not add up. `extract --json` and
`lint --json` save their calls too (`Extraction.usage`, `Lint.usage`), so a standalone run
can be priced afterwards. A rescored run keeps the saved run's token counts.

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
  each and 40,000 in all; a clause inside one already sent is not sent twice. A token
  counts as a reference when it reads as a clause number ("S5", "T.1.2", "5.1.1"); a bare
  number only after a word such as "paragraph" or "clause", so "2 bulbs" is not clause 2.
  Annexes restart their numbering: "paragraph 2.1 of Annex 3" and "Annex 3, paragraph
  2.1" name the annex's 2.1, and a bare "2.1" inside an annex means that annex's own.

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
uv run spec2test generate eval-reports/fmvss-111/<run>/results.json --corpus fmvss-111 --out generation.json
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
gold-set labelers. Kimi generates. The main judge is Claude Opus in a Claude Code session
(`--provider session`, the default), which costs no API tokens; Gemini (`--provider
gemini`, `GEMINI_API_KEY`) is paid, so it only judges a small sample as an independent
check.

```bash
uv run spec2test judge generation.json --corpus fmvss-111 --answers opus.jsonl   # session
uv run spec2test judge gold/judge/fmvss-111.jsonl --provider gemini              # live, Gemini
```

`--provider session` runs twice. The first pass writes every judge call it has no answer
for to `opus.requests.jsonl` (the rubric, the requirement with its source context, and
the item, under a key) and exits 1. In a Claude Code session, Opus answers each request
and appends `{"key": ..., "response": {...}}` lines to `opus.jsonl`
(`.claude/skills/session-judge`). The second pass, the same command, reads the answers and
prints the scores; a request still unanswered, or an answer that does not fit the schema,
is a failed item, and only those requests are written again, each refused answer with its
validation error in `error`. The key hashes
the whole prompt, so an answer never answers a changed prompt. `spec2test.llm.SessionLLM`
does this for any stage, behind the same `LLMClient` interface.

`spec2test judge` also takes a calibration sample (`.jsonl`): it judges every line with
the source context stored on it, labeled or not. Those are the prompts the full run sent,
so session answers carry over. To check the main judge against a second one:

```bash
uv run spec2test calibrate compare opus-judging.json gemini-sample.json   # no model calls
```

`compare` reports, over the items both judged, the agreement and Cohen's kappa per
criterion and for `acceptable`, and each disagreement with both judges' reasons. It warns
when both judges are from one model family. Every judgement stores a hash of the
requirement and item it graded (`Judgement.graded`): item ids are deterministic, so an item
regenerated under the same id is left out of the comparison, with a warning, instead of
being scored against the other judge's grade of the old text. Agreement between two
models only says where to look; agreement with a person (below) is what makes the judge's
numbers count.

### Calibration

The judge's numbers mean something only once it agrees with a person. To measure that:

```bash
uv run spec2test calibrate sample generation.json --corpus fmvss-111 --out gold/judge/fmvss-111.jsonl
# fill in "human" on every line: labeler, a true/false per rubric criterion, acceptable
uv run spec2test calibrate score gold/judge/fmvss-111.jsonl --answers opus.jsonl   # session
uv run spec2test calibrate score gold/judge/fmvss-111.jsonl --judgements opus-judging.json   # no model calls
```

`sample` draws items in turn from each item kind (`--size`, `--seed`) and stores each with
its requirement and source context, so the person and the judge see the same thing.
`score` judges the labeled lines (lines without a complete label are skipped and counted)
and reports, per criterion and for `acceptable`, the judge-human agreement, Cohen's kappa
and every disagreement with the judge's reason. The labels must come from a person: a
model's labels would only measure two models agreeing, so a `labeler` named like a model
ID gets a warning. Saved judgements (`--judgements`) count only for the text they graded:
a line whose requirement or item changed since is reported as failed, not scored.

`sample` never overwrites a file that holds human labels, or one it cannot read as a
sample: the labels are a person's work and no command can make them again. Write the new
sample elsewhere, or pass `--force`.

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

The app serves `localhost` only, since anyone who can open it can start a paid run.

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

## Export

`spec2test export` writes the items for one customer, in the format, ID scheme and words
their profile sets. Built-in formats: CSV, XLSX, a Markdown checklist and YAML. No model
calls.

```bash
uv run spec2test export generation.json                          # CSV to the terminal
uv run spec2test export generation.json --db review.sqlite \
    --profile examples/profiles/widget-co.toml --out checklist.md
uv run spec2test export --db review.sqlite --generation 258ba75d --format xlsx --out items.xlsx
```

- **Review decisions**: with `--db`, edits replace the generated fields and only accepted
  and edited items go out, unless the profile's `output.statuses` lists others (such as
  `unreviewed`): unreviewed model output never ships next to reviewed items unasked.
  Without `--db` every item is unreviewed and all of them go out. The Generation must be
  the one reviewed; a regenerated one has no decisions yet.
- **Saving**: `--out FILE` writes the file as the exporter encodes it and refuses to
  replace an existing one unless `--force` is passed. Use it on Windows: printing a CSV and
  redirecting it with `>` drops the byte-order mark and PowerShell re-encodes the text.
- **Columns**: identity and trace (`id`, `kind`, `requirements`, `section`, `page`,
  `method`, `strength`, `requirement`, `quote`), what to do and what passes for any kind
  (`actions`, `acceptance`), each kind's own fields (`inspect`, `steps`, `equipment`,
  `evidence`, ...), and review and provenance (`status`, `reviewer`, `note`, `model`).
  `spec2test.export.COLUMNS` lists them with their default labels.
- **CSV** has a UTF-8 byte-order mark, so spreadsheet programs show '§' and '±'. **XLSX**
  writes every cell as text, and CSV puts a `'` before a cell that would start a formula
  (`=`, `+`, `-`, `@`, unless it is a signed number with plain unit words such as `-40 °C`;
`-1 cmd|' /C calc'!A0` is not one), so nothing an
  item says runs in a spreadsheet. The **Markdown** checklist has one box per item,
  grouped by section. **YAML** keys each item by column name and keeps lists as lists.

A profile is a TOML file; [examples/profiles/widget-co.toml](examples/profiles/widget-co.toml)
(an invented customer) uses every part of it:

| Part | What it sets |
| --- | --- |
| `[ids]` | `pattern`, a Python format string over `{item}` (the generated ID), `{doc}`, `{kind}`, `{section}`, `{req}`, `{n}` and `{n_kind}`, e.g. `"{doc}-{kind}-{n:03}"`; `doc`, `kind_codes` and `start`. Numbers run over every item before rejected ones are left out, so rejecting one does not renumber the rest. |
| `[output]` | `format` (an exporter's name), `columns`, `title`, `statuses`, and `options` passed to the exporter as is. |
| `[vocabulary]` | The customer's words for `columns` headers, item `kinds` and review `statuses`. |
| `required` | Columns every item must fill. An empty one is written as `[Label]` and listed, and the command exits 1; the core never invents a value. |
| `[values]` | Columns of the customer's own with one value for every item (program, owner). |

Every mistake fails when the profile loads, before anything is written: an unknown
column, placeholder or term, a pattern that would give items the same ID, a required
column that is not exported.

## Plugins

Input adapters and exporters are plugins. The built-ins register the same way an outside
one does, and `spec2test plugins` lists what is installed. A plugin lives in its own
package and declares an entry point in that package's `pyproject.toml`; nothing in this
repo changes:

```toml
[project.entry-points."spec2test.adapters"]
lamp = "my_plugins.lamp:LampAdapter"

[project.entry-points."spec2test.exporters"]
jsonl = "my_plugins.jsonl:JsonLinesExporter"
```

The entry point's name must match the plugin's `name`. A plugin that fails to load, or
takes a name already in use, is skipped with a warning and the built-ins keep working;
asking for it by name says why. Plugins load the first time one is needed. To register
one in code instead (a notebook, a test), call `ADAPTERS.register(...)` or
`EXPORTERS.register(...)`.

### Writing an adapter

An adapter says what text is on which page; the core does the rest (clause numbers,
sections, page spans, offsets), so every adapter's output cites the same way.

```python
from pathlib import Path

from spec2test.ingest import Block


class LampAdapter:
    name = "lamp"  # `--adapter lamp`; no spaces
    suffixes = (".lamp",)  # lowercase, with the dot

    def blocks(self, path: Path) -> list[Block]:
        # Paragraphs, headings, list items and tables in reading order, with their page.
        # A block opening with a clause number ("2.1 Brightness. ...") starts a section.
        page, out = 1, []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                page += 1
            elif line.strip():
                out.append(Block("text", line.strip(), page))
        return out
```

`Block(kind, text, page)` takes kind `"heading"`, `"text"`, `"list"` or `"table"`; set
`margin=True` on text from the page margins, so running heads and footers repeated on
three or more pages are dropped. Raise `ValueError` for a file the adapter cannot read.
For a suffix a built-in already reads, the built-in wins; pass `--adapter` to use yours.

### Writing an exporter

An exporter gets an `Export`: the profile, the title, the `columns` with their `labels`,
and one `ExportRow` per item with its `values` (a string, or a list of strings), the
item itself with edits applied, its requirements and its review status. Use `values` so
the output matches the other formats; `spec2test.export.cell(value, column)` lays a value
out as one cell.

```python
import json

from spec2test.export import Export


class JsonLinesExporter:
    name = "jsonl"  # what a profile's output.format names
    suffix = ".jsonl"

    def export(self, export: Export) -> str:  # str is written as UTF-8; bytes as is
        return "".join(json.dumps(row.values) + "\n" for row in export.rows)
```

Set `encoding = "utf-8-sig"` on the class to write text with a byte-order mark, and read
your own settings from `export.options` (the profile's `[output.options]`). Exporters
write the profile's columns, never fields of their own invention. The tests carry a
working pair in `tests/plugin_demo.py`.

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
To also run the live tests (they call Kimi with `KIMI_CODE_API_KEY`, and Claude with
`ANTHROPIC_API_KEY`; none calls Gemini):

```bash
SPEC2TEST_LIVE=1 uv run pytest -m live
```

Both are needed: without `-m live` the live tests are skipped even when `SPEC2TEST_LIVE=1`
is set in the shell, and Claude Code asks before any `pytest` command that names `live`.

To also parse the four gold-set documents (they must be in `corpora/` first):

```bash
SPEC2TEST_CORPORA=1 uv run pytest -m corpora
```
