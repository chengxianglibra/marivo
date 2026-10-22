"""Public grouping capture preserves source bindings and complete-domain semantics."""

from dataclasses import FrozenInstanceError, replace

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.expressions import PerReduce
from marivo._definition.relations import LiteralPopulation, SampleDomain, population_keys
from marivo._definition.types import AxisBinding
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import SemanticError
from tests.analysis.graph.execution_fixtures import source_graph
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import DslCaseFactory, terminal_runs


def test_identity_grouping_and_domain_have_distinct_completion(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = session.catalog.entities.get("sales.customer")
    metric = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    period = session.time_scope(start="2026-08-01", end="2026-09-01")
    members = session.members(session.catalog.entities.get("sales.customer"))
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Grouping construction must not access source rows")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        grouping = mv.identity(entity)
        assert grouping.kind == members.axes[0].kind == "identity"
        assert isinstance(grouping, mv.Grouping)
        grouping.show()
        assert "sales.customer" in capsys.readouterr().out
        assert len(repr(grouping)) < 200 and "\n" not in repr(grouping)
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            grouping.kind = "field"  # type: ignore[misc]  # Runtime immutability probe.
        for by in ((grouping, region), (region, grouping)):
            reuse = tuple(members.axes[0] if item is grouping else item for item in by)
            left = session.observe({"value": metric}, by=(period, *by))
            right = session.observe({"value": metric}, by=(period, *reuse))
            assert source_graph(left).signature == source_graph(right).signature
    assert terminal_runs(session) == before
    observed = session.observe({"value": metric}, by=(period, grouping))
    complete = session.observe({"value": metric}, by=mv.product(period, members))
    inferred = session.observe(
        {"value": metric}, by=(period, grouping), where=mv.in_population(members)
    )
    assert observed.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400]
    assert complete.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400, 0]
    pd.testing.assert_frame_equal(inferred.compute().to_pandas(), observed.compute().to_pandas())


def test_calendar_grouping_is_the_unique_output_time_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    metric = session.catalog.metrics.get("sales.revenue")
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert isinstance(calendar, mv.Grouping) and calendar.kind == "calendar"
    members = session.members(session.catalog.entities.get("sales.customer"))
    captured_calendar = mv.product(members, calendar).axes[1]
    direct = session.observe({"value": metric}, by=(calendar,))
    reused = session.observe({"value": metric}, by=(captured_calendar,))
    assert source_graph(direct).signature == source_graph(reused).signature
    assert direct.compute().to_pandas()[("value", "value")].tolist() == [1000, 99]


def test_shared_consumers_keep_their_grouping_boundaries(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = session.catalog.entities.get("sales.customer")
    region = session.catalog.fields.get("sales.customer.region")
    metric = session.catalog.metrics.get("sales.revenue")
    identity = mv.identity(entity)
    fields = mv.coordinates(by=(region,), keys=(("East",),))
    field = fields.axes[0]
    assert field.kind == "field"
    assert (
        consume_metric(metric.per(field).mean()).node
        == consume_metric(metric.per(region).mean()).node
    )
    people = session.members(session.catalog.entities.get("sales.customer"))
    assert people.project(by=(identity,)).axes == people.axes
    assert people.classify(by=(identity, region)).axes[0].kind == "identity"
    frame = session.values(
        by=(identity,), columns={"value": None}, rows=(((("A",),), {"value": 1}),)
    )
    assert frame.axes[0].role == "identity"
    assert mv.coordinates(by=(identity,), keys=()).axes == people.axes
    with pytest.raises(SemanticError):
        consume_metric(metric.per(identity).mean())
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(people._definition.axes[0],))  # type: ignore[arg-type]  # Internal records are not public grouping inputs.
    with pytest.raises(AnalysisError):
        mv.identity(())
    with pytest.raises(AnalysisError):
        mv.identity(ms.ref.entity("sales.customer"))  # type: ignore[arg-type]  # Unresolved lookup keys are not grouping definitions.


