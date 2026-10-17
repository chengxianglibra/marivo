"""Public observation grains retain complete contribution tuples and domains."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.semantic.errors import SemanticError
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


def _members(case: DslCase) -> mv.Population:
    return case.session.members(ms.ref.entity("sales.customer"))


def _august(case: DslCase) -> mv.TimeScope:
    return case.session.time_scope(start="2026-08-01", end="2026-09-01")


def _query(
    case: DslCase,
    *,
    metric: Deferred | None = None,
    by: tuple[Deferred | mv.MemberAxis, ...] | mv.Population = (),
    members: mv.Population | None = None,
    during: mv.TimeScope | mv.CalendarAxis | None = None,
) -> mv.Query:
    return case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue") if metric is None else metric},
        during=during,
        by=by,
        members=members,
    )


def _keyed(frame: pd.DataFrame, name: str = "value") -> dict[tuple[object, ...], object]:
    coordinates = [column for column in frame.columns if column[0] == "coordinate"]
    return {
        tuple(row[column] for column in coordinates): row[("value", name)]
        for _, row in frame.iterrows()
    }


def test_overall_and_member_observations(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    members = _members(case)
    total = _query(case, members=members, during=_august(case))
    assert total.execute().to_pandas()[("value", "value")].tolist() == [1000]
    assert case.session.query(total.population()).execute().to_pandas().shape == (1, 0)
    with pytest.raises(AnalysisError):
        total.population().project(by=(mv.member(),))
    individual = _query(case, by=members, members=members, during=_august(case))
    fixed = individual.execute()
    assert _keyed(fixed.to_pandas()) == {("A",): 450, ("B",): 150, ("C",): 400, ("D",): 0}
    keys = case.session.query(individual.population()).execute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["A", "B", "C", "D"]


def test_member_and_contribution_grouping(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = _members(case)
    region = session.catalog.fields.get("sales.customer.region")
    channel = session.catalog.fields.get("sales.order.channel")
    grouped = _query(case, by=members.classify(by=(region,)), members=members, during=_august(case))
    assert _keyed(grouped.execute().to_pandas()) == {("east",): 600, ("south",): 400, ("west",): 0}
    channels = _query(case, by=(channel,), members=members, during=_august(case))
    assert _keyed(channels.execute().to_pandas()) == {("mobile",): 150, ("web",): 850}
    assert channels.rollup().execute().to_pandas()[("value", "value")].tolist() == [1000]
    tuples = (
        _query(case, by=(region, channel), members=members, during=_august(case))
        .execute()
        .to_pandas()
    )
    assert _keyed(tuples) == {("east", "web"): 450, ("east", "mobile"): 150, ("south", "web"): 400}
    classifications = session.read(members, {"region": region})
    selected = classifications.where(classifications["region"] == "east").population()
    saved = _query(
        case, by=selected.classify(by=(region,)), members=selected, during=_august(case)
    ).execute()
    assert _keyed(saved.to_pandas()) == {("east",): 600}
    assert session.artifact(saved.id).to_pandas().equals(saved.to_pandas())


@pytest.mark.parametrize("member_first", [True, False])
def test_member_channel_axes_keep_identity_and_selected_scope(
    analysis_dsl_case_factory: DslCaseFactory, member_first: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
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
    assert _keyed(observed.execute().to_pandas()) == expected
    assert observed.rollup().execute().to_pandas()[("value", "value")].tolist() == [1099]
    region = session.catalog.fields.get("sales.customer.region")
    classifications = session.read(_members(case), {"region": region})
    selected = classifications.where(classifications["region"] == "east").population()
    result = _query(case, by=selected, members=selected).execute().to_pandas()
    assert _keyed(result) == {("A",): 626, ("B",): 150}


def test_component_domains_union_complete_tuples(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    region = session.catalog.fields.get("sales.customer.region")
    difference = revenue.where(channel == "web") - revenue.where(channel == "mobile")
    grouped = _query(
        case, metric=difference, by=(region, channel), members=_members(case), during=_august(case)
    )
    assert _keyed(grouped.execute().to_pandas()) == {
        ("east", "web"): 450,
        ("east", "mobile"): -150,
        ("south", "web"): 400,
    }
    assert grouped.rollup().execute().to_pandas()[("value", "value")].tolist() == [700]


@pytest.mark.parametrize("keep_member", [False, True])
def test_member_classification_survives_rollup_for_additive_comparison(
    analysis_dsl_case_factory: DslCaseFactory, keep_member: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    members = _members(case)

    def endpoint(month: int) -> mv.Query:
        query = _query(
            case,
            by=(mv.member(), region) if keep_member else members.classify(by=(region,)),
            members=members,
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
        )
        return query.rollup(by=(region,))

    comparison = endpoint(8).compare(endpoint(7))
    selected = comparison.where(comparison["value.absolute"] != 0)
    selected = selected.select({"value": selected["value.absolute"]})
    saved = selected.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    frame = session.artifact(saved.id).to_pandas()
    assert _keyed(frame) == {("east",): 523, ("south",): 400}
    assert frame[("value", "value")].sum() == 923


def test_grid_observation_keeps_time_without_member_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    observed = _query(case, during=calendar, members=_members(case))
    assert observed.execute().to_pandas()[("value", "value")].tolist() == [1000, 99]
    region = session.catalog.fields.get("sales.customer.region")
    region_domain = mv.product(_members(case).classify(by=(region,)), calendar)
    region_rows = _query(case, by=region_domain, members=_members(case)).execute().to_pandas()
    assert len(region_rows) == 6
    channel = session.catalog.fields.get("sales.order.channel")
    rows = (
        _query(case, by=(channel,), during=calendar, members=_members(case)).execute().to_pandas()
    )
    assert len(rows) == 4
    assert sorted(rows[("value", "value")]) == [0, 99, 150, 850]


def test_empty_members_keep_overall_buckets(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("empty_domain")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert _query(case, during=calendar, members=_members(case)).execute().to_pandas()[
        ("value", "value")
    ].tolist() == [0, 0]
    assert _query(case, members=_members(case)).execute().to_pandas()[
        ("value", "value")
    ].tolist() == [0]


def test_explicit_business_coverage_masks_exported_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    saved = _query(case, during=calendar, members=_members(case)).execute()
    frame = saved.to_pandas()
    assert frame[("value", "value")].tolist() == [1000, 99]
    complete_until = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # Caller business coverage is an explicit pandas policy; Query has no four-state override.
    covered = frame[("value", "value")].where(frame.iloc[:, 0] < complete_until)
    assert covered.iloc[0] == 1000 and pd.isna(covered.iloc[1])
    assert not hasattr(saved, "rollup")


def test_invalid_axes_and_removed_entries(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    members = session.members(ms.ref.entity("sales.order"))
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, members=members, by=(mv.member(), mv.member()))
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(mv.member(),))
    for invalid in (ms.ref.entity("sales.order"), ms.ref.entity("sales.customer")):
        with pytest.raises((AnalysisError, SemanticError)):
            session.observe({"value": metric}, by=(invalid,))  # type: ignore[arg-type]  # Exercise an Entity where a Field is required.
    observed = session.observe({"value": metric}, members=members, by=(mv.member(),))
    assert len(observed.execute().to_pandas()) == 5
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
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
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
    result = _query(case, metric=metric, members=_members(case)).execute()
    expected = {
        "sum": 200,
        "count": 101,
        "mean": 200 / 101,
        "weighted_mean": 50.5,
        "median": 1,
        "percentile": 1,
        "count_distinct": 1,
    }[method]
    assert result.to_pandas()[("value", "value")].tolist() == pytest.approx([expected])
    if method == "count_distinct":
        assert (
            _query(case, metric=metric, by=_members(case), members=_members(case))
            .execute()
            .to_pandas()[("value", "value")]
            .sum()
            == 2
        )
    if method in ("median", "count_distinct"):
        region = session.catalog.fields.get("sales.customer.region")
        regions = session.read(_members(case), {"region": region})
        selected = regions.where(regions["region"] == "east").population()
        completed = _query(
            case, metric=metric, by=selected.classify(by=(region,)), members=selected
        ).execute()
        assert _keyed(completed.to_pandas()) == {("east",): 1}
        assert session.artifact(completed.id).to_pandas().equals(completed.to_pandas())


def test_overall_ratio_differs_from_member_mean(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    metric = case.session.catalog.metrics.get("sales.aov_from_lines")
    overall = _query(case, metric=metric, members=_members(case))
    individual = _query(case, metric=metric, by=_members(case), members=_members(case))
    assert overall.execute().to_pandas()[("value", "value")].tolist() == pytest.approx([200 / 101])
    assert individual.reduce({"mean": individual["value"].mean()}).execute().to_pandas()[
        ("value", "mean")
    ].tolist() == [50.5]


@pytest.mark.slow
def test_overall_linear_and_fixed_group_recovery(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    assert _query(
        case, metric=revenue + revenue, members=_members(case), during=_august(case)
    ).execute().to_pandas()[("value", "value")].tolist() == [2000]
    channel = session.catalog.fields.get("sales.order.channel")
    query = _query(case, by=(channel,), members=_members(case), during=_august(case))
    grouped, total = query.execute(), query.rollup().execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert session.artifact(grouped.id).to_pandas()[("value", "value")].sum() == 1000
    script = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
def forbidden(*args, **kwargs):
    raise AssertionError('terminal group read accessed source or Semantic')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1],by='id')
before = len(session.runs().items)
assert session.artifact(sys.argv[2]).to_pandas()[('value','value')].sum() == 1000
assert session.artifact(sys.argv[3]).to_pandas()[('value','value')].tolist() == [1000]
assert len(session.runs().items) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, grouped.id, total.id],
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
    session = mv.session.get_or_create("compound-grain", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.customer"))
    observed = session.observe({"value": session.catalog.metrics.get("sales.revenue")}, by=members)
    result = observed.execute()
    assert len(result._manifest.data.schema.axes[0].columns) == 2
    with duckdb.connect(str(case.database_path)) as connection:
        expected = set(connection.execute("SELECT customer_id,region FROM customer").fetchall())
    assert set(result.to_pandas().iloc[:, 0]) == expected


@pytest.mark.parametrize("logical_axes", [False, True])
def test_multiple_member_classifications_keep_complete_time_keys(
    analysis_dsl_case_factory: DslCaseFactory, logical_axes: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    identity = session.catalog.fields.get("sales.customer.customer_id")
    members = _members(case)
    identities = session.read(members, {"identity": identity})
    if logical_axes:
        members = identities.where(~identities["identity"].is_null()).population()
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    domain = mv.product(members.classify(by=(region, identity)), calendar)
    observed = _query(case, by=domain, members=members)
    saved = observed.execute()
    frame = saved.to_pandas()
    assert len(frame) == 8
    actual = {key: value for key, value in _keyed(frame).items() if value != 0}
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
    with pytest.raises(AnalysisError):
        identities.align(incomplete).execute()
    if logical_axes:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
        assert session.artifact(saved.id).to_pandas().equals(frame)


def test_member_groups_preserve_live_and_fixed_comparisons(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    domain = _members(case).classify(by=(region,))

    def endpoint(month: int) -> mv.Query:
        return _query(
            case,
            by=domain,
            members=_members(case),
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
        )

    current, baseline = endpoint(8), endpoint(7)
    comparison = current.compare(baseline)
    saved = comparison.execute()
    expected = {("east",): -20, ("south",): -50, ("west",): 0}
    assert _keyed(saved.to_pandas(), "value.absolute") == expected
    self_comparison = current.compare(current).execute()
    assert self_comparison.to_pandas()[("value", "value.absolute")].tolist() == [0, 0, 0]
    fixed_current, fixed_baseline = current.execute(), baseline.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    left, right = (
        session.artifact(fixed_current.id).to_pandas(),
        session.artifact(fixed_baseline.id).to_pandas(),
    )
    assert dict(
        zip(left.iloc[:, 0], left[("value", "value")] - right[("value", "value")], strict=True)
    ) == {key[0]: value for key, value in expected.items()}
    assert session.artifact(saved.id).to_pandas().equals(saved.to_pandas())
