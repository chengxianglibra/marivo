"""Tests for app.core.semantic.typed_resolution pure functions and data classes."""

from __future__ import annotations

from typing import Any

import pytest

from marivo.core.semantic.typed_resolution import (
    EntityComposition,
    FieldResolutionIssue,
    NormalizedCompilerRequest,
    ResolvedCompilerInputs,
    ResolvedEntityField,
    ResolvedImportedDimensionBridge,
    ResolvedRelationship,
    build_entity_composition,
    collect_entity_field_refs_from_value,
    entity_field_snapshot,
    filter_none_dict,
    mapping_dict,
    metric_component_items,
    metric_entity_anchor_ref,
    normalize_dimension_refs,
    normalize_entity_field_ref,
    normalize_metric_ref,
    optional_bool,
    optional_int,
    predicate_atoms,
    split_entity_field_ref,
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
    assert inputs.resolved_imported_dimension_refs == []
    assert inputs.resolved_entity_field_refs == []


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


def test_resolved_entity_field() -> None:
    ef = ResolvedEntityField(
        field_ref="entity.user.field.age",
        entity_ref="entity.user",
        local_field_ref="field.age",
        entity_revision=1,
        value_type="integer",
    )
    assert ef.nullable is None
    assert ef.usage_paths == []


def test_resolved_imported_dimension_bridge() -> None:
    bridge = ResolvedImportedDimensionBridge(
        dimension_ref="dimension.platform",
        source_binding_ref="binding.1",
        source_entity_ref="entity.user",
        import_key="import_1",
    )
    assert bridge.dimension_ref == "dimension.platform"


def test_field_resolution_issue() -> None:
    issue = FieldResolutionIssue(
        code="missing_entity_binding",
        field_ref="entity.user.field.age",
        message="not found",
    )
    assert issue.usage_path is None
    assert issue.details == {}


def test_entity_composition() -> None:
    comp = EntityComposition(
        anchor_entity_ref="entity.user",
        component_entity_refs=["entity.user"],
        all_entity_refs=["entity.user"],
        is_cross_entity=False,
    )
    assert comp.anchor_entity_ref == "entity.user"
    assert comp.is_cross_entity is False


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


# ── metric_entity_anchor_ref ───────────────────────────────────────────


class _FakeMetric:
    def __init__(self, semantic_object: dict) -> None:
        self.semantic_object = semantic_object


def test_metric_entity_anchor_ref_observed() -> None:
    metric = _FakeMetric({"header": {"observed_entity_ref": "entity.user"}})
    assert metric_entity_anchor_ref(metric) == "entity.user"


def test_metric_entity_anchor_ref_population() -> None:
    metric = _FakeMetric({"header": {"population_subject_ref": "entity.session"}})
    assert metric_entity_anchor_ref(metric) == "entity.session"


def test_metric_entity_anchor_ref_observed_preferred() -> None:
    metric = _FakeMetric(
        {
            "header": {
                "observed_entity_ref": "entity.user",
                "population_subject_ref": "entity.session",
            }
        }
    )
    assert metric_entity_anchor_ref(metric) == "entity.user"


def test_metric_entity_anchor_ref_none() -> None:
    metric = _FakeMetric({"header": {}})
    assert metric_entity_anchor_ref(metric) is None


# ── metric_component_items ─────────────────────────────────────────────


def test_metric_component_items() -> None:
    payload = {
        "measure": {"input_field_ref": "entity.user.field.age", "aggregation": "mean"},
        "numerator": {"input_field_ref": "entity.event.field.revenue"},
        "denominator": None,  # Not a dict
    }
    items = metric_component_items(payload)
    names = [name for name, _ in items]
    assert "measure" in names
    assert "numerator" in names
    assert "denominator" not in names


def test_metric_component_items_empty() -> None:
    assert metric_component_items({}) == []


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


# ── collect_entity_field_refs_from_value ───────────────────────────────


def test_collect_entity_field_refs_from_value_dict() -> None:
    value = {
        "field1": "entity.user.field.age",
        "field2": "not_a_ref",
        "nested": {"field3": "field.name"},
    }
    refs = collect_entity_field_refs_from_value(value)
    assert "entity.user.field.age" in refs
    assert "field.name" in refs
    assert "not_a_ref" not in refs


def test_collect_entity_field_refs_from_value_list() -> None:
    value = ["entity.user.field.age", "plain_string", 42]
    refs = collect_entity_field_refs_from_value(value)
    assert "entity.user.field.age" in refs


# ── normalize_entity_field_ref ─────────────────────────────────────────


def test_normalize_entity_field_ref_entity_field() -> None:
    assert normalize_entity_field_ref("entity.user.field.age") == "entity.user.field.age"


def test_normalize_entity_field_ref_field_only() -> None:
    assert normalize_entity_field_ref("field.age") == "field.age"


def test_normalize_entity_field_ref_plain() -> None:
    assert normalize_entity_field_ref("age") is None


def test_normalize_entity_field_ref_none() -> None:
    assert normalize_entity_field_ref(None) is None


# ── split_entity_field_ref ─────────────────────────────────────────────


def test_split_entity_field_ref_entity_field() -> None:
    entity_ref, local = split_entity_field_ref("entity.user.field.age")
    assert entity_ref == "entity.user"
    assert local == "field.age"


def test_split_entity_field_ref_field_only() -> None:
    entity_ref, local = split_entity_field_ref("field.age")
    assert entity_ref is None
    assert local == "field.age"


def test_split_entity_field_ref_plain() -> None:
    entity_ref, local = split_entity_field_ref("age")
    assert entity_ref is None
    assert local == "age"


# ── build_entity_composition ───────────────────────────────────────────


class _FakeMetricForComp:
    semantic_object = {"header": {"observed_entity_ref": "entity.user"}}


class _FakeResolvedForComp:
    def __init__(self, metric: Any) -> None:
        self.resolved_metric = metric


def test_build_entity_composition_single_entity() -> None:
    resolved = _FakeResolvedForComp(_FakeMetricForComp())
    field_usages = {
        "entity.user.field.age": ["metric.measure.input_field_ref"],
    }
    comp = build_entity_composition(resolved, field_usages)
    assert comp.anchor_entity_ref == "entity.user"
    assert comp.is_cross_entity is False


def test_build_entity_composition_cross_entity() -> None:
    resolved = _FakeResolvedForComp(_FakeMetricForComp())
    field_usages = {
        "entity.user.field.age": ["metric.measure.input_field_ref"],
        "entity.event.field.revenue": ["metric.numerator.input_field_ref"],
    }
    comp = build_entity_composition(resolved, field_usages)
    assert comp.is_cross_entity is True
    assert "entity.user" in comp.all_entity_refs
    assert "entity.event" in comp.all_entity_refs


def test_build_entity_composition_no_metric() -> None:
    resolved = _FakeResolvedForComp(None)
    field_usages = {}
    comp = build_entity_composition(resolved, field_usages)
    assert comp.anchor_entity_ref is None
    assert comp.is_cross_entity is False


# ── entity_field_snapshot ──────────────────────────────────────────────


class _FakeEntity:
    ref = "entity.user"
    revision = 1
    semantic_object = {
        "interface_contract": {
            "fields": [
                {
                    "field_ref": "field.age",
                    "value_type": "integer",
                    "nullable": True,
                },
            ],
            "binding": {
                "source_object_ref": "dataset.users",
                "source_object_fqn": "public.users",
                "carrier_kind": "column",
            },
        }
    }


def test_entity_field_snapshot_found() -> None:
    result = entity_field_snapshot(
        "entity.user.field.age",
        local_field_ref="field.age",
        entity=_FakeEntity(),
        usage_paths=["metric.measure.input_field_ref"],
    )
    assert result is not None
    assert result.field_ref == "entity.user.field.age"
    assert result.value_type == "integer"
    assert result.nullable is True
    assert result.source_object_ref == "dataset.users"


def test_entity_field_snapshot_not_found() -> None:
    result = entity_field_snapshot(
        "entity.user.field.missing",
        local_field_ref="field.missing",
        entity=_FakeEntity(),
        usage_paths=[],
    )
    assert result is None
