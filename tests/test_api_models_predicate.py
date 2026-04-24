"""Tests for predicate semantic object models."""

import pytest
from pydantic import ValidationError

from app.api.models.predicate import (
    PredicateAtom,
    PredicateConjunction,
    PredicateHeader,
    PredicatePayload,
)

# =============================================================================
# PredicateHeader
# =============================================================================


def test_predicate_header_valid_with_entity_subject():
    header = PredicateHeader(
        predicate_ref="predicate.exclude_test_data",
        display_name="Exclude Test Data",
        description="Filters out test data from analysis",
        subject_ref="entity.user",
        predicate_contract_version="predicate.v1",
    )
    assert header.predicate_ref == "predicate.exclude_test_data"
    assert header.subject_ref == "entity.user"
    assert header.display_name == "Exclude Test Data"


def test_predicate_header_valid_with_subject_prefix():
    header = PredicateHeader(
        predicate_ref="predicate.tenant_guardrail",
        subject_ref="subject.order",
        predicate_contract_version="predicate.v1",
    )
    assert header.subject_ref == "subject.order"


def test_predicate_header_minimal():
    header = PredicateHeader(
        predicate_ref="predicate.is_active",
        subject_ref="entity.user",
        predicate_contract_version="predicate.v1",
    )
    assert header.display_name is None
    assert header.description is None


def test_predicate_header_invalid_ref_prefix():
    with pytest.raises(ValidationError, match=r"must start with 'predicate\.'"):
        PredicateHeader(
            predicate_ref="wrong.ref",
            subject_ref="entity.user",
            predicate_contract_version="predicate.v1",
        )


def test_predicate_header_invalid_subject_prefix():
    with pytest.raises(
        ValidationError, match=r"subject_ref must start with 'entity\.' or 'subject\.'"
    ):
        PredicateHeader(
            predicate_ref="predicate.test",
            subject_ref="dimension.country",
            predicate_contract_version="predicate.v1",
        )


def test_predicate_header_invalid_contract_version():
    with pytest.raises(ValidationError, match=r"contract_version must start with 'predicate\.'"):
        PredicateHeader(
            predicate_ref="predicate.test",
            subject_ref="entity.user",
            predicate_contract_version="v1",
        )


# =============================================================================
# PredicateAtom — valid operators
# =============================================================================


def test_predicate_atom_eq():
    atom = PredicateAtom(target_ref="dimension.country", op="eq", value="CN")
    assert atom.op == "eq"
    assert atom.value == "CN"


def test_predicate_atom_neq():
    atom = PredicateAtom(target_ref="dimension.status", op="neq", value="inactive")
    assert atom.op == "neq"


def test_predicate_atom_gt():
    atom = PredicateAtom(target_ref="entity.age", op="gt", value=18)
    assert atom.value == 18


def test_predicate_atom_gte():
    atom = PredicateAtom(target_ref="entity.score", op="gte", value=0)
    assert atom.value == 0


def test_predicate_atom_lt():
    atom = PredicateAtom(target_ref="entity.score", op="lt", value=100)


def test_predicate_atom_lte():
    atom = PredicateAtom(target_ref="entity.score", op="lte", value=100)


def test_predicate_atom_between():
    atom = PredicateAtom(target_ref="entity.age", op="between", value=[18, 65])
    assert len(atom.value) == 2


def test_predicate_atom_in():
    atom = PredicateAtom(target_ref="dimension.country", op="in", value=["CN", "US", "JP"])
    assert len(atom.value) == 3


def test_predicate_atom_not_in():
    atom = PredicateAtom(target_ref="dimension.status", op="not_in", value=["deleted", "archived"])


def test_predicate_atom_is_null():
    atom = PredicateAtom(target_ref="field.description", op="is_null")
    assert atom.value is None


def test_predicate_atom_is_not_null():
    atom = PredicateAtom(target_ref="field.email", op="is_not_null")
    assert atom.value is None


def test_predicate_atom_bool_value():
    atom = PredicateAtom(target_ref="dimension.is_active", op="eq", value=True)
    assert atom.value is True


