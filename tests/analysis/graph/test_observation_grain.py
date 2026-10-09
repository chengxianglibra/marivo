"""Public observation grains are computed from original contributions."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
def test_overall_and_member_observations(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = ms.ref.entity("sales.customer")
    members = case.session.members(entity)
    metric = ms.ref.metric("sales.revenue")
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship("sales.order_buyer")
    total = members.observe(metric, during=window, via=via)
    assert total._node.root.signature.domain.kind == "singleton"
    assert total.execute().to_pandas()["value"].tolist() == [1000]
    assert not any(action.call == "relation.members()" for action in total.contract().actions)
    with pytest.raises(AnalysisError):
        total.members()
    individual = members.observe(metric, during=window, via=via, by=(entity,))
    frame = individual.execute().to_pandas()
    assert frame["value"].dropna().tolist() == [450, 150, 400]
    assert len(frame) == 4


@pytest.mark.runtime
def test_member_and_contribution_grouping(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric = ms.ref.metric("sales.revenue")
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship("sales.order_buyer")
    region = ms.ref.dimension("sales.customer.region")
    channel = ms.ref.dimension("sales.order.channel")
    grouped = members.observe(metric, during=window, via=via, by=(region,))
    assert grouped.execute().to_pandas().set_index("group")["value"].dropna().to_dict() == {
        "east": 600,
        "south": 400,
    }
    channels = members.observe(metric, during=window, via=via, by=(channel,))
    assert channels.execute().to_pandas().set_index("group")["value"].to_dict() == {
        "web": 850,
        "mobile": 150,
    }
    assert channels.rollup().execute().to_pandas()["value"].tolist() == [1000]
    tuples = (
        members.observe(metric, during=window, via=via, by=(region, channel)).execute().to_pandas()
    )
    assert tuples.set_index(["group", "coord_0"])["value"].to_dict() == {
        ("east", "web"): 450,
        ("east", "mobile"): 150,
        ("south", "web"): 400,
    }
    classification = members.read(region)
    assert isinstance(classification, mv.LogicalCategoryRelation)
    logical_groups = members.observe(metric, during=window, via=via, by=(classification,)).execute()
    assert logical_groups.to_pandas().equals(grouped.execute().to_pandas())
    selected = classification.where(classification.value.eq("east")).members()
    grouped = selected.observe(metric, during=window, via=via, by=(region,)).execute()
    assert grouped.to_pandas().set_index("group")["value"].dropna().to_dict() == {"east": 600}
    assert len(grouped.to_pandas()) == 1
    restored = case.session.artifact(grouped.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedNumericRelation)
    assert restored.to_pandas().equals(grouped.to_pandas())


@pytest.mark.runtime
def test_component_domains_union_complete_tuples(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + """
web_total = ms.aggregate(name='web_total', measure=amount, agg='sum', time=ordered_at,
    filter=ms.where(channel='web'), empty=ms.empty.zero())
mobile_total = ms.aggregate(name='mobile_total', measure=amount, agg='sum', time=ordered_at,
    filter=ms.where(channel='mobile'), empty=ms.empty.zero())
