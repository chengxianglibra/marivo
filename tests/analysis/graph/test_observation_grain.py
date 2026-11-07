"""Public observation grains retain complete contribution tuples and domains."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Literal

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic._expression_values import MetricExpression, RowExpression
from marivo.semantic.errors import SemanticError
from tests.analysis.graph.execution_fixtures import source_graph
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    catalog_get,
    catalog_value,
    export_dsl_parquet_models,
    terminal_runs,
)
from tests.support.paths import PROJECT_ROOT


def _members(case: DslCase) -> mv.Population:
    return case.session.members(
        catalog_get(case.session.catalog, "entity:sales.customer", kind="entity")
    )


def _august(case: DslCase) -> mv.TimeScope:
    return case.session.time_scope(start="2026-08-01", end="2026-09-01")


def _query(
    case: DslCase,
    *,
    metric: MetricExpression[Literal["numeric"]] | None = None,
    by: tuple[ms.Dimension | mv.Grouping, ...] | mv.Population | Deferred = (),
    members: mv.Population | None = None,
    during: mv.TimeScope | mv.CalendarAxis | None = None,
) -> mv.AnalysisFrame:
    assert not isinstance(by, Deferred)
    outputs = {"value": _revenue(case) if metric is None else metric}
    condition = mv.in_population(members) if members is not None else None
    if isinstance(by, mv.Population):
        domain = mv.product(during, by) if during is not None else by
        return case.session.observe(outputs, by=domain, where=condition)
    scope = during.scope if isinstance(during, mv.CalendarAxis) else during
    axes = (during.grid.grain_spec, *by) if isinstance(during, mv.CalendarAxis) else by
    return case.session.observe(
        outputs,
        by=axes,
        where=condition if scope is None else scope if condition is None else (scope, condition),
    )


def _revenue(case: DslCase) -> ms.Metric:
    return catalog_get(case.session.catalog, "metric:sales.revenue", kind="metric")


def _keyed(result: mv.AnalysisFrame, name: str = "value") -> dict[tuple[object, ...], object]:
    coordinates = [
        axis.name.rsplit(".", 1)[-1]
        for axis in result.axes
        if axis.role in ("calendar", "field", "identity")
    ]
    frame = result.to_pandas()
    return {tuple(row[column] for column in coordinates): row[name] for _, row in frame.iterrows()}


def test_overall_and_member_observations(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    members = _members(case)
    total = _query(case, members=members, during=_august(case))
    assert total.persist().to_pandas()["value"].tolist() == [1000]
    assert case.session.frame(
        case.session.population_from(total, on=())
    ).persist().to_pandas().shape == (1, 0)
    with pytest.raises(AnalysisError):
        case.session.population_from(total, on=()).project(by=(members.axes[0],))
    individual = _query(case, by=members, members=members, during=_august(case))
    fixed = individual.persist()
    assert _keyed(fixed) == {("A",): 450, ("B",): 150, ("C",): 400, ("D",): 0}
    keys = (
        case.session.frame(
            case.session.population_from(
                individual,
                on=(
                    (
                        individual.axes[1],
                        catalog_get(case.session.catalog, "entity:sales.customer", kind="entity"),
                    ),
                ),
            )
        )
        .persist()
        .to_pandas()
    )
    assert keys.iloc[:, 0].tolist() == ["A", "B", "C", "D"]


def test_member_and_contribution_grouping(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = _members(case)
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="dimension")
    grouped = _query(case, by=members.classify(by=(region,)), members=members, during=_august(case))
    assert _keyed(grouped.persist()) == {("east",): 600, ("south",): 400, ("west",): 0}
    channels = _query(case, by=(channel,), members=members, during=_august(case))
    assert _keyed(channels.persist()) == {("mobile",): 150, ("web",): 850}
    tuples = _query(case, by=(region, channel), members=members, during=_august(case)).persist()
    assert _keyed(tuples) == {("east", "web"): 450, ("east", "mobile"): 150, ("south", "web"): 400}
    classifications = session.read(members, {"region": region})
    chosen = classifications.where(classifications["region"] == "east")
    selected = session.population_from(
        chosen,
        on=(
            (chosen.axes[0], catalog_get(session.catalog, "entity:sales.customer", kind="entity")),
        ),
    )
    saved = _query(
        case, by=selected.classify(by=(region,)), members=selected, during=_august(case)
    ).persist()
    assert _keyed(saved) == {("east",): 600}
    assert session.persisted_frame(saved.artifact_id).to_pandas().equals(saved.to_pandas())


@pytest.mark.parametrize("member_first", [True, False])
def test_member_channel_axes_keep_identity_and_selected_scope(
    analysis_dsl_case_factory: DslCaseFactory, member_first: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    session = case.session
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="dimension")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="dimension")
    axes = (customer, channel) if member_first else (channel, customer)
    observed = _query(
        case,
        by=axes,
        members=_members(case),
        during=session.time_scope(start="2026-08-01", end="2026-10-01"),
    )
    expected = {("A", "web"): 450, ("A", "mobile"): 99, ("B", "mobile"): 150, ("C", "web"): 400}
    if not member_first:
        expected = {(channel, customer): value for (customer, channel), value in expected.items()}
    assert _keyed(observed.persist()) == expected
    assert observed.reduce({"value": observed["value"].sum()}).persist().to_pandas()[
        "value"
    ].tolist() == [1099]
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    classifications = session.read(_members(case), {"region": region})
    chosen = classifications.where(classifications["region"] == "east")
    selected = session.population_from(
        chosen,
        on=(
            (chosen.axes[0], catalog_get(session.catalog, "entity:sales.customer", kind="entity")),
        ),
    )
    result = _query(case, by=selected, members=selected).persist()
    assert _keyed(result) == {("A",): 626, ("B",): 150}


def test_component_domains_union_complete_tuples(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="dimension")
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    difference = revenue.where(channel == "web") - revenue.where(channel == "mobile")
    grouped = _query(
        case, metric=difference, by=(region, channel), members=_members(case), during=_august(case)
    )
    assert _keyed(grouped.persist()) == {
        ("east", "web"): 450,
        ("east", "mobile"): -150,
        ("south", "web"): 400,
    }
    assert grouped.reduce({"value": grouped["value"].sum()}).persist().to_pandas()[
        "value"
    ].tolist() == [700]


@pytest.mark.parametrize("keep_member", [False, True])
def test_member_classification_survives_rollup_for_additive_comparison(
    analysis_dsl_case_factory: DslCaseFactory, keep_member: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    members = _members(case)

    def endpoint(month: int) -> mv.AnalysisFrame:
        query = _query(
            case,
            by=members.classify(by=(members.axes[0], region))
            if keep_member
            else members.classify(by=(region,)),
            members=members,
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
        )
        return query.reduce({"value": query["value"].sum()}, by=(query.axes[-1],))

    current, baseline = endpoint(8), endpoint(7)
    comparison = current.compare(
        {
            "reference": mv.baseline(
                baseline, on=tuple(zip(current.axes, baseline.axes, strict=True))
            )
        }
    )
    selected = comparison.where(comparison["reference.absolute.value"] != 0)
    selected = selected.select({"value": selected["reference.absolute.value"]})
    saved = selected.persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    restored = session.persisted_frame(saved.artifact_id)
    frame = restored.to_pandas()
    assert _keyed(restored) == {("east",): 523, ("south",): 400}
    assert frame["value"].sum() == 923


def test_grid_observation_keeps_time_without_member_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    observed = _query(case, during=calendar, members=_members(case))
    assert observed.persist().to_pandas()["value"].tolist() == [1000, 99]
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    region_domain = mv.product(_members(case).classify(by=(region,)), calendar)
    region_rows = _query(case, by=region_domain, members=_members(case)).persist().to_pandas()
    assert len(region_rows) == 6
    channel = catalog_value(session.catalog, "sales.order.channel", kind="dimension")
    rows = (
        _query(case, by=(channel,), during=calendar, members=_members(case)).persist().to_pandas()
    )
    assert len(rows) == 4
    assert sorted(rows["value"]) == [0, 99, 150, 850]


@pytest.mark.parametrize(
    ("metric_path", "explicit_path"),
    [("sales.revenue", False), ("sales.line_revenue", False), ("sales.line_revenue", True)],
)
def test_calendar_rejects_raw_observation_time_before_source_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    metric_path: str,
    explicit_path: bool,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    metric = catalog_value(session.catalog, metric_path, kind="metric")
    raw_time: RowExpression[Literal["time"]] = catalog_value(
        session.catalog, "sales.order.ordered_at", kind="time_dimension"
    )
    if explicit_path:
        path = catalog_value(session.catalog, "sales.line_order", kind="relationship")
        raw_time = raw_time.via((path,))
    scope = session.time_scope(start="2026-08-01", end="2026-10-01")
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Observation time-axis validation must precede source reads")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        with pytest.raises(AnalysisError) as failure:
            session.observe(
                {"value": metric},
                by=(raw_time, mv.grain("month")),  # type: ignore[arg-type]  # Reject a raw time axis.
                where=scope,
            )
    error = failure.value
    assert error.expected and error.received and error.repair
    assert "Grain" in error.repair.action
    assert terminal_runs(session) == before


def test_raw_time_population_cannot_bypass_observation_admission(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    metric = catalog_value(session.catalog, "sales.revenue", kind="metric")
    raw_time = catalog_value(session.catalog, "sales.order.ordered_at", kind="time_dimension")
    scope = session.time_scope(start="2026-08-01", end="2026-10-01")
    month = mv.grain("month")
    buckets = session.observe({"value": metric}, by=(month,), where=scope).compute().to_pandas()
    assert buckets["value"].tolist() == [1000, 99]
    times = mv.coordinates(
        by=(raw_time,), keys=((datetime.fromisoformat("2026-08-01T00:00:00+00:00"),),)
    )
    with pytest.raises(AnalysisError, match="fieldless"):
        session.observe({"value": metric}, by=mv.product(scope.by(month), times))


def test_calendar_keeps_distinct_time_fields_and_relationship_roles(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nreceived_at = ms.time_dimension_column(name='received_at', entity=orders, column='ordered_at')\n"
        + "alternate = ms.relationship(name='alternate_line_order', from_entity=lines, to_entity=orders, keys=[ms.join_on(line_order_id, order_id)])\n"
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("distinct-time-roles", timezone="UTC")
    scope = session.time_scope(start="2026-08-01", end="2026-10-01")
    month = mv.grain("month")
    received_at = catalog_value(session.catalog, "sales.order.received_at", kind="time_dimension")
    raw_time = catalog_value(session.catalog, "sales.order.ordered_at", kind="time_dimension")
    alternate = catalog_value(session.catalog, "sales.alternate_line_order", kind="relationship")
    revenue = catalog_value(session.catalog, "sales.revenue", kind="metric")
    line_revenue = catalog_value(session.catalog, "sales.line_revenue", kind="metric")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Distinct time-role capture must not access source rows")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        distinct_field = session.observe(
            {"value": revenue}, by=(month,), where=(scope, received_at.within(scope))
        )
        distinct_path = session.observe(
            {"value": line_revenue},
            by=(month,),
            where=(scope, raw_time.via((alternate,)).within(scope)),
        )
    for frame in (distinct_field, distinct_path):
        binding = source_graph(frame).signature.observation
        assert binding is not None and len(binding.coordinates.axes) == 1
        (contribution,) = binding.contributions
        assert contribution.time is not None and contribution.common_condition is not None
        condition_reads = [
            read for read in contribution.reads if read.field.ref.kind.value == "time_dimension"
        ]
        assert any(
            read.field != contribution.time.field or read.path != contribution.time.path
            for read in condition_reads
        )


def test_calendar_matches_exact_time_attribute_anchor(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + """
history = ms.entity(name='history', datasource=warehouse, source=md.table('history'),
    primary_key=['customer_id'], versioning=ms.snapshot(
        partition_field=ms.ref.time_dimension('sales.history.recorded_at'), timezone='UTC'))
