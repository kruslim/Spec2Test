# Spec2Test

Turns a requirements document into verification items. Pipeline:
ingest → extract → lint → generate → judge, scored by the eval harness. Every stage
works on one canonical record, `spec2test.schema.Requirement`. `README.md` explains each
stage and its CLI; `docs/PLAN.md` is the build plan and the status of each phase.

## Data firewall (non-negotiable)

- Only public documents go in this repo: public-domain regulations, published standards
  excerpts that are free to redistribute, and documents written for this project.
- Never add employer data: no employer specs, test cases, file formats, code or any
  internal document, not even as a test fixture or an example.
- Fixtures and gold sets must be traceable to a public source or be invented for the test.
- Corpus documents (`corpora/*`) and gold sets for non-redistributable documents
  (`gold/unece-r158.*`, `gold/fsae-rules.*`) are git-ignored: never force-add them.
- Run `/firewall-check` before every commit.

## Commands

- Install: `uv sync`
- Lint: `uv run ruff check .` and `uv run ruff format --check .`
- Test: `uv run pytest` (offline; the live test needs `SPEC2TEST_LIVE=1`, the gold-set
  corpora test needs `SPEC2TEST_CORPORA=1` and the documents in `corpora/`)
- One test file: `uv run pytest tests/test_lint.py -q`
- Offline pipeline run: any stage with `--fake tests/fixtures/llm`
- Live runs (`SPEC2TEST_LIVE=1`, `spec2test eval|generate|judge` without `--fake`) call paid
  APIs: ask before running one.

## Layout

- `src/spec2test/<stage>/`: `models.py` (Pydantic), `prompts.py`, `pipeline.py`; one
  package per stage (`ingest`, `extract`, `lint`, `generate`, `judge`, `evals`).
- `src/spec2test/llm/`: `LLMClient` (`base.py`), Anthropic, OpenAI-compatible (Kimi,
  Gemini) and `FakeLLM` (`fake.py`, documents the fixture format).
- `tests/conftest.py`: the invented `widget_lamp` document and the `fake_llm` fixture.
- `gold/`: labeled gold sets (format in `gold/README.md`); `corpora/manifest.toml`
  pins each source document by sha256.

## Conventions

- `docs/PLAN.md` is the build plan; work one phase at a time (`/phase`).
- Pipeline stages depend on `spec2test.llm.LLMClient`, never on a provider SDK directly.
- Every test runs offline against `FakeLLM` fixtures in `tests/fixtures/llm/`. A new
  model call needs a fixture there, matched by `schema` and `prompt_contains`.
- Model roles: a model never grades its own work. The labeler, the scored model and the
  judge are different models; keep the warnings and refusals that enforce this.
- Values the source does not give go in [brackets]; never invent limits.
- Ruff, line length 100. A hook formats each edited Python file.
- Commit messages: `Phase N: <summary>` (or a plain summary outside a phase), then a
  bullet list of what changed and any measured numbers.
- When a stage's behaviour or CLI changes, update its section in `README.md`; when a
  phase lands, update its status paragraph in `docs/PLAN.md`.

## Environment

- Windows and Linux are both supported (CI runs both); use `pathlib`, never hard-coded
  separators.
- The first PDF parse downloads Docling's models (~500 MB) to the Hugging Face cache.
- API keys come from the environment (`ANTHROPIC_API_KEY`, `KIMI_CODE_API_KEY`,
  `GEMINI_API_KEY`); never write them to a file in the repo.
