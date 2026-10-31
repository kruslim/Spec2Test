"""Render review stats as plain text for the terminal."""

from __future__ import annotations

from spec2test.review.models import ReviewStats, Tally


def stats_report(stats: ReviewStats) -> str:
    """The acceptance rate overall, then per item kind and per verification method."""
    n = len(stats.generations)
    lines = [f"{n} generation{'s' if n != 1 else ''} reviewed", ""]
    lines.append(f"{'':16}{'items':>7}{'reviewed':>10}{'accepted':>10}{'edited':>8}"
                 f"{'rejected':>10}{'rate':>8}")  # fmt: skip
    lines.append(_row("overall", stats.overall))
    if stats.by_kind:
        lines += ["", "by item kind:"]
        lines += [_row(k, t) for k, t in stats.by_kind.items()]
    if stats.by_method:
        lines += ["", "by verification method:"]
        lines += [_row(m, t) for m, t in stats.by_method.items()]
    lines += [
        "",
        "rate: share of reviewed items accepted unedited",
    ]
    return "\n".join(lines)


def _row(name: str, t: Tally) -> str:
    rate = "-" if t.acceptance_rate is None else f"{t.acceptance_rate:.1%}"
    return (
        f"{name:16}{t.items:>7}{t.reviewed:>10}{t.accepted:>10}{t.edited:>8}"
        f"{t.rejected:>10}{rate:>8}"
    )
