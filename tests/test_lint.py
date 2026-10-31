"""The testability linter: deterministic rules, the LLM pass against FakeLLM, the CLI.

Every requirement here is invented for the tests.
"""

import json
from pathlib import Path

import pytest

from spec2test import cli
from spec2test.extract import Extraction, extract
from spec2test.lint import FlagDraft, FlagDrafts, Lint, lint, lint_rules, relint
from spec2test.lint import pipeline as lint_pipeline
from spec2test.lint.prompts import SYSTEM
from spec2test.lint.rules import compound, missing_conditions, missing_units, vague_terms
from spec2test.llm import FakeLLM, Fixture
from spec2test.schema import Criterion, Requirement, SourceAnchor, TestabilityFlag

FIXTURES = Path(__file__).parent / "fixtures"


def req(text, id="spec:1:1", section="1", **fields):
    anchor = SourceAnchor(doc="spec", page=1, section=section, start=0, end=len(text), quote=text)
    fields.setdefault("type", "functional")
    fields.setdefault("strength", "shall")
    return Requirement(id=id, source_anchor=anchor, text=text, **fields)


def found(flags):
    return [(f.kind.value, f.reason, f.suggested_rewrite) for f in flags]


def answer(*flags, prompt_contains=()):
    return Fixture(
        schema="FlagDrafts",
        response=FlagDrafts(flags=list(flags)).model_dump(mode="json"),
        prompt_contains=tuple(prompt_contains),
    )


def draft(requirement_id="spec:1:1", phrase="one hand", kind="vague_term", reason="no force"):
    return FlagDraft(
        requirement_id=requirement_id,
        kind=kind,
        phrase=phrase,
        reason=reason,
        suggested_rewrite="[rewrite]",
    )


# Rules


def test_a_testable_requirement_raises_nothing():
    r = req(
        "The pump shall deliver at least 20 L/min within 3 s of start-up.",
        type="performance",
        criteria=[Criterion(parameter="flow", comparator="ge", value="20", unit="L/min")],
        conditions=["at 20 °C"],
        verification_method="test",
    )
    assert lint_rules(r) == []


def test_vague_terms_are_flagged_with_a_bracketed_rewrite():
    r = req("The bracket shall give stable support and be free of sharp corners.")
    assert found(vague_terms(r)) == [
        (
            "vague_term",
            "'stable' is vague: it sets no measurable limit",
            "The bracket shall give stable [state the maximum deflection, drift or vibration] "
            "support and be free of sharp corners.",
        ),
        (
            "vague_term",
            "'sharp' is vague: it sets no measurable limit",
            "The bracket shall give stable support and be free of sharp [state the minimum "
            "edge radius] corners.",
        ),
    ]


def test_escape_clauses_open_lists_and_and_or_are_replaced_in_the_rewrite():
    r = req("Filters shall be replaced as necessary, and/or cleaned, etc.")
    assert found(vague_terms(r)) == [
        (
            "vague_term",
            "'as necessary' is an escape clause: it leaves open when the requirement applies",
            "Filters shall be replaced [state exactly when this applies, or delete], and/or "
            "cleaned, etc.",
        ),
        (
            "vague_term",
            "'and/or' is ambiguous: it does not say whether both or either must hold",
            "Filters shall be replaced as necessary, ['and' or 'or'] cleaned, etc.",
        ),
        (
            "vague_term",
            "'etc.' leaves the list open-ended",
            "Filters shall be replaced as necessary, and/or cleaned, [list every item].",
        ),
    ]


def test_unbounded_wording():
    r = req("Noise shall be minimised and the seal shall never leak.")
    kinds = [(f.kind.value, f.reason.split(" is ")[0]) for f in vague_terms(r)]
    assert kinds == [("unbounded", "'minimised'"), ("unbounded", "'never'")]
    [f] = vague_terms(req("Latency shall be as low as possible."))
    assert f.suggested_rewrite == "Latency shall be [state the target value and unit]."


def test_a_longer_phrase_is_flagged_once():
    # "as far as possible" is one escape clause, not also an unbounded "possible".
    [f] = vague_terms(req("Cables shall be routed away from heat as far as possible."))
    assert f.reason.startswith("'as far as possible' is an escape clause")


