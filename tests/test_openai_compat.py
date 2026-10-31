"""OpenAICompatLLM tests. The offline tests run against a mocked HTTP transport."""

import json
import os

import httpx2
import pytest
from pydantic import BaseModel

from spec2test.llm import (
    PRESETS,
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    OpenAICompatLLM,
)

ENV = "SPEC2TEST_TEST_KEY"


class Echo(BaseModel):
    word: str


def completion(content, finish_reason="stop"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }


class Recorder:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    @property
    def sent(self) -> dict:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def llm_returning(monkeypatch):
    monkeypatch.setenv(ENV, "test-key")

    def make(body, status=200):
        recorder = Recorder(body, status)
        client = httpx2.Client(transport=httpx2.MockTransport(recorder))
        llm = OpenAICompatLLM(
            "https://llm.example.test/v1/", "test-model", api_key_env=ENV, client=client
        )
        return llm, recorder

    return make


def test_is_an_llm_client():
    assert isinstance(OpenAICompatLLM("https://x.test", "m", api_key_env=ENV), LLMClient)


def test_returns_validated_schema_instance(llm_returning):
    llm, recorder = llm_returning(completion('{"word": "ready"}'))
    assert llm.generate("Say ready.", Echo, system="You echo.") == Echo(word="ready")

    request = recorder.requests[-1]
    assert str(request.url) == "https://llm.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["user-agent"].startswith("spec2test/")
    sent = recorder.sent
    assert sent["model"] == "test-model"
    system, user = sent["messages"]
    assert system["role"] == "system"
    assert system["content"].startswith("You echo.\n\nReply with one JSON object")
    assert '"word"' in system["content"]  # the JSON Schema
    assert user == {"role": "user", "content": "Say ready."}


def test_reads_json_inside_a_code_fence(llm_returning):
    llm, _ = llm_returning(completion('```json\n{"word": "ready"}\n```'))
    assert llm.generate("Say ready.", Echo).word == "ready"


def test_truncated_output_raises(llm_returning):
    llm, _ = llm_returning(completion('{"word": "rea', finish_reason="length"))
    with pytest.raises(LLMOutputError, match="truncated"):
        llm.generate("Say ready.", Echo)


def test_content_filter_raises_refusal(llm_returning):
    llm, _ = llm_returning(completion("", finish_reason="content_filter"))
    with pytest.raises(LLMRefusalError):
        llm.generate("Say ready.", Echo)


def test_output_that_violates_schema_raises(llm_returning):
    llm, _ = llm_returning(completion('{"other": 1}'))
    with pytest.raises(LLMOutputError, match="Echo"):
        llm.generate("Say ready.", Echo)


def test_http_error_raises(llm_returning):
    llm, _ = llm_returning({"error": {"message": "invalid key"}}, status=401)
    with pytest.raises(LLMError, match="HTTP 401"):
        llm.generate("Say ready.", Echo)


@pytest.mark.parametrize(
    "body",
    [b"<html>bad gateway</html>", b'{"error": {"message": "overloaded"}}', b'{"choices": []}'],
)
def test_a_malformed_reply_is_an_llm_error(monkeypatch, body):
    # A stage catches LLMError per section or item; anything else would end the whole run.
    monkeypatch.setenv(ENV, "test-key")
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, content=body))
    llm = OpenAICompatLLM(
        "https://x.test", "m", api_key_env=ENV, client=httpx2.Client(transport=transport)
    )
    with pytest.raises(LLMOutputError):
        llm.generate("Say ready.", Echo)


def flaky(monkeypatch, *responses):
    """An LLM whose endpoint answers with `responses` in turn; pauses are recorded."""
    monkeypatch.setenv(ENV, "test-key")
    queue, sent = list(responses), []

    def answer(request):
        sent.append(request)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    pauses: list[float] = []
    llm = OpenAICompatLLM(
        "https://x.test",
        "m",
        api_key_env=ENV,
        client=httpx2.Client(transport=httpx2.MockTransport(answer)),
        sleep=pauses.append,
    )
    return llm, sent, pauses


def test_rate_limits_and_server_errors_are_retried(monkeypatch):
    ok = httpx2.Response(200, json=completion('{"word": "ready"}'))
    llm, sent, pauses = flaky(
        monkeypatch,
        httpx2.Response(429, json={}),
        httpx2.Response(503, json={}),
        httpx2.ConnectError("refused"),
        ok,
    )
    assert llm.generate("Say ready.", Echo).word == "ready"
    assert len(sent) == 4 and pauses == [2.0, 4.0, 8.0]


def test_retries_give_up_with_the_last_error(monkeypatch):
    llm, sent, _ = flaky(monkeypatch, *[httpx2.Response(503, text="busy")] * 4)
    with pytest.raises(LLMError, match="HTTP 503: busy"):
        llm.generate("Say ready.", Echo)
    assert len(sent) == 4


def test_a_timeout_is_not_sent_again(monkeypatch):
    # The model may have run and billed the call before the reply was lost.
    llm, sent, pauses = flaky(monkeypatch, httpx2.ReadTimeout("slow"))
    with pytest.raises(LLMError, match="slow"):
        llm.generate("Say ready.", Echo)
    assert len(sent) == 1 and pauses == []


def test_missing_key_raises_before_any_request(llm_returning, monkeypatch):
    llm, recorder = llm_returning(completion('{"word": "ready"}'))
    monkeypatch.delenv(ENV)
    with pytest.raises(LLMError, match=ENV):
        llm.generate("Say ready.", Echo)
    assert recorder.requests == []


def test_kimi_code_preset():
    llm = OpenAICompatLLM.preset("kimi-code")
    assert llm.base_url == "https://api.kimi.com/coding/v1"
    assert llm.model == "kimi-for-coding"
    assert llm.api_key_env == "KIMI_CODE_API_KEY"
    assert OpenAICompatLLM.preset("kimi-code", model="other").model == "other"
    assert "kimi-code" in PRESETS


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEC2TEST_LIVE") != "1" or not os.environ.get("KIMI_CODE_API_KEY"),
    reason="set SPEC2TEST_LIVE=1 and KIMI_CODE_API_KEY",
)
def test_live_kimi_code_structured_output():
    result = OpenAICompatLLM.preset("kimi-code").generate("Reply with the word 'ready'.", Echo)
    assert result.word.strip().lower().strip(".'\"") == "ready"
