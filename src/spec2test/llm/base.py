"""Provider-agnostic LLM interface.

Every stage that calls a model (extract, lint, generate, the judge) depends only on
`LLMClient`, so a different provider or an approved private endpoint can be plugged in
without touching the core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMError(Exception):
    """Base class for every failure an `LLMClient` raises."""


class LLMRefusalError(LLMError):
    """The model declined the request."""


class LLMOutputError(LLMError):
    """The model's output was truncated or did not match the requested schema."""


class LLMClient(ABC):
    @abstractmethod
    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        """Return the model's answer to `prompt` as a validated instance of `schema`.

        Raises `LLMError` (or a subclass) instead of returning invalid output.
        """
