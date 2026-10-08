"""occurrence-bounded routes and multi-component observation."""

from __future__ import annotations

import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_fields import (
    RootRoutesValue,
    RootRouteValue,
    root_route,
    root_routes,
)
from marivo.refs import ref
from marivo.semantic.errors import SemanticError
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


def _assert_cold_rollup(case: DslCase, artifact: str, expected: float | int) -> None:
    """Recover in a new process with both authored models and datasource offline."""
    import os
    import subprocess
    import sys

    offline = case.database_path.with_suffix(".offline")
    models = case.root / "models"
    models_offline = case.root / "models.offline"
    case.database_path.rename(offline)
    models.rename(models_offline)
    script = """
import math
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('retained rollup cannot load Semantic or connect a source')

ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
fixed = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
assert isinstance(fixed, mv.MaterializedNumericRelation)
assert any(action.call == 'relation.rollup()' for action in fixed.contract().actions)
actual = fixed.rollup().execute().to_pandas().iloc[0]['value']
assert math.isclose(actual, float(sys.argv[3]), rel_tol=1e-14), actual
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, case.session.id, artifact, str(expected)],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
        models_offline.rename(models)
    assert result.returncode == 0, result.stderr


def _route(entity: str):
    return root_route(ref.entity(f"sales.{entity}"), through=(ref.relationship("sales.buyer"),))


def test_root_routes_accepts_three_distinct_contribution_roots() -> None:
    routes = root_routes(
        _route("line"),
        _route("order"),
        _route("return"),
    )

    assert isinstance(routes, RootRoutesValue)
    assert tuple(item.root.path for item in routes.routes) == (
        "sales.line",
        "sales.order",
        "sales.return",
    )


def test_root_routes_accepts_one_route() -> None:
    routes = root_routes(_route("order"))

    assert isinstance(routes, RootRoutesValue)
    assert len(routes.routes) == 1


def test_root_routes_rejects_repeated_contribution_root() -> None:
    with pytest.raises(AnalysisError, match="distinct contribution roots"):
        root_routes(_route("order"), _route("order"))


def test_root_routes_rejects_no_route() -> None:
    with pytest.raises(AnalysisError, match="route"):
        root_routes()


def test_root_route_requires_a_nonempty_relationship_path() -> None:
    with pytest.raises(AnalysisError, match="route"):
        RootRouteValue(ref.entity("sales.order"), ())


@pytest.mark.runtime
def test_observe_runtime_ratio_over_two_contribution_roots(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A two-component runtime expression observes each root independently."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    names = case.names
    order = ms.ref.entity(f"{names.domain}.{names.order}")
    line = ms.ref.entity(f"{names.domain}.{names.order_line}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    line_order = ms.ref.relationship(f"{names.domain}.{names.line_order}")
    line_revenue = ms.ref.metric(f"{names.domain}.{names.line_revenue}")
    order_count = ms.ref.metric(f"{names.domain}.{names.order_count}")

    expression = mv.runtime_metric.ratio(line_revenue, order_count, label="energy_per_reading")
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.routes(
            mv.route(line, through=(line_order, buyer)),
            mv.route(order, through=(buyer,)),
        ),
        by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()

    # Raw facts per member: A has lines 40+60 over 2 orders; B has lines 20+40 over
    # 2 orders. Each root reduces independently, so this is 100/2 and 60/2 — not the
    # table-multiplied (100+60)/(2+2), which would give 40 for both members.
    assert list(zip(framed["member"], framed["value"], strict=True)) == [
        ("A", 50.0),
        ("B", 30.0),
    ]


@pytest.mark.runtime
def test_observe_sliced_runtime_component_selects_its_own_branch(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A slice limits its own contribution branch without merging or fanning out."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    channel = ms.ref.dimension(f"{names.domain}.{names.order}.{names.channel}")
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")

    sliced = mv.runtime_metric.slice(revenue, by={channel: "web"}, label="web_revenue")
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        sliced,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=buyer,
        by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()
    rows = {
        member: (value, tag, reason)
        for member, value, tag, reason in zip(
            framed["member"],
            framed["value"],
            framed["cell_tag"],
            framed["cell_reason"],
            strict=True,
        )
    }

    # j1 in-window orders: A has one web order of 450; B has one mobile order of
    # 150 that the web slice must exclude; C has one web order of 400; D has none.
    assert rows["A"][:2] == (450.0, "defined")
    assert rows["C"][:2] == (400.0, "defined")
    # An excluded branch is an empty contribution, never a silent zero: B's 150
    # must not be counted, and it must not be reported as 0 either.
    assert rows["B"][1] == "null"
    assert rows["B"][2] == "empty_contribution"
    assert rows["D"][1] == "null"
    # The unfiltered total would be 1000; the slice must not silently include B.
    assert sum(value for value, tag, _ in rows.values() if tag == "defined") == 850


@pytest.mark.runtime
def test_unsliced_observation_keeps_every_member_contribution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """The same table without a slice keeps the contributions the slice excluded."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")

    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        revenue,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=buyer,
        by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()
    rows = {
        member: (value, tag)
        for member, value, tag in zip(
            framed["member"], framed["value"], framed["cell_tag"], strict=True
        )
    }

    # Unfiltered, B's mobile order of 150 is counted and the whole window is 1000.
    assert rows["A"] == (450.0, "defined")
    assert rows["B"] == (150.0, "defined")
    assert rows["C"] == (400.0, "defined")
    # D has no in-window order at all, so its cell is empty rather than zero.
    assert rows["D"][1] == "null"
    assert sum(value for value, tag in rows.values() if tag == "defined") == 1000


