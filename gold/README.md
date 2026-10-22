# Gold sets

Hand-labeled requirements that `spec2test eval` scores extraction against, one gold set
per corpus document in `corpora/manifest.toml`:

```text
gold/<corpus>.jsonl   one labeled Requirement per line
gold/<corpus>.toml    optional: the scope that was labeled
```

Gold sets for documents that stay out of git (UN R158, the FSAE rules) are git-ignored:
their quotes would republish the document. Label them here all the same; they just never
get committed.

## A line

Each line is a `spec2test.schema.Requirement` as JSON. The anchor needs only the section
and the quote; the loader finds the quote in that section and fills in `page`, `start`
and `end`:

```json
{"id": "fmvss-111:S5.5.1:1", "source_anchor": {"doc": "fmvss-111", "section": "S5.5.1", "quote": "..."}, "text": "...", "type": "performance", "strength": "shall", "criteria": [{"parameter": "...", "comparator": "le", "value": "2.0", "unit": "s"}], "conditions": ["..."], "verification_method": "test", "testability_flags": []}
```

- `quote`: copied from the source, character for character. Differences in whitespace
  are fine (the anchor then cites the source's own text); anything else fails the load.
  If the quote appears twice in the section, quote more of it.
- One line per obligation, the way extraction is told to split: a sentence that obliges
  two things is two lines, and both may quote the same sentence. "Either X or Y" is one.
- `criteria`: value, unit and tolerance as the source writes them. The parameter name is
  free text and is not scored.
- `conditions`: free text; scored by similarity, not equality.
- `verification_method`: leave it out only if you cannot decide; that line is then left
  out of method accuracy.
- `testability_flags`: kind and a short reason for each. Eval scores the kinds raised on
  each aligned pair, so a requirement left without a flag counts as testable: label every
  kind that applies (`vague_term`, `missing_unit`, `missing_condition`, `compound`,
  `unbounded`), not only the most obvious one.

The `id` is yours; it only has to be unique in the file.

## Who labeled it

```toml
labeler = "claude-opus-5-5"   # a person's name, or the model ID that labeled it
reviewed = false              # true once a person has checked every line
```

The model being scored must not be the one that labeled the gold set: their shared
mistakes would count as correct. Every eval run warns when the extraction model is from
the labeler's model family, and when `reviewed` is not true.

## Scope

Without a `.toml`, the whole document is the scope: every requirement in it must be in
the gold set, or extraction gets marked down for finding it. To label part of a document:

```toml
scope = ["S5", "S6"]   # these sections and every subsection were labeled completely
```

Both settings go in the same `<corpus>.toml`.

Only the scoped sections are sent to the model, and only extractions inside them are
scored.

## Checking a gold set

`spec2test eval <corpus>` loads the gold set before calling any model and lists every
problem with its line number. To score again after fixing labels, without paying for
another extraction:

```bash
uv run spec2test eval fmvss-111 --rescore eval-reports/fmvss-111/<run>/results.json
```
