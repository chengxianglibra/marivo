"""Ordinary Metric-first binding uses real Catalog declarations without source I/O."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.deferred import resolver_of, semantic_input
from marivo._definition.observations import ObservationBinding
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._temporal import InstantAnchor
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic._deferred_semantics import consume_field, consume_metric, consume_predicate
from marivo.semantic._expression_values import RowExpression
from marivo.semantic.catalog import Entity
from marivo.semantic.errors import SemanticError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value, terminal_runs


class ProjectFactory(Protocol):
    def __call__(self, files: dict[str, str], load: bool = True) -> SemanticProject: ...


class BoundCatalogFactory(Protocol):
    def __call__(
        self,
        *,
        alternate: bool = False,
        missing: bool = False,
        versioned: bool = False,
        fanout: bool = False,
        sampled: bool = False,
    ) -> Session: ...


def _captured_graph(frame: AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, SourceFrameBinding)
    return frame._backing.root.graph


def _captured_observation(frame: AnalysisFrame) -> ObservationBinding:
    observation = _captured_graph(frame).signature.observation
    assert observation is not None
    return observation


def _model(
    *,
    alternate: bool = False,
    missing: bool = False,
    versioned: bool = False,
    fanout: bool = False,
    sampled: bool = False,
) -> str:
    code = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
customers = ms.entity(name="customers", datasource=warehouse, source=md.table("customers"), primary_key=["id"])
customer_id = ms.dimension_column(name="id", entity=customers, column="id")
region = ms.dimension_column(name="region", entity=customers, column="region")
customer_time = ms.time_dimension_column(name="created_at", entity=customers, column="created_at")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
order_customer = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
order_time = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", is_default=True)
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])
refunds = ms.entity(name="refunds", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])
refund_customer = ms.dimension_column(name="customer_id", entity=refunds, column="customer_id")
refund_time = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at", is_default=True)
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="CNY")
"""
    if versioned:
        code = code.replace(
            'source=md.table("customers"), primary_key=["id"])',
            'source=md.table("customers"), primary_key=["id"], versioning=ms.validity(valid_from=ms.ref.time_dimension("sales.customers.valid_from"), valid_to=ms.ref.time_dimension("sales.customers.valid_to"), interval="closed_open", open_end=(None,), timezone="UTC"))',
        )
        code += 'valid_from = ms.time_dimension_column(name="valid_from", entity=customers, column="valid_from")\nvalid_to = ms.time_dimension_column(name="valid_to", entity=customers, column="valid_to")\n'
    if fanout:
        code = code.replace(
            "from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)]",
            "from_entity=customers, to_entity=orders, keys=[ms.join_on(customer_id, order_customer)]",
        )
    if sampled:
        code = code.replace(
            'column="ordered_at", is_default=True',
            'column="ordered_at", parse=ms.timestamp(timezone="UTC"), sample_interval=(1, "hour"), is_default=True',
        )
    if not missing:
        code += 'refund_buyer = ms.relationship(name="refund_buyer", from_entity=refunds, to_entity=customers, keys=[ms.join_on(refund_customer, customer_id)])\n'
    if alternate:
        code += 'other_buyer = ms.relationship(name="other_buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])\n'
    return (
        code
        + """# Named expressions
amount_value = amount
refund_value = refund_amount
@ms.metric()
def revenue():
    return amount_value.sum()
@ms.metric()
def refund_total():
    return refund_value.sum()
@ms.metric()
def net():
    return revenue - refund_total
"""
    )


