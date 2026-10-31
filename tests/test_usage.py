"""Token counts per call, the price table, and cost per page and per run. Offline: the
clients run against mocked transports, the pipelines against FakeLLM fixtures whose token
counts are invented for the test."""

import dataclasses
import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from spec2test import evals
from spec2test.evals import evaluate, rescore
from spec2test.extract import extract
from spec2test.generate import generate
from spec2test.judge import judge, quality_report
from spec2test.lint import lint
from spec2test.llm import (
    AnthropicLLM,
    FakeLLM,
    Fixture,
    LLMOutputError,
    OpenAICompatLLM,
    TokenUsage,
    cost,
    cost_line,
    price_of,
)

FIXTURES = Path(__file__).parent / "fixtures"
ENV = "SPEC2TEST_TEST_KEY"


class Echo(BaseModel):
    word: str


def calls(model, *tokens):
    return [TokenUsage(model=model, input_tokens=i, output_tokens=o) for i, o in tokens]


def metered(model, input_tokens=1000, output_tokens=100):
    """The widget lamp fixtures, each call billed `input_tokens` in, `output_tokens` out."""
    fake = FakeLLM.from_dir(FIXTURES / "llm", model=model)
    fake.fixtures = [
        dataclasses.replace(f, input_tokens=input_tokens, output_tokens=output_tokens)
        for f in fake.fixtures
    ]
    return fake


# Prices and cost


def test_cost_uses_the_price_per_million_tokens():
    c = cost(calls("claude-opus-5-5", (1_000_000, 0), (0, 500_000)))
    assert c.dollars == pytest.approx(4.0 + 10.0)
    assert (c.calls, c.input_tokens, c.output_tokens) == (2, 1_000_000, 500_000)
    assert c.per(7) == pytest.approx(2.0)
    assert c.per(0) is None


def test_long_prompts_are_billed_at_the_long_context_rate():
    short = cost(calls("gemini-3.1-pro-preview", (200_000, 1_000_000))).dollars
    long = cost(calls("gemini-3.1-pro-preview", (200_001, 1_000_000))).dollars
    assert short == pytest.approx(0.4 + 12.0)
    assert long == pytest.approx(200_001 * 4 / 1e6 + 18.0)


def test_a_model_without_a_price_has_no_cost():
    c = cost(calls("kimi-for-coding", (10, 10)) + calls("claude-opus-5-5", (10, 10)))
    assert c.dollars is None and c.unpriced == ["kimi-for-coding"]
    assert c.per(3) is None
    assert "no price for kimi-for-coding" in cost_line(calls("kimi-for-coding", (10, 10)), 3)


def test_a_provider_prefix_finds_the_price():
    assert price_of("models/gemini-3.1-pro-preview") is price_of("gemini-3.1-pro-preview")


def test_cost_line():
    line = cost_line(calls("claude-opus-5-5", (100_000, 10_000)), 4)
    assert (
        line == "cost $0.60 ($0.1500 per page over 4 cited pages); "
        "1 calls, 100,000 in / 10,000 out tokens"
    )
    assert cost_line([], 4) == "cost: no model calls recorded"
    assert "over 4 scope pages" in cost_line(calls("claude-opus-5-5", (10, 10)), 4, "scope")


# Clients


def test_fake_records_the_fixture_token_counts():
    fake = FakeLLM(
        [Fixture(schema="Echo", response={"word": "a"}, input_tokens=7, output_tokens=3)],
        model="m",
    )
    fake.generate("x", Echo)
    fake.generate("y", Echo)
    assert fake.usage == calls("m", (7, 3), (7, 3))
    assert FakeLLM.from_dir(FIXTURES / "llm").fixtures[0].input_tokens == 0


def test_fixture_usage_is_read_from_json():
    f = Fixture.from_dict(
        {"schema": "Echo", "response": {}, "usage": {"input_tokens": 5, "output_tokens": 2}}
    )
    assert (f.input_tokens, f.output_tokens) == (5, 2)


def anthropic_llm(body):
    def respond(request):
        return httpx2.Response(200, json=body)

    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    )
    return AnthropicLLM(client=client)


def anthropic_message(text, stop_reason="end_turn", **usage):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300, **usage},
    }


def test_anthropic_records_tokens_even_when_the_output_is_truncated():
    llm = anthropic_llm(anthropic_message('{"word": "a"}'))
    llm.generate("x", Echo)
    assert llm.usage == calls("claude-opus-5-5", (1200, 300))

    llm = anthropic_llm(anthropic_message('{"wo', stop_reason="max_tokens"))
    with pytest.raises(LLMOutputError):
        llm.generate("x", Echo)
    assert llm.usage == calls("claude-opus-5-5", (1200, 300))


