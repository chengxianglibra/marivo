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
    return case.session.members(case.session.catalog.entities.get("sales.customer"))


def _august(case: DslCase) -> mv.TimeScope:
    return case.session.time_scope(start="2026-08-01", end="2026-09-01")


def _query(
    case: DslCase,
    *,
    metric: Deferred | None = None,
    by: tuple[Deferred | mv.Grouping, ...] | mv.Population = (),
    members: mv.Population | None = None,
    during: mv.TimeScope | mv.CalendarAxis | None = None,
) -> mv.AnalysisFrame:
    domain: tuple[Deferred | mv.Grouping | mv.TimeScope, ...] | mv.Population = by
    if during is not None:
        domain = mv.product(during, by) if isinstance(by, mv.Population) else (during, *by)
    return case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue") if metric is None else metric},
        by=domain,
        where=mv.in_population(members) if members is not None else None,
    )


def _keyed(frame: pd.DataFrame, name: str = "value") -> dict[tuple[object, ...], object]:
    coordinates: list[tuple[str, str]] = []
    for column in frame.columns:
        assert isinstance(column, tuple) and len(column) == 2
        kind, column_name = column
        assert isinstance(kind, str) and isinstance(column_name, str)
        if kind == "coordinate":
            coordinates.append((kind, column_name))
    calendars: dict[tuple[str, str], dict[object, object]] = {}
    for axis in frame.attrs["axes"]:
        if axis["role"] in ("interval", "paired"):
            temporal_column = ("coordinate", axis["token"])
            assert frame[temporal_column].nunique() <= 1
            coordinates.remove(temporal_column)
        if axis["role"] == "calendar":
            descriptor = axis["descriptor"]
            directory = next(
                part["rows"]
                for part in frame.attrs["frozen_coordinates"]
                if part["name"] == descriptor["frozen"]["directory_name"]
            )
            key = descriptor["columns"][0]["slot_id"]
            calendars[("coordinate", axis["token"])] = dict(
                zip(directory[key], directory["actual_start"], strict=True)
            )
    return {
        tuple(
            calendars[column][row[column]] if column in calendars else row[column]
            for column in coordinates
        ): row[("value", name)]
        for _, row in frame.iterrows()
    }


