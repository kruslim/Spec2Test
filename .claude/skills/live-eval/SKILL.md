---
name: live-eval
description: Run a live (paid) Spec2Test eval against a gold-set corpus and compare the numbers with the previous run.
argument-hint: "<corpus> [extra eval flags]"
disable-model-invocation: true
---

# Live eval run

This calls paid model APIs. The user invoked it, so the run is approved; do not run
more passes than asked.

Arguments: $ARGUMENTS (default corpus `fmvss-111`).

1. Check the setup: `uv run spec2test corpora status` (the document must match its
   manifest hash) and that `KIMI_CODE_API_KEY` is set. Print only whether the key is
   set, never its value.
2. Run `uv run spec2test eval $ARGUMENTS` in the background (it can take minutes).
3. Find the previous run in `eval-reports/<corpus>/` and compare: extraction precision
   and recall, field, criteria, conditions and method accuracy, flag precision and
   recall, and the failure-category counts. If the scoring code changed since the old
   run, rescore it with `--rescore` first (no model calls).
4. Report a short table (previous, now, change) and the three most common failure
   categories with one example quote each. Point to the new `report.html`.
5. Mention the model-role warnings the run printed (labeler family, unreviewed gold set).