@pytest.mark.runtime
def test_root_routes_rejects_a_route_per_occurrence_of_one_root(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Routes bind distinct roots; repetition within one observation rejects."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    names = case.names
    domain = names.domain
    line = ms.ref.entity(f"{domain}.{names.order_line}")
    order = ms.ref.entity(f"{domain}.{names.order}")
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    line_order = ms.ref.relationship(f"{domain}.{names.line_order}")
    line_revenue = ms.ref.metric(f"{domain}.{names.line_revenue}")
    order_count = ms.ref.metric(f"{domain}.{names.order_count}")

    expression = mv.runtime_metric.linear(add=[line_revenue, order_count], label="net_readings")
    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))

    with pytest.raises(AnalysisError, match="distinct contribution roots"):
        members.observe(
            expression,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=mv.routes(
                mv.route(line, through=(line_order, buyer)),
                mv.route(line, through=(line_order, buyer)),
                mv.route(order, through=(buyer,)),
            ),
            by=(ms.ref.entity(f"{domain}.{names.customer}"),),
        )


@pytest.mark.runtime
def test_observe_two_branches_of_one_root_over_a_single_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Two filtered occurrences of one table share one root route."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    domain = names.domain
    order = ms.ref.entity(f"{domain}.{names.order}")
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    channel = ms.ref.dimension(f"{domain}.{names.order}.{names.channel}")
    revenue = ms.ref.metric(f"{domain}.{names.revenue}")
    order_count = ms.ref.metric(f"{domain}.{names.order_count}")

    # Both components reduce from sales.order, but under different branch filters,
    # so the observation has two occurrences and exactly one distinct root route.
    web = mv.runtime_metric.slice(revenue, by={channel: "web"}, label="web_revenue")
    expression = mv.runtime_metric.ratio(web, order_count, label="web_share")
    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))
    observed = members.observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.route(order, through=(buyer,)),
        by=(ms.ref.entity(f"{domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()
    rows = {
        member: (value, tag, reason)
        for member, value, tag, reason in zip(
            framed["member"],
            framed["value"],
            framed["cell_tag"],
            framed["cell_reason"],
            strict=True,
        )
    }

    # j1 in-window orders: A is web/450, B is mobile/150, C is web/400, D has none.
    # The numerator keeps only the web branch while the denominator counts every
    # order, so A is 450/1 and C is 400/1.
    assert rows["A"][:2] == (450.0, "defined")
    assert rows["C"][:2] == (400.0, "defined")
    # B is the discriminating member: had the slice leaked, its mobile 150 would
    # appear as 150/1. It must instead be an empty contribution.
    assert rows["B"][1] == "null"
    assert rows["B"][2] == "empty_contribution"


@pytest.mark.runtime
def test_observe_linear_over_two_distinct_contribution_roots(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A signed linear combination reduces each root before combining."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    names = case.names
    domain = names.domain
    order = ms.ref.entity(f"{domain}.{names.order}")
    line = ms.ref.entity(f"{domain}.{names.order_line}")
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    line_order = ms.ref.relationship(f"{domain}.{names.line_order}")
    line_revenue = ms.ref.metric(f"{domain}.{names.line_revenue}")
    order_count = ms.ref.metric(f"{domain}.{names.order_count}")

    expression = mv.runtime_metric.linear(add=[line_revenue, order_count], label="net_readings")
    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))
    observed = members.observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.routes(
            mv.route(line, through=(line_order, buyer)),
            mv.route(order, through=(buyer,)),
        ),
        by=(ms.ref.entity(f"{domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()

    # j3 raw facts per member: lines 40+60 (A) and 20+40 (B), two orders each.
    # Both roots reduce independently, so A is 100 + 2 and B is 60 + 2. A single
    # combined table would fan out to (40+60+20+40) lines and (2+2) orders.
    assert list(zip(framed["member"], framed["value"], strict=True)) == [
        ("A", 102),
        ("B", 62),
    ]


@pytest.mark.runtime
def test_linear_subtract_reverses_its_named_term(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A subtracted term is negated while an added term keeps its sign."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    names = case.names
    domain = names.domain
    order = ms.ref.entity(f"{domain}.{names.order}")
    line = ms.ref.entity(f"{domain}.{names.order_line}")
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    line_order = ms.ref.relationship(f"{domain}.{names.line_order}")
    line_revenue = ms.ref.metric(f"{domain}.{names.line_revenue}")
    order_count = ms.ref.metric(f"{domain}.{names.order_count}")

    expression = mv.runtime_metric.linear(
        add=[order_count], subtract=[line_revenue], label="orders_minus_lines"
    )
    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))
    observed = members.observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.routes(
            mv.route(order, through=(buyer,)),
            mv.route(line, through=(line_order, buyer)),
        ),
        by=(ms.ref.entity(f"{domain}.{names.customer}"),),
    )

    framed = observed.execute().to_pandas()

    # A is 2 - 100 and B is 2 - 60, so the sign follows the declared term order.
    assert list(zip(framed["member"], framed["value"], strict=True)) == [
        ("A", -98),
        ("B", -58),
    ]


@pytest.mark.runtime
def test_observe_without_a_window_keeps_every_admitted_contribution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """An omitted window drops the time restriction without claiming history."""
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    domain = names.domain
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    revenue = ms.ref.metric(f"{domain}.{names.revenue}")

    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))
    windowless = members.observe(
        revenue, during=None, via=buyer, by=(ms.ref.entity(f"{domain}.{names.customer}"),)
    )
    framed = windowless.execute().to_pandas()
    by_member = dict(zip(framed["member"], framed["value"], strict=True))

    # j1 holds July, August and September orders for A. The windowed observation
    # selects only August; omitting the window keeps every admitted contribution,
    # so A is 450 + 99 + 77 rather than 450.
    assert by_member["A"] == 450 + 99 + 77
    assert by_member["B"] == 150
    assert by_member["C"] == 400