"""
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("tuple-union", report_timezone="UTC")
    difference = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.web_total")],
        subtract=[ms.ref.metric("sales.mobile_total")],
        label="channel balance",
    )
    grouped = session.members(ms.ref.entity("sales.customer")).observe(
        difference,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.dimension("sales.customer.region"), ms.ref.dimension("sales.order.channel")),
    )
    rows = grouped.execute().to_pandas()
    assert rows.set_index(["group", "coord_0"])["value"].to_dict() == {
        ("east", "web"): 450,
        ("east", "mobile"): -150,
        ("south", "web"): 400,
    }
    assert grouped.rollup().execute().to_pandas()["value"].tolist() == [700]


@pytest.mark.runtime
@pytest.mark.parametrize("keep_member", [False, True])
def test_member_classification_survives_rollup_for_fixed_attribution(
    analysis_dsl_case_factory: DslCaseFactory, keep_member: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = ms.ref.entity("sales.customer")
    region = ms.ref.dimension("sales.customer.region")
    members = case.session.members(entity)

    def endpoint(month: int) -> mv.LogicalRolledNumericRelation:
        return members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(entity, region) if keep_member else (region,),
        ).rollup()

    fixed = endpoint(8).compare(endpoint(7)).execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    attributed = fixed.attribute(axes=(region,)).execute()
    rows = attributed.contribution.to_pandas()
    assert rows.set_index("coord_0")["value"].to_dict() == {"east": 523, "south": 400}


@pytest.mark.runtime
def test_grid_observation_keeps_time_without_member_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=ms.ref.relationship("sales.order_buyer"),
    )
    assert len(observed._node.root.signature.domain.instance_key) == 1
    frame = observed.execute().to_pandas()
    assert len(frame) == 2
    assert frame["value"].iloc[0] == 1000
    grouped = members.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.dimension("sales.customer.region"),),
    ).execute()
    assert len(grouped.to_pandas()) == 6
    contribution_groups = (
        members.observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.dimension("sales.order.channel"),),
        )
        .execute()
        .to_pandas()
    )
    assert len(contribution_groups) == 4
    assert contribution_groups["cell_tag"].tolist().count("null") == 1


@pytest.mark.runtime
def test_empty_members_keep_overall_buckets(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("empty_domain")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    fixed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=ms.ref.relationship("sales.order_buyer"),
    ).execute()
    assert len(fixed.to_pandas()) == 2
    assert fixed.to_pandas()["cell_tag"].tolist() == ["null", "null"]
    total = members.observe(
        ms.ref.metric("sales.revenue"), via=ms.ref.relationship("sales.order_buyer")
    ).execute()
    assert len(total.to_pandas()) == 1
    assert total.to_pandas()["cell_tag"].tolist() == ["null"]


@pytest.mark.runtime
def test_overall_bucket_coverage_marks_unknown_after_aggregation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            complete_during=(
                mv.time_scope(
                    start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    end=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ),
            ),
        )
        .execute()
    )
    rows = fixed.to_pandas()
    assert rows["cell_tag"].tolist() == ["defined", "unknown"]
    assert rows["value"].iloc[0] == 1000
    assert rows["cell_reason"].iloc[1] == "insufficient_business_coverage"
    with pytest.raises(AnalysisError, match="business"):
        fixed.rollup()


@pytest.mark.runtime
def test_invalid_axes_and_removed_entries(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = ms.ref.entity("sales.order")
    members = case.session.members(entity)
    metric = ms.ref.metric("sales.revenue")
    with pytest.raises(AnalysisError):
        members.observe(metric, by=(entity, entity))
    with pytest.raises(AnalysisError):
        members.observe(metric, by=(ms.ref.entity("sales.customer"),))
    assert not hasattr(members, "group_by")


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method", ["sum", "count", "mean", "weighted_mean", "median", "percentile", "count_distinct"]
)
def test_direct_aggregate_uses_selected_contributions(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    with duckdb.connect(str(case.database_path)) as con:
        con.execute(
            'UPDATE "order" SET amount='
            + (
                "1"
                if method == "count_distinct"
                else "CASE WHEN customer_id='A' THEN 1 ELSE 100 END"
            )
        )
    model = case.root / "models/semantic/sales/models.py"
    declaration = (
        "ms.count(name='grain_metric', entity=orders, time=ordered_at)"
        if method == "count"
        else "ms.weighted_mean(name='grain_metric', value=amount, weight=amount)"
        if method == "weighted_mean"
        else "ms.aggregate(name='grain_metric', measure=amount, agg=('percentile', 0.9), time=ordered_at)"
        if method == "percentile"
        else f"ms.aggregate(name='grain_metric', measure=amount, agg={method!r}, time=ordered_at)"
    )
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + f"\ngrain_metric = {declaration}\n"
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create(name=f"grain-{method}", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.customer"))
    metric = ms.ref.metric("sales.grain_metric")
    routes = ms.ref.relationship("sales.order_buyer")
    result = members.observe(metric, via=routes).execute()
    expected = {
        "sum": 200,
        "count": 101,
        "mean": 200 / 101,
        "weighted_mean": 50.5,
        "median": 1,
        "percentile": 1,
        "count_distinct": 1,
    }[method]
    assert result.to_pandas()["value"].iloc[0] == pytest.approx(expected)
    if method == "count_distinct":
        individual = members.observe(
            metric, via=routes, by=(ms.ref.entity("sales.customer"),)
        ).execute()
        assert individual.to_pandas()["value"].sum() == 2
    if method in ("median", "percentile", "count_distinct"):
        with pytest.raises(AnalysisError):
            result.rollup()
    if method in ("median", "count_distinct"):
        region = ms.ref.dimension("sales.customer.region")
        classification = members.read(region)
        assert isinstance(classification, mv.LogicalCategoryRelation)
        selected = classification.where(classification.value.eq("east")).members()
        completed = selected.observe(metric, via=routes, by=(region,)).execute()
        rows = completed.to_pandas().set_index("group")
        assert rows.loc["east", "value"] == 1
        assert rows.index.tolist() == ["east"]
        recovered = session.artifact(completed.state.artifact_ref)
        assert isinstance(recovered, mv.MaterializedNumericRelation)
        with pytest.raises(AnalysisError):
            recovered.rollup()


@pytest.mark.runtime
def test_overall_ratio_differs_from_member_mean(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    entity = ms.ref.entity("sales.customer")
    members = case.session.members(entity)
    routes = mv.routes(
        mv.route(
            ms.ref.entity("sales.order_line"),
            through=(
                ms.ref.relationship("sales.line_order"),
                ms.ref.relationship("sales.order_buyer"),
            ),
        ),
        mv.route(ms.ref.entity("sales.order"), through=(ms.ref.relationship("sales.order_buyer"),)),
    )
    overall = members.observe(ms.ref.metric("sales.aov_from_lines"), via=routes)
    individual = members.observe(ms.ref.metric("sales.aov_from_lines"), via=routes, by=(entity,))
    assert overall.execute().to_pandas()["value"].iloc[0] == pytest.approx(200 / 101)
    assert individual.summarize(mv.mean()).execute().to_pandas()["value"].iloc[0] == 50.5


@pytest.mark.runtime
def test_overall_linear_and_fixed_group_recovery(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = ms.ref.entity("sales.customer")
    metric = ms.ref.metric("sales.revenue")
    members = case.session.members(entity)
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship("sales.order_buyer")
    linear = mv.runtime_metric.linear(add=[metric, metric], label="twice")
    assert members.observe(linear, during=window, via=via).execute().to_pandas()[
        "value"
    ].tolist() == [2000]
    grouped = members.observe(
        metric, during=window, via=via, by=(ms.ref.dimension("sales.order.channel"),)
    ).execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    recovered = case.session.artifact(grouped.state.artifact_ref)
    assert isinstance(recovered, mv.MaterializedNumericRelation)
    assert recovered.rollup().execute().to_pandas()["value"].tolist() == [1000]
    script = """