@pytest.fixture
def bound_catalog(
    semantic_project_factory: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> BoundCatalogFactory:
    def build(
        *,
        alternate: bool = False,
        missing: bool = False,
        versioned: bool = False,
        fanout: bool = False,
        sampled: bool = False,
    ) -> Session:
        import marivo.datasource.backends as backends

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("binding opened a datasource")

        monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
        semantic_project_factory(
            {
                "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
                "sales/models.py": _model(
                    alternate=alternate,
                    missing=missing,
                    versioned=versioned,
                    fanout=fanout,
                    sampled=sampled,
                ),
            },
            load=False,
        )
        monkeypatch.chdir(tmp_path)
        return mv.session.get_or_create("binding", timezone="Asia/Shanghai")

    return build


@pytest.mark.parametrize("anchored_first", (False, True))
def test_execute_checks_every_repeated_field_anchor(
    bound_catalog: BoundCatalogFactory, anchored_first: bool
) -> None:
    session = bound_catalog(versioned=True)
    entity = session.catalog.get("entity:sales.customers")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    assert isinstance(entity, Entity) and isinstance(region, RowExpression)
    direct = region == "APAC"
    anchored = region.at(session.instant("2026-01-01")) == "APAC"
    condition = anchored & direct if anchored_first else direct & anchored
    query = session.observe({"count": entity.count()}, where=condition)
    observation = _captured_observation(query)
    assert observation is not None
    reads = observation.contributions[0].reads
    assert {read.at for read in reads} == {None, session.instant("2026-01-01")}
    assert terminal_runs(session) == ()


def test_each_fact_occurrence_keeps_its_condition_and_complete_classification(
    bound_catalog: BoundCatalogFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    session = bound_catalog()
    catalog = session.catalog
    region = catalog_value(catalog, "sales.customers.region", kind="field")
    revenue = catalog.get("metric:sales.revenue")
    refunds = catalog.get("metric:sales.refund_total")
    # Two occurrences of the same fact root with different intrinsic conditions.
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    branches = amount.sum().where(amount > 10) + amount.sum().where(amount < 5)
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    query = session.observe(
        {"net": revenue - refunds, "branches": branches},
        by=(scope.by(mv.grain("day")), region),
        where=region == "APAC",
    )
    binding = _captured_observation(query)
    assert binding is not None
    assert len(binding.contributions) == 4
    assert len({c.occurrence for c in binding.contributions}) == 4
    assert {c.root.ref.path for c in binding.contributions} == {"sales.orders", "sales.refunds"}
    assert all(
        c.common_condition is not None
        and c.common_condition.recipe == consume_predicate(region == "APAC").node.recipe
        for c in binding.contributions
    )
    assert [len(c.intrinsic_conditions) for c in binding.contributions] == [0, 0, 1, 1]
    assert (
        binding.contributions[2].intrinsic_conditions
        != binding.contributions[3].intrinsic_conditions
    )
    assert all(c.coordinates[0].path for c in binding.contributions)
    assert all(c.physical_dtype == "unresolved" for c in binding.contributions)
    assert isinstance(binding.coordinates.axes[0], CalendarCoordinate)
    assert isinstance(binding.coordinates.axes[1], AxisBinding)
    assert len(binding.coordinates.axes[0].cells) == 31
    assert terminal_runs(session) == ()
    query.show()
    shown = capsys.readouterr().out
    assert "net" in shown and "branches" in shown
    assert "rows: pending; no input rows read" in shown
    assert terminal_runs(session) == ()
    with pytest.raises(FrozenInstanceError):
        frozen_attribute = "period"
        setattr(binding, frozen_attribute, None)


@pytest.mark.parametrize("problem", ("missing", "alternate"))
def test_each_root_must_have_an_unambiguous_directed_path(
    bound_catalog: BoundCatalogFactory, problem: str
) -> None:
    session = bound_catalog(missing=problem == "missing", alternate=problem == "alternate")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    net = session.catalog.get("metric:sales.net")
    with pytest.raises(AnalysisError) as caught:
        session.observe({"net": net}, where=region == "APAC")
    assert caught.value.expected and caught.value.received and caught.value.repair
    if problem == "alternate":
        assert caught.value.repair.candidates
    assert terminal_runs(session) == ()


def test_explicit_field_routes_and_during_none_are_frozen(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog(alternate=True)
    catalog = session.catalog
    region = catalog_value(catalog, "sales.customers.region", kind="field")
    buyer = catalog.get("relationship:sales.buyer")
    routed = region.via((buyer,))
    with pytest.raises(SemanticError):
        consume_metric(routed.count())
    with pytest.raises(SemanticError):
        ms.additive_over(routed)
    query = session.observe(
        {"revenue": catalog.get("metric:sales.revenue")}, by=(routed,), where=routed == "APAC"
    )
    explicit_none = session.observe(
        {"revenue": catalog.get("metric:sales.revenue")}, by=(routed,), where=routed == "APAC"
    )
    binding = _captured_observation(query)
    assert binding is not None and binding.period is None
    assert binding.contributions[0].period is None
    assert _captured_graph(query).fingerprint == _captured_graph(explicit_none).fingerprint
    assert binding.contributions[0].reads[-1].path == (buyer._parameter.ref,)
    changed = session.observe(
        {"revenue": catalog.get("metric:sales.revenue")}, by=(routed,), where=routed == "EMEA"
    )
    assert _captured_graph(query).fingerprint != _captured_graph(changed).fingerprint
    for bad in ((), [buyer], buyer):
        with pytest.raises(SemanticError):
            consume_field(region.via(bad))
    with pytest.raises(SemanticError):
        consume_field(region.at(session.instant("2026-01-01")))


def test_time_samples_without_period_keep_formula_and_declared_closure(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog()
    revenue = session.catalog.get("metric:sales.revenue")
    field = catalog_value(session.catalog, "sales.orders.ordered_at", kind="field")
    mean = revenue.per((field,)).mean() * 0
    assert _captured_observation(session.observe({"zero": mean})).period is None
    nested = revenue.per((field,)).median() * 0
    assert _captured_observation(session.observe({"zero": nested})).period is None
    # A where time range remains a contribution filter, without an output interval.
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    query = session.observe({"zero": nested}, where=field.within(scope))
    assert _captured_observation(query).period is None
    assert (
        _captured_graph(session.observe({"sample": nested}, by=(scope,))).signature.observation
        is not None
    )


def test_time_role_is_optional_but_explicit_cross_entity_time_keeps_its_path(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog()
    catalog = session.catalog
    customers = catalog.get("entity:sales.customers")
    query = session.observe({"count": customers.count()})
    assert _captured_observation(query).contributions[0].time is None
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    with pytest.raises(CoreRuleError, match="business time role"):
        session.observe({"count": customers.count()}, by=(scope,))
    time = catalog_value(catalog, "sales.customers.created_at", kind="field")
    buyer = catalog.get("relationship:sales.buyer")
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    query = session.observe({"revenue": amount.sum(time=time.via((buyer,)))}, by=(scope,))
    contribution = _captured_observation(query).contributions[0]
    assert contribution.time is not None
    assert contribution.time.field == consume_field(time).reference
    assert contribution.time.path == (buyer._parameter.ref,)
    assert contribution.root == consume_field(amount).root
    assert terminal_runs(session) == ()


def test_same_path_definition_versions_are_not_an_admission_gate(
    bound_catalog: BoundCatalogFactory,
) -> None:

    from marivo._definition.types import ExactRef

    session = bound_catalog()
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    current = consume_field(amount)
    previous = replace(
        current,
        reference=ExactRef(current.reference.ref, "previous"),
        root=ExactRef(current.root.ref, "previous"),
    )
    metric = (
        RowExpression(resolver_of(semantic_input(previous)), amount._context, "numeric") + amount
    ).sum()
    query = session.observe({"value": metric})
    contribution = _captured_observation(query).contributions[0]
    assert contribution.root == current.root
    assert {read.field.ref for read in contribution.reads} >= {current.reference.ref}
    assert terminal_runs(session) == ()


def test_exact_boundaries_calendar_and_host_independence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._temporal import _time_scope_from_contract_data, _validate_time_scope_data

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("clock", timezone="America/New_York")
    scope = session.time_scope(start="2026-03-07T12:00:00", end="2026-03-10T12:00:00")
    assert isinstance(scope.start, InstantAnchor) and isinstance(scope.end, InstantAnchor)
    assert scope.start == datetime(2026, 3, 7, 17, tzinfo=timezone.utc)
    axis = scope.by(mv.grain("day"))
    assert axis.scope is scope
    cells = axis.grid.cells
    assert len(cells) == 4 and cells[0].partial and cells[-1].partial
    assert cells[1].original_end - cells[1].original_start == timedelta(hours=23)
    assert session.instant(scope.end, timezone="Asia/Shanghai") == scope.end
    assert session.instant("2026-01-01T08:00:00+08:00") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    before = repr(axis)
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    assert repr(axis) == before
    assert scope.by(mv.grain("day")) == axis
    assert mv.grain("day")._width_seconds() == 86400
    assert scope._contract().model_dump(mode="json")["schema"] == "time-scope/v1"
    reopened = _time_scope_from_contract_data(scope._contract().model_dump(mode="json"))
    assert reopened == scope
    assert reopened.by(mv.grain("day")) == axis
    assert _validate_time_scope_data(scope._model_dump(mode="json")) == scope
    for value in (datetime(2026, 1, 1), "2026-03-08T02:30:00", "2026-11-01T01:30:00"):
        with pytest.raises(AnalysisError):
            session.instant(value)
    with pytest.raises(ValueError):
        # Intentionally pass a retired string shape to the runtime validator.
        by: object = scope.by
        assert callable(by)
        by("day")
    with pytest.raises(AnalysisError):
        session.time_scope(start="2026-01-01", end="2026-01-01")
    assert terminal_runs(session) == ()


def test_attribute_anchor_preserves_exact_exclusive_endpoint(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog(versioned=True)
    catalog = session.catalog
    region = catalog_value(catalog, "sales.customers.region", kind="field")
    revenue = catalog.get("metric:sales.revenue")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    with pytest.raises(CoreRuleError, match="attribute anchor"):
        session.observe({"revenue": revenue}, by=(region,))
    field = region.via((catalog.get("relationship:sales.buyer"),)).at(scope.end)
    query = session.observe({"revenue": revenue}, by=(field,), where=field == "APAC")
    binding = _captured_observation(query)
    assert binding is not None
    coordinate = binding.contributions[0].coordinates[0]
    assert coordinate.at == scope.end
    assert coordinate.at != scope.start
    assert binding.contributions[0].reads[-1] == coordinate
    original = consume_field(region)
    assert original.field_read is not None
    assert consume_field(field).reference == original.reference
    assert original.field_read.at is None
    assert terminal_runs(session) == ()


def test_to_many_scalar_path_is_rejected_without_source_reads(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog(fanout=True)
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    buyer = session.catalog.get("relationship:sales.buyer")
    with pytest.raises(SemanticError, match="single-valued"):
        consume_field(region.via((buyer,)))
    with pytest.raises(AnalysisError):
        session.observe({"revenue": session.catalog.get("metric:sales.revenue")}, by=(region,))
    assert terminal_runs(session) == ()


def test_sample_interval_does_not_require_period_for_nested_declarations(
    bound_catalog: BoundCatalogFactory,
) -> None:
    session = bound_catalog(sampled=True)
    net = session.catalog.get("metric:sales.net")
    query = session.observe({"zero": net * 0})
    assert _captured_observation(query) is not None
    assert terminal_runs(session) == ()


def test_certified_calendar_uses_frozen_snapshot_and_checks_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    from marivo._temporal import certify_period_calendar, semantic_grain
    from marivo.refs import ref

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("fiscal", timezone="UTC")
    rows = tuple(
        {"date": date(2026, 1, day), "week": "W1" if day < 3 else "W2"} for day in range(1, 5)
    )
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 1, 5)),
        levels={"week": "week"},
        rows=rows,
    )
    grain = semantic_grain(calendar=snapshot.calendar_ref, level="week", snapshot=snapshot)
    scope = session.time_scope(start="2026-01-02", end="2026-01-05")
    axis = scope.by(grain)
    assert len(axis.grid.cells) == 2 and axis.grid.cells[0].partial
    assert axis.grid.snapshot_digest == snapshot.snapshot_digest
    certified_scope = snapshot.period_scope("week", "W1")
    assert certified_scope.by(grain).grid.snapshot_digest == snapshot.snapshot_digest
    with pytest.raises(ValueError, match="coverage"):
        session.time_scope(start="2025-12-31", end="2026-01-05").by(grain)
    with pytest.raises(ValueError, match="timezones"):
        session.time_scope(start="2026-01-01", end="2026-01-05", timezone="Asia/Shanghai").by(grain)
    other = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.other"),
        boundary_timezone="UTC",
        coverage=snapshot.coverage,
        levels={"week": "week"},
        rows=rows,
    )
    with pytest.raises(ValueError, match="different calendar snapshot"):
        certified_scope.by(
            semantic_grain(calendar=other.calendar_ref, level="week", snapshot=other)
        )
    assert terminal_runs(session) == ()


@pytest.mark.parametrize("field_kind", ("dimension", "measure"))
def test_declared_metric_binds_inline_scalar_via(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_kind: str,
) -> None:
    source = _model().replace(
        "# Named expressions",
        'credit = ms.measure_column(name="credit", entity=customers, column="credit", additivity=ms.additive_all(), unit="CNY")\n# Named expressions',
    )
    field_name, literal = ("region", '"APAC"') if field_kind == "dimension" else ("credit", "100")
    source += f"""
field_value = {field_name}
buyer_value = buyer
@ms.metric()
def filtered():
    return amount_value.sum().where(field_value.via((buyer_value,)) == {literal})
"""
    semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/models.py": source,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    import marivo.datasource.backends as backends

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("binding opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    session = mv.session.get_or_create("inline-via", timezone="UTC")
    catalog = session.catalog
    metric = catalog.get("metric:sales.filtered")
    query = session.observe({"filtered": metric})
    contribution = _captured_observation(query).contributions[0]
    assert len(contribution.intrinsic_conditions) == 1
    assert contribution.reads[-1].path == (catalog.get("relationship:sales.buyer")._parameter.ref,)
    assert contribution.reads[-1].field.ref.path == f"sales.customers.{field_name}"
    routed = catalog_value(catalog, f"sales.customers.{field_name}", kind="field").via(
        (catalog.get("relationship:sales.buyer"),)
    )
    with pytest.raises(SemanticError, match="native field"):
        consume_metric(routed.count())
    assert terminal_runs(session) == ()


def test_explicit_count_capture_keeps_each_domain_without_reading(
    bound_catalog: BoundCatalogFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._definition.expressions import MemberCount
    from marivo._definition.graph import walk
    from marivo.datasource.adapters import SourceSession

    session = bound_catalog()
    entity = session.catalog.get("entity:sales.orders")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    p = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((3,),), ((999,),)))
    q = mv.coordinates(by=(mv.identity(entity),), keys=(((2,),),))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("count capture read a datasource")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    frame = session.observe(
        {"p": p.count(), "q": q.count(), "ratio": amount.sum() / p.count()}, by=(scope,)
    )
    graph = _captured_graph(frame)
    counts = tuple(
        node
        for node in walk((graph.root,), resolve=dict(graph.signature.declared_metrics).get).order
        if isinstance(node, MemberCount)
    )
    assert {node.population for node in counts} == {p._definition, q._definition}
    assert len(_captured_observation(frame).count_populations) == 2
    assert frame.axes[0].role == "interval"
    assert terminal_runs(session) == ()
    with pytest.raises(AnalysisError, match="at most one output time axis"):
        session.observe({"p": p.count()}, by=(scope, scope.by(mv.grain("day"))))


def test_observation_context_captures_direct_subject_and_only_explicit_window(
    bound_catalog: BoundCatalogFactory,
) -> None:
    from marivo.analysis.core.frame_interpretation import (
        FrameAxisTime,
        FrameObservationContext,
        FrameTrailingPeriodsWindow,
    )

    session = bound_catalog()
    revenue = session.catalog.get("metric:sales.revenue")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    frame = session.observe(
        {
            "daily": revenue,
            "weekly": revenue.window(mv.trailing_periods(count=7)),
            "anonymous": revenue + 0,
        },
        by=(scope.by(mv.grain("day")),),
    )
    daily, weekly, anonymous = (
        frame.schema.value_contexts[name] for name in ("daily", "weekly", "anonymous")
    )
    assert isinstance(daily, FrameObservationContext) and isinstance(
        weekly, FrameObservationContext
    )
    assert daily.subject is not None and daily.subject.path == "sales.revenue"
    assert weekly.subject == daily.subject
    assert daily.window is None
    assert weekly.window == FrameTrailingPeriodsWindow(7)
    assert daily.time == weekly.time == FrameAxisTime(frame.axes[0].token)
    assert anonymous.subject is None
    selected = frame.select(
        {"copy": frame["weekly"], "again": frame["weekly"], "arithmetic": frame["weekly"] + 1}
    )
    assert tuple(selected.schema.value_contexts) == ("copy", "again")
    assert selected.schema.value_contexts["copy"] is selected.schema.value_contexts["again"]
    assert (
        selected.where(selected["copy"] > 0).schema.value_contexts == selected.schema.value_contexts
    )
    assert terminal_runs(session) == ()


@pytest.mark.parametrize(
    "variant", ["all_history", "since", "to_date", "trailing_duration", "trailing_periods"]
)
def test_explicit_window_fact_variants_are_closed(
    bound_catalog: BoundCatalogFactory, variant: str
) -> None:
    session = bound_catalog()
    metric = session.catalog.get("metric:sales.revenue")
    rules = {
        "all_history": mv.all_history(),
        "since": mv.since(start=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        "to_date": mv.to_date(grain=mv.grain("month")),
        "trailing_duration": mv.trailing_duration(duration=timedelta(days=7)),
        "trailing_periods": mv.trailing_periods(count=7),
    }
    axis = session.time_scope(start="2026-01-10", end="2026-01-12").by(mv.grain("day"))
    frame = session.observe({"value": metric.window(rules[variant])}, by=(axis,))
    assert frame.schema.value_contexts["value"].window.kind == variant


def test_comparison_contexts_keep_distinct_endpoint_windows_and_fixed_choices(
    bound_catalog: BoundCatalogFactory,
) -> None:
    from marivo.analysis.core.frame_interpretation import FrameComparedTime, FrameObservationContext

    session = bound_catalog()
    revenue = session.catalog.get("metric:sales.revenue")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    axis = session.time_scope(start="2026-01-10", end="2026-01-12").by(mv.grain("day"))
    current = session.observe({"q": revenue}, by=(axis, region))
    current = current.fix(current.axes[1], "east")
    baseline = session.observe(
        {"q": revenue.window(mv.trailing_periods(count=7))}, by=(axis, region)
    )
    baseline = baseline.fix(baseline.axes[1], "west")
    compared = current.compare({"prior": mv.baseline(baseline)})
    relation = compared.schema.comparisons[0]
    assert relation.alias == "prior" and relation.binding.kind == "calendar"
    assert compared.schema.fixed_axes == ()
    assert relation.current_fixed_axes[0].key == ("east",)
    assert relation.baseline_fixed_axes[0].key == ("west",)
    group = relation.groups[0]
    assert group.current_context == compared.schema.value_contexts["q"]
    assert group.baseline_context == compared.schema.value_contexts["prior.baseline.q"]
    assert isinstance(group.current_context, FrameObservationContext)
    assert isinstance(group.baseline_context, FrameObservationContext)
    assert group.current_context.time == FrameComparedTime(relation.comparison_id, "current")
    assert group.baseline_context.time == FrameComparedTime(relation.comparison_id, "baseline")
    assert group.current_context.window is None
    assert group.baseline_context.window.count == 7
    projected = compared.select(
        {
            "old": compared["prior.baseline.q"],
            "copy": compared["prior.baseline.q"],
            "delta": compared["prior.absolute.q"],
            "arithmetic": compared["prior.absolute.q"] + 1,
        }
    )
    assert [(v.role, v.name) for v in projected.schema.comparisons[0].groups[0].values] == [
        ("baseline", "old"),
        ("baseline", "copy"),
        ("absolute", "delta"),
    ]
    assert tuple(projected.schema.value_contexts) == ("old", "copy")
    assert current.compare(baseline).schema.comparisons[0].alias is None


def test_ordinal_align_drops_context_references_to_unpublished_comparisons(
    bound_catalog: BoundCatalogFactory,
) -> None:
    from marivo.analysis.core.frame_interpretation import FrameComparedTime, FrameObservationContext

    session = bound_catalog()
    revenue = session.catalog.get("metric:sales.revenue")
    axis = session.time_scope(start="2026-01-10", end="2026-01-12").by(mv.grain("day"))
    current = session.observe({"q": revenue}, by=(axis,))
    baseline = session.observe({"q": revenue.window(mv.trailing_periods(count=7))}, by=(axis,))
    compared = current.compare({"prior": mv.baseline(baseline)})

    aligned = compared.align(
        compared,
        on=((compared.axes[0], compared.axes[0]),),
        match=mv.ordinal(),
    )

    assert aligned.schema.comparisons == ()
    assert all(
        not isinstance(context.time, FrameComparedTime)
        for context in aligned.schema.value_contexts.values()
        if isinstance(context, FrameObservationContext)
    )


def test_nested_comparison_rebinds_existing_endpoint_contexts_without_changing_time(
    bound_catalog: BoundCatalogFactory,
) -> None:
    from marivo.analysis.core.frame_interpretation import FrameComparedTime

    session = bound_catalog()
    revenue = session.catalog.get("metric:sales.revenue")
    axis = session.time_scope(start="2026-01-10", end="2026-01-12").by(mv.grain("day"))
    current = session.observe({"q": revenue}, by=(axis,))
    baseline = session.observe({"q": revenue.window(mv.trailing_periods(count=7))}, by=(axis,))
    first = current.compare({"prior": mv.baseline(baseline)})
    second = first.compare({"next": mv.baseline(first)})
    context = second.schema.value_contexts["prior.baseline.q"]
    assert context.time == FrameComparedTime("current.prior", "baseline")
    assert context.window.count == 7
    assert second.schema.value_contexts["next.baseline.prior.baseline.q"].time == FrameComparedTime(
        "next.prior", "baseline"
    )
    assert {c.comparison_id for c in second.schema.comparisons} >= {"current.prior", "next.prior"}
