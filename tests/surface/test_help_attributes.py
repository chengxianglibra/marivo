"""Variant attribute identities stay attached to their public receivers."""

from datetime import timedelta

import pytest

import marivo.analysis as mv
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


def test_window_attribute_identifies_its_public_constructor() -> None:
    window = mv.trailing_duration(duration=timedelta(days=1))
    assert isinstance(window, TrailingDuration)
    assert window.duration == timedelta(days=1)
    history = mv.all_history()
    assert isinstance(history, AllHistory)
    with pytest.raises(AttributeError):
        _ = history.duration  # type: ignore[attr-defined]  # Verify the absent variant attribute.


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
    owner: type[object],
    target: str,
    members: tuple[str, ...],
) -> None:
    assert isinstance(owner, type)
    for member in members:
        canonical = f"analysis.{target}.{member}"
        _, _, resolved = render_help_text(canonical)
        assert resolved == f"{target}.{member}"


def test_frame_axis_role_help_discloses_its_closed_public_values() -> None:
    text, _, _ = render_help_text("analysis.frame_axis.role")
    for role in ("field", "identity", "calendar", "paired"):
        assert role in text


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
    for field in fields(owner):
        canonical = f"analysis.{target}.{field.name}"
        _, _, resolved = render_help_text(canonical)
        assert resolved == f"{target}.{field.name}"