def test_anthropic_records_each_model_of_a_fallback_separately():
    entry = {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    iterations = [
        {"type": "message", "model": "claude-opus-5-5", "input_tokens": 1000,
         "output_tokens": 50, **entry},
        {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 1000,
         "output_tokens": 250, **entry},
    ]  # fmt: skip
    llm = anthropic_llm(anthropic_message('{"word": "a"}', iterations=iterations))
    llm.generate("x", Echo)
    assert llm.usage == calls("claude-opus-5-5", (1000, 50)) + calls("claude-opus-5", (1000, 250))


def openai_llm(monkeypatch, body):
    monkeypatch.setenv(ENV, "test-key")
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json=body))
    return OpenAICompatLLM(
        "https://llm.example.test/v1",
        "gemini-3.1-pro-preview",
        api_key_env=ENV,
        client=httpx2.Client(transport=transport),
    )


def completion(content, finish_reason="stop", usage=None):
    body = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return body


def test_openai_compat_counts_thinking_tokens_left_out_of_completion_tokens(monkeypatch):
    usage = {"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1500}
    llm = openai_llm(monkeypatch, completion('{"word": "a"}', usage=usage))
    llm.generate("x", Echo)
    assert llm.usage == calls("gemini-3.1-pro-preview", (900, 600))


def test_openai_compat_records_tokens_of_a_truncated_answer(monkeypatch):
    usage = {"prompt_tokens": 900, "completion_tokens": 100}
    llm = openai_llm(monkeypatch, completion('{"wo', finish_reason="length", usage=usage))
    with pytest.raises(LLMOutputError):
        llm.generate("x", Echo)
    assert llm.usage == calls("gemini-3.1-pro-preview", (900, 100))


def test_openai_compat_without_usage_records_nothing(monkeypatch):
    llm = openai_llm(monkeypatch, completion('{"word": "a"}'))
    llm.generate("x", Echo)
    assert llm.usage == []


# Pipelines


def test_extract_and_lint_save_their_own_calls(widget_lamp):
    llm = metered("claude-opus-5-5")
    extraction = extract(widget_lamp, llm)
    linted = lint(extraction.requirements, llm)
    assert extraction.usage and linted.usage
    assert extraction.usage + linted.usage == llm.usage
    saved = type(extraction).model_validate_json(extraction.model_dump_json())
    assert cost(saved.usage).dollars == cost(extraction.usage).dollars > 0
    assert lint(extraction.requirements).usage == []  # the rules alone call no model


def test_generate_and_judge_save_their_own_calls(widget_lamp):
    generator = metered("claude-sonnet-5-5")
    linted = lint(extract(widget_lamp, generator).requirements, generator).requirements
    before = len(generator.usage)
    generation = generate(linted, generator, doc=widget_lamp)
    assert generation.usage == generator.usage[before:]
    assert len(generation.usage) >= len(generation.items) > 0
    assert generation.pages == len({r.source_anchor.page for r in linted})

    judging = judge(generation, metered("claude-opus-5-5", 2000, 400))
    assert len(judging.usage) == len(judging.judgements)
    assert 0 < judging.pages <= generation.pages
    assert "per page" in quality_report(judging)

    saved = type(generation).model_validate_json(generation.model_dump_json())
    assert saved.usage == generation.usage


def test_eval_prices_extraction_and_linting(widget_lamp, tmp_path):
    gold = evals.load_gold(FIXTURES / "gold" / "widget-lamp.jsonl", widget_lamp)
    llm = metered("claude-opus-5-5")
    run = evaluate(widget_lamp, gold, llm, corpus="widget-lamp")
    assert run.usage == llm.usage and run.usage
    dollars = cost(run.usage).dollars
    assert dollars == pytest.approx(len(run.usage) * (1000 * 4 + 100 * 20) / 1e6)

    text = evals.text_report(run)
    assert f"cost ${dollars:.2f}" in text and f"over {run.pages} scope pages" in text
    html = evals.html_report(run)
    assert "Cost per scope page" in html and f"${dollars / run.pages:.4f}" in html

    again = rescore(widget_lamp, gold, run, corpus="widget-lamp")
    assert again.usage == run.usage
    _, results = evals.write_report(run, tmp_path)
    assert json.loads(results.read_text(encoding="utf-8"))["usage"][0]["input_tokens"] == 1000


def test_an_unpriced_model_shows_its_tokens_but_no_dollars(widget_lamp):
    gold = evals.load_gold(FIXTURES / "gold" / "widget-lamp.jsonl", widget_lamp)
    run = evaluate(widget_lamp, gold, metered("kimi-for-coding"), corpus="widget-lamp")
    assert "cost unknown (no price for kimi-for-coding)" in evals.text_report(run)
    assert "unknown: no price for kimi-for-coding" in evals.html_report(run)


def test_a_results_file_without_usage_still_loads(widget_lamp, fake_llm):
    gold = evals.load_gold(FIXTURES / "gold" / "widget-lamp.jsonl", widget_lamp)
    data = json.loads(evaluate(widget_lamp, gold, fake_llm, corpus="w").model_dump_json())
    del data["usage"]
    assert evals.EvalRun.model_validate(data).usage == []
