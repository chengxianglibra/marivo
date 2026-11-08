"""Public Help ids for interpreting captured Frame facts stay reachable."""

import pytest

import marivo
import marivo.analysis as mv


def test_observation_and_group_variants_are_discoverable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for owner, variants in (
        (
            "frame_observation_context",
            (
                "frame_axis_time",
                "frame_compared_time",
                "frame_fixed_time",
                "frame_all_history_window",
                "frame_since_window",
                "frame_to_date_window",
                "frame_trailing_duration_window",
                "frame_trailing_periods_window",
            ),
        ),
        ("frame_statistic_context", ("frame_axis_group", "frame_fixed_group")),
        (
            "frame_share_context",
            (
                "frame_named_value_input",
                "frame_derived_value_input",
                "frame_axis_group",
                "frame_fixed_group",
            ),
        ),
        (
            "frame_pair_statistic_context",
            (
                "frame_named_value_input",
                "frame_derived_value_input",
                "frame_axis_group",
                "frame_fixed_group",
            ),
        ),
        ("frame_alignment", ("frame_alignment_side",)),
        ("frame_alignment_side", ("frame_alignment_axis", "frame_fixed_group")),
        ("frame_decomposition", ("frame_axis_group", "frame_fixed_group")),
        ("frame_to_date_window", ("frame_builtin_grain",)),
        ("frame_calendar_axis_schema", ("frame_builtin_grain",)),
        ("frame_calendar_endpoint", ("frame_builtin_grain",)),
    ):
        marivo.help("analysis." + owner)
        page = capsys.readouterr().out
        for target in variants:
            assert "analysis." + target in page

    for target, owner in (
        ("frame_observation_context.time", "frame_observation_context"),
        ("frame_observation_context.window", "frame_observation_context"),
        ("frame_statistic_context.groups", "frame_statistic_context"),
        ("frame_decomposition.groups", "frame_decomposition"),
        ("frame_to_date_window.grain", "frame_to_date_window"),
        ("frame_calendar_axis_schema.grain", "frame_calendar_axis_schema"),
        ("frame_calendar_endpoint.grain", "frame_calendar_endpoint"),
    ):
        marivo.help("analysis." + target)
        assert "analysis." + owner in capsys.readouterr().out


def test_result_fields_explain_interpretation(capsys: pytest.CaptureFixture[str]) -> None:
    marivo.help("analysis.frame_decomposition_field.role")
    page = capsys.readouterr().out
    assert "analysis.analysis_frame.attribute" in page


def test_new_interpretation_cards_remain_bounded_and_link_complete_tuple_facts() -> None:
    groups = tuple(mv.FrameAxisGroup(f"axis_{i}") for i in range(40))
    named = mv.FrameNamedValueInput("captured." + "very_long_name" * 100, None)
    origins = tuple(
        mv.SemanticObjectIdentity("metric", f"sales.metric_{i:03}", f"definition_{i}")
        for i in range(40)
    )
    derived = mv.FrameDerivedValueInput(origins)
    share = mv.FrameShareContext(named, groups[:20], groups[20:])
    pair = mv.FramePairStatisticContext("pearson", derived, named, groups)
    coordinate = mv.FrameAlignmentAxis("axis_0", "self")
    side = mv.FrameAlignmentSide(
        tuple(f"value_{i}" for i in range(40)),
        ("present",),
        tuple(mv.FrameAlignmentAxis(f"axis_{i}", "self") for i in range(40)),
    )
    alignment = mv.FrameAlignment(
        "current-input",
        "alignment",
        side,
        mv.FrameAlignmentSide((), ("other_present",), (coordinate,)),
    )
    for value in (named, derived, share, pair, coordinate, side, alignment):
        identity = repr(value)
        assert len(identity) <= 260 and "\n" not in identity
        assert len(value.render().encode()) <= 8192
    assert len(derived.origins) == len(side.values) == len(side.coordinates) == 40
