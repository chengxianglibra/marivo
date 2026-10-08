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
    history = session.lifecycle.replay(
        ms.ref.state_model("sales.lifecycle"),
        population=members,
        window=mv.time_scope(start="2026-08-01T00:00:00+00:00", end="2026-09-01T00:00:00+00:00"),
        seed=mv.from_inception(),
    )
    assert_type(history, mv.LogicalHistoryResult)
    assert_type(history.execute(), mv.MaterializedHistoryResult)
    assert_type(history.contract(), mv.AnalysisContract)
    approximate_count = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg="approx_count_distinct", label="distinct"
    )
    approximate_median = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg="approx_median", label="median"
    )
    approximate_percentile = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.order.amount"), agg=("approx_percentile", 0.95), label="p95"
    )
    for metric in (approximate_count, approximate_median, approximate_percentile):
        assert_type(
            members.observe(
                metric,
                via=ms.ref.relationship("sales.order_buyer"),
                by=(ms.ref.entity("sales.customer"),),
            ),
            mv.LogicalNumericRelation | mv.LogicalRatioRelation,
        )
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
        product.read(
            ms.ref.measure("sales.customer.balance"),
            at=grid.before_end,
            match_verification="assume",
        ),
        mv.LogicalNumericRelation,
    )
    assert_type(
        product.observe(
            ms.ref.metric("sales.running"), at=grid.end, by=(ms.ref.entity("sales.customer"),)
        ),
        mv.LogicalNumericRelation | mv.LogicalRatioRelation,
    )
    category = members.read(ms.ref.dimension("sales.customer.region"))
    assert_type(category, mv.LogicalCategoryRelation | mv.LogicalBooleanRelation)
    assert isinstance(category, mv.LogicalCategoryRelation)
    assert_type(members.penetration_in(members), mv.LogicalNumericRelation)
    chosen = category.where(category.value.eq("west"))
    assert_type(chosen, mv.LogicalSelectedCategoryRelation)
    fixed_chosen = chosen.execute()
    assert_type(fixed_chosen, mv.MaterializedSelectedCategoryRelation)
    assert_type(fixed_chosen.members(), mv.LogicalFixedAnalysisDomain)
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
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
        by=(ms.ref.entity("sales.customer"),),
    )
    assert_type(ratio, mv.LogicalNumericRelation | mv.LogicalRatioRelation)
    assert isinstance(ratio, mv.LogicalRatioRelation)
    assert_type(ratio.rollup(), mv.LogicalRolledRatioRelation)
    assert_type(
        ratio.group_by(ms.ref.dimension("sales.order.channel")),
        mv.GroupedRatioRelation,
    )
    fixed = observed.execute()
    shares = observed.share_of(observed.rollup())
    assert_type(shares, mv.LogicalNumericRelation)
    grouped_values = observed.group_by(category).rollup()
    composition = grouped_values.share_of(grouped_values.rollup())
    weights = mv.reference_weights(
        composition, strata=(category,), unit=ms.ref.entity("sales.order")
    )
    assert_type(weights, mv.ReferenceWeights)
    assert_type(grouped_values.standardize(reference=weights), mv.LogicalNumericRelation)
    assert_type(fixed.share_of(fixed.rollup()), mv.LogicalNumericRelation)
    assert_type(fixed, mv.MaterializedNumericRelation)
    assert_type(
        fixed.rollup().execute(),
        mv.MaterializedRolledNumericRelation | mv.MaterializedGroupedNumericRelation,
    )
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

    assert_type(
        observed.compare(
            observed,
            value="relative_change",
            design=mv.TimeChange(pairing=mv.UnionKeys(missing="keep")),
        ),
        mv.LogicalDifferenceRelation,
    )
    assert_type(fixed.compare(fixed, design=mv.CohortContrast()), mv.LogicalDifferenceRelation)
    assert_type(difference.compare(difference), mv.LogicalDifferenceRelation)
    assert_type(
        observed.ratio(observed, pairing=mv.ExactKeys(verification="assume")),
        mv.LogicalNumericRelation,
    )
    assert_type(fixed.ratio(fixed), mv.LogicalNumericRelation)
    pairing = mv.one_to_one(
        left=observed, right=observed, via=ms.ref.relationship("sales.identity")
    )
    assert_type(pairing, mv.OneToOneCorrespondence)
    assert_type(observed.ratio(observed, pairing=pairing), mv.LogicalNumericRelation)
    assert_type(mv.PeriodChange(alignment=mv.window_bucket()), mv.PeriodChange)

if TYPE_CHECKING:
    ranking = observed.rank(order="descending", ties="dense", partition_by=(category,))
    assert_type(ranking, mv.LogicalRankingResult)
    assert_type(ranking.values, mv.LogicalNumericRelation)
    assert_type(ranking.ranks, mv.LogicalNumericRelation)
    assert_type(ranking.limit(5), mv.LogicalRankingResult)
    materialized_ranking = ranking.execute()
    assert_type(materialized_ranking, mv.MaterializedRankingResult)
    assert_type(materialized_ranking.values, mv.MaterializedNumericRelation)
    assert_type(materialized_ranking.ranks, mv.MaterializedNumericRelation)
    selected_ranking = materialized_ranking.where(materialized_ranking.ranks.value.is_defined())
    assert_type(selected_ranking, mv.LogicalRankingResult)
    terminal = mv.table(values=ranking.values, ranks=ranking.ranks, category=category)
    assert_type(terminal, mv.LogicalTable)
    assert_type(terminal.execute(), mv.MaterializedTable)
    attribution = difference.attribute(
        axes=(ms.ref.dimension("sales.order.channel"),), mode="joint", top_k=5
    )
    assert_type(attribution, mv.LogicalAttributionResult)
    assert_type(attribution.contribution, mv.LogicalNumericRelation)
    assert_type(attribution.current, mv.LogicalNumericRelation)
    assert_type(attribution.baseline, mv.LogicalNumericRelation)
    allocated = attribution.execute()
    assert_type(allocated, mv.MaterializedAttributionResult)
    assert_type(allocated.contribution, mv.MaterializedNumericRelation)
    assert_type(allocated.current, mv.MaterializedNumericRelation)
    assert_type(allocated.baseline, mv.MaterializedNumericRelation)
    assert_type(allocated.where(allocated.contribution.value.gt(0)), mv.LogicalAttributionResult)
