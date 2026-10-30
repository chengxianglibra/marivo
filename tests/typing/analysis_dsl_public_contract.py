"""Static contracts for typed observations, coordinate domains and terminal reads."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ibis.common.deferred import Deferred
from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic import Entity, Relationship

if TYPE_CHECKING:
    session = mv.session.get_or_create("typing-public", timezone="UTC")

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
                    elif method.kind == "linear" or method.kind == "level_linear":
                        assert_type(method.terms[0].coefficient, int | float | Decimal)
                    elif method.kind == "division" or method.kind == "level_division":
                        assert_type(method.divisor, int | float | Decimal)
                        assert_type(method.operation, Literal["divide", "rescale"])
                    elif method.kind == "shapley":
                        assert_type(method.permutations, int)
                        assert_type(method.seed, int)
                        assert_type(method.game.kind, Literal["group_replacement"])
                    else:
                        if method.kind == "level_ratio_component":
                            assert_type(
                                method.denominator.value,
                                bool | int | float | str | date | Decimal | None,
                            )
                        else:
                            assert_type(method.kind, Literal["additive", "level_additive"])

    def ordinary_observation(
        metric: ms.MetricExpression[Literal["numeric"]], region: ms.Dimension
    ) -> mv.AnalysisFrame:
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
        entity: Entity, region: ms.Dimension, relation: Relationship
    ) -> mv.AnalysisFrame:
        customer = session.catalog.get("entity:sales.customer")
        assert customer.kind == "entity"
        members = session.members(customer)
        assert_type(members, mv.Population)
        assert_type(members.axes, tuple[mv.Grouping, ...])
        assert_type(
            members.axes[0].kind, Literal["field", "identity", "calendar", "interval", "membership"]
        )
        assert_type(session.frame(members), mv.AnalysisFrame)
        fixed = mv.coordinates(by=(mv.identity(entity),), keys=((("C1",),), (("C2",),)))
        assert_type(fixed, mv.Population)
        assert isinstance(fixed, mv.Population)
        complete = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
        assert_type(complete, mv.Population)
        assert isinstance(complete, mv.Population)
        assert_type(mv.in_population(complete), ms.RowExpression[Literal["boolean"]])
        assert_type(complete.where(~mv.in_population(complete)), mv.Population | Deferred)
        assert_type(fixed.union(members), mv.Population | Deferred)
        assert_type(fixed.intersection(members), mv.Population | Deferred)
        assert_type(fixed.difference(members), mv.Population | Deferred)
        assert_type(fixed.project(by=(fixed.axes[0],)), mv.Population | Deferred)
        assert_type(fixed.classify(by=(region,)), mv.Population | Deferred)
        assert_type(fixed.image(via=(relation,)), mv.Population | Deferred)
        assert_type(fixed.preimage(members, via=(relation,)), mv.Population | Deferred)
        descriptor = mv.membership(region, via=((relation,),), unmatched="exclude")
        assert_type(descriptor, mv.Grouping)
        scope = session.time_scope(start="2026-01-01", end="2026-02-01")
        assert_type(mv.product(complete, members), mv.Population)
        assert_type(mv.product(scope, complete), mv.Population)
        assert_type(mv.product(complete, scope.by(mv.grain("day"))), mv.Population)
        revenue_entry = session.catalog.get("metric:sales.revenue")
        assert revenue_entry.kind == "metric"
        revenue = revenue_entry
        assert_type(revenue, ms.Metric)
        assert_type(
            session.observe({"value": revenue}, by=(descriptor,)),
            mv.AnalysisFrame,
        )
        assert_type(session.observe({"value": revenue}, by=complete), mv.AnalysisFrame)
        return session.frame(complete)

    def terminal_reads(artifact: mv.AnalysisFrame) -> tuple[mv.FrameSourceRead, ...]:
        from pandas import DataFrame

        assert_type(session.persisted_frame(artifact.artifact_id), mv.AnalysisFrame)
        assert_type(artifact.show(), None)
        assert_type(session.persisted_frame(artifact.artifact_id).to_pandas(), DataFrame)
        fixed = session.persisted_frame(artifact.artifact_id)
        assert_type(fixed.to_pandas(axis_names={fixed.axes[0].token: "region"}), DataFrame)
        reads = artifact.source_reads
        assert_type(reads, tuple[mv.FrameSourceRead, ...])
        for read in reads:
            assert_type(read.read_id, str)
            assert_type(read.completed_at, datetime)
            assert_type(read.returned_rows, int | None)
            assert_type(read.scopes, tuple[mv.FrameSourceReadScope, ...])
            for scope in read.scopes:
                assert_type(scope.source, str)
                assert_type(scope.period, mv.FrameSourceReadPeriod | None)
                if scope.period is not None:
                    assert_type(scope.period.start, datetime | None)
                    assert_type(scope.period.end, datetime)
                    assert_type(scope.period.timezone, str)
        assert_type(artifact.schema, mv.FrameSchema)
        assert_type(artifact.schema.values["value"], mv.FrameField)
        assert_type(artifact.schema.axes, tuple[mv.FrameAxisSchema, ...])
        page = artifact.read_page(columns=(), limit=100)
        assert_type(page, mv.FramePage)
        assert_type(page.read_stats, mv.FrameReadStats)
        assert_type(page.total_count, int)
        assert_type(page.to_pandas(), DataFrame)
        return reads

    def result_algebra(
        query: mv.AnalysisFrame, other: mv.AnalysisFrame, axis: mv.FrameAxis, field: ms.Dimension
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
        assert_type(value / 2, mv.Column)
        assert_type(value.share(along=(axis,)), mv.Column)
        assert_type(value.share(along=query.axes), mv.Column)
        assert_type(query.over(value.sum(), by=(axis,)), mv.Column)
        assert_type(query.over(mv.row_count()), mv.Column)
        assert_type(query.over(query.over(value.sum()).mean()), mv.Column)
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
        assert_type(query.reduce(value.sum()), mv.AnalysisFrame)
        assert_type(query.reduce((value.sum(), value.mean(), mv.row_count())), mv.AnalysisFrame)
        assert_type(query.align(other, on=((axis, other.axes[0]),)), mv.AnalysisFrame)
        declaration = mv.baseline(other, on=((axis, other.axes[0]),))
        assert_type(declaration, mv.ComparisonBaseline)
        assert_type(declaration.frame, mv.AnalysisFrame)
        assert_type(declaration.on, tuple[tuple[mv.FrameAxis, mv.FrameAxis], ...] | None)
        assert_type(declaration.match, mv.CalendarCorrespondence | None)
        assert_type(query.compare(other), mv.AnalysisFrame)
        assert_type(query.compare(other, on=((axis, other.axes[0]),)), mv.AnalysisFrame)
        assert_type(query.compare(other, match=mv.ordinal()), mv.AnalysisFrame)
        assert_type(query.compare({"reference": mv.baseline(other)}), mv.AnalysisFrame)
        assert_type(
            query.compare(
                {"named": mv.baseline(other, on=((axis, other.axes[0]),), match=mv.ordinal())}
            ),
            mv.AnalysisFrame,
        )
        return query.select({"ratio": value / 2})

    def explicit_identity(
        metric: ms.MetricExpression[Literal["numeric"]], field: ms.Dimension, relation: Relationship
    ) -> mv.AnalysisFrame:
        customer = session.catalog.get("entity:sales.customer")
        assert customer.kind == "entity"
        people = session.members(customer)
        assert_type(mv.identity(customer), mv.Grouping)
        assert_type(people.classify(by=(people.axes[0], field)), mv.Population | Deferred)
        assert_type(people.project(by=(people.axes[0],)), mv.Population | Deferred)
        assert_type(session.read(people, {"category": field}), mv.AnalysisFrame)
        assert_type(
            session.members(
                customer,
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


if TYPE_CHECKING:

    def explain_targets(
        frame: mv.AnalysisFrame, field: ms.Dimension, population: mv.Population
    ) -> None:
        assert_type(frame.attribute(by=(field,)), mv.AnalysisFrame)
        assert_type(frame.attribute(by=population), mv.AnalysisFrame)


def fixed_frame_contract(frame: mv.AnalysisFrame) -> mv.AnalysisFrame:
    from decimal import Decimal

    assert_type(frame.fix(frame.axes[0], "east"), mv.AnalysisFrame)
    assert_type(frame.fix(frame.axes[0], None), mv.AnalysisFrame)
    assert_type(frame.fix(frame.axes[0], ("tenant", 1)), mv.AnalysisFrame)
    assert_type(frame.fix(frame.axes[0], Decimal("1.2")), mv.AnalysisFrame)
    return frame.fix(frame.axes[0], 1)
