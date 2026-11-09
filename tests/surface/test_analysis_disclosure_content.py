"""Independent semantic, example and family-projection disclosure regressions."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

import marivo.analysis as mv
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput, TypeInput
from marivo.analysis._capabilities.dataset_registry import assemble, prepare
from marivo.analysis._capabilities.model import ANALYSIS_HELP_RENDER_BUDGETS
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
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.errors import AnalysisError
from marivo.cli import main
from tests.shared_fixtures import DslCaseFactory, catalog_value
from tests.support.documentation import _example as documentation_example


def test_analysis_root_example_discovers_and_reads_sparse_daily_revenue(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    analysis_dsl_case_factory("j1")
    text = render_help_text("analysis")[0]
    capsys.readouterr()
    main(["help", "analysis"])
    cli = capsys.readouterr()
    assert cli.err == ""
    code = text.split("\nExample:\n", 1)[1].split("\nExpected:", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(code, "<analysis-root-help-example>", "exec"), namespace)
    output = capsys.readouterr().out
    assert "metric:sales.revenue" in output
    assert "2026-08-01" in output and "2026-08-31" in output
    result = namespace["result"]
    assert isinstance(result, mv.AnalysisFrame)
    assert result.output_names == ("revenue",)
    rows = result.to_pandas().sort_values("calendar:day")
    assert rows["calendar:day"].dt.strftime("%Y-%m-%d").tolist() == [
        f"2026-08-{day:02d}" for day in (1, 15, 31)
    ]
    # Include the exact start and final second; exclude July and the exact end.
    # Missing days remain in the calendar directory, without result rows.
    assert rows["revenue"].tolist() == [450, 150, 400]
    for edition in ("docs", "zh-cn/docs"):
        source = documentation_example(edition, "complete-time-domain", page="analysis/observe")
        exec(compile(source, "<complete-time-domain>", "exec"), namespace)
        complete = namespace["complete"]
        assert isinstance(complete, mv.AnalysisFrame)
        values = complete.to_pandas()["revenue"]
        assert len(values) == 31 and values.eq(0).sum() == 28


def test_observe_routes_identity_audit_details_to_source_checks() -> None:
    from marivo.analysis.session.core import Session

    for target in ("analysis.session.observe", Session.observe):
        text = render_help_text(target)[0]
        assert "semantic.source_check" in text


def test_output_naming_help_discloses_and_runs_both_entry_shapes(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = catalog_value(case.catalog, "sales.revenue", kind="metric")
    frame = session.values(by=(), columns={"value": None}, rows=(((), {"value": 6}),))
    baseline = session.values(by=(), columns={"value": None}, rows=(((), {"value": 2}),))
    namespace: dict[str, object] = {
        "session": session,
        "revenue": revenue,
        "frame": frame,
        "previous": baseline,
    }
    registry = prepare()
    for target, expected_names in (
        ("session.observe", ("revenue",)),
        ("analysis_frame.reduce", ("value_sum",)),
        (
            "analysis_frame.compare",
            (
                "value",
                "reference.baseline.value",
                "reference.absolute.value",
                "reference.relative.value",
            ),
        ),
    ):
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, CallableInput)
        text = render_help_text("analysis." + target)[0]
        assert len(text) <= ANALYSIS_HELP_RENDER_BUDGETS["exact_callable"].max_codepoints
        exec(compile(descriptor.example.code, "<naming-help-example>", "exec"), namespace)
        result = namespace["result"]
        assert isinstance(result, mv.AnalysisFrame)
        assert result.output_names == expected_names
        if target == "session.observe":
            # The published period-total example must produce the answer, not just a plan.
            assert result.to_pandas()["revenue"].tolist() == [180]
        if target == "analysis_frame.compare":
            assert result._schema.values[-1].unit is None
            comparison_rows = result.compute().to_pandas()
            assert comparison_rows["reference.relative.value"].tolist() == [2]
    main(["help", "analysis.analysis_frame.compare"])
    output = capsys.readouterr()
    assert output.err == ""


def test_parameter_semantics_and_real_producers() -> None:
    registry = prepare()
    for target, producer in (
        ("analysis_frame", "session.observe"),
        ("analysis_frame", "session.read"),
        ("analysis_frame", "session.values"),
        ("population", "session.members"),
        ("population", "session.population_from"),
        ("column", "analysis_frame.__getitem__"),
    ):
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, TypeInput) and producer in descriptor.producers
        callable_descriptor = registry.by_canonical_id(producer)
        assert isinstance(callable_descriptor, CallableInput)
        assert descriptor.bindings[0].implementation.__name__ in str(
            callable_descriptor.bindings[0].implementation.__annotations__["return"]
        )


def test_family_projection_preserves_every_canonical_member() -> None:
    registry = prepare()
    families: dict[str, list[str]] = {}
    for descriptor in registry.descriptors:
        if isinstance(descriptor, CallableInput) and descriptor.discovery_family:
            families.setdefault(descriptor.discovery_family, []).append(descriptor.canonical_id)
    # AnalysisFrame owns each result operation once, without legacy receiver-family projections.
    assert families == {}
    for owner, methods in (
        (
            mv.AnalysisFrame,
            ("select", "where", "fix", "reduce", "over", "correlate", "align", "compare"),
        ),
        (mv.Column, ("sum", "mean", "corr", "pair_count", "share", "is_null")),
    ):
        for method in methods:
            target = ("analysis_frame" if owner is mv.AnalysisFrame else "column") + "." + method
            descriptor = registry.by_callable(getattr(owner, method))
            assert isinstance(descriptor, CallableInput)
            assert descriptor.canonical_id == target and descriptor.discovery_group
            text, _, _ = render_help_text("analysis." + descriptor.discovery_group)
            targets = re.findall(r"marivo.help\('analysis.([^']+)'\)", text)
            assert targets.count(target) == 1


def test_frame_targets_have_one_semantic_owner_and_no_retired_edges() -> None:
    registry = prepare()
    frame_methods = (
        "compute",
        "persist",
        "show",
        "to_pandas",
        "__getitem__",
        "axis",
        "coord",
        "where",
        "select",
        "top",
        "reduce",
        "over",
        "correlate",
        "align",
        "compare",
    )
    for name in frame_methods:
        target = "analysis_frame." + name
        descriptor = registry.by_callable(getattr(mv.AnalysisFrame, name))
        assert descriptor.canonical_id == target


def test_partition_and_share_help_examples_return_composable_same_input_columns(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = case.session.values(
        by=(region,),
        columns={"value": "CNY"},
        rows=((("east",), {"value": 1}), (("west",), {"value": 3})),
    ).compute()
    registry = prepare()
    for target, expected in (
        ("analysis_frame.over", [4, 4]),
        ("column.share", [0.25, 0.75]),
    ):
        text = render_help_text("analysis." + target)[0]
        assert len(text) < 3500
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, CallableInput)
        namespace: dict[str, object] = {"frame": frame}
        exec(descriptor.example.code, namespace)
        column = namespace["result"]
        assert isinstance(column, mv.Column)
        result = frame.select({"result": column}).compute()
        assert result.to_pandas()["result"].tolist() == expected
        if target == "analysis_frame.over":
            selected = namespace["selected"]
            assert isinstance(selected, mv.AnalysisFrame)
            rows = selected.to_pandas()
            assert rows["value"].tolist() == [1, 3]
            assert rows["total"].tolist() == expected


@pytest.mark.parametrize("selection", ("where", "top"))
def test_selected_frame_statistics_reacquire_exact_input_handles(
    analysis_dsl_case_factory: DslCaseFactory, selection: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = case.session.values(
        by=(region,),
        columns={"amount": None},
        rows=(
            (("east",), {"amount": -2}),
            (("north",), {"amount": 1}),
            (("west",), {"amount": 4}),
        ),
    ).compute()
    selected = (
        frame.where(frame["amount"] > 0)
        if selection == "where"
        else frame.top(1, by=frame["amount"])
    )
    with pytest.raises(AnalysisError):
        selected.reduce({"total": frame["amount"].sum()})
    total = selected.reduce({"total": selected["amount"].sum()}).compute()
    assert total.to_pandas()["total"].tolist() == [5 if selection == "where" else 4]
    column = selected.over(selected["amount"].sum(), by=selected.axes)
    broadcast = selected.select({"total": column}).compute()
    assert broadcast.to_pandas()["total"].tolist() == ([1, 4] if selection == "where" else [4])


@pytest.mark.parametrize("method", ("compute", "reduce", "over", "to_pandas"))
def test_composition_help_is_static_bounded_and_matches_cli(
    method: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    descriptor = prepare().by_canonical_id("analysis_frame." + method)
    assert isinstance(descriptor, CallableInput)

    target = "analysis.analysis_frame." + method
    text = render_help_text(target)[0]
    if method == "to_pandas":
        assert "Pending inputs evaluate once per call" in text
        assert "no mutation, cache or save" in text
        assert "Fixed data needs no source reads" in text
        assert "result = frame.to_pandas()" in descriptor.example.code
    assert descriptor.example.requires == ("frame",)
    budget = ANALYSIS_HELP_RENDER_BUDGETS["exact_callable"]
    assert len(text.splitlines()) <= budget.max_lines + 4
    assert len(text) <= budget.max_codepoints + 100
    main(["help", target])
    cli = capsys.readouterr()
    assert cli.err == ""


def test_correlation_help_contract_and_executable_examples(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """C09: canonical, class and bound targets share executable, bounded facts."""
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = case.session.values(
        by=(region,),
        columns={"value": "CNY", "other": "count"},
        rows=(
            (("east",), {"value": 1, "other": 1}),
            (("north",), {"value": 2, "other": 3}),
            (("west",), {"value": 3, "other": 2}),
        ),
    )
    registry = prepare()
    for target, expected in (
        ("analysis_frame.correlate", ("coefficient", "n_pairs")),
        ("column.corr", ("coefficient", "n_pairs")),
        ("column.pair_count", ("n_pairs", "n_values")),
    ):
        text = render_help_text("analysis." + target)[0]
        assert len(text) < 5000
        assert "analysis.analysis_frame.reduce" in text
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, CallableInput)
        namespace: dict[str, object] = {"frame": frame}
        exec(descriptor.example.code, namespace)
        result = namespace["result"]
        assert isinstance(result, mv.AnalysisFrame)
        assert result.output_names == expected
        data = result.to_pandas()
        assert data["n_pairs"].tolist() == [3]
        if "coefficient" in expected:
            assert data["coefficient"].tolist() == pytest.approx([0.5], abs=1e-12)
        else:
            assert data["n_values"].tolist() == [3]
    frame_help = render_help_text("analysis.analysis_frame")[0]
    column_help = render_help_text("analysis.column")[0]
    axis_help = render_help_text("analysis.frame_axis")[0]
    assert "analysis.analysis_frame.correlate" in frame_help
    assert "analysis.column.corr" in column_help and "analysis.column.pair_count" in column_help
    assert "analysis.analysis_frame.correlate" in axis_help


def test_fact_show_has_one_canonical_owner_for_every_concrete_record() -> None:
    registry = prepare()
    types = (
        FrameMethodValue,
        FrameMethodCoordinate,
        FrameMethodSupport,
        FrameAdditiveMethod,
        FrameRatioMethod,
        FrameShapleyMethod,
        FrameShapleyNativeQuantile,
        FrameShapleyQuery,
        FrameShapleyQueryGame,
        FrameShapleyGame,
        FrameShapleyLeaf,
        FrameShapleyCapture,
        FrameShapleyReduction,
        FrameShapleyQuantile,
        FrameLinearTerm,
        FrameLinearMethod,
        FrameDivisionMethod,
        FrameAttributeMetric,
        FrameLevelSupport,
        FrameLevelAdditiveMethod,
        FrameLevelRatioMethod,
        FrameLevelLinearMethod,
        FrameLevelDivisionMethod,
        FrameLevelLinearTerm,
        FrameMethodOutput,
        FrameMethodTarget,
        FrameMethodParent,
        mv.FrameMethodFacts,
    )
    assert len({owner.show for owner in types}) == 1
    assert registry.by_callable(mv.FrameMethodFacts.show).canonical_id == "frame_method_facts.show"
    for owner in types:
        assert "analysis.frame_method_facts.show" in render_help_text(owner)[0]
    owners = [
        descriptor
        for descriptor in registry.descriptors
        if isinstance(descriptor, CallableInput)
        and any(
            binding.implementation is mv.FrameMethodFacts.show for binding in descriptor.bindings
        )
    ]
    assert len(owners) == 1


def test_attribute_help_distinguishes_actual_qualification_and_inert_facts() -> None:
    text = render_help_text("analysis.analysis_frame.attribute")[0]
    assert "analysis.analysis_frame.method_facts" in text


def test_construction_type_families_and_retired_surface() -> None:
    registry = prepare()
    assert {d.canonical_id for d in registry.descriptors if isinstance(d, TypeInput)} == {
        "frame_reference",
        "dataframe_reference",
        "dataframe_result",
        "frame_clocked_contribution",
        "frame_report_window_endpoint",
        "frame_contribution_sample_window_endpoint",
        "frame_field_sample_window_endpoint",
        "frame_independent_contribution",
        "frame_observed_time_semantics",
        "frame_derived_time_semantics",
        "grain",
        "time_scope",
        "calendar_correspondence",
        "comparison_baseline",
        "frame_instant_source_time",
        "frame_wall_clock_source_time",
        "frame_civil_date_source_time",
        "frame_source_read",
        "frame_source_read_scope",
        "frame_source_read_period",
        "frame_schema",
        "frame_field",
        "frame_axis_schema",
        "frame_ordinary_axis_schema",
        "frame_row_attribute_time",
        "frame_calendar_axis_schema",
        "frame_interval_axis_schema",
        "frame_paired_axis_schema",
        "frame_builtin_grain",
        "frame_calendar_endpoint",
        "frame_interval_endpoint",
        "frame_fixed_axis",
        "frame_axis_time",
        "frame_compared_time",
        "frame_fixed_time",
        "frame_all_history_window",
        "frame_since_window",
        "frame_to_date_window",
        "frame_trailing_duration_window",
        "frame_trailing_periods_window",
        "frame_observation_context",
        "frame_axis_group",
        "frame_fixed_group",
        "frame_statistic_context",
        "frame_named_value_input",
        "frame_derived_value_input",
        "frame_share_context",
        "frame_pair_statistic_context",
        "frame_alignment",
        "frame_alignment_axis",
        "frame_alignment_side",
        "frame_comparison_value",
        "frame_comparison_group",
        "frame_comparison_presence",
        "frame_ordinary_comparison_binding",
        "frame_calendar_comparison_binding",
        "frame_interval_comparison_binding",
        "frame_comparison",
        "frame_level_target",
        "frame_change_target",
        "frame_decomposition_field",
        "frame_decomposition",
        "field_export_column",
        "coordinate_export_column",
        "scalar_data_type",
        "decimal_data_type",
        "timestamp_data_type",
        "duration_data_type",
        "unresolved_data_type",
        "frame_page",
        "frame_read_stats",
        "frame_period_page",
        "frame_calendar_period",
        "frame_interval_period",
        "frame_paired_period",
        "frame_comparison_period",
        "session",
        "runtime.session_summary_page",
        "runtime.session_summary",
        "runtime.persisted_frame_summary",
        "runtime.persisted_frame_summary_page",
        "analysis_frame",
        "frame_lineage",
        "column_lineage",
        "semantic_object_identity",
        "frame_axis",
        "frame_method_facts",
        "frame_method_parent",
        "frame_attribute_metric",
        "frame_method_target",
        "frame_method_output",
        "frame_level_support",
        "frame_level_additive_method",
        "frame_level_ratio_method",
        "frame_level_linear_term",
        "frame_level_linear_method",
        "frame_level_division_method",
        "frame_method_value",
        "frame_method_coordinate",
        "frame_method_support",
        "frame_additive_method",
        "frame_ratio_method",
        "frame_shapley_method",
        "frame_shapley_native_quantile",
        "frame_shapley_query",
        "frame_shapley_query_game",
        "frame_shapley_game",
        "frame_shapley_leaf",
        "frame_shapley_capture",
        "frame_shapley_reduction",
        "frame_shapley_quantile",
        "frame_linear_term",
        "frame_linear_method",
        "frame_division_method",
        "instant_anchor",
        "calendar_axis",
        "population",
        "grouping",
        "observation_window",
        "value_statistic",
        "column",
    }
    assert len([name for name in mv.__all__ if isinstance(getattr(mv, name), type)]) == 81
    descriptor = registry.by_canonical_id("value_statistic")
    assert isinstance(descriptor, TypeInput)
    assert tuple(f.name for f in descriptor.bindings[0].fields) == ("unit",)
    statistic = mv.row_count()
    assert "analysis.value_statistic" in render_help_text(type(statistic))[0]
    assert "analysis.value_statistic" in render_help_text(mv.row_count)[0]


def test_fix_help_matches_callable_and_runs_public_example(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = case.session.values(
        by=(region,),
        columns={"value": "CNY"},
        rows=((("east",), {"value": 120}), (("west",), {"value": 100})),
    )
    descriptor = prepare().by_canonical_id("analysis_frame.fix")
    assert isinstance(descriptor, CallableInput)
    namespace = {"frame": frame}
    exec(descriptor.example.code, namespace)
    assert namespace["result"].compute().to_pandas()["value"].tolist() == [120]
    assert "analysis.analysis_frame.fix" in render_help_text("analysis.operators")[0]


def test_fixed_read_surface_has_one_reachable_contract_per_entry() -> None:
    registry = prepare()
    assert "analysis_frame.read_page" in registry.discovery_ids()
    inspect = render_help_text("analysis.results.inspect")[0]
    assert "analysis.analysis_frame.schema" in inspect
    assert "analysis.analysis_frame.read_page" in inspect
    assert "analysis.analysis_frame.read_periods" in inspect
    assert "analysis.analysis_frame.source_reads" in inspect


def test_interpretation_facts_and_periods_share_canonical_display_contracts() -> None:
    registry = prepare()
    for owner, method, target in (
        (mv.FrameObservationContext, "show", "runtime.values.show"),
        (mv.FrameStatisticContext, "render", "runtime.values.render"),
        (mv.FrameNamedValueInput, "show", "runtime.values.show"),
        (mv.FrameDerivedValueInput, "show", "runtime.values.show"),
        (mv.FrameShareContext, "show", "runtime.values.show"),
        (mv.FramePairStatisticContext, "show", "runtime.values.show"),
        (mv.FrameAlignment, "show", "runtime.values.show"),
        (mv.FrameAlignmentAxis, "show", "runtime.values.show"),
        (mv.FrameAlignmentSide, "show", "runtime.values.show"),
        (mv.FrameCalendarAxisSchema, "show", "frame_axis_schema.show"),
        (mv.FramePairedAxisSchema, "render", "frame_axis_schema.render"),
        (mv.FramePeriodPage, "show", "runtime.values.show"),
        (mv.FrameComparisonPeriod, "render", "runtime.values.render"),
    ):
        assert registry.by_callable(getattr(owner, method)).canonical_id == target
        assert "analysis." + target in render_help_text(owner)[0]

    descriptor = registry.by_canonical_id("analysis_frame.read_periods")
    assert isinstance(descriptor, CallableInput)
    assert descriptor.bindings[0].implementation is mv.AnalysisFrame.read_periods
    assert descriptor.output == "FramePeriodPage"


@pytest.mark.parametrize("fault", ("input", "guidance", "family", "missing_family", "syntax"))
def test_registration_rejects_incomplete_example_or_foreign_family(fault: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, provider = next(
        (i, p)
        for i, p in enumerate(providers)
        if any(
            isinstance(d, CallableInput) and d.canonical_id == "analysis_frame.reduce"
            for d in p.descriptors
        )
    )
    descriptors = list(provider.descriptors)
    position, descriptor = next(
        (i, d)
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "analysis_frame.reduce"
    )
    if fault == "input":
        broken = replace(descriptor, example=replace(descriptor.example, requires=()))
    elif fault == "guidance":
        broken = replace(
            descriptor, parameters=(replace(descriptor.parameters[0], acquisition=""),)
        )
    elif fault == "family":
        broken = replace(descriptor, discovery_family="analysis_frame.compare")
    elif fault == "missing_family":
        broken = replace(descriptor, discovery_family="Missing.reduce")
    else:
        broken = replace(descriptor, example=replace(descriptor.example, code="result = ("))
    descriptors[position] = broken
    providers[index] = replace(provider, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))
