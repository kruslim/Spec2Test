---
name: phase
description: Build the next phase (or a named phase) of docs/PLAN.md end to end - plan, implement, test offline, update docs, and draft the commit. Use when the user says "start phase N", "next phase", or "continue the build plan".
argument-hint: "[phase number]"
disable-model-invocation: true
---

# Build a phase of the plan

Phase requested: $ARGUMENTS (if empty, take the first phase in `docs/PLAN.md` that has
no "Status: built" paragraph).

1. **Read** the phase in `docs/PLAN.md`: its "Done when" line and its prompt block. Read
   the `docs/USAGE.md` sections for the stages it touches, and the code of the nearest existing
   stage to copy its shape (`models.py`, `prompts.py`, `pipeline.py`, a CLI subcommand
   in `cli.py`, tests).
2. **Plan** before writing code: the modules, the CLI surface, the fixtures, and how
   "Done when" will be shown. Ask the user about any choice the plan leaves open.
3. **Implement** in small steps. Stages talk to `spec2test.llm.LLMClient` only. Every
   new model call gets a `FakeLLM` fixture in `tests/fixtures/llm/`, built on the
   invented `widget_lamp` document (`tests/conftest.py`) or another invented one.
4. **Check** after each step: `uv run pytest -q`, `uv run ruff check .`,
   `uv run ruff format --check .`. Fix failures before moving on.
5. **Live run** only with the user's go-ahead (it costs money): offer the command and the
   numbers it would produce.
6. **Docs**: add or update the stage's section in `docs/USAGE.md` (what it does, the
   CLI, the numbers), and add a "Status (<date>): built." paragraph to the phase in
   `docs/PLAN.md` with what was measured.
7. **Firewall**: run the `/firewall-check` steps on the diff.
8. **Commit message**: draft it in the house style and show it; commit only when the
   user says so.

   ```text
   Phase N: <one-line summary>
   - <module>: what it does, in plain words.
   - <CLI / eval / docs change>.
   - <measured numbers, if a live run happened>.
   - Tests: what is covered offline; live tests behind SPEC2TEST_LIVE.
   ```
