import json

import pytest
from pydantic import BaseModel

from spec2test.llm import (
    FakeLLM,
    Fixture,
    FixtureNotFoundError,
    LLMClient,
    LLMError,
    LLMOutputError,
)
from spec2test.schema import Requirement, VerificationItem, VerificationItemKind, VerificationMethod

PROMPT = "Extract: The rear image shall be displayed within 2.0 seconds of selecting reverse."


def test_is_an_llm_client(fake_llm):
    assert isinstance(fake_llm, LLMClient)


def test_returns_canned_structured_output(fake_llm):
    req = fake_llm.generate(PROMPT, Requirement)
    assert isinstance(req, Requirement)
    assert req.id == "REQ-001"
    assert req.verification_method is VerificationMethod.TEST
    assert req.criteria[0].unit == "s"
    assert req.source_anchor.end - req.source_anchor.start == len(req.source_anchor.quote)


def test_first_matching_fixture_wins(fake_llm):
    specific = fake_llm.generate("Write a test case for REQ-001", VerificationItem)
    assert specific.kind is VerificationItemKind.TEST_CASE
    fallback = fake_llm.generate("Write a checklist item for REQ-777", VerificationItem)
    assert fallback.id == "VI-999"


def test_records_calls(fake_llm):
    fake_llm.generate(PROMPT, Requirement, system="You extract requirements.")
    [call] = fake_llm.calls
    assert call.prompt == PROMPT
    assert call.system == "You extract requirements."
    assert call.schema is Requirement
    assert call.fixture == "requirement-rear-image"


def test_unmatched_prompt_raises(fake_llm):
    with pytest.raises(FixtureNotFoundError, match="Requirement"):
        fake_llm.generate("Something unrelated", Requirement)


def test_unknown_schema_raises(fake_llm):
    class Other(BaseModel):
        x: int

    with pytest.raises(LLMError):
        fake_llm.generate(PROMPT, Other)


def test_fixture_that_violates_schema_raises_output_error():
    llm = FakeLLM([Fixture(schema="Requirement", response={"id": "REQ-1"}, name="broken")])
    with pytest.raises(LLMOutputError, match="broken"):
        llm.generate(PROMPT, Requirement)


def test_from_dir_reads_single_objects_and_lists(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps({"schema": "X", "prompt_contains": "hello", "response": {}}), encoding="utf-8"
    )
    (tmp_path / "b.json").write_text(
        json.dumps([{"schema": "Y", "response": {}}, {"schema": "Z", "response": {}}]),
        encoding="utf-8",
    )
    llm = FakeLLM.from_dir(tmp_path)
    assert [(f.schema, f.name) for f in llm.fixtures] == [("X", "a"), ("Y", "b"), ("Z", "b")]
    assert llm.fixtures[0].prompt_contains == ("hello",)


def test_from_dir_rejects_incomplete_fixture(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps({"schema": "X"}), encoding="utf-8")
    with pytest.raises(ValueError, match="response"):
        FakeLLM.from_dir(tmp_path)
