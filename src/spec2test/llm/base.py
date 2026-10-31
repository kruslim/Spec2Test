"""Provider-agnostic LLM interface.

Every stage that calls a model (extract, lint, generate, the judge) depends only on
`LLMClient`, so a different provider or an approved private endpoint can be plugged in
without touching the core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from spec2test.llm.usage import TokenUsage


class LLMError(Exception):
    """Base class for every failure an `LLMClient` raises."""


class LLMRefusalError(LLMError):
    """The model declined the request."""


class LLMOutputError(LLMError):
    """The model's output was truncated or did not match the requested schema."""


class LLMClient(ABC):
    @property
    def usage(self) -> list[TokenUsage]:
        """One `TokenUsage` per model call so far, oldest first. A stage records the calls
        it made as `llm.usage[start:]`, with `start = len(llm.usage)` taken before."""
        if not hasattr(self, "_usage"):
            self._usage: list[TokenUsage] = []
        return self._usage

    def record_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.usage.append(
            TokenUsage(model=model, input_tokens=input_tokens, output_tokens=output_tokens)
        )

    @abstractmethod
    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        """Return the model's answer to `prompt` as a validated instance of `schema`.

        Raises `LLMError` (or a subclass) instead of returning invalid output.
        """


def model_family(model: str) -> str:
    """'claude-opus-5-5' -> 'claude', 'kimi-for-coding' -> 'kimi', 'models/gemini-3.1-pro'
    -> 'gemini'. Two models of one family are assumed to share blind spots."""
    return model.casefold().split("/")[-1].split("-")[0]


def model_name(llm: LLMClient) -> str:
    """The model ID an `LLMClient` calls, or its class name if it has none."""
    return getattr(llm, "model", None) or type(llm).__name__
