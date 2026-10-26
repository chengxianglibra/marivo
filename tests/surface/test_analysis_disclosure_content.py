"""Independent semantic, example and family-projection disclosure regressions."""

from __future__ import annotations

import re
from dataclasses import replace
from inspect import signature
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
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
)
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.errors import AnalysisError
from marivo.cli import main
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory


def test_simplified_policies_have_one_public_route() -> None:
    assert not hasattr(mv.Column, "divide")
    with pytest.raises((AnalysisError, MarivoHelpTargetError)):
        render_help_text("analysis.column.divide")
    for method in (mv.AnalysisFrame.align, mv.AnalysisFrame.compare, mv.Column.share):
        parameters = signature(method).parameters
        assert "how" not in parameters and "zero_denominator" not in parameters
    division = render_help_text(mv.Column.__truediv__)[0]
    assert "zero denominators give NA before arithmetic" in division
    observation = render_help_text("analysis.session.observe")[0]
    assert "Input memberships:" not in observation
    assert "necessary output keys before sampling" in observation
    membership = render_help_text("analysis.membership")[0]
    assert "(rule, customer, channel)" in membership and "(R,S,b,d)" in membership


def test_output_naming_help_discloses_and_runs_both_entry_shapes(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = case.catalog.metrics.get("sales.revenue")
    frame = session.values(by=(), columns={"value": None}, rows=(((), {"value": 6}),))
    baseline = session.values(by=(), columns={"value": None}, rows=(((), {"value": 2}),))
    namespace: dict[str, object] = {
        "session": session,
        "revenue": revenue,
        "frame": frame,
        "previous": baseline,
    }
    registry = prepare()
    for target, implementation, bound, expected_names in (
        ("session.observe", mv.Session.observe, session.observe, ("revenue",)),
        ("analysis_frame.reduce", mv.AnalysisFrame.reduce, frame.reduce, ("value_sum",)),
        (
            "analysis_frame.compare",
            mv.AnalysisFrame.compare,
            frame.compare,
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
        assert text == render_help_text(implementation)[0] == render_help_text(bound)[0]
        assert "Mapping" in text
        assert len(text) <= ANALYSIS_HELP_RENDER_BUDGETS["exact_callable"].max_codepoints
        exec(compile(descriptor.example.code, "<naming-help-example>", "exec"), namespace)
        result = namespace["result"]
        assert isinstance(result, mv.AnalysisFrame)
        assert result.output_names == expected_names
        if target == "analysis_frame.compare":
            assert result._schema.values[-1].unit is None
            comparison_rows = result.compute().to_pandas()
            assert comparison_rows["reference.relative.value"].tolist() == [2]
    observation = render_help_text("analysis.session.observe")[0]
    for fact in ("nonempty tuple", "root DeclaredMetricRef", "business identity"):
        assert fact in observation
    assert "conflicts" in observation or "duplicate" in observation.lower()
    for fact in (
        "nonempty",
        "dots",
        "backslashes",
        "controls",
        "left_present/right_present",
        "no name cleaning/numbering",
    ):
        assert fact in observation
    reduction = render_help_text("analysis.analysis_frame.reduce")[0]
    for fact in ("<name>_sum/<name>_mean", "multi-segment", "row_count", "bound value slots"):
        assert fact in reduction
    comparison = render_help_text("analysis.analysis_frame.compare")[0]
    assert comparison.count("\nSignature:") == 2
    for fact in (
        "Bare Frame only",
        "mv.baseline",
        "reference.present",
        "infers no time relationship",
    ):
        assert fact in comparison
    for fact in (
        "relative is dimensionless (unit=None)",
        "Calendar ordinal needs complete buckets",
        "week/month/year bucket-start offset prefixes",
        "exactly equal saved progress",
    ):
        assert fact in comparison
    assert "marivo.help('analysis.ordinal')" in comparison
    ordinal = render_help_text("analysis.ordinal")[0]
    assert "one declared record per side" in ordinal and "equal boundary timezones" in ordinal
    main(["help", "analysis.analysis_frame.compare"])
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("\nSignature:") == 2


@pytest.mark.release
def test_every_advertised_native_member_has_exact_help() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for binding in descriptor.bindings:
                for name in binding.methods:
                    method = getattr(binding.implementation, name)
                    resolved = registry.by_callable(method)
                    text, _, _ = render_help_text(method)
                    assert text == render_help_text("analysis." + resolved.canonical_id)[0]
    assert (
        render_help_text(mv.ordinal().show)[0]
        == render_help_text("analysis.calendar_correspondence.show")[0]
    )


def test_parameter_semantics_and_real_producers() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for producer in descriptor.producers:
                assert render_help_text("analysis." + producer)[0]
            if descriptor.canonical_id != "population":
                assert "session.members" not in descriptor.producers, descriptor.canonical_id
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
        (mv.AnalysisFrame, ("select", "where", "reduce", "over", "correlate", "align", "compare")),
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
        assert render_help_text("analysis." + target)[0].startswith(
            f"Target: analysis.{target}\nKind: callable"
        )
    for target in registry.canonical_ids():
        assert not target.startswith(("query", "analysisframe", "frameaxis"))
        assert target not in (
            "broadcast",
            "corresponding",
            "declared_correspondence",
            "correspondence_axis",
            "session.query",
            "session.artifact",
            "artifact.to_pandas",
            "calendar_axis.coarsen",
        )
    compare = render_help_text("analysis.analysis_frame.compare")[0]
    assert "<alias>.absolute.<name>" in compare and "absolute baseline" in compare
    assert "on/outer cannot repair ownership" in compare
    assert "partition_by" not in render_help_text("analysis.analysis_frame.top")[0]
    assert (
        "Native Deferred predicates belong to source"
        in render_help_text("analysis.analysis_frame.where")[0]
    )


def test_partition_and_share_help_examples_return_composable_same_input_columns(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    frame = case.session.values(
        by=(region,),
        columns={"value": "CNY"},
        rows=((("east",), {"value": 1}), (("west",), {"value": 3})),
    ).compute()
    registry = prepare()
    for target, implementation, bound, expected in (
        ("analysis_frame.over", mv.AnalysisFrame.over, frame.over, [4, 4]),
        ("column.share", mv.Column.share, frame["value"].share, [0.25, 0.75]),
    ):
        text = render_help_text("analysis." + target)[0]
        assert text == render_help_text(implementation)[0] == render_help_text(bound)[0]
        assert text.startswith(f"Target: analysis.{target}\nKind: callable")
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
    region = case.catalog.fields.get("sales.customer.region")
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
    with pytest.raises(AnalysisError, match="exact Frame input"):
        selected.reduce({"total": frame["amount"].sum()})
    total = selected.reduce({"total": selected["amount"].sum()}).compute()
    assert total.to_pandas()["total"].tolist() == [5 if selection == "where" else 4]
    column = selected.over(selected["amount"].sum(), by=selected.axes)
    broadcast = selected.select({"total": column}).compute()
    assert broadcast.to_pandas()["total"].tolist() == ([1, 4] if selection == "where" else [4])


@pytest.mark.parametrize("method", ("compute", "reduce", "over", "to_pandas"))
def test_composition_help_is_static_bounded_and_matches_cli(
    method: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    descriptor = prepare().by_canonical_id("analysis_frame." + method)
    assert isinstance(descriptor, CallableInput)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Static Help executed an example or opened a source")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mv.session, "get_or_create", forbidden)
    monkeypatch.setattr(mv.Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    for name in ("compute", "reduce", "over", "to_pandas"):
        monkeypatch.setattr(mv.AnalysisFrame, name, forbidden)
    target = "analysis.analysis_frame." + method
    text = render_help_text(target)[0]
    assert text == render_help_text(target)[0]
    assert descriptor.example.requires == ("frame",)
    assert descriptor.example.code in text
    assert "Example needs" in text or "Example input: frame has a" in text
    assert text.count("\nExample:\n") == 1
    budget = ANALYSIS_HELP_RENDER_BUDGETS["exact_callable"]
    assert len(text.splitlines()) <= budget.max_lines + 4
    assert len(text) <= budget.max_codepoints + 100
    main(["help", target])
    cli = capsys.readouterr()
    assert cli.err == ""
    expected = re.sub(
        r"marivo\.help\(([\"'])([a-z_][a-z0-9_.]*)\1\)",
        lambda match: "marivo help " + match.group(2),
        text,
    )
    assert cli.out.rstrip() == expected.rstrip()
    assert not (tmp_path / ".marivo").exists()


def test_correlation_help_contract_and_executable_examples(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """C09: canonical, class and bound targets share executable, bounded facts."""
    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
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
    for target, implementation, bound, expected in (
        (
            "analysis_frame.correlate",
            mv.AnalysisFrame.correlate,
            frame.correlate,
            ("coefficient", "n_pairs"),
        ),
        ("column.corr", mv.Column.corr, frame["value"].corr, ("coefficient", "n_pairs")),
        (
            "column.pair_count",
            mv.Column.pair_count,
            frame["value"].pair_count,
            ("n_pairs", "n_values"),
        ),
    ):
        text = render_help_text("analysis." + target)[0]
        assert text == render_help_text(implementation)[0] == render_help_text(bound)[0]
        assert text.startswith(f"Target: analysis.{target}\nKind: callable")
        assert len(text) < 5000
        for fact in (
            "Duration",
            "Frame/Session",
            "constant",
            "NA",
            "analysis.analysis_frame.reduce",
        ):
            assert fact in text
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
    high = render_help_text("analysis.analysis_frame.correlate")[0]
    assert "coefficient" in high and "n_pairs" in high and "one input evaluation" in high
    assert "nonnullable int64" in high and "nullable float64" in high
    count = render_help_text("analysis.column.pair_count")[0]
    assert "Self pair_count counts non-NA" in count and "without ranking" in count


def test_fact_show_has_one_canonical_owner_for_every_concrete_record() -> None:
    registry = prepare()
    types = (
        FrameMethodValue,
        FrameMethodCoordinate,
        FrameMethodSupport,
        FrameAdditiveMethod,
        FrameRatioMethod,
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
    expected = render_help_text("analysis.frame_method_facts.show")[0]
    assert registry.by_callable(mv.FrameMethodFacts.show).canonical_id == "frame_method_facts.show"
    assert render_help_text(mv.FrameMethodFacts.show)[0] == expected
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
    assert text == render_help_text(mv.AnalysisFrame.attribute)[0]
    for expected in (
        "qualified physical tables",
        "reads are independent",
        "requested final targets",
        "1% of abs(total)",
        "zero totals retain the numeric budget",
        "Integer/count closure is exact",
        "Trust declared identity",
        "without arithmetic recomputation",
        "independent reopen",
        "complete",
        "observed_total",
        "each target",
        "source declaration",
    ):
        assert expected.lower() in text.lower()
    assert "analysis.analysis_frame.method_facts" in text
    for target in ("frame_method_facts", "frame_ratio_method"):
        assert target not in prepare().discovery_ids()


def test_construction_type_families_and_retired_surface() -> None:
    registry = prepare()
    assert {d.canonical_id for d in registry.descriptors if isinstance(d, TypeInput)} == {
        "grain",
        "time_scope",
        "calendar_correspondence",
        "comparison_baseline",
        "artifact_digest",
        "artifact_ref",
        "finding",
        "finding_page",
        "session",
        "runtime.output_evidence",
        "runtime.output_coordinate",
        "runtime.session_summary_page",
        "runtime.session_summary",
        "analysis_frame",
        "frame_axis",
        "artifact",
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
    assert len([name for name in mv.__all__ if isinstance(getattr(mv, name), type)]) == 18
    removed = {
        "before_end_boundary",
        "before_end_boundary.end",
        "before_end_boundary.boundary_timezone",
        "before_end_boundary.show",
        "time_scope.before_end",
        "grain.to_token",
        "grain.width_seconds",
        "grain.is_day",
        "grain.is_subday",
        "time_scope.contract",
        "time_scope.model_dump",
    }
    for target in removed:
        with pytest.raises((AnalysisError, MarivoHelpTargetError)) as error:
            render_help_text("analysis." + target)
        assert "registered" in str(error.value).lower() or "unknown" in str(error.value).lower()
    assert not hasattr(mv, "BeforeEndBoundary")
    for member in ("to_token", "width_seconds", "is_day", "is_subday"):
        assert not hasattr(mv.grain("day"), member)
    assert not hasattr(mv.TimeScope, "before_end")
    assert not hasattr(mv.TimeScope, "contract")
    assert "model_dump" not in vars(mv.TimeScope)
    descriptor = registry.by_canonical_id("value_statistic")
    assert isinstance(descriptor, TypeInput)
    assert tuple(f.name for f in descriptor.bindings[0].fields) == ("unit",)
    statistic = mv.row_count()
    assert not hasattr(statistic, "input_id") and not hasattr(statistic, "definition")
    page = render_help_text(type(statistic))[0]
    assert page == render_help_text("analysis.value_statistic")[0]
    assert "input_id" not in page and "definition" not in page
    assert "input_id" not in repr(statistic) and "definition" not in repr(statistic)
    assert "analysis.value_statistic" in render_help_text(mv.row_count)[0]
    assert not hasattr(mv, "attribute_endpoint")
    assert not hasattr(mv, "AttributeEndpoint")


def test_nested_metadata_help_keeps_read_boundaries() -> None:
    for target, fact in (
        ("calendar_axis.scope", "never rebuilds"),
        ("calendar_axis.grid", "frozen finite buckets"),
        ("frame_method_facts.output_schema", "complete original saved output layout"),
        ("frame_method_coordinate.schema", "complete saved coordinate layout"),
        ("frame_method_value.column", "physical dtype, unit and nullability"),
        ("finding.value", "finding_type/kind"),
    ):
        page = render_help_text("analysis." + target)[0]
        assert "Kind: attribute" in page and fact in page
    for target in ("time_scope_contract_v1", "data_schema", "bound_time_grid", "run_failure"):
        with pytest.raises((AnalysisError, MarivoHelpTargetError)):
            render_help_text("analysis." + target)
