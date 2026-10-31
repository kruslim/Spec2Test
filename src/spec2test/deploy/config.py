"""The deployment's settings: where partner data lives, which model the hosted app calls,
the monthly cost cap, and who may sign in to which workspace.

One TOML file holds them (`SPEC2TEST_DEPLOY_CONFIG`). It holds no secrets: API keys and the
sign-in provider's client secret come from the environment (see `spec2test.deploy.serve`).
`load_config` checks the file as it loads, so a cap that cannot be enforced, an unknown
provider or a member listed in two workspaces fails at start instead of during a run.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from spec2test.llm import PRESETS, AnthropicLLM, LLMClient, OpenAICompatLLM, price_of
from spec2test.llm.anthropic_client import DEFAULT_MODEL as ANTHROPIC_DEFAULT

CONFIG_ENV = "SPEC2TEST_DEPLOY_CONFIG"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class DeployConfigError(ValueError):
    """The deployment config is missing, malformed or cannot be enforced."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelChoice(_Record):
    """The one model every hosted run calls; partners cannot pick another."""

    provider: str = Field(description="A preset (kimi-code, gemini) or 'anthropic'.")
    model: str | None = Field(default=None, description="Default: the provider's.")

    @model_validator(mode="after")
    def _known(self) -> Self:
        if self.provider not in {*PRESETS, "anthropic"}:
            known = ", ".join([*PRESETS, "anthropic"])
            raise ValueError(f"unknown provider {self.provider!r} (known: {known})")
        return self

    @property
    def model_id(self) -> str:
        if self.model:
            return self.model
        if self.provider == "anthropic":
            return ANTHROPIC_DEFAULT
        return PRESETS[self.provider].model

    def client(self) -> LLMClient:
        if self.provider == "anthropic":
            return AnthropicLLM(self.model) if self.model else AnthropicLLM()
        return OpenAICompatLLM.preset(
            self.provider, **({"model": self.model} if self.model else {})
        )


class Cap(_Record):
    """Monthly limits over every workspace together, per UTC calendar month. A run stops at
    the first model call made after either limit is reached, so a month can end up over the
    cap by at most one call."""

    monthly_usd: float | None = Field(default=None, gt=0, description="Priced models only.")
    monthly_tokens: int | None = Field(
        default=None, gt=0, description="Input plus output tokens, every model."
    )

    @model_validator(mode="after")
    def _some(self) -> Self:
        if self.monthly_usd is None and self.monthly_tokens is None:
            raise ValueError("set monthly_usd, monthly_tokens or both")
        return self


class WorkspaceConfig(_Record):
    name: str = Field(min_length=1, description="Shown to the workspace's members.")
    members: list[str] = Field(min_length=1, description="Sign-in emails, any case.")


_PROVIDERS = {
    "kimi-code": "Moonshot AI (Kimi)",
    "gemini": "Google (Gemini)",
    "anthropic": "Anthropic",
}


class DataPage(_Record):
    """The facts the data-handling page states. One left out shows as [a bracket], so the
    page never claims what the operator has not written down."""

    host: str | None = Field(default=None, description="The host and region, e.g. 'Fly.io, ...'.")
    contact: str | None = Field(default=None, description="Who partners write to.")
    provider_terms: str | None = Field(
        default=None, description="Link to the model provider's data terms."
    )
    backup_days: int | None = Field(default=None, ge=0)


class DeployConfig(_Record):
    data_dir: Path = Field(description="Holds every workspace's data and the usage ledger.")
    llm: ModelChoice
    cap: Cap
    workspaces: dict[str, WorkspaceConfig] = Field(default_factory=dict)
    data_page: DataPage = Field(default_factory=DataPage)

    @model_validator(mode="after")
    def _check(self) -> Self:
        model = self.llm.model_id
        if self.cap.monthly_tokens is None and price_of(model) is None:
            # Never guess a price: an unpriced model can only be held to a token cap.
            raise ValueError(
                f"{model} has no price in spec2test.llm.usage, so a dollar cap cannot "
                "limit it; set cap.monthly_tokens"
            )
        seen: dict[str, str] = {}
        for wid, ws in self.workspaces.items():
            if not _SLUG.fullmatch(wid):
                raise ValueError(f"workspace id {wid!r}: use lowercase letters, digits and dashes")
            for email in ws.members:
                key = email.strip().casefold()
                if "@" not in key:
                    raise ValueError(f"workspace {wid}: {email!r} is not an email")
                if key in seen and seen[key] != wid:
                    raise ValueError(f"{email} is a member of both {seen[key]} and {wid}")
                seen[key] = wid
        return self

    def workspace_for(self, email: str | None) -> str | None:
        """The workspace `email` belongs to, or None if it is not invited."""
        if not email:
            return None
        key = email.strip().casefold()
        return next(
            (
                wid
                for wid, ws in self.workspaces.items()
                if key in {m.strip().casefold() for m in ws.members}
            ),
            None,
        )


def data_handling(config: DeployConfig) -> str:
    """The data-handling page, filled in from the config."""
    page = config.data_page
    text = (Path(__file__).with_name("data-handling.md")).read_text(encoding="utf-8")
    return text.format(
        host=page.host or "[host and region]",
        provider=_PROVIDERS.get(config.llm.provider, config.llm.provider),
        model=config.llm.model_id,
        provider_terms=page.provider_terms or "[link to the provider's data terms]",
        backup_days="[N]" if page.backup_days is None else page.backup_days,
        contact=page.contact or "[contact]",
    )


def load_config(path: str | Path) -> DeployConfig:
    """Read and check a deployment config; a relative `data_dir` is relative to the file."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployConfigError(f"no deployment config at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DeployConfigError(f"{path}: {exc}") from exc
    try:
        config = DeployConfig.model_validate(raw)
    except ValidationError as exc:
        raise DeployConfigError(f"{path}: {exc}") from exc
    if not config.data_dir.is_absolute():
        config.data_dir = (path.parent / config.data_dir).resolve()
    return config
