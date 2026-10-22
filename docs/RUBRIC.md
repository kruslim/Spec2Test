# Verification item rubric

The LLM judge (`spec2test judge`) grades every generated item against these four
criteria, and a person grades a calibration sample against the same four, so the two can
be compared (`spec2test calibrate`). Each criterion is pass or fail; an item is
**acceptable** when it passes all four, meaning a reviewer could accept it unedited.

The criteria are the same for checklist items, test cases and evidence items. The text
of each one below is the text the judge is given (`spec2test.judge.rubric.RUBRIC`); a
test keeps the two identical, so change both together.

## Criteria

**complete**: The item verifies the whole requirement: every criterion and condition it states, and every limit appears in the pass criterion. It adds no obligation the requirement does not state.

**faithful**: Every value, unit, tolerance, comparator, condition and procedure in the item matches the requirement or the source clauses given. Nothing is invented: what the source does not give is marked in [brackets] or cited by clause number.

**executable**: A competent tester could carry it out without guessing: the steps, what to inspect, or the evidence to produce are concrete and in order, and the preconditions, setup and equipment it needs are stated or bracketed.

**decisive**: The pass criterion is objective: two testers with the same observations reach the same verdict. It compares a measured or observed result with the requirement's limit, in the requirement's direction (at least, at most, between). A [bracketed] limit is faithful but not yet decisive.

## How to grade

- Fail a criterion only for a defect you can point to in the item. Wording, style and
  length are not defects.
- Grade against the requirement and its source clauses, not against what you would have
  written. A procedure the item cites by clause number is fine when that clause states it.
- A checklist item has no steps: grade *executable* on whether what to inspect, where and
  with what is specific. An evidence item: on whether the procedure and the evidence to
  capture or attach are specific.
- Grade each criterion on its own: one defect can fail more than one criterion, but do
  not fail a criterion because another one failed.

## Calibrating the judge

1. Draw a sample from a generation, ungraded, so you grade without seeing the judge's
   verdicts:

   ```bash
   uv run spec2test generate lint.json --doc spec.pdf --json > generation.json
   uv run spec2test calibrate generation.json --new-sample --size 30 > calibration/my-sample.jsonl
   ```

2. Grade each line: set `labels.complete`, `labels.faithful`, `labels.executable` and
   `labels.decisive` to `true` or `false`, and put anything worth remembering in `note`.
   A line left all `null` is skipped; a single `null` leaves that line out of that
   criterion only.
3. Say who graded it in a TOML file with the same name (`calibration/my-sample.toml`):

   ```toml
   labeler = "Kenneth"
   ```

   A sample quotes its spec. As with gold sets, a sample of a document that stays out of
   git (UN R158, the FSAE rules) must never be committed.

4. Run the judge on it and read the agreement table:

   ```bash
   uv run spec2test calibrate calibration/my-sample.jsonl --doc spec.pdf --json > calibration.json
   uv run spec2test calibrate calibration/my-sample.jsonl --rescore calibration.json   # after fixing a grade: no model calls
   ```

The report gives, per criterion and for *acceptable*, the share of items the judge and
the person agree on, Cohen's kappa (agreement beyond what chance gives at their pass
rates; n/a when one of them gave the same verdict throughout), and every disagreement
with the judge's reason and your note. "judge only" counts items the judge passed and the
person failed: a lenient judge, the costly direction, because its scores then overstate
quality.

Model roles: the judge must not be the model that wrote the items (`docs/PLAN.md`, Model
roles). `spec2test judge` and `spec2test calibrate` default to `--provider anthropic`,
since Kimi is the default generator, and both warn when the judge is from the
generator's model family.