def test_predicate_atom_numeric_value():
    atom = PredicateAtom(target_ref="entity.amount", op="gt", value=99.5)
    assert atom.value == 99.5


def test_predicate_atom_none_in_list_value():
    atom = PredicateAtom(target_ref="dimension.status", op="in", value=["active", None])
    assert atom.value == ["active", None]


# =============================================================================
# PredicateAtom — target_ref prefix validation
# =============================================================================


@pytest.mark.parametrize(
    "prefix",
    [
        "dimension.",
        "entity.",
        "key.",
        "enum.",
        "subject.",
        "population.",
        "event.",
        "field.",
    ],
)
def test_predicate_atom_allowed_target_prefixes(prefix):
    atom = PredicateAtom(target_ref=f"{prefix}test", op="eq", value="x")
    assert atom.target_ref.startswith(prefix)


def test_predicate_atom_time_target_forbidden():
    with pytest.raises(ValidationError, match="time filtering belongs in time_scope"):
        PredicateAtom(target_ref="time.created_at", op="eq", value="2024-01-01")


def test_predicate_atom_metric_target_forbidden():
    with pytest.raises(ValidationError, match=r"must not use 'metric\.'"):
        PredicateAtom(target_ref="metric.dau", op="eq", value=100)


def test_predicate_atom_predicate_target_forbidden():
    with pytest.raises(ValidationError, match=r"must not use 'predicate\.'"):
        PredicateAtom(target_ref="predicate.active", op="eq", value=True)


def test_predicate_atom_unknown_target_prefix():
    with pytest.raises(ValidationError, match="governed semantic ref prefix"):
        PredicateAtom(target_ref="unknown.field", op="eq", value="x")


# =============================================================================
# PredicateAtom — operator value constraints
# =============================================================================


def test_predicate_atom_between_requires_two():
    with pytest.raises(ValidationError, match="exactly 2 elements"):
        PredicateAtom(target_ref="entity.age", op="between", value=[1])


def test_predicate_atom_between_rejects_three():
    with pytest.raises(ValidationError, match="exactly 2 elements"):
        PredicateAtom(target_ref="entity.age", op="between", value=[1, 2, 3])


def test_predicate_atom_between_requires_list():
    with pytest.raises(ValidationError, match="must be a list"):
        PredicateAtom(target_ref="entity.age", op="between", value=5)


def test_predicate_atom_in_requires_non_empty():
    with pytest.raises(ValidationError, match="non-empty list"):
        PredicateAtom(target_ref="dimension.country", op="in", value=[])


def test_predicate_atom_in_requires_list():
    with pytest.raises(ValidationError, match="must be a list"):
        PredicateAtom(target_ref="dimension.country", op="in", value="CN")


def test_predicate_atom_not_in_requires_non_empty():
    with pytest.raises(ValidationError, match="non-empty list"):
        PredicateAtom(target_ref="dimension.status", op="not_in", value=[])


def test_predicate_atom_is_null_rejects_value():
    with pytest.raises(ValidationError, match="must be None"):
        PredicateAtom(target_ref="field.desc", op="is_null", value="something")


def test_predicate_atom_is_not_null_rejects_value():
    with pytest.raises(ValidationError, match="must be None"):
        PredicateAtom(target_ref="field.desc", op="is_not_null", value="x")


def test_predicate_atom_scalar_op_requires_value():
    with pytest.raises(ValidationError, match="value is required"):
        PredicateAtom(target_ref="dimension.country", op="eq", value=None)


def test_predicate_atom_scalar_op_rejects_list():
    with pytest.raises(ValidationError, match="must be a scalar"):
        PredicateAtom(target_ref="dimension.country", op="eq", value=["a", "b"])


# =============================================================================
# PredicateConjunction
# =============================================================================


def test_predicate_conjunction_valid():
    conj = PredicateConjunction(
        op="and",
        items=[
            PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
            PredicateAtom(target_ref="dimension.platform", op="in", value=["ios", "android"]),
        ],
    )
    assert conj.op == "and"
    assert len(conj.items) == 2