def test_sufficient_is_resolved_by_a_stated_criterion():
    text = "The tank shall have sufficient capacity for 8 h of operation."
    assert [f.kind.value for f in vague_terms(req(text))] == ["vague_term"]
    measured = req(text, criteria=[Criterion(parameter="run time", value="8", unit="h")])
    assert vague_terms(measured) == []


def test_words_inside_other_words_are_not_vague():
    assert (
        vague_terms(req("The fastener shall be stainless; the property label shall be blue.")) == []
    )


def test_missing_unit_on_a_criterion():
    r = req(
        "The cable shall withstand 500 bends and a pull of 200.",
        criteria=[
            Criterion(parameter="number of bends", comparator="ge", value="500"),
            Criterion(parameter="pull force", comparator="ge", value="200"),
        ],
    )
    assert found(missing_units(r)) == [
        (
            "missing_unit",
            "criterion 'pull force' has the value 200 but no unit",
            "The cable shall withstand 500 bends and a pull of 200 [unit].",
        )
    ]


def test_missing_unit_on_a_bare_number_in_the_text():
    r = req("The pump shall start within 2 after power-on.")
    assert found(missing_units(r)) == [
        (
            "missing_unit",
            "'within 2' gives a number with no unit",
            "The pump shall start within 2 [unit] after power-on.",
        )
    ]
    # Decimals and thousands are one number, and a number with its unit is fine.
    assert missing_units(req("Letters shall be not less than 4.8 mm nor more than 6.4 mm.")) == []
    assert missing_units(req("The radius shall be not more than 1,651 mm.")) == []
    assert missing_units(req("Use at least 3 bolts.")) == []


def test_missing_condition_on_a_test_with_criteria_only():
    r = req(
        "The cell shall hold at least 90 percent of its capacity.",
        type="performance",
        criteria=[Criterion(parameter="capacity", comparator="ge", value="90", unit="percent")],
        verification_method="test",
    )
    assert found(missing_conditions(r)) == [
        (
            "missing_condition",
            "sets a measurable level (capacity ge 90 percent) but states no conditions to "
            "test it under",
            "The cell shall hold at least 90 percent of its capacity [when tested under: state "
            "the operating state, environment and procedure].",
        )
    ]
    assert missing_conditions(r.model_copy(update={"conditions": ["after 500 cycles"]})) == []
    in_text = r.model_copy(update={"text": "After 500 cycles the cell shall hold 90 percent."})
    assert missing_conditions(in_text) == []
    # A dimension checked by inspection does not depend on test conditions.
    assert missing_conditions(r.model_copy(update={"verification_method": "inspection"})) == []


def test_compound_requirement_is_split_in_the_rewrite():
    r = req("The housing shall be red, and shall carry a label; it must be sealed.")
    assert found(compound(r)) == [
        (
            "compound",
            "states 3 obligations ('shall', 'shall', 'must'); each needs its own verdict",
            "The housing shall be red. The housing shall carry a label. It must be sealed.",
        )
    ]
    assert compound(req("The housing shall not be red or blue.")) == []


def test_every_rule_flag_carries_a_reason_and_a_rewrite():
    r = req(
        "The unit shall start quickly and shall be cleaned as appropriate within 5.",
        criteria=[Criterion(parameter="start time", comparator="le", value="5")],
        type="performance",
        verification_method="test",
    )
    flags = lint_rules(r)
    assert {f.kind.value for f in flags} == {
        "vague_term",
        "missing_unit",
        "missing_condition",
        "compound",
    }
    assert all(f.reason and f.suggested_rewrite for f in flags)


# The lint pass


def test_rules_only_lint_replaces_existing_flags():
    old = TestabilityFlag(kind="unbounded", reason="stale")
    r = req("The lid shall be easy to open.", testability_flags=[old])
    result = lint([r])
    assert not result.llm_pass
    [flag] = result.requirements[0].testability_flags
    assert flag.reason == "'easy' is vague: it sets no measurable limit"


