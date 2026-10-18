"""Ordinary Metric-first binding uses real Catalog declarations without source I/O."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.deferred import semantic_input
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._temporal import InstantAnchor
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic._deferred_semantics import consume_field, consume_metric, consume_predicate
from marivo.semantic.errors import SemanticError
from marivo.semantic.expressions import Entity


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
customer_time = ms.time_dimension_column(name="created_at", entity=customers, column="created_at", granularity="day")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
order_customer = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
order_time = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", granularity="day", is_default=True)
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])
refunds = ms.entity(name="refunds", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])
refund_customer = ms.dimension_column(name="customer_id", entity=refunds, column="customer_id")
refund_time = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at", granularity="day", is_default=True)
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="CNY")
"""
    if versioned:
        code = code.replace(
            'source=md.table("customers"), primary_key=["id"])',
            'source=md.table("customers"), primary_key=["id"], versioning=ms.validity(valid_from=ms.ref.time_dimension("sales.customers.valid_from"), valid_to=ms.ref.time_dimension("sales.customers.valid_to"), interval="closed_open", open_end=(None,), timezone="UTC"))',
        )
        code += 'valid_from = ms.time_dimension_column(name="valid_from", entity=customers, column="valid_from", granularity="day")\nvalid_to = ms.time_dimension_column(name="valid_to", entity=customers, column="valid_to", granularity="day")\n'
    if fanout:
        code = code.replace(
            "from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)]",
            "from_entity=customers, to_entity=orders, keys=[ms.join_on(customer_id, order_customer)]",
        )
    if sampled:
        code = code.replace(
            'column="ordered_at", granularity="day", is_default=True',
            'column="ordered_at", granularity="hour", parse=ms.timestamp(timezone="UTC", sample_interval=(1, "hour")), is_default=True',
        )
    if not missing:
        code += 'refund_buyer = ms.relationship(name="refund_buyer", from_entity=refunds, to_entity=customers, keys=[ms.join_on(refund_customer, customer_id)])\n'
    if alternate:
        code += 'other_buyer = ms.relationship(name="other_buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])\n'
    return (
        code
        + """finalized = ms.finalize()
amount_value = finalized.resolve(amount)
refund_value = finalized.resolve(refund_amount)
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
def bound_catalog(semantic_project_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
        return mv.session.get_or_create("binding", report_timezone="Asia/Shanghai")

    return build


@pytest.mark.parametrize("anchored_first", (False, True))
def test_execute_checks_every_repeated_field_anchor(bound_catalog, anchored_first: bool) -> None:
    session = bound_catalog(versioned=True)
    entity = session.catalog.entities.get("sales.customers")
    region = session.catalog.fields.get("sales.customers.region")
    assert isinstance(entity, Entity) and isinstance(region, Deferred)
    direct = region == "APAC"
    anchored = region.at(session.instant("2026-01-01")) == "APAC"
    condition = anchored & direct if anchored_first else direct & anchored
    query = session.observe({"count": entity.count()}, where=condition)
    observation = query._graph.signature.observation
    assert observation is not None
    reads = observation.contributions[0].reads
    assert {read.at for read in reads} == {None, session.instant("2026-01-01")}
    assert session.runs().items == ()


def test_each_fact_occurrence_keeps_its_condition_and_complete_classification(
    bound_catalog, capsys
):
    session = bound_catalog()
    catalog = session.catalog
    region = catalog.fields.get("sales.customers.region")
    revenue = catalog.metrics.get("sales.revenue")
    refunds = catalog.metrics.get("sales.refund_total")
    # Two occurrences of the same fact root with different intrinsic conditions.
    amount = catalog.fields.get("sales.orders.amount")
    branches = amount.sum().where(amount > 10) + amount.sum().where(amount < 5)
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    query = session.observe(
        {"net": revenue - refunds, "branches": branches},
        during=scope.by(mv.grain("day")),
        by=(region,),
        where=region == "APAC",
    )
    binding = query._graph.signature.observation
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
    assert session.runs().items == ()
    query.show()
    assert "sales.refunds" in capsys.readouterr().out
    with pytest.raises(FrozenInstanceError):
        binding.period = None


@pytest.mark.parametrize("problem", ("missing", "alternate"))
def test_each_root_must_have_an_unambiguous_directed_path(bound_catalog, problem):
    session = bound_catalog(**{problem: True})
    region = session.catalog.fields.get("sales.customers.region")
    net = session.catalog.metrics.get("sales.net")
    with pytest.raises(AnalysisError) as caught:
        session.observe({"net": net}, where=region == "APAC")
    assert caught.value.expected and caught.value.received and caught.value.repair
    if problem == "alternate":
        assert caught.value.repair.candidates
    assert session.runs().items == ()


def test_explicit_field_routes_and_during_none_are_frozen(bound_catalog):
    session = bound_catalog(alternate=True)
    catalog = session.catalog
    region = catalog.fields.get("sales.customers.region")
    buyer = catalog.relationships.get("sales.buyer")
    routed = region.via((buyer,))
    with pytest.raises(SemanticError):
        consume_metric(routed.count())
    with pytest.raises(SemanticError):
        ms.additive(over=(routed,))
    query = session.observe(
        {"revenue": catalog.metrics.get("sales.revenue")}, by=(routed,), where=routed == "APAC"
    )
    explicit_none = session.observe(
        {"revenue": catalog.metrics.get("sales.revenue")},
        during=None,
        by=(routed,),
        where=routed == "APAC",
    )
    binding = query._graph.signature.observation
    assert binding is not None and binding.period is None
    assert binding.contributions[0].period is None
    assert query._graph.fingerprint == explicit_none._graph.fingerprint
    assert binding.contributions[0].reads[-1].path == (buyer.ref,)
    changed = session.observe(
        {"revenue": catalog.metrics.get("sales.revenue")}, by=(routed,), where=routed == "EMEA"
    )
    assert query._graph.fingerprint != changed._graph.fingerprint
    for bad in ((), [buyer], buyer):
        with pytest.raises(SemanticError):
            consume_field(region.via(bad))
    with pytest.raises(SemanticError):
        consume_field(region.at(session.instant("2026-01-01")))


def test_time_sample_requirement_is_checked_inside_formula_and_declared_closure(bound_catalog):
    session = bound_catalog()
    revenue = session.catalog.metrics.get("sales.revenue")
    field = session.catalog.fields.get("sales.orders.ordered_at")
    nested = revenue.per((field,)).mean() * 0
    with pytest.raises(CoreRuleError, match="explicit observation period"):
        session.observe({"zero": nested})
    # A where time range is a contribution filter, not a substitute for sampling scope.
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    with pytest.raises(CoreRuleError, match="explicit observation period"):
        session.observe({"zero": nested}, where=field.within(scope))
    assert (
        session.observe({"sample": nested}, during=scope)._graph.signature.observation is not None
    )


def test_time_role_is_optional_but_explicit_cross_entity_time_keeps_its_path(bound_catalog):
    session = bound_catalog()
    catalog = session.catalog
    customers = catalog.entities.get("sales.customers")
    query = session.observe({"count": customers.count()})
    assert query._graph.signature.observation.contributions[0].time is None
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    with pytest.raises(CoreRuleError, match="business time role"):
        session.observe({"count": customers.count()}, during=scope)
    time = catalog.fields.get("sales.customers.created_at")
    buyer = catalog.relationships.get("sales.buyer")
    amount = catalog.fields.get("sales.orders.amount")
    query = session.observe({"revenue": amount.sum(time=time.via((buyer,)))}, during=scope)
    contribution = query._graph.signature.observation.contributions[0]
    assert contribution.time.field == consume_field(time).reference
    assert contribution.time.path == (buyer.ref,)
    assert contribution.root == consume_field(amount).root
    assert session.runs().items == ()


def test_same_path_definition_versions_are_not_an_admission_gate(bound_catalog):

    from marivo._definition.types import ExactRef

    session = bound_catalog()
    amount = session.catalog.fields.get("sales.orders.amount")
    current = consume_field(amount)
    previous = replace(
        current,
        reference=ExactRef(current.reference.ref, "previous"),
        root=ExactRef(current.root.ref, "previous"),
    )
    metric = (semantic_input(previous) + amount).sum()
    query = session.observe({"value": metric})
    contribution = query._graph.signature.observation.contributions[0]
    assert contribution.root == current.root
    assert {read.field.ref for read in contribution.reads} >= {current.reference.ref}
    assert session.runs().items == ()


def test_exact_boundaries_calendar_and_host_independence(tmp_path, monkeypatch):
    from marivo._temporal import _time_scope_from_contract_data, _validate_time_scope_data

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("clock", report_timezone="America/New_York")
    scope = session.time_scope(start="2026-03-07T12:00:00", end="2026-03-10T12:00:00")
    assert isinstance(scope.start, InstantAnchor) and isinstance(scope.end, InstantAnchor)
    assert scope.start == datetime(2026, 3, 7, 17, tzinfo=timezone.utc)
    axis = scope.by(mv.grain("day"))
    cells = axis.grid.cells
    assert len(cells) == 4 and cells[0].partial and cells[-1].partial
    assert cells[1].original_end - cells[1].original_start == timedelta(hours=23)
    assert session.instant(scope.end, timezone="Asia/Shanghai") == scope.end
    assert session.instant("2026-01-01T08:00:00+08:00") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    before = repr(axis)
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    assert repr(axis) == before
    assert scope.by(mv.grain("day")) == axis
    reopened = _time_scope_from_contract_data(scope.contract().model_dump(mode="json"))
    assert reopened == scope
    assert reopened.by(mv.grain("day")) == axis
    assert _validate_time_scope_data(scope.model_dump(mode="json")) == scope
    for value in (datetime(2026, 1, 1), "2026-03-08T02:30:00", "2026-11-01T01:30:00"):
        with pytest.raises(AnalysisError):
            session.instant(value)
    with pytest.raises(ValueError):
        scope.by("day")
    with pytest.raises(AnalysisError):
        session.time_scope(start="2026-01-01", end="2026-01-01")
    assert session.runs().items == ()


def test_attribute_anchor_preserves_exact_exclusive_endpoint(bound_catalog):
    session = bound_catalog(versioned=True)
    catalog = session.catalog
    region = catalog.fields.get("sales.customers.region")
    revenue = catalog.metrics.get("sales.revenue")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    with pytest.raises(CoreRuleError, match="attribute anchor"):
        session.observe({"revenue": revenue}, by=(region,))
    field = region.via((catalog.relationships.get("sales.buyer"),)).at(scope.end)
    query = session.observe({"revenue": revenue}, by=(field,), where=field == "APAC")
    binding = query._graph.signature.observation
    assert binding is not None
    coordinate = binding.contributions[0].coordinates[0]
    assert coordinate.at == scope.end
    assert coordinate.at != scope.start
    assert binding.contributions[0].reads[-1] == coordinate
    assert (
        consume_field(field).reference == consume_field(region).reference
        and consume_field(region).field_read.at is None
    )
    assert session.runs().items == ()


def test_to_many_scalar_path_is_rejected_without_source_reads(bound_catalog):
    session = bound_catalog(fanout=True)
    region = session.catalog.fields.get("sales.customers.region")
    buyer = session.catalog.relationships.get("sales.buyer")
    with pytest.raises(SemanticError, match="single-valued"):
        consume_field(region.via((buyer,)))
    with pytest.raises(AnalysisError):
        session.observe({"revenue": session.catalog.metrics.get("sales.revenue")}, by=(region,))
    assert session.runs().items == ()


def test_sampled_time_requirement_survives_nested_declarations_and_zero(bound_catalog):
    session = bound_catalog(sampled=True)
    net = session.catalog.metrics.get("sales.net")
    with pytest.raises(CoreRuleError, match="sampled time"):
        session.observe({"zero": net * 0})
    assert session.runs().items == ()


def test_certified_calendar_uses_frozen_snapshot_and_checks_coverage(tmp_path, monkeypatch):
    from datetime import date

    from marivo._temporal import certify_period_calendar, semantic_grain
    from marivo.refs import ref

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("fiscal", report_timezone="UTC")
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
    assert session.runs().items == ()


@pytest.mark.parametrize("field_kind", ("dimension", "measure"))
def test_declared_metric_binds_inline_scalar_via(
    semantic_project_factory, tmp_path, monkeypatch, field_kind
):
    source = _model().replace(
        "finalized = ms.finalize()",
        'credit = ms.measure_column(name="credit", entity=customers, column="credit", additivity=ms.additive_all(), unit="CNY")\nfinalized = ms.finalize()',
    )
    field_name, literal = ("region", '"APAC"') if field_kind == "dimension" else ("credit", "100")
    source += f"""
field_value = finalized.resolve({field_name})
buyer_value = finalized.resolve(buyer)
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

    def forbidden(*args, **kwargs):
        raise AssertionError("binding opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    session = mv.session.get_or_create("inline-via", report_timezone="UTC")
    catalog = session.catalog
    metric = catalog.metrics.get("sales.filtered")
    query = session.observe({"filtered": metric})
    contribution = query._graph.signature.observation.contributions[0]
    assert len(contribution.intrinsic_conditions) == 1
    assert contribution.reads[-1].path == (catalog.relationships.get("sales.buyer").ref,)
    assert contribution.reads[-1].field.ref.path == f"sales.customers.{field_name}"
    routed = catalog.fields.get(f"sales.customers.{field_name}").via(
        (catalog.relationships.get("sales.buyer"),)
    )
    with pytest.raises(SemanticError, match="native field"):
        consume_metric(routed.count())
    assert session.runs().items == ()