@pytest.mark.runtime
def test_windowless_observation_rejects_a_cumulative_metric(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A cumulative Metric needs an endpoint and cannot degrade to no window."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    domain = names.domain
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    revenue = ms.ref.metric(f"{domain}.{names.revenue}")

    # Extend the loaded fixture domain with a cumulative base over the same axis.
    models = case.root / "models" / "semantic" / domain / "models.py"
    models.write_text(
        models.read_text()
        + "\ncumulative_revenue = ms.cumulative("
        + f"name='cumulative_revenue', base=revenue, over={names.ordered_at})\n"
    )
    ms.load(workspace_dir=case.root)
    cumulative = ms.ref.metric(f"{domain}.cumulative_revenue")
    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))

    with pytest.raises(AnalysisError, match="explicit endpoint window"):
        members.observe(
            cumulative, during=None, via=buyer, by=(ms.ref.entity(f"{domain}.{names.customer}"),)
        )
    # A contribution window never supplies an omitted cumulative endpoint.
    with pytest.raises(AnalysisError, match="endpoint"):
        members.observe(
            cumulative,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=buyer,
            by=(ms.ref.entity(f"{domain}.{names.customer}"),),
        )


@pytest.mark.runtime
def test_opaque_metric_rejects_instead_of_inventing_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """An opaque Metric has no governed component and is never decomposed."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    names = case.names
    domain = names.domain
    buyer = ms.ref.relationship(f"{domain}.{names.buyer}")
    opaque = ms.ref.metric(f"{domain}.opaque_revenue")
    assert case.catalog.require(opaque).ref == opaque
    revenue = ms.ref.metric(f"{domain}.{names.revenue}")

    members = case.session.members(ms.ref.entity(f"{domain}.{names.customer}"))
    # The opaque body is numerically equal to the declared revenue sum, but the
    # graph must not read a component out of the body or the equal value: only a
    # governed aggregate graph is admissible, so the opaque leaf rejects.
    with pytest.raises(SemanticError, match="governed contribution graph"):
        members.observe(
            opaque,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=buyer,
            by=(ms.ref.entity(f"{domain}.{names.customer}"),),
        )
    # The declared Metric over the same rows still observes normally.
    framed = (
        members.observe(
            revenue,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=buyer,
            by=(ms.ref.entity(f"{domain}.{names.customer}"),),
        )
        .execute()
        .to_pandas()
    )
    by_member = dict(zip(framed["member"], framed["value"], strict=True))
    assert by_member["A"] == 450


@pytest.mark.runtime
@pytest.mark.parametrize("metric_name", ("revenue", "order_count"))
def test_ratio_uses_each_components_own_state_and_rolls_up(
    analysis_dsl_case_factory: DslCaseFactory,
    metric_name: str,
) -> None:
    """Both sum/sum and count/count finish and retain independently mergeable state."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    metric = ms.ref.metric(f"sales.{metric_name}")
    expression = mv.runtime_metric.ratio(metric, metric, label="identity_ratio")
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    assert rows.loc[["A", "B", "C"], "value"].tolist() == [1.0, 1.0, 1.0]
    assert rows.loc["D", "cell_tag"] == ("null" if metric_name == "revenue" else "undefined")
    assert rows.loc["D", "cell_reason"] == (
        "empty_contribution" if metric_name == "revenue" else "zero_denominator"
    )
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [1.0]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [1.0]