def test_overall_and_member_observations(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    members = _members(case)
    total = _query(case, members=members, during=_august(case))
    assert total.persist().to_pandas()[("value", "value")].tolist() == [1000]
    assert case.session.frame(
        case.session.population_from(total, on=())
    ).persist().to_pandas().shape == (1, 0)
    with pytest.raises(AnalysisError):
        case.session.population_from(total, on=()).project(by=(members.axes[0],))
    individual = _query(case, by=members, members=members, during=_august(case))
    fixed = individual.persist()
    assert _keyed(fixed.to_pandas()) == {("A",): 450, ("B",): 150, ("C",): 400, ("D",): 0}
    keys = (
        case.session.frame(
            case.session.population_from(
                individual,
                on=((individual.axes[1], case.session.catalog.entities.get("sales.customer")),),
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
    region = session.catalog.fields.get("sales.customer.region")
    channel = session.catalog.fields.get("sales.order.channel")
    grouped = _query(case, by=members.classify(by=(region,)), members=members, during=_august(case))
    assert _keyed(grouped.persist().to_pandas()) == {("east",): 600, ("south",): 400, ("west",): 0}
    channels = _query(case, by=(channel,), members=members, during=_august(case))
    assert _keyed(channels.persist().to_pandas()) == {("mobile",): 150, ("web",): 850}
    tuples = (
        _query(case, by=(region, channel), members=members, during=_august(case))
        .persist()
        .to_pandas()
    )
    assert _keyed(tuples) == {("east", "web"): 450, ("east", "mobile"): 150, ("south", "web"): 400}
    classifications = session.read(members, {"region": region})
    chosen = classifications.where(classifications["region"] == "east")
    selected = session.population_from(
        chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.customer")),)
    )
    saved = _query(
        case, by=selected.classify(by=(region,)), members=selected, during=_august(case)
    ).persist()
    assert _keyed(saved.to_pandas()) == {("east",): 600}
    assert session.persisted_frame(saved.artifact.id).to_pandas().equals(saved.to_pandas())


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
    assert _keyed(observed.persist().to_pandas()) == expected
    assert observed.reduce({"value": observed["value"].sum()}).persist().to_pandas()[
        ("value", "value")
    ].tolist() == [1099]
    region = session.catalog.fields.get("sales.customer.region")
    classifications = session.read(_members(case), {"region": region})
    chosen = classifications.where(classifications["region"] == "east")
    selected = session.population_from(
        chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.customer")),)
    )
    result = _query(case, by=selected, members=selected).persist().to_pandas()
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
    assert _keyed(grouped.persist().to_pandas()) == {
        ("east", "web"): 450,
        ("east", "mobile"): -150,
        ("south", "web"): 400,
    }
    assert grouped.reduce({"value": grouped["value"].sum()}).persist().to_pandas()[
        ("value", "value")
    ].tolist() == [700]


@pytest.mark.parametrize("keep_member", [False, True])
def test_member_classification_survives_rollup_for_additive_comparison(
    analysis_dsl_case_factory: DslCaseFactory, keep_member: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
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
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    )
    selected = comparison.where(comparison["reference.absolute.value"] != 0)
    selected = selected.select({"value": selected["reference.absolute.value"]})
    saved = selected.persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    frame = session.persisted_frame(saved.artifact.id).to_pandas()
    assert _keyed(frame) == {("east",): 523, ("south",): 400}
    assert frame[("value", "value")].sum() == 923


def test_grid_observation_keeps_time_without_member_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    observed = _query(case, during=calendar, members=_members(case))
    assert observed.persist().to_pandas()[("value", "value")].tolist() == [1000, 99]
    region = session.catalog.fields.get("sales.customer.region")
    region_domain = mv.product(_members(case).classify(by=(region,)), calendar)
    region_rows = _query(case, by=region_domain, members=_members(case)).persist().to_pandas()
    assert len(region_rows) == 6
    channel = session.catalog.fields.get("sales.order.channel")
    rows = (
        _query(case, by=(channel,), during=calendar, members=_members(case)).persist().to_pandas()
    )
    assert len(rows) == 4
    assert sorted(rows[("value", "value")]) == [0, 99, 150, 850]


def test_empty_members_keep_overall_buckets(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("empty_domain")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert _query(case, during=calendar, members=_members(case)).persist().to_pandas()[
        ("value", "value")
    ].tolist() == [0, 0]
    assert _query(case, members=_members(case)).persist().to_pandas()[
        ("value", "value")
    ].tolist() == [0]


def test_explicit_business_coverage_masks_exported_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    saved = _query(case, during=calendar, members=_members(case)).persist()
    frame = saved.to_pandas()
    assert frame[("value", "value")].tolist() == [1000, 99]
    complete_until = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # Caller business coverage is an explicit pandas policy; Query has no four-state override.
    calendar = frame.attrs["axes"][0]
    directory = next(
        part["rows"]
        for part in frame.attrs["frozen_coordinates"]
        if part["name"] == calendar["descriptor"]["frozen"]["directory_name"]
    )
    key = calendar["descriptor"]["columns"][0]["slot_id"]
    complete_keys = directory.loc[directory["actual_start"] < complete_until, key]
    covered = frame[("value", "value")].where(frame.iloc[:, 0].isin(complete_keys))
    assert covered.iloc[0] == 1000 and pd.isna(covered.iloc[1])
    assert not hasattr(saved, "rollup")


def test_invalid_axes_and_removed_entries(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    members = session.members(session.catalog.entities.get("sales.order"))
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
    assert result.to_pandas()[("value", "value")].tolist() == pytest.approx([expected])
    if method == "count_distinct":
        assert (
            _query(case, metric=metric, by=_members(case), members=_members(case))
            .persist()
            .to_pandas()[("value", "value")]
            .sum()
            == 2
        )
    if method in ("median", "count_distinct"):
        region = session.catalog.fields.get("sales.customer.region")
        regions = session.read(_members(case), {"region": region})
        chosen = regions.where(regions["region"] == "east")
        selected = session.population_from(
            chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.customer")),)
        )
        completed = _query(
            case, metric=metric, by=selected.classify(by=(region,)), members=selected
        ).persist()
        assert _keyed(completed.to_pandas()) == {("east",): 1}
        assert (
            session.persisted_frame(completed.artifact.id).to_pandas().equals(completed.to_pandas())
        )


def test_overall_ratio_differs_from_member_mean(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    metric = case.session.catalog.metrics.get("sales.aov_from_lines")
    overall = _query(case, metric=metric, members=_members(case))
    individual = _query(case, metric=metric, by=_members(case), members=_members(case))
    assert overall.persist().to_pandas()[("value", "value")].tolist() == pytest.approx([200 / 101])
    assert individual.reduce({"mean": individual["value"].mean()}).persist().to_pandas()[
        ("value", "mean")
    ].tolist() == [50.5]


@pytest.mark.release
@pytest.mark.slow
def test_overall_linear_and_fixed_group_recovery(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    assert _query(
        case, metric=revenue + revenue, members=_members(case), during=_august(case)
    ).persist().to_pandas()[("value", "value")].tolist() == [2000]
    channel = session.catalog.fields.get("sales.order.channel")
    query = _query(case, by=(channel,), members=_members(case), during=_august(case))
    grouped, total = query.persist(), query.reduce({"value": query["value"].sum()}).persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert (
        session.persisted_frame(grouped.artifact.id).to_pandas()[("value", "value")].sum() == 1000
    )
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
assert session.persisted_frame(sys.argv[2]).to_pandas()[('value','value')].sum() == 1000
assert session.persisted_frame(sys.argv[3]).to_pandas()[('value','value')].tolist() == [1000]
assert len(terminal_runs(session)) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, grouped.artifact.id, total.artifact.id],
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
    members = session.members(session.catalog.entities.get("sales.customer"))
    observed = session.observe({"value": session.catalog.metrics.get("sales.revenue")}, by=members)
    result = observed.persist()
    assert len(result.artifact._manifest.data.schema.axes[0].columns) == 2
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
        chosen = identities.where(~identities["identity"].is_null())
        members = session.population_from(
            chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.customer")),)
        )
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    domain = mv.product(members.classify(by=(region, identity)), calendar)
    observed = _query(case, by=domain, members=members)
    saved = observed.persist()
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
    paired = (
        identities.align(incomplete, on=tuple(zip(identities.axes, incomplete.axes, strict=True)))
        .compute()
        .to_pandas()
    )
    assert paired[("presence", "left_present")].all()
    assert (~paired[("presence", "right_present")]).any()
    if logical_axes:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
        assert session.persisted_frame(saved.artifact.id).to_pandas().equals(frame)


@pytest.mark.release
def test_member_groups_preserve_live_and_fixed_comparisons(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
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
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    saved = comparison.persist()
    expected = {("east",): -20, ("south",): -50, ("west",): 0}
    assert _keyed(saved.to_pandas(), "reference.absolute.value") == expected
    self_comparison = current.compare(
        {"reference": current},
        on={"reference": tuple(zip(current.axes, current.axes, strict=True))},
    ).persist()
    assert self_comparison.to_pandas()[("value", "reference.absolute.value")].tolist() == [0, 0, 0]
    fixed_current, fixed_baseline = current.persist(), baseline.persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    left, right = (
        session.persisted_frame(fixed_current.artifact.id).to_pandas(),
        session.persisted_frame(fixed_baseline.artifact.id).to_pandas(),
    )
    assert dict(
        zip(
            left.iloc[:, 1],
            left[("value", "value")].astype(object) - right[("value", "value")].astype(object),
            strict=True,
        )
    ) == {key[0]: value for key, value in expected.items()}
    assert session.persisted_frame(saved.artifact.id).to_pandas().equals(saved.to_pandas())