def test_population_predicates_and_classify_have_explicit_boundaries(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = analysis_dsl_case_factory("j1").session
    region = session.catalog.fields.get("sales.customer.region")
    entity = session.catalog.entities.get("sales.customer")
    people = mv.coordinates(by=(mv.identity(entity),), keys=((("missing",),),))
    with pytest.raises(AnalysisError) as caught:
        people.where(region == "east")  # Population selection permits membership leaves only.
    assert caught.value.expected and caught.value.received and caught.value.repair
    with pytest.raises(AnalysisError):
        bool(people)
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    interval = mv.product(people, scope).axes[1]
    with pytest.raises(AnalysisError):
        people.classify(by=(interval,))
    unchanged = people.classify(by=people.axes)
    assert session.frame(unchanged).compute().to_pandas().iloc[:, 0].tolist() == ["missing"]
    left = mv.in_population(mv.coordinates(by=(region,), keys=(("east",),)))
    right = mv.in_population(mv.coordinates(by=(region,), keys=(("west",),)))
    domain = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    assert domain.where(left & right).show() is None
    conjunction = capsys.readouterr().out
    assert domain.where(left | right).show() is None
    disjunction = capsys.readouterr().out
    assert conjunction != disjunction
    assert " & " in conjunction and " | " in disjunction
    assert all(value in conjunction and value in disjunction for value in ("east", "west"))
    assert "in_population" in conjunction and "in_population" in disjunction


def test_population_algebra_matches_business_coordinates_across_definition_versions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    region = session.catalog.fields.get("sales.customer.region")
    current = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    definition = current._definition
    assert isinstance(definition, LiteralPopulation)
    axis = definition.axes[0]
    assert axis.field is not None
    older = mv.Population(
        replace(
            definition,
            axes=(
                replace(
                    axis,
                    entity=replace(axis.entity, version="earlier-entity"),
                    field=replace(axis.field, version="earlier-field"),
                ),
            ),
            keys=(("east",),),
        )
    )
    assert population_keys(older.union(current)._definition) == (("east",), ("west",))
    assert population_keys(older.project(by=current.axes)._definition) == (("east",),)
    assert population_keys(current.where(mv.in_population(older))._definition) == (("east",),)


def test_membership_expansion_preserves_interleaved_fields_and_capture_purity(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    region = session.catalog.fields.get("sales.customer.region")
    period = session.time_scope(start="2026-08-01", end="2026-09-01")
    rule = mv.membership((channel, status), via=(), unmatched="exclude")
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unified grouping capture must not read source rows")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        compact = session.observe({"value": revenue}, by=(period, rule))
        positioned = session.observe({"value": revenue}, by=(period, rule, status))
        assert source_graph(compact).fingerprint == source_graph(positioned).fingerprint
        interleaved = session.observe({"value": revenue}, by=(period, rule, region, status))
        axes = source_graph(interleaved).signature.axes
        assert tuple(
            axis.field.ref.path for axis in axes[1:] if isinstance(axis, AxisBinding) and axis.field
        ) == ("sales.order.channel", "sales.customer.region", "sales.order.status")
        observation = source_graph(interleaved).signature.observation
        assert observation is not None
        assert observation.coordinates.memberships == (rule._binding,)
        assert (
            consume_metric(revenue.per(rule).mean()).node
            == consume_metric(revenue.per((rule, status)).mean()).node
        )
        inner = revenue.per((rule, region, status)).mean()
        nested = consume_metric(inner.per(channel).mean()).node
        assert nested != consume_metric(revenue.per(channel).mean().per(rule).mean()).node
    assert terminal_runs(session) == before
    result = interleaved.compute().to_pandas()
    assert result[("value", "value")].tolist() == [150, 450, 400]


@pytest.mark.parametrize("invalid_by", (None, 1, True))
def test_observe_invalid_grouping_shape_has_structured_repair(
    analysis_dsl_case_factory: DslCaseFactory, invalid_by: object
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    with pytest.raises(AnalysisError) as caught:
        # Exercise the runtime boundary with deliberately invalid public inputs.
        session.observe({"value": revenue}, by=invalid_by)  # type: ignore[arg-type]
    assert caught.value.expected and caught.value.received and caught.value.repair


def test_unified_memberships_reject_ambiguous_positions_and_domain_shapes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    identity = mv.identity(session.catalog.entities.get("sales.order"))
    rule = mv.membership((channel, status), via=(), unmatched="exclude")
    domain = mv.coordinates(by=(channel, status), keys=(("web", "paid"),))
    other = mv.coordinates(by=(status, channel), keys=(("paid", "web"),))
    malformed = (
        (rule, channel),
        (status, rule),
        (rule, status, status),
        (rule, rule),
        (domain,),
        (domain, channel),
        (rule, domain),
        (domain, rule, domain),
        (domain, rule, status),
        (other, rule),
    )
    for by in malformed:
        with pytest.raises(AnalysisError) as caught:
            session.observe({"value": revenue}, by=by)
        assert caught.value.expected and caught.value.received and caught.value.repair
        with pytest.raises(SemanticError):
            consume_metric(revenue.per(by).mean())
    # An identity axis is distinct from a field position even on the same Entity.
    frame = session.observe({"value": revenue}, by=(rule, identity))
    assert len(source_graph(frame).signature.axes) == 3
    with pytest.raises(AnalysisError):
        mv.membership((channel, channel), via=(), unmatched="exclude")
    with pytest.raises(TypeError):
        session.observe({"value": revenue}, memberships=(rule,))  # type: ignore[call-arg]  # Removed public keyword.
    with pytest.raises(SemanticError):
        consume_metric(revenue.per(channel, memberships=(rule,)).mean())
    for consumer in (
        lambda: mv.coordinates(by=(rule,), keys=()),
        lambda: domain.project(by=(rule,)),
        lambda: session.values(by=(rule,), columns={"value": None}, rows=()),
    ):
        with pytest.raises(AnalysisError):
            consumer()


def test_complete_population_keeps_axis_order_and_rule_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    region = session.catalog.fields.get("sales.customer.region")
    rule = mv.membership((channel, status), via=(), unmatched="exclude")
    domain = mv.coordinates(
        by=(channel, region, status),
        keys=(("web", "east", "paid"), ("mobile", "east", "paid"), ("web", "south", "paid")),
    )
    observed = session.observe({"value": revenue}, by=(domain, rule))
    graph = source_graph(observed)
    assert graph.root.population == domain._definition
    assert graph.signature.axes == domain._definition.axes
    sampled = consume_metric(revenue.per((domain, rule)).mean()).node
    assert isinstance(sampled, PerReduce) and isinstance(sampled.samples, SampleDomain)
    assert sampled.samples.population == domain._definition
    assert sampled.samples.axes == domain._definition.axes
    assert sampled.samples.memberships == (rule._binding,)


def test_interleaved_rules_and_nested_samples_have_independent_numeric_oracles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("SET threads=1")
        connection.execute(
            "INSERT INTO \"order\" VALUES ('extra', 'A', 'mobile', 'paid', "
            "TIMESTAMPTZ '2026-08-15T12:00:00+00:00', 52)"
        )
    revenue = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    customer = session.catalog.fields.get("sales.customer.customer_id")
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get("sales.order_buyer")
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    channels = mv.membership((channel, status), via=(), unmatched="exclude")
    customers = mv.membership((customer, region), via=((buyer,),), unmatched="exclude")
    # R=(channel,status), S=(customer,region); (R,S,status,region) gives a,c,b,d.
    ordinary_status = mv.coordinates(by=(status,), keys=()).axes[0]
    pending = session.observe(
        {"value": revenue}, by=(scope, channels, customers, ordinary_status, region)
    )
    axes = source_graph(pending).signature.axes
    assert tuple(
        axis.field.ref.path for axis in axes[1:] if isinstance(axis, AxisBinding) and axis.field
    ) == (
        "sales.order.channel",
        "sales.customer.customer_id",
        "sales.order.status",
        "sales.customer.region",
    )
    observation = source_graph(pending).signature.observation
    assert observation is not None
    assert observation.coordinates.memberships == (channels._binding, customers._binding)
    result = pending.compute().to_pandas()
    keys = ordinary_columns(result)
    assert list(result[[*keys, ("value", "value")]].itertuples(index=False, name=None)) == [
        ("mobile", "A", "paid", "east", 52),
        ("mobile", "B", "paid", "east", 150),
        ("web", "A", "paid", "east", 450),
        ("web", "C", "paid", "south", 400),
    ]
    nested = revenue.per((channels, status)).mean().per((customers, region)).mean()
    reversed_layers = revenue.per((customers, region)).mean().per((channels, status)).mean()
    captured = consume_metric(nested).node
    assert isinstance(captured, PerReduce) and isinstance(captured.samples, SampleDomain)
    assert captured.samples.memberships == (customers._binding,)
    assert isinstance(captured.operand, PerReduce) and isinstance(
        captured.operand.samples, SampleDomain
    )
    assert captured.operand.samples.memberships == (channels._binding,)
    values = (
        session.observe(
            {
                "joint": revenue.per((channels, customers, status, region)).mean(),
                "by_customer": nested,
                "by_channel": reversed_layers,
            },
            by=(scope,),
        )
        .compute()
        .to_pandas()
    )
    # A has two channels (450,52), B one (150), C one (400): (251+150+400)/3.
    assert values[("value", "by_customer")].tolist() == [267]
    # Four joint buckets average 1052/4; channel means are 425 and 101.
    assert values[("value", "joint")].tolist() == [263]
    assert values[("value", "by_channel")].tolist() == [263]
