"""An item's fields as the edit form shows them, and back: a text field is one box, a list
field one box with an entry per line."""

from __future__ import annotations

from typing import Any, get_origin

from spec2test.review.models import FIELDS
from spec2test.schema import VerificationItemKind


def form_fields(kind: VerificationItemKind | str) -> list[tuple[str, bool]]:
    """The fields of `kind` in their order, each with whether it is a list."""
    model = FIELDS[VerificationItemKind(kind)]
    return [(name, get_origin(f.annotation) is list) for name, f in model.model_fields.items()]


def to_form(kind: VerificationItemKind | str, fields: dict[str, Any]) -> dict[str, str]:
    return {
        name: "\n".join(fields.get(name, [])) if is_list else fields.get(name, "")
        for name, is_list in form_fields(kind)
    }


def from_form(kind: VerificationItemKind | str, values: dict[str, str]) -> dict[str, Any]:
    """The fields the form holds; blank lines in a list box are dropped. Not validated:
    `ReviewStore.record` checks them against the kind."""
    out: dict[str, Any] = {}
    for name, is_list in form_fields(kind):
        value = values.get(name, "")
        if is_list:
            out[name] = [line.strip() for line in value.splitlines() if line.strip()]
        else:
            out[name] = value.strip()
    return out