@pytest.mark.runtime
@pytest.mark.parametrize("term_count", (3, 4))
def test_linear_executes_more_than_two_occurrences(
    analysis_dsl_case_factory: DslCaseFactory,
    term_count: int,
) -> None:
    """Exact qualification preserves every occurrence, including repeated roots."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    expression = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.order_count")] * term_count,
        label="repeated_count",
    )
    rows = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            expression,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"),),
        )
        .execute()
        .to_pandas()
    )
    assert rows["value"].tolist() == [term_count, term_count, term_count, 0]
    assert rows["cell_tag"].tolist() == ["defined"] * 4


@pytest.mark.runtime
@pytest.mark.parametrize("metric_name", ("order_count", "zero_revenue", "revenue"))
def test_linear_preserves_each_empty_branch_policy(
    analysis_dsl_case_factory: DslCaseFactory,
    metric_name: str,
) -> None:
    """An empty filtered count is zero, while an empty filtered sum remains Null."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + "\nzero_revenue = ms.aggregate(name='zero_revenue', measure=amount, agg='sum', time=ordered_at, empty=ms.empty.zero())\n"
    )
    ms.load(workspace_dir=case.root)
    count = ms.ref.metric("sales.order_count")
    web_count = mv.runtime_metric.slice(
        ms.ref.metric(f"sales.{metric_name}"),
        by={ms.ref.dimension("sales.order.channel"): "web"},
        label="web_count",
    )
    expression = mv.runtime_metric.linear(add=[web_count, count], label="total")
    rows = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            expression,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"),),
        )
        .execute()
        .to_pandas()
    )
    rows = rows.set_index("member")
    assert rows.loc[["A", "C"], "value"].tolist() == (
        [2, 2] if metric_name == "order_count" else [451, 401]
    )
    if metric_name == "revenue":
        assert rows.loc[["B", "D"], "cell_tag"].tolist() == ["null", "null"]
        assert rows.loc[["B", "D"], "cell_reason"].tolist() == ["empty_contribution"] * 2
    else:
        assert rows.loc[["B", "D"], "value"].tolist() == [1, 0]
        assert rows["cell_tag"].tolist() == ["defined"] * 4


