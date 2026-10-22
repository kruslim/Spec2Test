"""Model names and families, for the rule that a model never grades its own work."""

from __future__ import annotations

from spec2test.llm.base import LLMClient


def model_name(llm: LLMClient) -> str:
    """The model ID a client calls, or its class name when it has none (FakeLLM)."""
    return getattr(llm, "model", None) or type(llm).__name__


def model_family(model: str) -> str:
    """'claude-opus-5-5' -> 'claude', 'kimi-for-coding' -> 'kimi'."""
    return model.casefold().split("/")[-1].split("-")[0]


def same_family(a: str, b: str) -> bool:
    return model_family(a) == model_family(b)