def test_predicate_conjunction_nested():
    conj = PredicateConjunction(
        op="and",
        items=[
            PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
            PredicateConjunction(
                op="and",
                items=[
                    PredicateAtom(target_ref="dimension.platform", op="eq", value="ios"),
                    PredicateAtom(target_ref="entity.age", op="gte", value=18),
                ],
            ),
        ],
    )
    assert len(conj.items) == 2


def test_predicate_conjunction_empty_items_rejected():
    with pytest.raises(ValidationError):
        PredicateConjunction(op="and", items=[])


def test_predicate_conjunction_single_item():
    conj = PredicateConjunction(
        op="and",
        items=[PredicateAtom(target_ref="dimension.country", op="eq", value="CN")],
    )
    assert len(conj.items) == 1


# =============================================================================
# PredicatePayload
# =============================================================================


def test_predicate_payload_with_atom():
    payload = PredicatePayload(
        expression=PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
        allowed_usage=["metric_qualifier"],
    )
    assert payload.time_policy == "non_time_only"


def test_predicate_payload_with_conjunction():
    payload = PredicatePayload(
        expression=PredicateConjunction(
            op="and",
            items=[
                PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
                PredicateAtom(target_ref="dimension.platform", op="in", value=["ios", "android"]),
            ],
        ),
        allowed_usage=["request_scope"],
    )


def test_predicate_payload_multiple_usage():
    payload = PredicatePayload(
        expression=PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
        allowed_usage=["metric_qualifier", "request_scope"],
    )
    assert len(payload.allowed_usage) == 2


def test_predicate_payload_empty_usage_rejected():
    with pytest.raises(ValidationError):
        PredicatePayload(
            expression=PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
            allowed_usage=[],
        )


def test_predicate_payload_default_time_policy():
    payload = PredicatePayload(
        expression=PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
        allowed_usage=["metric_qualifier"],
    )
    assert payload.time_policy == "non_time_only"


@pytest.mark.parametrize(
    "usage",
    [
        "metric_qualifier",
        "carrier_row_filter",
        "request_scope",
        "governance_policy",
    ],
)
def test_predicate_payload_all_usage_values(usage):
    payload = PredicatePayload(
        expression=PredicateAtom(target_ref="dimension.country", op="eq", value="CN"),
        allowed_usage=[usage],
    )
    assert usage in payload.allowed_usage


# =============================================================================
# PredicateAtom — value domain edge cases (task 7.1)
# =============================================================================


def test_between_with_reversed_bounds_passes_pydantic():
    """Reversed bounds (lo > hi) pass Pydantic; semantic check is validator-level."""
    atom = PredicateAtom(target_ref="entity.age", op="between", value=[65, 18])
    assert atom.value == [65, 18]


def test_between_with_mixed_string_number_passes_pydantic():
    """Mixed-type bounds pass Pydantic; type mismatch caught at narrowing time."""
    atom = PredicateAtom(target_ref="entity.age", op="between", value=[18, "old"])
    assert atom.value == [18, "old"]


def test_between_with_none_element_passes_pydantic():
    """None in bounds passes Pydantic; _compare_values returns None at validator."""
    atom = PredicateAtom(target_ref="entity.age", op="between", value=[18, None])
    assert atom.value == [18, None]


def test_in_with_mixed_types_passes_pydantic():
    """Mixed-type list passes Pydantic; runtime handles type-incompatible comparisons."""
    atom = PredicateAtom(target_ref="dimension.country", op="in", value=["CN", 42, True])
    assert len(atom.value) == 3


def test_not_in_with_mixed_types_passes_pydantic():
    """Mixed-type list in not_in passes Pydantic; runtime handles type checks."""
    atom = PredicateAtom(target_ref="dimension.status", op="not_in", value=["active", 0, None])
    assert len(atom.value) == 3


def test_between_with_string_bounds_passes_pydantic():
    """String range bounds are valid in Python; constructs successfully."""
    atom = PredicateAtom(target_ref="dimension.name", op="between", value=["A", "Z"])
    assert atom.value == ["A", "Z"]
