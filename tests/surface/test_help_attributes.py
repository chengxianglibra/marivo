"""Variant attribute Help preserves public receivers and field availability."""

from datetime import timedelta

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo._temporal import TimeScope
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
    FrameShapleyQuantile,
    FrameShapleyReduction,
)
from tests.support.refs import as_ref


def test_preview_help_distinguishes_retained_rows_from_acquisition() -> None:
    for target in ("semantic.preview", "semantic.preview_many"):
        text, _, _ = render_help_text(target)
        assert "integer from 1 to 100 inclusive" in text
        assert "retained preview rows" in text
        assert "scope.max_rows" in text

    text, _, _ = render_help_text("semantic.preview_result.is_truncated")
    assert "Kind: attribute" in text
    assert "retained preview rows" in text
    assert "exhaustive temporal certification can succeed" in text
    assert "Read coverage for acquisition completeness" in text
    text, _, _ = render_help_text("semantic.preview_result.coverage")
    assert "exhaustive/scope_exact" in text
    assert "is_truncated=True" in text
    text, _, _ = render_help_text("semantic.preview_result.requested_limit")
    assert "integer from 1 to 100 inclusive" in text
    assert "scope.max_rows" in text
    assert ".show(n=...)" in text


def test_grain_field_help_matches_runtime_variant_availability() -> None:
    builtin = mv.grain("day")
    calendar = ms.ref.period_calendar("sales.fiscal")
    semantic = ms.calendar_grain(calendar=calendar, level="week")

    assert builtin.kind == "builtin"
    assert semantic.kind == "semantic"
    assert semantic._calendar == as_ref(calendar)
    with pytest.raises(AttributeError):
        _ = builtin.calendar

    text, _, _ = render_help_text("analysis.grain.calendar")
    assert "Target: analysis.grain.calendar\nKind: attribute" in text
    assert "Python: grain.calendar" in text
    assert "Applies when: grain.kind == 'semantic'" in text
    assert "_SemanticGrain" not in text
    type_text, _, _ = render_help_text("analysis.grain")
    assert "grain.kind == 'semantic'" in type_text
    assert "grain.kind == 'builtin'" in type_text

    builtin_text, _, _ = render_help_text("analysis.grain.unit")
    assert "Applies when: grain.kind == 'builtin'" in builtin_text
    common_text, _, _ = render_help_text("analysis.grain.kind")
    assert "Applies to all grain values." in common_text
    assert "Applies when:" not in common_text


def test_time_scope_field_help_does_not_evaluate_variant_getter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_read(_scope: TimeScope) -> str:
        raise AssertionError("Help must not read a kind getter to discover applicability")

    monkeypatch.setattr(TimeScope, "kind", property(fail_if_read))
    text, _, _ = render_help_text("analysis.time_scope.calendar")
    assert "Python: time_scope.calendar" in text
    assert "Applies when: time_scope.kind == 'calendar_period'" in text
    assert "_CalendarPeriodTimeScope" not in text

    text, _, _ = render_help_text("analysis.time_scope.temporal_set")
    assert "Applies when: time_scope.kind == 'temporal_occurrence'" in text
    assert "_TemporalOccurrenceTimeScope" not in text


def test_shared_variant_field_lists_each_applicable_public_condition() -> None:
    text, _, _ = render_help_text("analysis.time_scope.boundary_timezone")
    assert "Python: time_scope.boundary_timezone" in text
    assert "time_scope.kind == 'absolute' constructed by session.time_scope(...)" in text
    assert "time_scope.kind == 'calendar_period'" in text
    assert "time_scope.kind == 'temporal_occurrence'" in text
    assert "without a frozen session timezone" not in text
    assert "Applies to all" not in text
    assert "_SessionTimeScope" not in text


def test_window_attribute_identifies_its_public_constructor() -> None:
    window = mv.trailing_duration(duration=timedelta(days=1))
    assert window.duration == timedelta(days=1)
    with pytest.raises(AttributeError):
        _ = mv.all_history().duration

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
    from dataclasses import fields

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
