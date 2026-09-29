"""Positive static contract for the public first-round Analysis DSL."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    session = mv.session.get_or_create("typing-public", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.customer"))
    assert_type(members, mv.LogicalAnalysisDomain)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    assert_type(grid, mv.TimeGrid)
    assert_type(grid.window, mv.GridWindow)
    assert_type(grid.end, mv.GridEndpoint)
    product = members.each(grid)
    assert_type(product, mv.LogicalTimeAnalysisDomain)
    assert_type(product.execute(), mv.MaterializedTimeAnalysisDomain)
    assert_type(
        product.read(ms.ref.measure("sales.customer.balance"), at=grid.before_end),
        mv.LogicalNumericRelation,
    )
    assert_type(
        product.observe(ms.ref.metric("sales.running"), at=grid.end),
        mv.LogicalNumericRelation | mv.LogicalRatioRelation,
    )
    category = members.read(ms.ref.dimension("sales.customer.region"))
    assert_type(category, mv.LogicalCategoryRelation | mv.LogicalBooleanRelation)
    assert isinstance(category, mv.LogicalCategoryRelation)
    chosen = category.where(category.value.eq("west"))
    assert_type(chosen, mv.LogicalSelectedCategoryRelation)
    fixed_chosen = chosen.execute()
    assert_type(fixed_chosen, mv.MaterializedSelectedCategoryRelation)
    assert_type(fixed_chosen.members(), mv.LogicalFixedAnalysisDomain)
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    assert_type(observed, mv.LogicalNumericRelation | mv.LogicalRatioRelation)
    assert isinstance(observed, mv.LogicalNumericRelation)
    assert_type(observed.rollup(), mv.LogicalRolledNumericRelation)
    assert_type(
        observed.group_by(ms.ref.dimension("sales.order.channel")),
        mv.GroupedNumericRelation,
    )
    assert_type(observed.summarize(mv.mean()), mv.LogicalStatisticRelation)
    difference = observed.compare(observed)
    assert_type(difference, mv.LogicalDifferenceRelation)
    selected = difference.where(difference.value.lt(0))
    assert_type(selected, mv.LogicalSelectedDifferenceRelation)
    ratio = members.observe(
        ms.ref.metric("sales.aov_from_lines"),
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
                ms.ref.entity("sales.order"), through=(ms.ref.relationship("sales.order_buyer"),)
            ),
        ),
    )
    assert_type(ratio, mv.LogicalNumericRelation | mv.LogicalRatioRelation)
    assert isinstance(ratio, mv.LogicalRatioRelation)
    assert_type(ratio.rollup(), mv.LogicalRolledRatioRelation)
    assert_type(
        ratio.group_by(ms.ref.dimension("sales.order.channel")),
        mv.GroupedRatioRelation,
    )
    fixed = observed.execute()
    assert_type(fixed, mv.MaterializedNumericRelation)
    assert_type(fixed.rollup().execute(), mv.MaterializedRolledNumericRelation)
    assert_type(fixed.contract(), mv.AnalysisContract)
    recovered = session.artifact(fixed.state.artifact_ref)
    if isinstance(recovered, mv.MaterializedNumericRelation):
        assert_type(recovered, mv.MaterializedNumericRelation)

if TYPE_CHECKING:
    temporal = members.read(ms.ref.time_dimension("sales.customer.registered_at"))
    assert_type(temporal, mv.LogicalTemporalRelation)
    numeric = members.read(ms.ref.measure("sales.customer.balance"))
    assert_type(numeric, mv.LogicalNumericRelation)
    assert_type(numeric.where(numeric.value.gt(0)), mv.LogicalSelectedNumericRelation)
    assert_type(temporal.execute(), mv.MaterializedTemporalRelation)
    assert_type(
        temporal.where(temporal.value.lt(datetime(2026, 9, 1, tzinfo=timezone.utc))).members(),
        mv.LogicalAnalysisDomain | mv.LogicalFixedAnalysisDomain,
    )
    assert_type(
        mv.time_scope(start="2026-08-01", end="2026-09-01").before_end, mv.BeforeEndBoundary
    )
