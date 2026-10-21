# Spec2Test

Turns a requirements document into verification items: inspection checklists, test cases
and evidence items. Every requirement is reduced to one canonical record first
(`spec2test.schema.Requirement`); each output is a rendering of that record.

See [docs/PLAN.md](docs/PLAN.md) for the build plan.

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
