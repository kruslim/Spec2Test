"""A deterministic `LLMClient` that replays canned responses from fixtures.

Used by every offline test and by offline eval runs, so no test ever needs a network
connection or an API key.

Fixture file format (JSON; one fixture object or a list of them per file):

    {
      "name": "rear-image-timing",          # optional; defaults to the file name
      "schema": "Requirement",              # class name of the requested schema
      "prompt_contains": ["2.0 seconds"],   # optional; every substring must be in the prompt
      "response": { ... },                  # validated against the requested schema
      "usage": {"input_tokens": 900, "output_tokens": 120}   # optional; default 0 and 0
    }

A call is answered by the first fixture, in load order, whose `schema` equals the
requested schema's class name and whose `prompt_contains` substrings all appear in the
prompt. Files load in sorted file-name order. Every answered call is recorded in
`FakeLLM.usage` under the fake's `model` name, with the fixture's token counts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from spec2test.llm.base import LLMClient, LLMError, LLMOutputError, model_name
from spec2test.textio import read_text


class FixtureNotFoundError(LLMError):
    """No fixture matches the request."""


@dataclass(frozen=True)
class Fixture:
    schema: str
    response: dict[str, Any]
    prompt_contains: tuple[str, ...] = ()
    name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def matches(self, prompt: str, schema: type[BaseModel]) -> bool:
        return self.schema == schema.__name__ and all(s in prompt for s in self.prompt_contains)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, default_name: str = "") -> Fixture:
        missing = {"schema", "response"} - data.keys()
        if missing:
            raise ValueError(f"fixture {default_name!r} is missing {sorted(missing)}")
        prompt_contains = data.get("prompt_contains", ())
        if isinstance(prompt_contains, str):
            prompt_contains = (prompt_contains,)
        usage = data.get("usage", {})
        return cls(
            schema=data["schema"],
            response=data["response"],
            prompt_contains=tuple(prompt_contains),
            name=data.get("name", default_name),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


@dataclass(frozen=True)
class LLMCall:
    prompt: str
    system: str | None
    schema: type[BaseModel]
    fixture: str


class FakeLLM(LLMClient):
    def __init__(self, fixtures: Iterable[Fixture], *, model: str | None = None) -> None:
        self.fixtures = list(fixtures)
        # Names the fake in reports; two fakes with different names count as two models.
        self.model = model
        self.calls: list[LLMCall] = []

    @classmethod
    def from_dir(cls, path: str | Path, *, model: str | None = None) -> FakeLLM:
        fixtures: list[Fixture] = []
        for file in sorted(Path(path).glob("*.json")):
            data = json.loads(read_text(file))
            entries = data if isinstance(data, list) else [data]
            fixtures.extend(Fixture.from_dict(entry, default_name=file.stem) for entry in entries)
        return cls(fixtures, model=model)

    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        fixture = next((f for f in self.fixtures if f.matches(prompt, schema)), None)
        if fixture is None:
            raise FixtureNotFoundError(
                f"no fixture for schema {schema.__name__!r} matches prompt starting {prompt[:80]!r}"
            )
        self.calls.append(
            LLMCall(prompt=prompt, system=system, schema=schema, fixture=fixture.name)
        )
        self.record_usage(model_name(self), fixture.input_tokens, fixture.output_tokens)
        try:
            return schema.model_validate(fixture.response)
        except ValidationError as exc:
            raise LLMOutputError(
                f"fixture {fixture.name!r} does not match {schema.__name__}: {exc}"
            ) from exc
