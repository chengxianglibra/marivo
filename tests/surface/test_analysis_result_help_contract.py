"""Public Help for interpreting and navigating captured Frame facts."""

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
        ("frame_to_date_window", ("frame_builtin_grain", "frame_named_grain")),
        ("frame_calendar_axis_schema", ("frame_builtin_grain", "frame_named_grain")),
        ("frame_calendar_endpoint", ("frame_builtin_grain", "frame_named_grain")),
    ):
        marivo.help("analysis." + owner)
        page = capsys.readouterr().out
        for target in variants:
            assert "analysis." + target in page
            marivo.help("analysis." + target)
            assert "Target: analysis." + target in capsys.readouterr().out

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
    for target in ("frame_statistic_context.row_scope", "frame_decomposition.row_scope"):
        marivo.help("analysis." + target)
        page = capsys.readouterr().out
        assert "restricted" in page
        assert "does not recompute" in page

    marivo.help("analysis.frame_field.nullable")
    assert "only for an unresolved pending field" in capsys.readouterr().out
    marivo.help("analysis.frame_field.unit")
    page = capsys.readouterr().out
    assert "None means no unit was declared" in page
    assert "'1' means dimensionless" in page
    marivo.help("analysis.frame_decomposition_field.role")
    page = capsys.readouterr().out
    assert "observed_total" in page and "effect_mix" in page
    assert "analysis.analysis_frame.attribute" in page


def test_value_input_and_alignment_help_explains_snapshot_and_endpoint_boundaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for target, facts in (
        ("frame_named_value_input", ("captured complete input name", "not a reference")),
        ("frame_derived_value_input", ("without a formula", "not computation/cache keys")),
        ("frame_share_context", ("negative or exceed 100%", "does not recompute")),
        ("frame_pair_statistic_context", ("call order", "statistical significance")),
        ("frame_alignment", ("not certified", "ancestor Artifact")),
        ("frame_alignment_side", ("every flag must be true", "differs from value NA")),
        ("frame_alignment_axis", ("exact schema", "paired endpoint")),
    ):
        marivo.help("analysis." + target)
        page = capsys.readouterr().out
        assert "Kind: object" in page
        for fact in facts:
            assert fact in page


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
        assert type(value).__name__ in identity and ".show()" in identity
        assert len(value.render().encode()) <= 8192
    assert "read .origins" in derived.render()
    assert "read .values" in side.render()
    assert "read .coordinates" in side.render()
    assert len(derived.origins) == len(side.values) == len(side.coordinates) == 40


def test_page_help_keeps_essential_facts_without_repeating_docstring(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help("analysis.frame_page")
    page = capsys.readouterr().out
    assert page.count("Direct construction") == 1
    assert "RangeIndex is not a business key" in page
    assert "fixed.read_page(limit=100).to_pandas()" in page
    assert "``" not in page

    marivo.help("analysis.frame_period_page")
    page = capsys.readouterr().out
    assert "selected periods need not have current rows" in page
    assert "fixed.read_periods(fixed.axes[0], limit=100)" in page
    assert "``" not in page