def test_llm_pass_adds_checked_flags_after_the_rule_flags():
    a = req("The switch shall be operable with one hand quickly.", id="spec:1:1")
    b = req("The lamp shall be red.", id="spec:1:2")
    llm = FakeLLM(
        [
            answer(
                draft("spec:1:1", "Operable with  one hand"),  # case and spaces don't matter
                draft("spec:1:1", "one hand"),  # same flag twice: kept once
                draft("spec:1:1", "hand quickly", kind="unbounded"),  # a rule flagged 'quickly'
                draft("spec:1:2", "glowing", reason="not in the text"),
                draft("spec:9:9", "red"),
            )
        ]
    )
    result = lint([a, b], llm)
    flags = result.requirements[0].testability_flags
    assert [f.reason for f in flags] == [
        "'quickly' is vague: it sets no measurable limit",
        "no force",
    ]
    assert result.requirements[1].testability_flags == []
    assert [r.error for r in result.rejected] == [
        "repeats the rule flag on 'quickly'",
        "'glowing' is not in the requirement's text or source quote",
        "no requirement 'spec:9:9' in this batch",
    ]
    assert list(result.llm_flags) == ["spec:1:1"]

    [call] = llm.calls
    assert call.system == SYSTEM and "Section: 1" in call.prompt
    records = [json.loads(line) for line in call.prompt.splitlines() if line.startswith("{")]
    assert records[0]["already_flagged"] == [
        {"kind": "vague_term", "reason": "'quickly' is vague: it sets no measurable limit"}
    ]
    assert "source" not in records[0]  # the quote is the text


def test_one_llm_call_per_section_and_failures_are_kept(monkeypatch):
    monkeypatch.setattr(lint_pipeline, "BATCH_SIZE", 2)
    reqs = [req(f"Part {i} shall be red.", id=f"spec:1:{i}") for i in range(3)]
    reqs.append(req("The case shall be blue.", id="spec:2:1", section="2"))
    llm = FakeLLM([answer(prompt_contains=["Section: 1"])])
    result = lint(reqs, llm)
    assert [c.prompt.count('{"id"') for c in llm.calls] == [2, 1]
    [failure] = result.failed
    assert (failure.section, failure.requirement_ids) == ("2", ["spec:2:1"])
    assert "no fixture" in failure.error


def test_relint_reruns_the_rules_and_keeps_the_llm_flags():
    r = req("The switch shall be operable with one hand.")
    first = lint([r], FakeLLM([answer(draft())]))
    saved = Lint.model_validate_json(first.model_dump_json())
    changed = r.model_copy(update={"text": "The switch shall be operable with one hand, etc."})
    again = relint([changed], saved)
    assert [f.reason for f in again.requirements[0].testability_flags] == [
        "'etc.' leaves the list open-ended",
        "no force",
    ]
    assert again.llm_pass and relint([], saved).llm_flags == {}


# CLI


def test_cli_lint_prints_flags_and_rewrites(widget_lamp, fake_llm, tmp_path, capsys):
    saved = tmp_path / "extraction.json"
    saved.write_text(extract(widget_lamp, fake_llm).model_dump_json(), encoding="utf-8")

    assert cli.main(["lint", str(saved), "--fake", str(FIXTURES / "llm")]) == 0
    out = capsys.readouterr().out
    assert "widget-lamp:2.3:1  The switch shall be operable with one hand.\n" in out
    assert "    vague_term: 'operable with one hand' sets no force, reach or time limit.\n" in out
    assert "        rewrite: The switch shall be operable with one hand with a force" in out
    assert "rejected  widget-lamp:2.1:1  vague_term: 'dazzling glare' is not in" in out
    summary, cost = out.rstrip().splitlines()[-2:]
    assert summary == "2 of 5 requirements flagged, 2 flags, 1 LLM flags rejected, 0 batches failed"
    assert cost.startswith("cost unknown (no price for FakeLLM); 4 calls")

    assert cli.main(["lint", str(saved), "--rules-only", "--json"]) == 0
    result = Lint.model_validate_json(capsys.readouterr().out)
    assert not result.llm_pass and len(result.requirements) == 5


def test_cli_lint_reports_bad_input(tmp_path, capsys):
    assert cli.main(["lint", str(tmp_path / "nope.json"), "--rules-only"]) == 2
    assert "nope.json" in capsys.readouterr().err
    empty = tmp_path / "empty.json"
    empty.write_text(Extraction(doc_id="d").model_dump_json(), encoding="utf-8")
    assert cli.main(["lint", str(empty), "--rules-only"]) == 0


@pytest.mark.parametrize("schema", [FlagDrafts])
def test_lint_answer_schema_is_strict(schema):
    with pytest.raises(ValueError):
        schema.model_validate({"flags": [{**draft().model_dump(), "severity": "high"}]})
