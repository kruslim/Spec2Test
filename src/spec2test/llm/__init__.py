from spec2test.llm.anthropic_client import AnthropicLLM
from spec2test.llm.base import (
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    model_family,
    model_name,
)
from spec2test.llm.fake import FakeLLM, Fixture, FixtureNotFoundError, LLMCall
from spec2test.llm.openai_compat import PRESETS, OpenAICompatLLM
from spec2test.llm.session import (
    AnswerMissingError,
    SessionAnswer,
    SessionLLM,
    SessionRequest,
    answer_line,
    load_requests,
    request_key,
    write_requests,
)
from spec2test.llm.usage import PRICES, Cost, Price, TokenUsage, cost, cost_line, price_of


def provider_name(llm: LLMClient) -> str:
    """How `llm` is called, for the record: "session" (answered in a Claude Code session,
    no API spend), "fake", "anthropic", an OpenAI-compatible preset's name ("kimi-code",
    "gemini") or "openai-compatible"."""
    if isinstance(llm, SessionLLM):
        return "session"
    if isinstance(llm, FakeLLM):
        return "fake"
    if isinstance(llm, AnthropicLLM):
        return "anthropic"
    if isinstance(llm, OpenAICompatLLM):
        url = llm.base_url.rstrip("/")
        return next(
            (name for name, p in PRESETS.items() if p.base_url.rstrip("/") == url),
            "openai-compatible",
        )
    return type(llm).__name__


__all__ = [
    "PRESETS",
    "PRICES",
    "AnswerMissingError",
    "AnthropicLLM",
    "Cost",
    "FakeLLM",
    "Fixture",
    "FixtureNotFoundError",
    "LLMCall",
    "LLMClient",
    "LLMError",
    "LLMOutputError",
    "LLMRefusalError",
    "OpenAICompatLLM",
    "Price",
    "SessionAnswer",
    "SessionLLM",
    "SessionRequest",
    "TokenUsage",
    "answer_line",
    "cost",
    "cost_line",
    "load_requests",
    "model_family",
    "model_name",
    "price_of",
    "provider_name",
    "request_key",
    "write_requests",
]
