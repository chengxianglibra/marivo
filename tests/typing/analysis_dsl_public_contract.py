"""Static contracts for Deferred observations, coordinate domains and terminal reads."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ibis.common.deferred import Deferred
from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.expressions import Entity, Relationship

if TYPE_CHECKING:
    session = mv.session.get_or_create("typing-public", report_timezone="UTC")

    def ordinary_observation(metric: Deferred, region: Deferred) -> mv.Query:
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(scope.start, mv.InstantAnchor)
        assert_type(scope.end, mv.InstantAnchor)
        assert_type(session.instant(scope.end), mv.InstantAnchor)
        assert_type(scope.by(mv.grain("day")), mv.CalendarAxis)
        query = session.observe(
            {"value": metric}, during=scope, by=(region,), where=region == "APAC"
        )
        assert_type(query, mv.Query)
        assert_type(query.population(), mv.Population)
        return query

    def population_contract(entity: Entity, region: Deferred, relation: Relationship) -> mv.Query:
        members = session.members(ms.ref.entity("sales.customer"))
        assert_type(members, mv.Population)
        assert_type(session.query(members), mv.Query)
        fixed = mv.identities(entity, keys=(("C1",), ("C2",)))
        assert_type(fixed, mv.Population)
        complete = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
        assert_type(complete, mv.Population)
        assert_type(mv.in_population(complete), mv.PopulationPredicate)
        assert_type(complete.where(~mv.in_population(complete)), mv.Population)
        assert_type(fixed.union(members), mv.Population)
        assert_type(fixed.intersection(members), mv.Population)
        assert_type(fixed.difference(members), mv.Population)
        assert_type(fixed.project(by=(fixed.axes[0],)), mv.Population)
        assert_type(fixed.classify(by=(region,)), mv.Population)
        assert_type(fixed.image(via=(relation,)), mv.Population)
        assert_type(fixed.preimage(members, via=(relation,)), mv.Population)
        descriptor = mv.membership(region, via=((relation,),), unmatched="exclude")
        assert_type(descriptor, mv.MembershipAxis)
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(mv.product(complete, scope.by(mv.grain("day"))), mv.Population)
        revenue = session.catalog.metrics.get("sales.revenue")
        assert_type(revenue, Deferred)
        assert_type(session.observe({"value": revenue}, by=(descriptor,)), mv.Query)
        assert_type(session.observe({"value": revenue}, by=complete), mv.Query)
        return session.query(complete)

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

    def retained_relation_contracts(
        observed: mv.LogicalNumericRelation,
        ratio: mv.LogicalRatioRelation,
        category: mv.LogicalCategoryRelation,
        temporal: mv.LogicalTemporalRelation,
        numeric: mv.LogicalNumericRelation,
        member_domain: mv.LogicalAnalysisDomain,
    ) -> None:
        """Keep unrelated receiver contracts independent of retired acquisition APIs."""
        assert_type(member_domain.penetration_in(member_domain), mv.LogicalNumericRelation)
        chosen = category.where(category.value.eq("west"))
        assert_type(chosen, mv.LogicalCategoryRelation)
        assert_type(chosen.execute(), mv.MaterializedCategoryRelation)
        assert_type(observed.rollup(), mv.LogicalRolledNumericRelation)
        assert_type(
            observed.group_by(ms.ref.dimension("sales.order.channel")), mv.GroupedNumericRelation
        )
        assert_type(observed.aggregate(mv.mean()), mv.LogicalStatisticRelation)
        comparison = observed.compare(observed)
        assert_type(comparison, mv.LogicalComparisonResult)
        assert_type(comparison.execute(), mv.MaterializedComparisonResult)
        assert_type(comparison.relative, mv.LogicalNumericRelation)
        difference = comparison.absolute
        assert_type(difference, mv.LogicalDifferenceRelation)
        assert_type(difference.where(difference.value.lt(0)), mv.LogicalSelectedDifferenceRelation)
        assert_type(ratio.rollup(), mv.LogicalRolledRatioRelation)
        assert_type(
            ratio.group_by(ms.ref.dimension("sales.order.channel")), mv.GroupedRatioRelation
        )
        assert_type(observed.share_of(observed.rollup()), mv.LogicalNumericRelation)
        grouped_values = observed.group_by(category).rollup()
        composition = grouped_values.share_of(grouped_values.rollup())
        weights = mv.reference_weights(
            composition, strata=(category,), unit=ms.ref.entity("sales.order")
        )
        assert_type(weights, mv.ReferenceWeights)
        assert_type(grouped_values.standardize(reference=weights), mv.LogicalNumericRelation)
        fixed = observed.execute()
        assert_type(fixed, mv.MaterializedNumericRelation)
        assert_type(session.artifact(fixed.state.artifact_ref.ref), mv.Artifact)
        assert_type(numeric.where(numeric.value.gt(0)), mv.LogicalSelectedNumericRelation)
        assert_type(temporal.execute(), mv.MaterializedTemporalRelation)
        assert_type(
            temporal.where(temporal.value.lt(datetime(2026, 9, 1, tzinfo=timezone.utc))).members(),
            mv.LogicalAnalysisDomain,
        )
        assert_type(
            observed.compare(observed, align=mv.outer()).relative, mv.LogicalNumericRelation
        )
        assert_type(difference.compare(difference).absolute, mv.LogicalDifferenceRelation)
        assert_type(observed.ratio(observed, align=mv.exact()), mv.LogicalNumericRelation)
        pairing = mv.one_to_one(
            left=observed, right=observed, via=ms.ref.relationship("sales.identity")
        )
        assert_type(pairing, mv.OneToOneCorrespondence)
        assert_type(observed.ratio(observed, align=pairing), mv.LogicalNumericRelation)
        ranking = observed.rank(order="descending", ties="dense", partition_by=(category,))
        assert_type(ranking, mv.LogicalRankingResult)
        assert_type(ranking.values, mv.LogicalNumericRelation)
        assert_type(ranking.ranks, mv.LogicalNumericRelation)
        assert_type(ranking.limit(5), mv.LogicalRankingResult)
        assert_type(ranking.execute(), mv.MaterializedRankingResult)
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
        assert_type(attribution.execute(), mv.MaterializedAttributionResult)

    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    assert_type(grid, mv.TimeGrid)
    assert_type(grid.end, mv.GridEndpoint)
    assert_type(grid.before_end, mv.GridEndpoint)
    assert_type(
        mv.time_scope(start="2026-08-01", end="2026-09-01").before_end, mv.BeforeEndBoundary
    )
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