@pytest.mark.runtime
@pytest.mark.parametrize("windowed", (False, True))
def test_runtime_aggregate_resolves_the_declared_default_axis(
    analysis_dsl_case_factory: DslCaseFactory,
    windowed: bool,
) -> None:
    """A runtime sum uses an explicit Entity default without a catalog Metric wrapper."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',",
            "granularity='second', is_default=True,",
        )
    )
    ms.load(workspace_dir=case.root)
    expression = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"),
        agg="sum",
        label="total",
    )
    rows = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            expression,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01") if windowed else None,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"),),
        )
        .execute()
        .to_pandas()
        .set_index("member")
    )
    assert rows.loc[["A", "B", "C"], "value"].tolist() == [450 if windowed else 626, 150, 400]
    assert rows.loc["D", "cell_tag"] == "null"


@pytest.mark.runtime
def test_linear_executes_three_independently_reduced_roots(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Three explicit roots execute, rather than merely constructing route values."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + """
shipments = ms.entity(name='shipment', datasource=warehouse,
    source=md.table('order'), primary_key=['order_id'])
shipment_customer = ms.dimension_column(name='customer_id', entity=shipments, column='customer_id')
shipment_time = ms.time_dimension_column(name='ordered_at', entity=shipments,
    column='ordered_at', granularity='second', parse=ms.timestamp(timezone='UTC'))
shipment_buyer = ms.relationship(name='shipment_buyer', from_entity=shipments,
    to_entity=customer, keys=[ms.join_on(shipment_customer, customer_id)])
