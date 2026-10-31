"""`LLMClient` backed by the Anthropic Messages API, using structured outputs."""

from __future__ import annotations

from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ValidationError

from spec2test.llm.base import LLMClient, LLMOutputError, LLMRefusalError

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AnthropicLLM(LLMClient):
    """Calls Claude and returns its answer validated against the requested Pydantic schema.

    Credentials resolve the usual SDK way (ANTHROPIC_API_KEY, or an `ant auth login`
    profile). The SDK client is created on first use, so constructing this class never
    needs credentials.

    With `fallbacks=True` (the default), a request the model declines is retried
    server-side on Anthropic's recommended fallback model instead of failing.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = 16_000,
        effort: Effort = "high",
        fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
            },
        }
        if system is not None:
            request["system"] = system
        if self.fallbacks:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"

        # `create` rather than `parse`: `parse` validates eagerly, which would report a
        # truncated response as a schema mismatch before stop_reason can be checked.
        response = self.client.beta.messages.create(**request)
        self._record(response)

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = details.category if details else None
            raise LLMRefusalError(f"{self.model} declined the request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(
                f"{self.model} output was truncated at max_tokens={self.max_tokens}"
            )

        # A model that declined mid-answer may have left partial text before a `fallback`
        # block; only the text after the last one belongs to the model that finished.
        text = ""
        for block in response.content:
            if block.type == "fallback":
                text = ""
            elif block.type == "text":
                text += block.text
        if not text:
            raise LLMOutputError(f"{self.model} returned no {schema.__name__} output")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMOutputError(
                f"{self.model} output did not match {schema.__name__}: {exc}"
            ) from exc

    def _record(self, response: Any) -> None:
        """Record the call's tokens. With a server-side fallback, each model that ran is
        billed at its own rate, so each sampling iteration is recorded under its model."""
        usage = response.usage
        served = getattr(response, "model", None) or self.model
        iterations = [
            i
            for i in getattr(usage, "iterations", None) or []
            if i.type in ("message", "fallback_message")
        ]
        if not iterations:
            self.record_usage(served, usage.input_tokens, usage.output_tokens)
        for i in iterations:
            self.record_usage(i.model or served, i.input_tokens, i.output_tokens)