import sys
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError('Recovered group accessed its source')
SourceSession.batches = forbidden
session = mv.session.resume(sys.argv[1], by='id')
grouped = session.artifact(sys.argv[2])
assert isinstance(grouped, mv.MaterializedNumericRelation)
assert grouped.rollup().execute().to_pandas()['value'].tolist() == [1000]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, grouped.state.artifact_ref.ref],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.runtime
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
    entity = ms.ref.entity("sales.customer")
    observed = session.members(entity).observe(
        ms.ref.metric("sales.revenue"), via=ms.ref.relationship("sales.order_buyer"), by=(entity,)
    )
    assert len(observed._node.root.signature.domain.instance_key) == 2
    rows = observed.execute().to_pandas()
    with duckdb.connect(str(case.database_path)) as connection:
        expected = set(connection.execute("SELECT customer_id, region FROM customer").fetchall())
    assert set(zip(rows.iloc[:, 0], rows.iloc[:, 1], strict=True)) == expected


@pytest.mark.runtime
@pytest.mark.parametrize("logical_axes", [False, True])
def test_multiple_member_classifications_keep_complete_time_keys(
    analysis_dsl_case_factory: DslCaseFactory, logical_axes: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = ms.ref.entity("sales.customer")
    region = ms.ref.dimension("sales.customer.region")
    identity = ms.ref.dimension("sales.customer.customer_id")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    product = case.session.members(entity)
    regions, identities = product.read(region), product.read(identity)
    assert isinstance(regions, mv.LogicalCategoryRelation)
    assert isinstance(identities, mv.LogicalCategoryRelation)
    observed = product.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(regions, identities) if logical_axes else (region, identity),
    )
    frame = observed.execute().to_pandas()
    august, september = "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"
    assert len(frame) == 8
    assert frame.set_index(["group", "coord_0", "coord_1"]).value.dropna().to_dict() == {
        ("east", "A", august): 450,
        ("east", "A", september): 99,
        ("east", "B", august): 150,
        ("south", "C", august): 400,
    }
    assert frame.cell_reason.dropna().tolist() == ["empty_contribution"] * 4
    incomplete = identities.where(identities.value.eq("A"))
    with pytest.raises(AnalysisError):
        product.observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(regions, incomplete),
        ).execute()
    if logical_axes:
        saved_regions, saved_identities = regions.execute(), identities.execute()
        saved = product.observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(entity,),
        ).execute()
        case.database_path.rename(case.database_path.with_suffix(".offline"))
        continued = saved.group_by(saved_regions, saved_identities, grid).rollup().execute()
        assert continued.to_pandas().equals(frame)


@pytest.mark.runtime
def test_member_groups_preserve_live_and_fixed_comparisons(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.customer.region")

    def endpoint(month: int) -> mv.LogicalNumericRelation:
        observed = members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(region,),
        )
        assert isinstance(observed, mv.LogicalNumericRelation)
        return observed

    current, baseline = endpoint(8), endpoint(7)
    expected = {"east": -20, "south": -50, "west": 0}
    assert (
        current.compare(baseline).execute().to_pandas().set_index("group").value.to_dict()
        == expected
    )
    with pytest.raises(AnalysisError, match="distinct time bindings"):
        current.compare(current)
    fixed_current, fixed_baseline = current.execute(), baseline.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    restored = case.session.artifact(fixed_current.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedNumericRelation)
    assert (
        restored.compare(fixed_baseline).execute().to_pandas().set_index("group").value.to_dict()
        == expected
    )