shipment_count = ms.count(name='shipment_count', entity=shipments, time=shipment_time)
"""
    )
    ms.load(workspace_dir=case.root)
    expression = mv.runtime_metric.linear(
        add=[
            ms.ref.metric("sales.line_revenue"),
            ms.ref.metric("sales.order_count"),
            ms.ref.metric("sales.shipment_count"),
        ],
        label="three_roots",
    )
    rows = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            expression,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=mv.routes(
                mv.route(
                    ms.ref.entity("sales.order_line"),
                    through=(
                        ms.ref.relationship("sales.line_order"),
                        ms.ref.relationship("sales.order_buyer"),
                    ),
                ),
                mv.route(
                    ms.ref.entity("sales.order"),
                    through=(ms.ref.relationship("sales.order_buyer"),),
                ),
                mv.route(
                    ms.ref.entity("sales.shipment"),
                    through=(ms.ref.relationship("sales.shipment_buyer"),),
                ),
            ),
            by=(ms.ref.entity("sales.customer"),),
        )
        .execute()
        .to_pandas()
    )
    # Independent raw facts: line sums 100/60, two orders and two shipments each.
    assert rows["value"].tolist() == [104, 64]


@pytest.mark.runtime
def test_ratio_accepts_a_negative_sum_denominator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """A signed sum denominator is a magnitude, not a nonnegative row count."""
    import duckdb

    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("SET threads = 1")
        connection.execute('UPDATE "order" SET amount = -amount')
    expression = mv.runtime_metric.ratio(
        ms.ref.metric("sales.order_count"),
        ms.ref.metric("sales.revenue"),
        label="inverse",
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    assert rows.loc[["A", "B", "C"], "value"].tolist() == pytest.approx(
        [-1 / 450, -1 / 150, -1 / 400]
    )
    assert rows.loc["D", "cell_tag"] == "null"
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [-3 / 1000]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [-3 / 1000]


@pytest.mark.runtime
@pytest.mark.parametrize("runtime", (False, True))
@pytest.mark.parametrize("parquet", (False, True))
def test_weighted_mean_preserves_pairs_and_fixed_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
    runtime: bool,
    parquet: bool,
) -> None:
    """Value-null and weight-null rows must not contribute orphaned weights."""
    import duckdb

    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT DEFAULT 1')
        db.execute(
            "UPDATE \"order\" SET weight=CASE WHEN customer_id='A' THEN 2 WHEN customer_id='B' THEN 0 WHEN customer_id='C' THEN 4 ELSE NULL END"
        )
        db.execute(
            "INSERT INTO \"order\" VALUES ('null_value','A','web','paid','2026-08-15',NULL,9), ('null_weight','A','web','paid','2026-08-15',50,NULL), ('pair','A','web','paid','2026-08-15',100,1)"
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text().replace(
            "granularity='second',", "granularity='second', is_default=True,"
        )
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\nweighted = ms.weighted_mean(name='weighted', value=amount, weight=weight)\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    expression = (
        mv.runtime_metric.weighted_mean(
            ms.ref.measure("sales.order.amount"),
            ms.ref.measure("sales.order.weight"),
            label="weighted",
        )
        if runtime
        else ms.ref.metric("sales.weighted")
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    assert rows.loc["A", "value"] == pytest.approx(1000 / 3)
    assert rows.loc["B", "cell_reason"] == "zero_weight_sum"
    assert rows.loc["C", "value"] == 400
    assert rows.loc["D", "cell_reason"] == "empty_contribution"
    assert observed.rollup().execute().to_pandas()["value"].tolist() == pytest.approx([2600 / 7])
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == pytest.approx([2600 / 7])
    if runtime:
        _assert_cold_rollup(case, fixed.state.artifact_ref.ref, 2600 / 7)
        from marivo.analysis.materialization import graph_store

        store = case.session._runtime.store
        with store._read() as connection:
            record = graph_store.artifact(store, connection, fixed.state.artifact_ref.ref)
        assert record is not None
        original = next(part for part in record.descriptor.parts if part.role == "original_state")
        path = case.root / original.local.project_relative_path
        missing = path.with_name(path.name + ".missing")
        path.rename(missing)
        try:
            with pytest.raises(AnalysisError, match=r"(?i)part|receipt|file|missing"):
                fixed.contract()
            with pytest.raises(AnalysisError, match=r"(?i)part|receipt|file|missing"):
                case.session.artifact(fixed.state.artifact_ref)
        finally:
            missing.rename(path)


@pytest.mark.runtime
@pytest.mark.parametrize("ratio", (False, True))
def test_observation_preserves_full_member_identity(
    analysis_dsl_case_factory: DslCaseFactory,
    ratio: bool,
) -> None:
    """Equal first keys in different tenants cannot merge or multiply contributions."""
    import duckdb

    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute("ALTER TABLE customer ADD COLUMN tenant VARCHAR DEFAULT 'x'")
        db.execute("ALTER TABLE \"order\" ADD COLUMN tenant VARCHAR DEFAULT 'x'")
        db.execute("INSERT INTO customer VALUES ('A','north','y')")
        db.execute(
            "INSERT INTO \"order\" VALUES ('other_tenant','A','web','paid','2026-08-15',700,'y')"
        )
    models = case.root / "models/semantic/sales/models.py"
    text = models.read_text().replace(
        "primary_key=['customer_id']", "primary_key=['customer_id', 'tenant']"
    )
    text = text.replace(
        "buyer = ms.relationship(",
        "customer_tenant = ms.dimension_column(name='tenant', entity=customer, column='tenant')\norder_tenant = ms.dimension_column(name='tenant', entity=orders, column='tenant')\nbuyer = ms.relationship(",
    )
    text = text.replace(
        "keys=[ms.join_on(order_customer_id, customer_id)]",
        "keys=[ms.join_on(order_customer_id, customer_id), ms.join_on(order_tenant, customer_tenant)]",
    )
    models.write_text(text)
    ms.load(workspace_dir=case.root)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        mv.runtime_metric.ratio(
            ms.ref.metric("sales.revenue"), ms.ref.metric("sales.order_count"), label="aov"
        )
        if ratio
        else ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index(["member", "coord_0"])
    assert rows.loc[("A", "x"), "value"] == 450
    assert rows.loc[("A", "y"), "value"] == 700
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [425 if ratio else 1700]


@pytest.mark.runtime
def test_nested_linear_keeps_original_state_for_source_and_fixed_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Subtracting a nested difference distributes signs without averaging finishes."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    revenue = ms.ref.metric("sales.revenue")
    count = ms.ref.metric("sales.order_count")
    expression = mv.runtime_metric.linear(
        add=[revenue],
        subtract=[mv.runtime_metric.linear(add=[revenue], subtract=[count], label="inner")],
        label="nested",
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    assert rows.loc[["A", "B", "C"], "value"].tolist() == [1, 1, 1]
    assert rows.loc["D", "cell_reason"] == "empty_contribution"
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [3]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [3]
    _assert_cold_rollup(case, fixed.state.artifact_ref.ref, 3)


@pytest.mark.runtime
@pytest.mark.parametrize("extra", (False, True))
def test_observation_rejects_wrong_or_extra_root_routes(
    analysis_dsl_case_factory: DslCaseFactory, extra: bool
) -> None:
    """A valid relationship cannot acquire another declared root role."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j3")
    buyer = ms.ref.relationship("sales.order_buyer")
    line_order = ms.ref.relationship("sales.line_order")
    routes = (
        mv.routes(
            mv.route(ms.ref.entity("sales.order"), through=(buyer,)),
            mv.route(ms.ref.entity("sales.order_line"), through=(line_order, buyer)),
        )
        if extra
        else mv.routes(mv.route(ms.ref.entity("sales.order_line"), through=(buyer,)))
    )
    with pytest.raises(AnalysisError, match=r"route|root"):
        case.session.members(ms.ref.entity("sales.customer")).observe(
            ms.ref.metric("sales.order_count"), via=routes, by=(ms.ref.entity("sales.customer"),)
        )


