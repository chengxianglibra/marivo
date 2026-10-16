"""Positive static contract for the public first-round Analysis DSL."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    session = mv.session.get_or_create("typing-public", report_timezone="UTC")
    from marivo.semantic.expressions import Dimension, Metric

    def ordinary_observation(metric: Metric, region: Dimension) -> mv.Query:
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(scope.start, mv.InstantAnchor)
        assert_type(scope.end, mv.InstantAnchor)
        assert_type(session.instant(scope.end), mv.InstantAnchor)
        assert_type(scope.by(mv.grain("day")), mv.CalendarAxis)
        assert_type(region.at(scope.end), Dimension)
        return session.observe(
            {"value": metric}, during=scope, by=(region,), where=region == "APAC"
        )

    def terminal_reads(artifact: mv.Artifact) -> mv.ArtifactDigest:
        from pandas import DataFrame

        assert_type(session.artifact(artifact.id), mv.Artifact)
        assert_type(artifact.show(n=20), None)
        assert_type(artifact.to_pandas(), DataFrame)
        assert_type(artifact.findings(limit=20, cursor=None), mv.FindingPage)
        digest = artifact.evidence_digest()
        assert_type(digest, mv.ArtifactDigest)
        assert_type(digest.evidence_count, int)
        return digest

    members = session.members(ms.ref.entity("sales.customer"))
    assert_type(members, mv.LogicalAnalysisDomain)
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
                by=(mv.member(),),
            ),
            mv.LogicalNumericRelation | mv.LogicalRatioRelation,
        )
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    assert_type(grid, mv.TimeGrid)
    assert_type(grid.end, mv.GridEndpoint)
    assert_type(grid.before_end, mv.GridEndpoint)
    assert_type(
        members.read(
            ms.ref.measure("sales.customer.balance"),
            at=grid.before_end,
        ),
        mv.LogicalNumericRelation,
    )
    assert_type(
        members.observe(ms.ref.metric("sales.running"), at=grid.end, by=(mv.member(),)),
        mv.LogicalNumericRelation | mv.LogicalRatioRelation,
    )
    assert_type(
        members.observe(ms.ref.metric("sales.revenue"), during=grid),
        mv.LogicalNumericRelation | mv.LogicalRatioRelation,
    )
    assert_type(
        members.read(ms.ref.dimension("sales.customer.tier"), at=grid.before_end),
        mv.LogicalCategoryRelation | mv.LogicalBooleanRelation,
    )
    assert_type(
        members.read(ms.ref.time_dimension("sales.customer.updated_at"), at=grid.before_end),
        mv.LogicalTemporalRelation,
    )
    category = members.read(ms.ref.dimension("sales.customer.region"))
    assert_type(category, mv.LogicalCategoryRelation | mv.LogicalBooleanRelation)
    assert isinstance(category, mv.LogicalCategoryRelation)
    assert_type(members.penetration_in(members), mv.LogicalNumericRelation)
    chosen = category.where(category.value.eq("west"))
    assert_type(chosen, mv.LogicalCategoryRelation)
    fixed_chosen = chosen.execute()
    assert_type(fixed_chosen, mv.MaterializedCategoryRelation)
    assert_type(fixed_chosen.members(), mv.LogicalFixedAnalysisDomain)
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    assert_type(observed, mv.LogicalNumericRelation | mv.LogicalRatioRelation)
    assert isinstance(observed, mv.LogicalNumericRelation)
    assert_type(observed.rollup(), mv.LogicalRolledNumericRelation)
    assert_type(
        observed.group_by(ms.ref.dimension("sales.order.channel")),
        mv.GroupedNumericRelation,
    )
    assert_type(observed.aggregate(mv.mean()), mv.LogicalStatisticRelation)
    comparison = observed.compare(observed)
    assert_type(comparison, mv.LogicalComparisonResult)
    assert_type(comparison.execute(), mv.MaterializedComparisonResult)
    assert_type(comparison.relative, mv.LogicalNumericRelation)
    difference = comparison.absolute
    assert_type(difference, mv.LogicalDifferenceRelation)
    selected = difference.where(difference.value.lt(0))
    assert_type(selected, mv.LogicalSelectedDifferenceRelation)
    ratio = members.observe(
        ms.ref.metric("sales.aov_from_lines"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=(
            mv.path(
                ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")
            ),
            mv.path(ms.ref.relationship("sales.order_buyer")),
        ),
        by=(mv.member(),),
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
    recovered = session.artifact(fixed.state.artifact_ref.ref)
    assert_type(recovered, mv.Artifact)

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

    assert_type(observed.compare(observed, align=mv.outer()).relative, mv.LogicalNumericRelation)
    assert_type(fixed.compare(fixed), mv.LogicalComparisonResult)
    assert_type(difference.compare(difference).absolute, mv.LogicalDifferenceRelation)
    assert_type(observed.ratio(observed, align=mv.exact()), mv.LogicalNumericRelation)
    assert_type(fixed.ratio(fixed), mv.LogicalNumericRelation)
    pairing = mv.one_to_one(
        left=observed, right=observed, via=ms.ref.relationship("sales.identity")
    )
    assert_type(pairing, mv.OneToOneCorrespondence)
    assert_type(observed.ratio(observed, align=pairing), mv.LogicalNumericRelation)
    assert_type(mv.exact(time=mv.ordinal()), mv.ExactAlignment)
    assert_type(mv.outer(), mv.OuterAlignment)
    assert_type(mv.ordinal(), mv.OrdinalTimeKey)
    assert_type(mv.weekday(), mv.WeekdayTimeKey)
    occurrence = mv.time_scope(start="2026-01-01", end="2026-01-02")
    assert_type(mv.holiday(current=occurrence, baseline=occurrence), mv.HolidayTimeKey)
    assert_type(
        mv.holiday_then_weekday(current=occurrence, baseline=occurrence),
        mv.HolidayThenWeekdayTimeKey,
    )
    assert_type(mv.outer(time=mv.weekday()), mv.OuterAlignment)

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
