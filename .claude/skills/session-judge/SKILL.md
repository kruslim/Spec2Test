---
name: session-judge
description: Judge saved Kimi-generated verification items in this Claude Code session (Claude Opus as the judge, no API spend), via `spec2test judge --provider session`.
argument-hint: "<generation.json | calibration sample .jsonl> [--corpus ID]"
disable-model-invocation: true
---

# Judge in a session

Claude Opus is the project's main judge, and it runs only here, in Claude Code
sessions: there is no Anthropic API key. `--provider session` turns each judge call into
a request you answer in this session. No model API is called, so this costs nothing.

Input: $ARGUMENTS. A Generation (`generate --json`) goes to `spec2test judge`; a
calibration sample (`gold/judge/*.jsonl`) goes to `spec2test calibrate score`.

## Model roles: check before you start

- You judge items another model wrote. Kimi generates; if the generation's `model` is
  from the `claude` family, stop: the CLI only warns, but a model never grades its own
  family's work here.
- Never write the `human` labels in `gold/judge/`: those come from the user. You answer
  judge requests only.
- Use the model ID actually serving this session as `--model` (default
  `claude-opus-5-5`). Report the numbers as a session run by that model, not an API run.

## Steps

1. Pick a run folder, e.g. `eval-reports/<corpus>/<run>/` (git-ignored). Answers go in
   `opus-answers.jsonl` there.
2. First pass, which records every request:

   ```bash
   uv run spec2test judge GENERATION --corpus ID --provider session --model MODEL \
     --answers RUN/opus-answers.jsonl
   ```

   It exits 1 and writes `RUN/opus-answers.requests.jsonl`: one line per item, with
   `key`, `schema_name`, `json_schema`, `system` (the rubric) and `prompt` (the
   requirement, its source context and the item).
3. Answer every request yourself, one at a time. Read its `system` and `prompt` and
   grade the item strictly by that rubric, as an independent judge: reason first, then
   each verdict, then `acceptable`. Do not write a script that produces verdicts, and do
   not reuse one item's verdicts for another. Append one line per request to the
   answers file:

   ```json
   {"key": "<the request's key>", "response": {"covers": {"reason": "...", "passed": true}, "faithful": {...}, "objective_verdict": {...}, "executable": {...}, "method_fit": {...}, "acceptable": true}}
   ```

   Long runs can be split across sittings: answers already written are kept, and the
   next pass asks only for the rest. A later line for the same key replaces an earlier one.
4. Run the same command again, with `--json > RUN/opus-judging.json`. It exits 0 when
   every item has a valid answer; an answer that does not fit the schema shows up as a
   failed item with the validation error, so fix that line and rerun.
5. Report the quality table (`spec2test judge` without `--json` prints it), naming the
   generator, the judge model and "session run".

## The independent check (Gemini, paid: ask first)

Gemini costs API tokens, so it never judges a full run. It judges only the 30-item
calibration sample, once per rubric or generator change, and the agreement with your
judgements on the same items is reported. With the user's OK:

```bash
uv run spec2test judge gold/judge/<corpus>.jsonl --provider session --model MODEL \
  --answers RUN/opus-sample-answers.jsonl    # free; once answered, rerun: --json > RUN/opus-sample.json
uv run spec2test judge gold/judge/<corpus>.jsonl --provider gemini --json > RUN/gemini-sample.json
uv run spec2test calibrate compare RUN/opus-sample.json RUN/gemini-sample.json
```

A calibration sample stores each item's source context, so its prompts are the ones the
full run sent: answers you wrote for the full run answer the sample too, and
`opus-sample-answers.jsonl` can simply be the full run's answers file. Report the
per-criterion agreement and kappa, and list the disagreements for the user to settle.
Once the user has labeled the sample, `calibrate score SAMPLE --judgements
RUN/opus-sample.json` gives judge-human agreement with no model calls.
