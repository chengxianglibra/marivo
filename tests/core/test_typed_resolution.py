"""Tests for app.core.semantic.typed_resolution pure functions and data classes."""

from __future__ import annotations

import pytest

from marivo.core.semantic.typed_resolution import (
    NormalizedCompilerRequest,
    ResolvedCompilerInputs,
    ResolvedRelationship,
    filter_none_dict,
    mapping_dict,
    normalize_dimension_refs,
    normalize_metric_ref,
    optional_bool,
    optional_int,
    predicate_atoms,
    string_list,
)

# ── Data classes ───────────────────────────────────────────────────────


def test_normalized_compiler_request_defaults() -> None:
    req = NormalizedCompilerRequest(
        intent_kind="metric_query",
        request_class="root_metric_process",
        table_name="events",
    )
    assert req.metric_ref is None
    assert req.upstream_refs == []
    assert req.request_dimensions == []
    assert req.request_options == {}


def test_resolved_compiler_inputs_properties() -> None:
    req = NormalizedCompilerRequest(
        intent_kind="observe",
        request_class="root_metric_process",
        table_name=None,
    )
    inputs = ResolvedCompilerInputs(normalized_request=req)
    assert inputs.resolved_dimension_refs == []


def test_resolved_compiler_inputs_dimension_refs() -> None:
    req = NormalizedCompilerRequest(
        intent_kind="observe",
        request_class="root_metric_process",
        table_name=None,
    )
    inputs = ResolvedCompilerInputs(normalized_request=req)

    class _FakeDim:
        ref = "dimension.platform"

    inputs.resolved_dimensions = [_FakeDim()]
    assert inputs.resolved_dimension_refs == ["dimension.platform"]


def test_resolved_relationship() -> None:
    rel = ResolvedRelationship(
        relationship_ref="rel.user_event",
        left_entity_ref="entity.user",
        right_entity_ref="entity.event",
        cardinality="one_to_many",
    )
    assert rel.time_alignment is None
    assert rel.revision is None


# ── normalize_metric_ref ───────────────────────────────────────────────


def test_normalize_metric_ref_with_prefix() -> None:
    assert normalize_metric_ref("metric.revenue") == "metric.revenue"


def test_normalize_metric_ref_without_prefix() -> None:
    assert normalize_metric_ref("revenue") == "metric.revenue"


def test_normalize_metric_ref_whitespace() -> None:
    assert normalize_metric_ref("  revenue  ") == "metric.revenue"


# ── normalize_dimension_refs ───────────────────────────────────────────


def test_normalize_dimension_refs_basic() -> None:
    result = normalize_dimension_refs(["platform", "region"])
    assert result == ["platform", "region"]


def test_normalize_dimension_refs_dedup() -> None:
    result = normalize_dimension_refs(["platform", "platform"])
    assert result == ["platform"]


def test_normalize_dimension_refs_skip_empty() -> None:
    result = normalize_dimension_refs(["platform", "", "  "])
    assert result == ["platform"]


def test_normalize_dimension_refs_invalid_ref_raises() -> None:
    with pytest.raises(ValueError, match="Invalid dimension ref"):
        normalize_dimension_refs(["metric.revenue"])


# ── mapping_dict ───────────────────────────────────────────────────────


def test_mapping_dict_from_mapping() -> None:
    result = mapping_dict({"a": 1})
    assert result == {"a": 1}


def test_mapping_dict_none() -> None:
    assert mapping_dict(None) is None


def test_mapping_dict_non_mapping() -> None:
    assert mapping_dict("not a mapping") is None


# ── string_list ────────────────────────────────────────────────────────


def test_string_list_basic() -> None:
    assert string_list(["a", "b", "c"]) == ["a", "b", "c"]


def test_string_list_dedup() -> None:
    assert string_list(["a", "a", "b"]) == ["a", "b"]


def test_string_list_skip_empty() -> None:
    assert string_list(["a", "", None, "  "]) == ["a"]


def test_string_list_non_list() -> None:
    assert string_list("not a list") == []


# ── optional_int / optional_bool ───────────────────────────────────────


def test_optional_int_none() -> None:
    assert optional_int(None) is None


def test_optional_int_empty() -> None:
    assert optional_int("") is None


def test_optional_int_value() -> None:
    assert optional_int(42) == 42
    assert optional_int("10") == 10


def test_optional_bool_none() -> None:
    assert optional_bool(None) is None


def test_optional_bool_value() -> None:
    assert optional_bool(True) is True
    assert optional_bool(0) is False


# ── filter_none_dict ───────────────────────────────────────────────────


def test_filter_none_dict() -> None:
    result = filter_none_dict(a=1, b=None, c="hello", d=None)
    assert result == {"a": 1, "c": "hello"}


# ── predicate_atoms ────────────────────────────────────────────────────


def test_predicate_atoms_single() -> None:
    expr = {"target_ref": "status", "op": "=", "value": "active"}
    atoms = predicate_atoms(expr)
    assert len(atoms) == 1
    assert atoms[0]["target_ref"] == "status"


def test_predicate_atoms_nested() -> None:
    expr = {
        "items": [
            {"target_ref": "a", "op": "=", "value": "1"},
            {
                "items": [
                    {"target_ref": "b", "op": ">", "value": "0"},
                ]
            },
        ]
    }
    atoms = predicate_atoms(expr)
    assert len(atoms) == 2
    targets = [a["target_ref"] for a in atoms]
    assert "a" in targets
    assert "b" in targets


def test_predicate_atoms_empty() -> None:
    assert predicate_atoms({}) == []
