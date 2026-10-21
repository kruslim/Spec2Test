import anthropic
import pytest
from pydantic import ValidationError

from spec2test.schema import (
    ChecklistFields,
    Comparator,
    Criterion,
    EvidenceFields,
    Requirement,
    RequirementType,
    SourceAnchor,
    Strength,
    TestabilityFlagKind,
    TestCaseFields,
    VerificationItem,
    VerificationItemKind,
    VerificationMethod,
)

QUOTE = "The enclosure shall carry a high-voltage warning label."


def anchor(**overrides):
    data = {
        "doc": "example-spec",
        "page": 3,
        "section": "4.2",
        "start": 100,
        "end": 100 + len(QUOTE),
        "quote": QUOTE,
    }
    return {**data, **overrides}


def requirement(**overrides):
    data = {
        "id": "REQ-010",
        "source_anchor": anchor(),
        "text": QUOTE,
        "type": "safety",
        "strength": "shall",
    }
    return {**data, **overrides}


def test_minimal_requirement_gets_defaults():
    req = Requirement.model_validate(requirement())
    assert req.type is RequirementType.SAFETY
    assert req.strength is Strength.SHALL
    assert req.criteria == []
    assert req.conditions == []
    assert req.verification_method is None
    assert req.testability_flags == []


def test_full_requirement_round_trips_through_json():
    req = Requirement.model_validate(
        requirement(
            criteria=[
                {"parameter": "label height", "comparator": "ge", "value": "25", "unit": "mm"}
            ],
            conditions=["enclosure installed"],
            verification_method="inspection",
            testability_flags=[
                {"kind": "missing_unit", "reason": "no size", "suggested_rewrite": "at least 25 mm"}
            ],
        )
    )
    assert req.verification_method is VerificationMethod.INSPECTION
    assert req.testability_flags[0].kind is TestabilityFlagKind.MISSING_UNIT
    assert Requirement.model_validate_json(req.model_dump_json()) == req


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "aesthetic"},
        {"strength": "must"},
        {"verification_method": "guesswork"},
        {"surprise": "field"},
        {"source_anchor": anchor(page=0)},
        {"source_anchor": anchor(start=-1)},
        {"source_anchor": anchor(quote="")},
    ],
)
def test_invalid_requirement_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Requirement.model_validate(requirement(**overrides))


@pytest.mark.parametrize("end", [100, 99])
def test_anchor_span_must_be_ordered(end):
    with pytest.raises(ValidationError, match="must be greater than start"):
        SourceAnchor.model_validate(anchor(start=100, end=end))


def test_range_criterion():
    c = Criterion(parameter="field of view", comparator="between", value="10", upper_value="15")
    assert c.comparator is Comparator.BETWEEN


@pytest.mark.parametrize(
    "data",
    [
        {"comparator": "between", "value": "10"},
        {"comparator": "le", "value": "10", "upper_value": "15"},
        {"value": "10", "upper_value": "15"},
    ],
)
def test_upper_value_only_for_ranges(data):
    with pytest.raises(ValidationError, match="upper_value"):
        Criterion(parameter="p", **data)


CHECKLIST = {"inspect": "Enclosure label", "pass_condition": "Label present and legible"}
TEST_CASE = {
    "steps": ["Select reverse", "Time until image appears"],
    "expected_result": "Rear image displayed",
    "pass_criterion": "<= 2.0 s",
}
EVIDENCE = {
    "description": "Load analysis",
    "evidence": ["Stress calculation report"],
    "acceptance_criterion": "Safety factor >= 1.5",
}


@pytest.mark.parametrize(
    ("kind", "fields", "fields_type"),
    [
        ("checklist", CHECKLIST, ChecklistFields),
        ("test_case", TEST_CASE, TestCaseFields),
        ("evidence", EVIDENCE, EvidenceFields),
    ],
)
def test_verification_item_fields_follow_kind(kind, fields, fields_type):
    item = VerificationItem.model_validate(
        {"id": "VI-1", "requirement_ids": ["REQ-010"], "kind": kind, "fields": fields}
    )
    assert item.kind is VerificationItemKind(kind)
    assert isinstance(item.fields, fields_type)
    assert VerificationItem.model_validate_json(item.model_dump_json()) == item


def test_verification_item_fields_must_match_kind():
    with pytest.raises(ValidationError, match="needs ChecklistFields"):
        VerificationItem.model_validate(
            {"id": "VI-1", "requirement_ids": ["REQ-010"], "kind": "checklist", "fields": TEST_CASE}
        )


def test_verification_item_needs_a_requirement():
    with pytest.raises(ValidationError):
        VerificationItem.model_validate(
            {"id": "VI-1", "requirement_ids": [], "kind": "checklist", "fields": CHECKLIST}
        )


@pytest.mark.parametrize("model", [Requirement, VerificationItem])
def test_json_schema_forbids_extra_fields(model):
    assert model.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("model", [Requirement, VerificationItem])
def test_schema_survives_structured_output_transform(model):
    """Every field must still exist in the schema the Anthropic SDK sends to the model."""
    transformed = anthropic.transform_schema(model)
    assert set(transformed["properties"]) == set(model.model_fields)


def test_structured_output_schema_keeps_every_fields_variant():
    transformed = anthropic.transform_schema(VerificationItem)
    variants = transformed["properties"]["fields"]["anyOf"]
    defs = transformed["$defs"]
    names = {defs[v["$ref"].rsplit("/", 1)[-1]]["title"] for v in variants}
    assert names == {"ChecklistFields", "TestCaseFields", "EvidenceFields"}
    assert all(defs[name]["properties"] for name in names)
