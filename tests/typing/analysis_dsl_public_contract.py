"""Static contracts for Deferred observations, coordinate domains and terminal reads."""

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
        daily_axis = scope.by(mv.grain("day"))
        assert_type(daily_axis.coarsen(mv.grain("month")), mv.CalendarAxis)
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

    def result_algebra(
        query: mv.Query, other: mv.Query, axis: Deferred, field: Deferred
    ) -> mv.Query:
        value = query["value"]
        assert_type(value, mv.Column)
        assert_type(value + 1, mv.Column)
        assert_type(value / 2, mv.Column)
        assert_type(value.divide(2, zero_denominator="error"), mv.Column)
        assert_type(query.coord(axis), mv.Column)
        assert_type(query.select({"net": value - 1, "constant": 1}), mv.Query)
        assert_type(query.where((value > 0) & ~value.is_null()), mv.Query)
        assert_type(query.top(3, by=value, partition_by=(axis,)), mv.Query)
        assert_type(
            query.reduce(
                {"sum": value.sum(), "mean": value.mean(), "rows": mv.row_count()}, by=(axis,)
            ),
            mv.Query,
        )
        assert_type(query.rollup(by=(axis,)), mv.Query)
        assert_type(query.drill(add=(field,)), mv.Query)
        assert_type(query.share("value", partition_by=(axis,)), mv.Query)
        assert_type(query.align(other), mv.Query)
        assert_type(query.compare(other, zero_denominator="null"), mv.Query)
        assert_type(mv.broadcast(other["value"], to=query), mv.Column)
        return query.select({"ratio": value / mv.broadcast(other["value"], to=query)})

    def explicit_identity(metric: Deferred, field: Deferred, relation: Relationship) -> mv.Query:
        people = session.members(ms.ref.entity("sales.customer"))
        assert_type(mv.member(), mv.MemberAxis)
        assert_type(people.classify(by=(mv.member(), field)), mv.Population)
        assert_type(people.project(by=(mv.member(),)), mv.Population)
        assert_type(session.read(people, {"category": field}), mv.Query)
        assert_type(
            session.members(ms.ref.entity("sales.customer"), where=mv.in_population(people)),
            mv.Population,
        )
        assert_type(session.observe({"value": metric}, where=mv.in_population(people)), mv.Query)
        assert_type(
            session.observe({"value": mv.member_count()}, members=people, by=(mv.member(),)),
            mv.Query,
        )
        assert_type(mv.corresponding(via=(relation,)), mv.DeclaredCorrespondence)
        assert_type(mv.ordinal(), mv.CalendarCorrespondence)
        assert_type(mv.period_offset(-1), mv.CalendarCorrespondence)
        assert_type(mv.previous_year(), mv.CalendarCorrespondence)
        query = session.values(
            by=(field,), columns={"budget": "CNY"}, rows=(((1,), {"budget": 10}),)
        )
        assert_type(query, mv.Query)
        return query

    assert_type(
        session.time_scope(start="2026-08-01", end="2026-09-01").before_end, mv.BeforeEndBoundary
    )
