"""AnthropicLLM tests. The offline tests run the real SDK against a mocked HTTP transport."""

import json
import os

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from spec2test.llm import AnthropicLLM, LLMOutputError, LLMRefusalError
from spec2test.llm.anthropic_client import DEFAULT_MODEL, FALLBACK_BETA
from spec2test.schema import Requirement

REQUIREMENT = {
    "id": "REQ-001",
    "source_anchor": {
        "doc": "example-spec",
        "page": 1,
        "section": "5.1",
        "start": 0,
        "end": 11,
        "quote": "Shall work.",
    },
    "text": "Shall work.",
    "type": "functional",
    "strength": "shall",
}


def message(content, stop_reason="end_turn", **extra):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        **extra,
    }


def text_message(payload):
    return message([{"type": "text", "text": json.dumps(payload)}])


class Recorder:
    def __init__(self, body):
        self.body = body
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.body)

    @property
    def sent(self) -> dict:
        return json.loads(self.requests[-1].content)


def llm_returning(body, **kwargs):
    recorder = Recorder(body)
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicLLM(client=client, **kwargs), recorder


def test_returns_validated_schema_instance():
    llm, recorder = llm_returning(text_message(REQUIREMENT))
    req = llm.generate("Extract requirements.", Requirement, system="You extract requirements.")
    assert isinstance(req, Requirement)
    assert req.id == "REQ-001"

    sent = recorder.sent
    assert sent["model"] == DEFAULT_MODEL
    assert sent["system"] == "You extract requirements."
    assert sent["messages"] == [{"role": "user", "content": "Extract requirements."}]
    assert sent["output_config"]["effort"] == "high"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["fallbacks"] == "default"
    assert FALLBACK_BETA in recorder.requests[-1].headers["anthropic-beta"]


def test_fallbacks_and_system_can_be_left_out():
    llm, recorder = llm_returning(text_message(REQUIREMENT), fallbacks=False, effort="low")
    llm.generate("Extract requirements.", Requirement)
    sent = recorder.sent
    assert "fallbacks" not in sent
    assert "system" not in sent
    assert sent["output_config"]["effort"] == "low"
    assert "anthropic-beta" not in recorder.requests[-1].headers


def test_reads_only_the_output_after_a_fallback():
    body = message(
        [
            {"type": "text", "text": '{"id": "REQ-'},
            {
                "type": "fallback",
                "from": {"model": DEFAULT_MODEL},
                "to": {"model": "claude-opus-4-8"},
                "trigger": {"type": "refusal", "category": "cyber"},
            },
            {"type": "text", "text": json.dumps(REQUIREMENT)},
        ]
    )
    llm, _ = llm_returning(body)
    assert llm.generate("Extract requirements.", Requirement).id == "REQ-001"


def test_refusal_raises():
    body = message(
        [],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
    )
    llm, _ = llm_returning(body)
    with pytest.raises(LLMRefusalError, match="cyber"):
        llm.generate("Extract requirements.", Requirement)


def test_truncated_output_raises():
    llm, _ = llm_returning(message([{"type": "text", "text": '{"id": "REQ'}], "max_tokens"))
    with pytest.raises(LLMOutputError, match="truncated"):
        llm.generate("Extract requirements.", Requirement)


def test_output_that_violates_schema_raises():
    llm, _ = llm_returning(text_message({"id": "REQ-001"}))
    with pytest.raises(LLMOutputError, match="Requirement"):
        llm.generate("Extract requirements.", Requirement)


def test_construction_needs_no_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    AnthropicLLM()


class Echo(BaseModel):
    word: str


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and ANTHROPIC_API_KEY",
)
def test_live_structured_output():
    result = AnthropicLLM(effort="low").generate("Reply with the word 'ready'.", Echo)
    assert result.word.strip().lower().strip(".'\"") == "ready"
