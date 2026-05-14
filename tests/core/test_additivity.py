"""Tests for marivo.core.semantic.additivity pure functions."""

from __future__ import annotations

from marivo.core.semantic.additivity import derive_additivity_capabilities

# ---------------------------------------------------------------------------
# Non-additive (empty additive_dimensions)
# ---------------------------------------------------------------------------


def test_non_additive_empty_dimensions() -> None:
    result = derive_additivity_capabilities(additive_dimensions=[])
    assert result.supports_observe is True
    assert result.supports_decompose is False
    assert result.blocker == "ADDITIVITY_NONE"
    assert result.remediation_hint is not None
    assert result.capability_condition is None
    assert result.additive_dimensions == []


def test_non_additive_compare_always_supported() -> None:
    result = derive_additivity_capabilities(additive_dimensions=[])
    assert result.supports_compare is True


# ---------------------------------------------------------------------------
# Additive with dimensions (subset mode)
# ---------------------------------------------------------------------------


def test_subset_additive_basic() -> None:
    result = derive_additivity_capabilities(
        additive_dimensions=["country", "date"],
    )
    assert result.supports_decompose is True
    assert result.capability_condition == "dimension_must_be_allowed"
    assert result.additive_dimensions == ["country", "date"]
    assert result.blocker is None
    assert result.remediation_hint is None


def test_subset_additive_no_time_field() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country", "product"])
    assert result.supports_decompose is True
    assert result.supports_compare is True


# ---------------------------------------------------------------------------
# supports_compare (always True since time_scope.field is required)
# ---------------------------------------------------------------------------


def test_supports_compare_always_true() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"])
    assert result.supports_compare is True


# ---------------------------------------------------------------------------
# supports_attribute
# ---------------------------------------------------------------------------


def test_supports_attribute_requires_decompose() -> None:
    # Non-additive: decompose=False, so attribute=False
    result = derive_additivity_capabilities(additive_dimensions=[])
    assert result.supports_attribute is False


def test_supports_attribute_enabled() -> None:
    result = derive_additivity_capabilities(
        additive_dimensions=["country", "date"],
    )
    assert result.supports_attribute is True


# ---------------------------------------------------------------------------
# supports_test
# ---------------------------------------------------------------------------


def test_supports_test_numeric() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="numeric")
    assert result.supports_test is True


def test_supports_test_rate() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="rate")
    assert result.supports_test is True


def test_supports_test_binary() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="binary")
    assert result.supports_test is True


def test_supports_test_unsupported_kind() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="ordinal")
    assert result.supports_test is False


def test_supports_test_no_sample_kind() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"])
    assert result.supports_test is True


# ---------------------------------------------------------------------------
# supports_detect (now depends only on process_anchor_time_ref)
# ---------------------------------------------------------------------------


def test_supports_detect_no_time_ref() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"])
    assert result.supports_detect is False


def test_supports_detect_with_process_anchor_time_ref() -> None:
    result = derive_additivity_capabilities(
        additive_dimensions=["country"], process_anchor_time_ref="event_time"
    )
    assert result.supports_detect is True


# ---------------------------------------------------------------------------
# supports_validate
# ---------------------------------------------------------------------------


def test_supports_validate_rate() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="rate")
    assert result.supports_validate is True


def test_supports_validate_non_rate() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="numeric")
    assert result.supports_validate is False


def test_supports_validate_no_sample_kind() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"])
    assert result.supports_validate is False


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


def test_to_dict() -> None:
    result = derive_additivity_capabilities(
        additive_dimensions=["country", "date"],
    )
    d = result.to_dict()
    assert isinstance(d, dict)
    assert d["supports_observe"] is True
    assert d["additive_dimensions"] == ["country", "date"]
    assert d["blocker"] is None
    assert d["capability_condition"] == "dimension_must_be_allowed"
    assert "dimension_policy" not in d
    assert "time_axis_policy" not in d
    assert "time_rollup_allowed" not in d


# ---------------------------------------------------------------------------
# supports_observe is always True
# ---------------------------------------------------------------------------


def test_supports_observe_always_true() -> None:
    result = derive_additivity_capabilities(additive_dimensions=[])
    assert result.supports_observe is True
    result2 = derive_additivity_capabilities(additive_dimensions=["x"])
    assert result2.supports_observe is True


# ---------------------------------------------------------------------------
# Remediation hint content
# ---------------------------------------------------------------------------


def test_remediation_hint_non_additive() -> None:
    result = derive_additivity_capabilities(additive_dimensions=[])
    assert result.remediation_hint is not None
    assert "additive_dimensions" in result.remediation_hint


def test_no_remediation_hint_when_additive() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"])
    assert result.remediation_hint is None


# ---------------------------------------------------------------------------
# Whitespace / empty-string normalization for optional params
# ---------------------------------------------------------------------------


def test_sample_kind_whitespace_treated_as_default() -> None:
    result = derive_additivity_capabilities(additive_dimensions=["country"], sample_kind="  ")
    assert result.supports_test is True
    assert result.supports_validate is False
