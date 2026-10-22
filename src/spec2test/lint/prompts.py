"""Prompts for the LLM lint pass. The FakeLLM fixtures match on phrases from these, so a
wording change here may need the same change in `tests/fixtures/llm/lint_*.json`."""

from __future__ import annotations

import json

from spec2test.schema import Requirement, TestabilityFlag

SYSTEM = """\
You review requirements for testability, for engineers who must write a checklist item \
or test case for each one and reach a clear pass or fail.

Deterministic rules have already flagged listed vague words, escape clauses, bare \
numbers, untested conditions and multiple "shall"s; their flags are given with each \
requirement as already_flagged. Find only what they missed: the subtler reasons a \
requirement cannot be verified as written. Never flag words an already_flagged entry \
quotes, under any kind: that problem is already reported.

The test for every flag: would a competent tester, holding this requirement and the \
clauses it references, be unable to reach a pass or fail verdict, or could two testers \
reach different verdicts? If not, do not flag it. In particular:
- A requirement's conditions and criteria fields are part of it; judge it with them.
- A capability stated without a minimum amount is met by any working capability; reading \
it as satisfied by a trivially small amount is not a testability problem.
- A term with an ordinary, observable meaning is not vague just because it has no number.

Kinds:
- vague_term: a quality with no measurable limit where the verdict depends on judgement \
("operable with one hand", "free of glare", "legible"), an undefined or ambiguous \
reference ("the system", "it", "the applicable standard"), or a subjective judgement.
- missing_unit: a quantity stated with no unit, or a unit that does not fit the quantity.
- missing_condition: a measured level with no state, environment, load or procedure to \
measure it under, where the result depends on them.
- compound: more than one obligation that could pass or fail separately. Not compound, \
because the source states one obligation with one verdict: alternatives ("either X or \
Y", "one of the following"), where meeting any one option is enough; and one property \
required of several parts or in several directions together ("on both sides", "movable \
up and down", "after each of the tests in clause 3").
- unbounded: no limit that says when it is met (optimise, minimise, as soon as possible), \
or an absolute no finite test can cover (never, at all times, under any conditions).

For each problem:
- requirement_id: the id of the requirement, as given.
- phrase: the words in that requirement's text that cause it, copied exactly.
- reason: one sentence saying why it cannot be verified as written.
- suggested_rewrite: the whole requirement rewritten so it can be verified, keeping its \
wording. Put anything the source does not give in [brackets], such as [maximum force in \
N]; never invent a value.

Do not flag a requirement that can be verified as written. A clause that points to a \
procedure or limit stated elsewhere ("as specified in S14.1") is not a problem. Return an \
empty list if every requirement is testable."""


def lint_prompt(section: str, requirements: list[Requirement]) -> str:
    doc = requirements[0].source_anchor.doc
    records = "\n".join(json.dumps(_record(r), ensure_ascii=False) for r in requirements)
    return f"""\
Document: {doc}
Section: {section or "(front matter)"}

<requirements>
{records}
</requirements>

Find what else makes each requirement hard to verify."""


def _record(req: Requirement) -> dict:
    record: dict = {"id": req.id, "text": req.text}
    if req.source_anchor.quote != req.text:
        record["source"] = req.source_anchor.quote
    record["strength"] = req.strength.value
    if req.verification_method is not None:
        record["verification_method"] = req.verification_method.value
    record["criteria"] = [c.model_dump(mode="json", exclude_none=True) for c in req.criteria]
    record["conditions"] = req.conditions
    record["already_flagged"] = [_flag(f) for f in req.testability_flags]
    return record


def _flag(flag: TestabilityFlag) -> dict:
    return {"kind": flag.kind.value, "reason": flag.reason}