history_id = ms.dimension_column(name='customer_id', entity=history, column='customer_id')
recorded_at = ms.time_dimension_column(name='recorded_at', entity=history, column='recorded_at')
history_link = ms.relationship(name='order_history', from_entity=orders, to_entity=history,
    keys=[ms.join_on(order_customer_id, history_id)])
"""
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("anchored-time-role", timezone="UTC")
    scope = session.time_scope(start="2026-08-01", end="2026-10-01")
    raw_time = catalog_value(session.catalog, "sales.history.recorded_at", kind="time_dimension")
    path = catalog_value(session.catalog, "sales.order_history", kind="relationship")
    amount = catalog_value(session.catalog, "sales.order.amount", kind="measure")
    first, second = session.instant("2026-08-01"), session.instant("2026-09-01")
    source_time = raw_time.via((path,)).at(first)
    metric = amount.sum(time=source_time)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Time-anchor validation must precede source reads")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        frame = session.observe(
            {"value": metric},
            by=(mv.grain("month"),),
            where=(scope, raw_time.via((path,)).at(second).within(scope)),
        )
    binding = source_graph(frame).signature.observation
    assert binding is not None
    (contribution,) = binding.contributions
    assert contribution.time is not None and contribution.time.at == first
    assert any(
        read.field == contribution.time.field
        and read.path == contribution.time.path
        and read.at == second
        for read in contribution.reads
    )


def test_empty_members_keep_overall_buckets(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("empty_domain")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert _query(case, during=calendar, members=_members(case)).persist().to_pandas()[
        "value"
    ].tolist() == [0, 0]
    assert _query(case, members=_members(case)).persist().to_pandas()["value"].tolist() == [0]


def test_explicit_business_coverage_masks_exported_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    saved = _query(case, during=calendar, members=_members(case)).persist()
    frame = saved.to_pandas()
    assert frame["value"].tolist() == [1000, 99]
    complete_until = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # Caller business coverage is an explicit pandas policy; Query has no four-state override.
    covered = frame["value"].where(frame["calendar:month"] < complete_until)
    assert covered.iloc[0] == 1000 and pd.isna(covered.iloc[1])
    assert not hasattr(saved, "rollup")


def test_invalid_axes_and_removed_entries(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    metric = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    members = session.members(catalog_get(session.catalog, "entity:sales.order", kind="entity"))
    with pytest.raises(AnalysisError):
        session.observe(
            {"value": metric},
            by=(members.axes[0], members.axes[0]),
            where=mv.in_population(members),
        )
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(mv.identity(()),))
    for invalid in (ms.ref.entity("sales.order"), ms.ref.entity("sales.customer")):
        with pytest.raises((AnalysisError, SemanticError)):
            session.observe({"value": metric}, by=(invalid,))  # type: ignore[arg-type]  # Exercise an Entity where a Field is required.
    observed = session.observe(
        {"value": metric}, by=(members.axes[0],), where=mv.in_population(members)
    )
    assert len(observed.persist().to_pandas()) == 5
    assert not hasattr(members, "group_by")


@pytest.mark.parametrize(
    "method", ["sum", "count", "mean", "weighted_mean", "median", "percentile", "count_distinct"]
)
def test_direct_aggregate_uses_selected_contributions(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'UPDATE "order" SET amount='
            + (
                "1"
                if method == "count_distinct"
                else "CASE WHEN customer_id='A' THEN 1 ELSE 100 END"
            )
        )
    session = case.session
    amount = catalog_value(session.catalog, "sales.order.amount", kind="measure")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="time_dimension")
    metric = (
        amount.sum(time=time)
        if method == "sum"
        else amount.count(time=time)
        if method == "count"
        else amount.mean(time=time)
        if method == "mean"
        else amount.weighted_mean(weight=amount, time=time)
        if method == "weighted_mean"
        else amount.median(time=time)
        if method == "median"
        else amount.quantile(0.9, time=time)
        if method == "percentile"
        else amount.count_distinct(time=time)
    )
    result = _query(case, metric=metric, members=_members(case)).persist()
    expected = {
        "sum": 200,
        "count": 101,
        "mean": 200 / 101,
        "weighted_mean": 50.5,
        "median": 1,
        "percentile": 1,
        "count_distinct": 1,
    }[method]
    assert result.to_pandas()["value"].tolist() == pytest.approx([expected])
    if method == "count_distinct":
        assert (
            _query(case, metric=metric, by=_members(case), members=_members(case))
            .persist()
            .to_pandas()["value"]
            .sum()
            == 2
        )
    if method in ("median", "count_distinct"):
        region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
        regions = session.read(_members(case), {"region": region})
        chosen = regions.where(regions["region"] == "east")
        selected = session.population_from(
            chosen,
            on=(
                (
                    chosen.axes[0],
                    catalog_get(session.catalog, "entity:sales.customer", kind="entity"),
                ),
            ),
        )
        completed = _query(
            case, metric=metric, by=selected.classify(by=(region,)), members=selected
        ).persist()
        assert _keyed(completed) == {("east",): 1}
        assert (
            session.persisted_frame(completed.artifact_id).to_pandas().equals(completed.to_pandas())
        )


def test_overall_ratio_differs_from_member_mean(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    metric = catalog_get(case.session.catalog, "metric:sales.aov_from_lines", kind="metric")
    overall = _query(case, metric=metric, members=_members(case))
    individual = _query(case, metric=metric, by=_members(case), members=_members(case))
    assert overall.persist().to_pandas()["value"].tolist() == pytest.approx([200 / 101])
    assert individual.reduce({"mean": individual["value"].mean()}).persist().to_pandas()[
        "mean"
    ].tolist() == [50.5]


@pytest.mark.release
@pytest.mark.slow
def test_overall_linear_and_fixed_group_recovery(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    assert _query(
        case, metric=revenue + revenue, members=_members(case), during=_august(case)
    ).persist().to_pandas()["value"].tolist() == [2000]
    channel = catalog_value(session.catalog, "sales.order.channel", kind="dimension")
    query = _query(case, by=(channel,), members=_members(case), during=_august(case))
    grouped, total = query.persist(), query.reduce({"value": query["value"].sum()}).persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert session.persisted_frame(grouped.artifact_id).to_pandas()["value"].sum() == 1000
    script = """
