---
paths:
  - "tests/fixtures/**"
  - "gold/**"
  - "corpora/**"
---

# Fixtures, gold sets and corpora

- Data firewall: every fixture and gold line quotes a public document listed in
  `corpora/manifest.toml`, or is invented for the test (like `widget_lamp` in
  `tests/conftest.py`). Never employer material, not even paraphrased.
- `FakeLLM` fixtures (`tests/fixtures/llm/*.json`): `schema` is the requested Pydantic
  class name, `prompt_contains` picks the call, `response` must validate against the
  schema. The first match in sorted file order wins, so keep `prompt_contains` specific.
- Gold lines follow `gold/README.md`: quotes copied character for character from the
  source, one line per obligation. Gold sets are labeled from the source text, never
  from a pipeline run, and never by the model being scored.
- `gold/unece-r158.*`, `gold/fsae-rules.*` and everything in `corpora/` except the
  manifest and README are git-ignored on purpose: their quotes would republish the
  document.
