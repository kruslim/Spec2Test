# Spec2Test

## Data firewall (non-negotiable)

- Only public documents go in this repo: public-domain regulations, published standards
  excerpts that are free to redistribute, and documents written for this project.
- Never add employer data: no employer specs, test cases, file formats, code or any
  internal document, not even as a test fixture or an example.
- Fixtures and gold sets must be traceable to a public source or be invented for the test.

## Commands

- Install: `uv sync`
- Lint: `uv run ruff check .` and `uv run ruff format --check .`
- Test: `uv run pytest` (offline; the live test needs `SPEC2TEST_LIVE=1`)

## Conventions

- `docs/PLAN.md` is the build plan; work one phase at a time.
- Pipeline stages depend on `spec2test.llm.LLMClient`, never on a provider SDK directly.
- Every test runs offline against `FakeLLM` fixtures in `tests/fixtures/llm/`.
