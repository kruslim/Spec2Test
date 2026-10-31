"""Token counts per model call, the price table, and what a run cost.

Every `LLMClient` appends one `TokenUsage` to `LLMClient.usage` per model call, including
calls that fail after the model answered (a refusal, a truncation, output that does not
match the schema): those tokens are billed too. A stage slices the calls it made off
that list and saves them with its output, so a saved run can be priced again later.

Prices are USD per million tokens, copied from each provider's own pricing page on the
date in `Price.checked`. A model with no entry is not priced: its cost is reported as
unknown, never guessed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
_GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"


class TokenUsage(BaseModel):
    """The tokens one model call used. Output tokens include thinking tokens."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="The model that served the call.")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@dataclass(frozen=True)
class Price:
    """USD per million tokens. A call whose input exceeds `long_above` tokens is billed at
    the `long_` rates for all its tokens."""

    input: float
    output: float
    source: str
    checked: str
    long_above: int | None = None
    long_input: float | None = None
    long_output: float | None = None

    def dollars(self, call: TokenUsage) -> float:
        long = self.long_above is not None and call.input_tokens > self.long_above
        rate_in = self.long_input if long else self.input
        rate_out = self.long_output if long else self.output
        return (call.input_tokens * rate_in + call.output_tokens * rate_out) / 1_000_000


# Standard (not batch or priority) rates, uncached input. kimi-for-coding is not listed:
# Kimi Code is billed by subscription plan, and no per-token price for that model ID is
# published on Moonshot's own pages.
PRICES = {
    "claude-opus-5-5": Price(4.0, 20.0, source=_ANTHROPIC, checked="2026-10-30"),
    # Server-side fallback can serve a declined request on another Claude model.
    "claude-opus-5": Price(5.0, 25.0, source=_ANTHROPIC, checked="2026-10-30"),
    "claude-opus-4-8": Price(5.0, 25.0, source=_ANTHROPIC, checked="2026-10-30"),
    "claude-sonnet-5-5": Price(2.0, 10.0, source=_ANTHROPIC, checked="2026-10-30"),
    "gemini-3.1-pro-preview": Price(
        2.0,
        12.0,
        source=_GEMINI,
        checked="2026-10-30",
        long_above=200_000,
        long_input=4.0,
        long_output=18.0,
    ),
}


def price_of(model: str) -> Price | None:
    """The price for `model`; 'models/gemini-3.1-pro-preview' finds the bare ID too."""
    return PRICES.get(model) or PRICES.get(model.split("/")[-1])


class Cost(BaseModel):
    """What a list of calls cost. `dollars` is None when any call's model has no price."""

    calls: int
    input_tokens: int
    output_tokens: int
    dollars: float | None
    unpriced: list[str] = Field(description="Models the price table has no entry for.")

    def per(self, pages: int) -> float | None:
        return self.dollars / pages if self.dollars is not None and pages else None


def cost(usage: Iterable[TokenUsage]) -> Cost:
    calls = list(usage)
    unpriced = sorted({c.model for c in calls if price_of(c.model) is None})
    dollars = None
    if not unpriced:
        dollars = sum(price_of(c.model).dollars(c) for c in calls)
    return Cost(
        calls=len(calls),
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        dollars=dollars,
        unpriced=unpriced,
    )


def cost_line(usage: Iterable[TokenUsage], pages: int) -> str:
    """'cost $0.42 ($0.0140 per page over 30 pages); 12 calls, 98,000 in / 7,100 out tokens'"""
    c = cost(usage)
    tokens = f"{c.calls} calls, {c.input_tokens:,} in / {c.output_tokens:,} out tokens"
    if not c.calls:
        return "cost: no model calls recorded"
    if c.dollars is None:
        return f"cost unknown (no price for {', '.join(c.unpriced)}); {tokens}"
    per = c.per(pages)
    per_page = "" if per is None else f" (${per:.4f} per page over {pages} pages)"
    return f"cost ${c.dollars:.2f}{per_page}; {tokens}"
