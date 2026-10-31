"""An `LLMClient` whose answers are written by a model in a Claude Code session.

Claude Opus has no API key in this project: it runs only inside Claude Code sessions,
where it costs no API tokens. `SessionLLM` lets a stage call it all the same, in two
passes over the same command:

1. Every call without an answer yet is recorded as a `SessionRequest` (the schema, the
   system prompt and the prompt, under a key) and fails with `AnswerMissingError`. The
   CLI writes the recorded requests to a JSONL file.
2. In a session, the model reads each request and writes its answer, one
   `{"key": ..., "response": {...}}` line per request, to the answers file. Run the same
   command again: every call with an answer returns it, validated against the schema.

The key is a hash of the schema name, the system prompt and the prompt, so an answer
only ever answers the exact request it was written for; a changed prompt asks again.
Answers can be written in several sittings: the second pass asks only for what is
still missing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from spec2test.llm.base import LLMClient, LLMError, LLMOutputError

DEFAULT_MODEL = "claude-opus-5-5"


class AnswerMissingError(LLMError):
    """No answer has been written for this request yet; it was recorded instead."""


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    schema_name: str
    json_schema: dict[str, Any]
    system: str | None
    prompt: str


class SessionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    response: dict[str, Any]


def request_key(prompt: str, schema: type[BaseModel], system: str | None = None) -> str:
    text = "\0".join([schema.__name__, system or "", prompt])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class SessionLLM(LLMClient):
    """Replays answers written in a session; records every request it has no answer for.

    `model` is the model that writes the answers, for the model-role checks and reports.
    """

    def __init__(self, answers: dict[str, dict[str, Any]], *, model: str = DEFAULT_MODEL):
        self.answers = answers
        self.model = model
        self.pending: list[SessionRequest] = []

    @classmethod
    def from_file(cls, path: str | Path, *, model: str = DEFAULT_MODEL) -> SessionLLM:
        """Answers from a JSONL file; a file that does not exist yet means no answers."""
        path = Path(path)
        answers: dict[str, dict[str, Any]] = {}
        if path.exists():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    answer = SessionAnswer.model_validate_json(line)
                except ValidationError as exc:
                    raise ValueError(f"{path}:{number}: not an answer line: {exc}") from exc
                # A later line answers the same request again: the last one wins.
                answers[answer.key] = answer.response
        return cls(answers, model=model)

    def generate[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        key = request_key(prompt, schema, system)
        response = self.answers.get(key)
        if response is None:
            if all(r.key != key for r in self.pending):
                self.pending.append(
                    SessionRequest(
                        key=key,
                        schema_name=schema.__name__,
                        json_schema=schema.model_json_schema(),
                        system=system,
                        prompt=prompt,
                    )
                )
            raise AnswerMissingError(f"no session answer for request {key} yet")
        try:
            return schema.model_validate(response)
        except ValidationError as exc:
            raise LLMOutputError(
                f"the answer to request {key} does not match {schema.__name__}: {exc}"
            ) from exc


def write_requests(requests: list[SessionRequest], path: str | Path) -> None:
    lines = [r.model_dump_json() for r in requests]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_requests(path: str | Path) -> list[SessionRequest]:
    text = Path(path).read_text(encoding="utf-8")
    return [SessionRequest.model_validate_json(line) for line in text.splitlines() if line.strip()]


def answer_line(key: str, response: BaseModel | dict[str, Any]) -> str:
    """One line of an answers file."""
    if isinstance(response, BaseModel):
        response = response.model_dump(mode="json")
    return json.dumps({"key": key, "response": response}, ensure_ascii=False)
