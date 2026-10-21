"""Static contracts for Deferred observations, coordinate domains and terminal reads."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ibis.common.deferred import Deferred
from typing_extensions import assert_type

import marivo.analysis as mv
from marivo.semantic.expressions import Entity, Relationship

if TYPE_CHECKING:
    session = mv.session.create("typing-public", timezone="UTC")

    def inspect_method_facts(frame: mv.AnalysisFrame) -> None:
        assert_type(frame.method_facts, tuple[mv.FrameMethodFacts, ...])
        for facts in frame.method_facts:
            for parent in facts.parents:
                for metric in parent.metrics:
                    method = metric.method
                    if method.kind == "ratio_component":
                        assert_type(
                            method.numerator_left.value,
                            bool | int | float | str | date | Decimal | None,
                        )
                    elif method.kind == "linear":
                        assert_type(method.terms[0].coefficient, int | float | Decimal)
                    else:
                        assert_type(method.kind, Literal["additive"])

    def ordinary_observation(metric: Deferred, region: Deferred) -> mv.AnalysisFrame:
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(scope.start, mv.InstantAnchor)
        assert_type(scope.end, mv.InstantAnchor)
        assert_type(session.instant(scope.end), mv.InstantAnchor)
        assert_type(scope.by(mv.grain("day")), mv.CalendarAxis)
        assert_type(scope.by(mv.grain("month")).shift(mv.offset(years=-1)), mv.CalendarAxis)
        query = session.observe({"value": metric}, by=(scope, region), where=region == "APAC")
        assert_type(query, mv.AnalysisFrame)
        return query

    def population_contract(
        entity: Entity, region: Deferred, relation: Relationship
    ) -> mv.AnalysisFrame:
        members = session.members(session.catalog.entities.get("sales.customer"))
        assert_type(members, mv.Population)
        assert_type(members.axes, tuple[mv.Grouping, ...])
        assert_type(
            members.axes[0].kind, Literal["field", "identity", "calendar", "interval", "membership"]
        )
        assert_type(session.frame(members), mv.AnalysisFrame)
        fixed = mv.coordinates(by=(mv.identity(entity),), keys=((("C1",),), (("C2",),)))
        assert_type(fixed, mv.Population)
        complete = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
        assert_type(complete, mv.Population)
        assert_type(mv.in_population(complete), Deferred)
        assert_type(complete.where(~mv.in_population(complete)), mv.Population)
        assert_type(fixed.union(members), mv.Population)
        assert_type(fixed.intersection(members), mv.Population)
        assert_type(fixed.difference(members), mv.Population)
        assert_type(fixed.project(by=(fixed.axes[0],)), mv.Population)
        assert_type(fixed.classify(by=(region,)), mv.Population)
        assert_type(fixed.image(via=(relation,)), mv.Population)
        assert_type(fixed.preimage(members, via=(relation,)), mv.Population)
        descriptor = mv.membership(region, via=((relation,),), unmatched="exclude")
        assert_type(descriptor, mv.Grouping)
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(mv.product(complete, scope.by(mv.grain("day"))), mv.Population)
        revenue = session.catalog.metrics.get("sales.revenue")
        assert_type(revenue, Deferred)
        assert_type(
            session.observe({"value": revenue}, by=(region,), memberships=(descriptor,)),
            mv.AnalysisFrame,
        )
        assert_type(session.observe({"value": revenue}, by=complete), mv.AnalysisFrame)
        return session.frame(complete)

    def terminal_reads(artifact: mv.Artifact) -> mv.ArtifactDigest:
        from pandas import DataFrame

        assert_type(session.persisted_frame(artifact.id), mv.AnalysisFrame)
        assert_type(artifact.show(), None)
        assert_type(session.persisted_frame(artifact.id).to_pandas(), DataFrame)
        assert_type(artifact.findings(limit=20, cursor=None), mv.FindingPage)
        digest = artifact.evidence_digest()
        assert_type(digest, mv.ArtifactDigest)
        assert_type(digest.evidence_count, int)
        return digest

    def result_algebra(
        query: mv.AnalysisFrame, other: mv.AnalysisFrame, axis: mv.FrameAxis, field: Deferred
    ) -> mv.AnalysisFrame:
        value = query["value"]
        assert_type(value, mv.Column)
        assert_type(session.population_from(query, on=((axis, field),)), mv.Population)
        assert_type(session.population_from(query, on=()), mv.Population)
        assert_type(query.axes, tuple[mv.FrameAxis, ...])
        assert_type(query.output_names, tuple[str, ...])
        assert_type(query.presence_names, tuple[str, ...])
        assert_type(query.axis(axis.name), mv.FrameAxis)
        assert_type(axis.token, str)
        assert_type(axis.role, Literal["field", "identity", "calendar", "interval", "paired"])
        assert_type(axis.key_names, tuple[str, ...])
        assert_type(axis.show(), None)
        assert_type(query.show(n=0), None)
        assert_type(query.compute(), mv.AnalysisFrame)
        assert_type(query.persist(), mv.AnalysisFrame)
        assert_type(value + 1, mv.Column)
        assert_type(value / 2, mv.Column)
        assert_type(value.divide(2, zero_denominator="error"), mv.Column)
        assert_type(query.coord(axis), mv.Column)
        assert_type(query.select({"net": value - 1, "constant": 1}), mv.AnalysisFrame)
        assert_type(query.where((value > 0) & ~value.is_null()), mv.AnalysisFrame)
        assert_type(query.top(3, by=value), mv.AnalysisFrame)
        assert_type(
            query.reduce(
                {"sum": value.sum(), "mean": value.mean(), "rows": mv.row_count()}, by=(axis,)
            ),
            mv.AnalysisFrame,
        )
        assert_type(query.align(other, on=((axis, other.axes[0]),)), mv.AnalysisFrame)
        assert_type(query.compare({"reference": other}, zero_denominator="null"), mv.AnalysisFrame)
        return query.select({"ratio": value / 2})

    def explicit_identity(
        metric: Deferred, field: Deferred, relation: Relationship
    ) -> mv.AnalysisFrame:
        people = session.members(session.catalog.entities.get("sales.customer"))
        assert_type(mv.identity(session.catalog.entities.get("sales.customer")), mv.Grouping)
        assert_type(people.classify(by=(people.axes[0], field)), mv.Population)
        assert_type(people.project(by=(people.axes[0],)), mv.Population)
        assert_type(session.read(people, {"category": field}), mv.AnalysisFrame)
        assert_type(
            session.members(
                session.catalog.entities.get("sales.customer"),
                where=mv.in_population(people),
            ),
            mv.Population,
        )
        assert_type(
            session.observe({"value": metric}, where=mv.in_population(people)), mv.AnalysisFrame
        )
        assert_type(
            session.observe(
                {"value": people.count()}, by=(people.axes[0],), where=mv.in_population(people)
            ),
            mv.AnalysisFrame,
        )
        assert_type(mv.ordinal(), mv.CalendarCorrespondence)
        assert_type(mv.offset(periods=-1), mv.CalendarCorrespondence)
        assert_type(mv.offset(years=-1), mv.CalendarCorrespondence)
        query = session.values(
            by=(field,), columns={"budget": "CNY"}, rows=(((1,), {"budget": 10}),)
        )
        assert_type(query, mv.AnalysisFrame)
        return query

    assert_type(session.time_scope(start="2026-08-01", end="2026-09-01").end, mv.InstantAnchor)
