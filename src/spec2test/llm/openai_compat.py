"""`LLMClient` for any endpoint that speaks the OpenAI chat-completions protocol.

Structured output is asked for in the prompt (the JSON Schema goes in the system message)
and validated with Pydantic, so it works on endpoints that do not support
`response_format`. The client sends its own User-Agent, `spec2test/<version>`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import httpx2
from pydantic import BaseModel, ValidationError

from spec2test.llm.base import LLMClient, LLMError, LLMOutputError, LLMRefusalError


@dataclass(frozen=True)
class Preset:
    base_url: str
    model: str
    api_key_env: str


PRESETS = {
    "kimi-code": Preset(
        base_url="https://api.kimi.com/coding/v1",
        model="kimi-for-coding",
        api_key_env="KIMI_CODE_API_KEY",
    ),
}

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL)


class OpenAICompatLLM(LLMClient):
    """Calls `{base_url}/chat/completions` and validates the reply against the schema.

    The API key is read from `api_key_env` on first use, so constructing this class never
    needs credentials.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key_env: str,
        max_tokens: int = 16_000,
        timeout: float = 300.0,
        client: httpx2.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = client

    @classmethod
    def preset(cls, name: str, **kwargs: Any) -> OpenAICompatLLM:
        p = PRESETS[name]
        kwargs.setdefault("model", p.model)
        return cls(p.base_url, api_key_env=p.api_key_env, **kwargs)

    @property
    def client(self) -> httpx2.Client:
        if self._client is None:
            self._client = httpx2.Client(timeout=self.timeout)
        return self._client

    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise LLMError(f"set {self.api_key_env} to call {self.base_url}")
        instructions = (
            f"Reply with one JSON object that matches this JSON Schema, and nothing else:\n"
            f"{json.dumps(schema.model_json_schema())}"
        )
        messages = [
            {
                "role": "system",
                "content": f"{system}\n\n{instructions}" if system else instructions,
            },
            {"role": "user", "content": prompt},
        ]
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": f"spec2test/{version('spec2test')}",
                },
                json={"model": self.model, "max_tokens": self.max_tokens, "messages": messages},
            )
        except httpx2.HTTPError as exc:
            raise LLMError(f"{self.base_url}: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"{self.model} returned HTTP {response.status_code}: {response.text}")

        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusalError(f"{self.model} declined the request")
        if choice.get("finish_reason") == "length":
            raise LLMOutputError(f"{self.model} output was truncated at {self.max_tokens} tokens")
        text = (choice["message"].get("content") or "").strip()
        if fenced := _FENCE.match(text):
            text = fenced["body"]
        if not text:
            raise LLMOutputError(f"{self.model} returned no {schema.__name__} output")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMOutputError(
                f"{self.model} output did not match {schema.__name__}: {exc}"
            ) from exc