import sys
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
def forbidden(*args, **kwargs):
    raise AssertionError('terminal group read accessed source or Semantic')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1])
before = len(terminal_runs(session))
assert session.persisted_frame(sys.argv[2]).to_pandas()['value'].sum() == 1000
assert session.persisted_frame(sys.argv[3]).to_pandas()['value'].tolist() == [1000]
assert len(terminal_runs(session)) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, grouped.artifact_id, total.artifact_id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_complete_member_entity_keeps_compound_key(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "primary_key=['customer_id']", "primary_key=['customer_id', 'region']"
        )
    )
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ADD COLUMN region VARCHAR')
        connection.execute(
            'UPDATE "order" SET region=customer.region FROM customer WHERE "order".customer_id=customer.customer_id'
        )
    model.write_text(
        model.read_text()
        .replace(
            "buyer = ms.relationship",
            "order_region = ms.dimension_column(name='region', entity=orders, column='region')\nbuyer = ms.relationship",
        )
        .replace(
            "keys=[ms.join_on(order_customer_id, customer_id)]",
            "keys=[ms.join_on(order_customer_id, customer_id), ms.join_on(order_region, region)]",
        )
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("compound-grain", timezone="UTC")
    members = session.members(catalog_get(session.catalog, "entity:sales.customer", kind="entity"))
    observed = session.observe(
        {"value": catalog_get(session.catalog, "metric:sales.revenue", kind="metric")}, by=members
    )
    result = observed.persist()
    assert (
        len(
            result._local._manifest(result._require_stored("artifact_id"))
            .data.schema.axes[0]
            .columns
        )
        == 2
    )
    with duckdb.connect(str(case.database_path)) as connection:
        expected = set(connection.execute("SELECT customer_id,region FROM customer").fetchall())
    assert set(result.to_pandas().iloc[:, 0]) == expected


@pytest.mark.parametrize("logical_axes", [False, True])
def test_multiple_member_classifications_keep_complete_time_keys(
    analysis_dsl_case_factory: DslCaseFactory, logical_axes: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    identity = catalog_value(session.catalog, "sales.customer.customer_id", kind="field")
    members = _members(case)
    identities = session.read(members, {"identity": identity})
    if logical_axes:
        chosen = identities.where(~identities["identity"].is_null())
        members = session.population_from(
            chosen,
            on=(
                (
                    chosen.axes[0],
                    catalog_get(session.catalog, "entity:sales.customer", kind="entity"),
                ),
            ),
        )
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    domain = mv.product(members.classify(by=(region, identity)), calendar)
    observed = _query(case, by=domain, members=members)
    saved = observed.persist()
    frame = saved.to_pandas()
    assert len(frame) == 8
    actual = {key: value for key, value in _keyed(saved).items() if value != 0}
    august, september = (
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert actual == {
        ("east", "A", august): 450,
        ("east", "A", september): 99,
        ("east", "B", august): 150,
        ("south", "C", august): 400,
    }
    incomplete = identities.where(identities["identity"] == "A")
    paired = (
        identities.align(incomplete, on=tuple(zip(identities.axes, incomplete.axes, strict=True)))
        .compute()
        .to_pandas()
    )
    assert paired["left_present"].all()
    assert (~paired["right_present"]).any()
    if logical_axes:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
        assert session.persisted_frame(saved.artifact_id).to_pandas().equals(frame)


@pytest.mark.release
def test_member_groups_preserve_live_and_fixed_comparisons(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="dimension")
    domain = _members(case).classify(by=(region,))

    def endpoint(month: int) -> mv.AnalysisFrame:
        return _query(
            case,
            by=domain,
            members=_members(case),
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
        )

    current, baseline = endpoint(8), endpoint(7)
    comparison = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
                match=mv.ordinal(),
            )
        }
    )
    saved = comparison.persist()
    expected = {("east",): -20, ("south",): -50, ("west",): 0}
    assert _keyed(saved, "reference.absolute.value") == expected
    self_comparison = current.compare(
        {"reference": mv.baseline(current, on=tuple(zip(current.axes, current.axes, strict=True)))}
    ).persist()
    assert self_comparison.to_pandas()["reference.absolute.value"].tolist() == [0, 0, 0]
    fixed_current, fixed_baseline = current.persist(), baseline.persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    left, right = (
        session.persisted_frame(fixed_current.artifact_id).to_pandas(),
        session.persisted_frame(fixed_baseline.artifact_id).to_pandas(),
    )
    assert dict(
        zip(
            left[
                next(
                    axis.name.rsplit(".", 1)[-1]
                    for axis in fixed_current.axes
                    if axis.role in ("identity", "field")
                )
            ],
            left["value"].astype(object) - right["value"].astype(object),
            strict=True,
        )
    ) == {key[0]: value for key, value in expected.items()}
    assert session.persisted_frame(saved.artifact_id).to_pandas().equals(saved.to_pandas())