@pytest.mark.runtime
def test_linear_rejects_incommensurable_units(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Equal physical columns cannot erase incompatible semantic units."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + "\nrevenue_cny = ms.aggregate(name='revenue_cny', measure=amount, agg='sum', unit='CNY')\nrevenue_usd = ms.aggregate(name='revenue_usd', measure=amount, agg='sum', unit='USD')\n"
    )
    ms.load(workspace_dir=case.root)
    expression = mv.runtime_metric.linear(
        add=[ms.ref.metric("sales.revenue_cny"), ms.ref.metric("sales.revenue_usd")],
        label="invalid_currency_sum",
    )
    with pytest.raises((SemanticError, AnalysisError, ValueError), match="unit"):
        case.session.members(ms.ref.entity("sales.customer")).observe(
            expression,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(ms.ref.entity("sales.customer"),),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"original_state__non_null_pair_count": 3},
        {"original_state__row_count": -1},
        {"original_state__weight_sum": 0},
        {"original_state__weighted_numerator": 2**63},
        {"original_state__non_null_pair_count": 0},
    ),
)
def test_weighted_state_rejects_corrupted_components(changes: dict[str, int]) -> None:
    from marivo.analysis.methods.state_validation import state_matches

    primary = {"value": 250.0, "cell_tag": "defined", "cell_reason": None}
    state = {
        "original_state__weighted_numerator": 1000,
        "original_state__weight_sum": 4,
        "original_state__non_null_pair_count": 2,
        "original_state__row_count": 2,
    }
    assert state_matches("original_weighted_mean", primary, state)
    assert not state_matches("original_weighted_mean", primary, {**state, **changes})


@pytest.mark.runtime
@pytest.mark.parametrize("ratio", (False, True))
def test_composite_slice_reaches_each_leaf_before_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
    ratio: bool,
) -> None:
    import marivo.analysis as mv
    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    revenue = ms.ref.metric("sales.revenue")
    composite = (
        mv.runtime_metric.ratio(revenue, ms.ref.metric("sales.order_count"), label="aov")
        if ratio
        else mv.runtime_metric.linear(add=[revenue, revenue], label="twice")
    )
    expression = mv.runtime_metric.slice(
        composite,
        by={ms.ref.dimension("sales.order.channel"): "web"},
        label="web_composite",
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    fixed = observed.execute()
    rows = fixed.to_pandas().set_index("member")
    assert rows.loc[["A", "C"], "value"].tolist() == ([450, 400] if ratio else [900, 800])
    assert rows.loc[["B", "D"], "cell_reason"].tolist() == ["empty_contribution"] * 2
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [425 if ratio else 1700]
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [425 if ratio else 1700]
