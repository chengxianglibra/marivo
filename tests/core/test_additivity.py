"""Tests for app.core.semantic.additivity pure functions."""

from __future__ import annotations

from marivo.core.semantic.additivity import derive_additivity_capabilities

# ---------------------------------------------------------------------------
# derive_additivity_capabilities
# ---------------------------------------------------------------------------


def test_derive_additivity_missing_constraints() -> None:
    result = derive_additivity_capabilities(header={})
    assert result.supports_observe is True
    assert result.supports_compare is False
    assert result.supports_decompose is False
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_MISSING"
    assert result.dimension_policy == "none"
    assert result.time_axis_policy == "non_additive"


def test_derive_additivity_valid_all_additive() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "additive",
        },
        "primary_time_ref": "time.day",
        "sample_kind": "numeric",
    }
    result = derive_additivity_capabilities(header=header)
    assert result.supports_decompose is True
    assert result.time_rollup_allowed is True
    assert result.supports_compare is True
    assert result.supports_test is True
    assert result.blocker is None
    assert result.dimension_policy == "all"
    assert result.time_axis_policy == "additive"


def test_derive_additivity_valid_subset_additive() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "subset",
            "time_axis_policy": "additive",
            "additive_dimensions": ["region", "product"],
        },
        "primary_time_ref": "time.day",
        "sample_kind": "numeric",
    }
    result = derive_additivity_capabilities(header=header)
    assert result.supports_decompose is True
    assert result.supports_attribute is True
    assert result.capability_condition == "dimension_must_be_allowed"
    assert result.additive_dimensions == ["region", "product"]


def test_derive_additivity_subset_no_dimensions() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "subset",
            "time_axis_policy": "additive",
            "additive_dimensions": [],
        },
        "primary_time_ref": "time.day",
    }
    result = derive_additivity_capabilities(header=header)
    assert result.supports_decompose is False
    assert result.blocker == "ADDITIVITY_SUBSET_NO_DIMENSIONS"


def test_derive_additivity_none_dimension_policy() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": None,
        },
    }
    result = derive_additivity_capabilities(header=header)
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING"


def test_derive_additivity_invalid_dimension_policy() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "invalid",
        },
    }
    result = derive_additivity_capabilities(header=header)
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_INVALID"


def test_derive_additivity_missing_time_axis_policy() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
        },
    }
    result = derive_additivity_capabilities(header=header)
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING"


def test_derive_additivity_invalid_time_axis_policy() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "invalid",
        },
    }
    result = derive_additivity_capabilities(header=header)
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_INVALID"


def test_derive_additivity_non_additive_time_axis() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "non_additive",
        },
        "primary_time_ref": "time.day",
    }
    result = derive_additivity_capabilities(header=header)
    assert result.supports_decompose is True
    assert result.time_rollup_allowed is False


def test_derive_additivity_rate_sample_kind() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "additive",
        },
        "primary_time_ref": "time.day",
        "sample_kind": "rate",
    }
    result = derive_additivity_capabilities(header=header)
    assert result.supports_validate is True
    assert result.supports_test is True


def test_derive_additivity_process_anchor_time_ref() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "additive",
        },
        "sample_kind": "numeric",
    }
    result = derive_additivity_capabilities(header=header, process_anchor_time_ref="time.day")
    assert result.supports_detect is True


def test_derive_additivity_to_dict() -> None:
    header = {
        "additivity_constraints": {
            "dimension_policy": "all",
            "time_axis_policy": "additive",
        },
        "primary_time_ref": "time.day",
    }
    result = derive_additivity_capabilities(header=header)
    d = result.to_dict()
    assert d["supports_observe"] is True
    assert d["dimension_policy"] == "all"
    assert isinstance(d, dict)


def test_derive_additivity_constraints_not_dict() -> None:
    header = {"additivity_constraints": "invalid_string"}
    result = derive_additivity_capabilities(header=header)
    assert result.blocker == "ADDITIVITY_CONSTRAINTS_INVALID"
