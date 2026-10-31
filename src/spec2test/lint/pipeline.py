"""Lint requirements for testability: the deterministic rules, then an optional LLM pass.

The rules run on every record (`lint_rules`). The LLM pass then gets the records of one
section at a time, with the rule flags, and returns only what the rules missed. A flag it
raises must quote words from its own requirement's text or source quote, and not words a
rule flag already quotes; one that fails, or names a requirement not in the batch, lands
in `Lint.rejected`, never on a record.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from spec2test.lint.models import FlagDraft, FlagDrafts, Lint, LintFailure, RejectedFlag
from spec2test.lint.prompts import SYSTEM, lint_prompt
from spec2test.lint.rules import lint_rules
from spec2test.llm import LLMClient, LLMError
from spec2test.schema import Requirement, TestabilityFlag

BATCH_SIZE = 20
"""Most requirements sent in one LLM call; a longer section is split."""


def lint(requirements: Iterable[Requirement], llm: LLMClient | None = None) -> Lint:
    """Flag what makes each requirement hard to verify.

    Without `llm` only the rules run. Any flags the records already carry are replaced.
    """
    ruled = [_with_flags(r, lint_rules(r)) for r in requirements]
    result = Lint(requirements=ruled, llm_pass=llm is not None)
    if llm is not None:
        start = len(llm.usage)
        for section, batch in _batches(ruled):
            _lint_batch(section, batch, llm, result)
        result.usage = llm.usage[start:]
    result.requirements = [_with_llm_flags(r, result.llm_flags) for r in ruled]
    return result


def relint(requirements: Iterable[Requirement], previous: Lint) -> Lint:
    """Run the rules again and keep the LLM flags `previous` saved: no model calls.

    For a saved eval run, after a rule changes. LLM flags of records no longer present
    are dropped.
    """
    ruled = [_with_flags(r, lint_rules(r)) for r in requirements]
    ids = {r.id for r in ruled}
    llm_flags = {k: v for k, v in previous.llm_flags.items() if k in ids}
    return Lint(
        requirements=[_with_llm_flags(r, llm_flags) for r in ruled],
        llm_pass=previous.llm_pass,
        llm_flags=llm_flags,
        rejected=previous.rejected,
        failed=previous.failed,
    )


def _lint_batch(section: str, batch: list[Requirement], llm: LLMClient, result: Lint) -> None:
    ids = [r.id for r in batch]
    try:
        answer = llm.generate(lint_prompt(section, batch), FlagDrafts, system=SYSTEM)
    except LLMError as exc:
        result.failed.append(LintFailure(section=section, requirement_ids=ids, error=str(exc)))
        return
    by_id = {r.id: r for r in batch}
    for draft in answer.flags:
        error = _check(draft, by_id)
        if error:
            result.rejected.append(RejectedFlag(draft=draft, error=error))
            continue
        flags = result.llm_flags.setdefault(draft.requirement_id, [])
        flag = TestabilityFlag(
            kind=draft.kind, reason=draft.reason, suggested_rewrite=draft.suggested_rewrite
        )
        if flag not in flags:
            flags.append(flag)


def _check(draft: FlagDraft, batch: dict[str, Requirement]) -> str | None:
    """Why `draft` cannot go on a record, or None when it can."""
    req = batch.get(draft.requirement_id)
    if req is None:
        return f"no requirement {draft.requirement_id!r} in this batch"
    phrase = _norm(draft.phrase)
    if phrase not in _norm(req.text) and phrase not in _norm(req.source_anchor.quote):
        return f"{draft.phrase!r} is not in the requirement's text or source quote"
    for flagged in _quoted(req.testability_flags):
        if _contains(phrase, flagged) or _contains(flagged, phrase):
            return f"repeats the rule flag on {flagged!r}"
    return None


def _quoted(flags: list[TestabilityFlag]) -> list[str]:
    """The phrases rule flags quote: their reasons start "'<phrase>' ..."."""
    return [_norm(m.group(1)) for f in flags if (m := re.match(r"'([^']+)'", f.reason))]


def _contains(text: str, words: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text) is not None


def _batches(requirements: list[Requirement]) -> list[tuple[str, list[Requirement]]]:
    """Records grouped by document and section, in first-seen order, `BATCH_SIZE` at most."""
    groups: dict[tuple[str, str], list[Requirement]] = {}
    for r in requirements:
        groups.setdefault((r.source_anchor.doc, r.source_anchor.section), []).append(r)
    return [
        (section, group[i : i + BATCH_SIZE])
        for (_, section), group in groups.items()
        for i in range(0, len(group), BATCH_SIZE)
    ]


def _with_flags(req: Requirement, flags: list[TestabilityFlag]) -> Requirement:
    return req.model_copy(update={"testability_flags": flags})


def _with_llm_flags(req: Requirement, llm_flags: dict[str, list[TestabilityFlag]]) -> Requirement:
    return _with_flags(req, [*req.testability_flags, *llm_flags.get(req.id, [])])


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())
