"""Variant attribute Help preserves public receivers and field availability."""

from datetime import timedelta

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.windows import AllHistory, TrailingDuration
from marivo._help.render import render_help_text
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelLinearTerm,
    FrameLevelRatioMethod,
    FrameLevelSupport,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodSupport,
    FrameMethodTarget,
    FrameMethodValue,
    FrameRatioMethod,
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyMethod,
    FrameShapleyNativeQuantile,
    FrameShapleyQuantile,
    FrameShapleyQuery,
    FrameShapleyQueryGame,
    FrameShapleyReduction,
)


def test_preview_help_distinguishes_retained_rows_from_acquisition() -> None:
    for target in ("semantic.preview", "semantic.preview_many"):
        text, _, _ = render_help_text(target)
        assert "integer from 1 to 100 inclusive" in text
        assert "retained preview rows" in text
        assert "scope.max_rows" in text

    text, _, _ = render_help_text("semantic.preview_result.is_truncated")
    assert "Kind: attribute" in text
    assert "retained preview rows" in text
    assert "certification can succeed" not in text
    assert "Read coverage for acquisition completeness" in text
    text, _, _ = render_help_text("semantic.preview_result.coverage")
    assert "exhaustive/scope_exact" in text
    assert "is_truncated=True" in text
    text, _, _ = render_help_text("semantic.preview_result.requested_limit")
    assert "integer from 1 to 100 inclusive" in text
    assert "scope.max_rows" in text
    assert ".show(n=...)" in text


@pytest.mark.parametrize(
    ("target", "facts"),
    (
        ("entity_definition.default_time", ("str | None",)),
        ("metric_definition.time_bindings", ("MetricTimeFieldBinding", "MetricNoTimeBinding")),
        ("metric_time_field_binding.source", ("explicit", "entity_default")),
        ("metric_time_field_binding.anchor", ("InstantAnchor | None",)),
        ("metric_no_time_binding.reason", ("no_entity_default", "fixed_member_count")),
    ),
)
def test_contribution_time_definition_help_exposes_captured_fields(
    target: str, facts: tuple[str, ...]
) -> None:
    page, _, _ = render_help_text("semantic." + target)
    assert f"Target: semantic.{target}\nKind: attribute" in page
    assert f"Python: {target}" in page
    assert "Access: read-only" in page
    assert all(fact in page for fact in facts)


def test_window_attribute_identifies_its_public_constructor() -> None:
    window = mv.trailing_duration(duration=timedelta(days=1))
    assert isinstance(window, TrailingDuration)
    assert window.duration == timedelta(days=1)
    history = mv.all_history()
    assert isinstance(history, AllHistory)
    with pytest.raises(AttributeError):
        _ = history.duration  # type: ignore[attr-defined]  # Verify the absent variant attribute.

    text, _, _ = render_help_text("analysis.observation_window.duration")
    assert "Python: observation_window.duration" in text
    assert "Applies when: observation_window was returned by mv.trailing_duration(...)" in text
    assert "_Window" not in text
    assert "TrailingDuration" not in text


@pytest.mark.parametrize(
    "owner,target,members",
    (
        (
            mv.AnalysisFrame,
            "analysis_frame",
            (
                "session_id",
                "artifact_id",
                "output_names",
                "axes",
                "presence_names",
                "method_facts",
                "lineage",
            ),
        ),
        (mv.FrameAxis, "frame_axis", ("token", "name", "role", "key_names")),
        (mv.FrameLineage, "frame_lineage", ("values", "axes", "presence")),
        (mv.ColumnLineage, "column_lineage", ("value_origins", "context_origins")),
        (mv.SemanticObjectIdentity, "semantic_object_identity", ("kind", "path", "definition_id")),
    ),
)
def test_frame_metadata_help_is_static_without_getter_execution(
    monkeypatch: pytest.MonkeyPatch,
    owner: type[object],
    target: str,
    members: tuple[str, ...],
) -> None:
    def forbidden(_value: object) -> str:
        raise AssertionError("Help executed a Frame getter")

    for member in members:
        monkeypatch.setattr(owner, member, property(forbidden))
    for member in members:
        canonical = f"analysis.{target}.{member}"
        text, _, resolved = render_help_text(canonical)
        assert resolved == f"{target}.{member}"
        assert text.startswith(f"Target: {canonical}\nKind: attribute")
        assert "read-only" in text
    assert render_help_text(owner)[0] == render_help_text(f"analysis.{target}")[0]


def test_frame_axis_role_help_discloses_its_closed_public_values() -> None:
    text, _, _ = render_help_text("analysis.frame_axis.role")
    for role in ("field", "identity", "calendar", "paired"):
        assert role in text
    assert "Value type: AxisRole\n" not in text


@pytest.mark.parametrize(
    "owner,target",
    (
        (FrameMethodValue, "frame_method_value"),
        (FrameMethodCoordinate, "frame_method_coordinate"),
        (FrameMethodSupport, "frame_method_support"),
        (FrameAdditiveMethod, "frame_additive_method"),
        (FrameRatioMethod, "frame_ratio_method"),
        (FrameShapleyMethod, "frame_shapley_method"),
        (FrameShapleyNativeQuantile, "frame_shapley_native_quantile"),
        (FrameShapleyQuery, "frame_shapley_query"),
        (FrameShapleyQueryGame, "frame_shapley_query_game"),
        (FrameShapleyGame, "frame_shapley_game"),
        (FrameShapleyLeaf, "frame_shapley_leaf"),
        (FrameShapleyCapture, "frame_shapley_capture"),
        (FrameShapleyReduction, "frame_shapley_reduction"),
        (FrameShapleyQuantile, "frame_shapley_quantile"),
        (FrameLinearTerm, "frame_linear_term"),
        (FrameLinearMethod, "frame_linear_method"),
        (FrameDivisionMethod, "frame_division_method"),
        (FrameAttributeMetric, "frame_attribute_metric"),
        (FrameMethodTarget, "frame_method_target"),
        (FrameMethodOutput, "frame_method_output"),
        (FrameLevelSupport, "frame_level_support"),
        (FrameLevelAdditiveMethod, "frame_level_additive_method"),
        (FrameLevelRatioMethod, "frame_level_ratio_method"),
        (FrameLevelLinearTerm, "frame_level_linear_term"),
        (FrameLevelLinearMethod, "frame_level_linear_method"),
        (FrameLevelDivisionMethod, "frame_level_division_method"),
        (FrameMethodParent, "frame_method_parent"),
        (mv.FrameMethodFacts, "frame_method_facts"),
    ),
)
def test_attribute_record_fields_have_exact_static_public_help(
    owner: type[object],
    target: str,
) -> None:
    from dataclasses import fields, is_dataclass

    assert is_dataclass(owner)
    assert render_help_text(owner)[0] == render_help_text("analysis." + target)[0]
    for field in fields(owner):
        canonical = f"analysis.{target}.{field.name}"
        text, _, resolved = render_help_text(canonical)
        assert text.startswith(f"Target: {canonical}\nKind: attribute")
        assert resolved == f"{target}.{field.name}"
        assert "read-only" in text and str(field.type) in text
        assert "_MethodScalar" not in text and "_Method" not in text and "_LevelMethod" not in text


def test_method_facts_property_owns_full_read_and_exact_type_continuation() -> None:
    text, _, _ = render_help_text("analysis.analysis_frame.method_facts")
    assert "tuple[FrameMethodFacts, ...]" in text
    assert 'marivo.help("analysis.frame_method_facts")' in text
    assert "all owned method payloads" in text and "without reading current data" in text
    assert "compute/persist" in text and "not derived values" in text


@pytest.mark.parametrize(
    ("object_type", "target"),
    (
        (ms.Domain, "domain_object"),
        (ms.Datasource, "datasource_object"),
        (ms.Entity, "entity_object"),
        (ms.Dimension, "dimension_object"),
        (ms.TimeDimension, "time_dimension_object"),
        (ms.Measure, "measure_object"),
        (ms.Metric, "metric_object"),
        (ms.Relationship, "relationship_object"),
    ),
)
def test_named_object_type_targets_do_not_replace_constructors(
    object_type: type, target: str
) -> None:
    type_page, _, _ = render_help_text(object_type)
    string_page, _, _ = render_help_text(f"semantic.{target}")
    assert type_page == string_page
    assert type_page.startswith(f"Target: semantic.{target}\nKind: object\n")
    field_page, _, _ = render_help_text(f"semantic.{target}.kind")
    assert field_page.startswith(f"Target: semantic.{target}.kind\nKind: attribute\n")
    if object_type is not ms.Datasource:
        constructor = target.removesuffix("_object")
        constructor_page, _, _ = render_help_text(f"semantic.{constructor}.define")
        assert f"Target: semantic.{constructor}.define\nKind: callable\n" in constructor_page
        assert f"semantic.{constructor}.kind" not in constructor_page


def test_natural_time_fields_have_direct_help() -> None:
    for target in (
        "analysis.grain.unit",
        "analysis.grain.count",
        "analysis.time_scope.start",
        "analysis.time_scope.end",
        "analysis.time_scope.boundary_timezone",
    ):
        text, _, _ = render_help_text(target)
        assert "Kind: attribute" in text
        assert "Applies when:" not in text
