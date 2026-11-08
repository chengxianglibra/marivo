"""Contribution time is fixed by the semantic definition, before any source read."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal, NoReturn

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import ref
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._metric_time import metric_time_occurrences
from marivo.semantic.errors import SemanticError, SemanticLoadFailed
from tests.shared_fixtures import catalog_get, write_minimal_catalog_project

_MODEL = """
created_at = ms.time_dimension_column(name="created_at", entity=orders, column="created_at",
    parse=ms.timestamp(timezone="UTC"))
quantity = ms.measure_column(name="quantity", entity=orders, column="quantity",
    additivity=ms.additive_all(), unit="1")
customer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"),
    source=md.table("customers"), primary_key=["customer_id"])
customer_key = ms.dimension_column(name="customer_id", entity=customers, column="customer_id")
segment = ms.dimension_column(name="segment", entity=customers, column="segment")
joined_at = ms.time_dimension_column(name="joined_at", entity=customers, column="joined_at",
    parse=ms.timestamp(timezone="UTC"))
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers,
    keys=[ms.join_on(customer_id, customer_key)])
refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"),
    source=md.table("refunds"), primary_key=["refund_id"],
    default_time=ms.ref.time_dimension("sales.refunds.refunded_at"))
refunded_at = ms.time_dimension_column(name="refunded_at", entity=refunds, column="refunded_at",
    parse=ms.timestamp(timezone="UTC"))
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount",
    additivity=ms.additive_all(), unit="USD")

@ms.metric()
def inherited_amount():
    return amount.sum()

@ms.metric()
def inherited_count():
    return orders.count()

@ms.metric()
def explicit_created():
    return amount.sum(time=created_at)

@ms.metric()
def created_count():
    return orders.count(time=created_at)

@ms.metric()
def inherited_weighted():
    return amount.weighted_mean(weight=quantity)

@ms.metric()
def derived_quantity():
    return (amount - amount * 0.5).sum()

@ms.metric()
def field_count():
    return status.count_distinct()

@ms.metric()
def paid_mean():
    return inherited_amount / inherited_count

@ms.metric()
def created_mean():
    return explicit_created / created_count

@ms.metric()
def difference():
    return inherited_amount - explicit_created

inherited_alias = inherited_amount

@ms.metric()
def alias_twice():
    return inherited_alias + inherited_alias * 2

@ms.metric()
def cashflow():
    return inherited_amount - refund_amount.sum()

@ms.metric()
def customer_count():
    return customers.count()

@ms.metric()
def mixed():
    return inherited_count + customer_count

@ms.metric()
def customer_time_samples():
    return customers.count().per(joined_at).max()

@ms.metric()
def customer_category_samples():
    return customers.count().per(segment).max()
"""

_ORDERS = """orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"), primary_key=["order_id"])"""


def _project(
    path: Path,
    *,
    default: str = 'ms.ref.time_dimension("sales.orders.paid_at")',
    expression: bool = False,
    extra: str = "",
    database: bool = False,
) -> Path:
    write_minimal_catalog_project(path)
    model = path / "models/semantic/sales/objects.py"
    current = model.read_text()
    assert _ORDERS in current
    arguments = (
        'datasource=ms.ref.datasource("warehouse"), source=md.table("orders"),'
        f' primary_key=["order_id"], default_time={default}'
    )
    entity = (
        f"@ms.entity({arguments})\ndef orders(raw):\n    return raw\n"
        if expression
        else f'orders = ms.entity(name="orders", {arguments})'
    )
    model.write_text(current.replace(_ORDERS, entity) + _MODEL + extra)
    if database:
        database_path = path / "facts.duckdb"
        with duckdb.connect(str(database_path)) as connection:
            connection.execute("""
                CREATE TABLE orders AS SELECT * FROM (VALUES
                    (1, 'paid', TIMESTAMP '2026-11-02', TIMESTAMP '2026-10-31', 100.0, 2, 11),
                    (2, 'paid', TIMESTAMP '2026-11-03', TIMESTAMP '2026-11-03', 200.0, 1, 12)
                ) AS rows(order_id, status, paid_at, created_at, amount, quantity, customer_id);
                CREATE TABLE customers AS SELECT * FROM (VALUES
                    (11, 'north', TIMESTAMP '2026-01-01'),
                    (12, 'south', TIMESTAMP '2026-01-02')
                ) AS rows(customer_id, segment, joined_at);
                CREATE TABLE refunds AS SELECT * FROM (VALUES
                    (1, TIMESTAMP '2026-10-30', 25.0),
                    (2, TIMESTAMP '2026-11-02', 50.0)
                ) AS rows(refund_id, refunded_at, amount);
                """)
        (path / "models/datasources/warehouse.py").write_text(
            f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database_path)!r})\n"
        )
    return model


def _reject_source_io(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("Definition or admission reached a business datasource.")


def _clock_facts(
    value: ms.MetricExpression[Literal["numeric"]],
) -> tuple[tuple[str | None, str | None], ...]:
    facts = consume_metric(value)
    return tuple(
        (item.binding.field.ref.key, item.binding.source)
        if item.binding is not None
        else (None, None)
        for item in metric_time_occurrences(facts.node, dict(facts._declared))
    )


@pytest.mark.parametrize("expression", [False, True])
def test_entity_default_closes_forward_reference_for_all_source_reduction_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: bool
) -> None:
    _project(tmp_path, expression=expression)
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    catalog = ms.load(workspace_dir=tmp_path)
    orders = catalog_get(catalog, "entity:sales.orders", kind="entity")
    paid = "time_dimension:sales.orders.paid_at"
    created = "time_dimension:sales.orders.created_at"
    assert orders.definition.default_time == paid
    assert paid in orders.definition.dependencies
    for name in (
        "inherited_amount",
        "inherited_count",
        "inherited_weighted",
        "derived_quantity",
        "field_count",
    ):
        metric = catalog_get(catalog, f"metric:sales.{name}", kind="metric")
        (binding,) = metric.definition.time_bindings
        assert isinstance(binding, ms.MetricTimeFieldBinding)
        assert (binding.field, binding.path, binding.anchor, binding.source) == (
            paid,
            (),
            None,
            "entity_default",
        )
    explicit = catalog_get(catalog, "metric:sales.explicit_created", kind="metric")
    (binding,) = explicit.definition.time_bindings
    assert isinstance(binding, ms.MetricTimeFieldBinding)
    assert (binding.field, binding.source) == (created, "explicit")
    same_clock = catalog_get(catalog, "metric:sales.revenue", kind="metric")
    assert _clock_facts(same_clock) == ((paid, "explicit"),)
    amount = catalog_get(catalog, "measure:sales.orders.amount", kind="measure")
    quantity = catalog_get(catalog, "measure:sales.orders.quantity", kind="measure")
    status = catalog_get(catalog, "dimension:sales.orders.status", kind="dimension")
    buyer = catalog_get(catalog, "relationship:sales.buyer", kind="relationship")
    segment = catalog_get(catalog, "dimension:sales.customers.segment", kind="dimension")
    for reduction in (
        amount.sum(),
        (amount - amount * 0.5).sum(),
        orders.count(),
        amount.weighted_mean(weight=quantity),
        status.count_distinct(),
        amount.sum(where=segment.via((buyer,)) == "north"),
    ):
        assert _clock_facts(reduction) == ((paid, "entity_default"),)


def test_aliases_keep_repeated_occurrences_and_independent_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="defined_clocks", timezone="UTC")
    catalog = session.catalog
    paid = "time_dimension:sales.orders.paid_at"
    created = "time_dimension:sales.orders.created_at"
    difference = catalog_get(catalog, "metric:sales.difference", kind="metric")
    cashflow = catalog_get(catalog, "metric:sales.cashflow", kind="metric")
    twice = catalog_get(catalog, "metric:sales.alias_twice", kind="metric")
    assert _clock_facts(difference) == ((paid, "entity_default"), (created, "explicit"))
    assert _clock_facts(twice) == ((paid, "entity_default"), (paid, "entity_default"))
    assert len({item.branch for item in twice.definition.time_bindings}) == 2
    assert _clock_facts(cashflow) == (
        (paid, "entity_default"),
        ("time_dimension:sales.refunds.refunded_at", "entity_default"),
    )

    november = session.time_scope(start="2026-11-01", end="2026-12-01")
    expected = {
        "created": 200,
        "paid": 300,
        "twice": 900,
        "mean_created": 200,
        "mean_paid": 150,
        "difference": 100,
        "explicit": 300,
    }
    rows = (
        session.observe(
            {
                "created": catalog_get(catalog, "metric:sales.explicit_created", kind="metric"),
                "paid": catalog_get(catalog, "metric:sales.inherited_amount", kind="metric"),
                "twice": twice,
                "mean_created": catalog_get(catalog, "metric:sales.created_mean", kind="metric"),
                "mean_paid": catalog_get(catalog, "metric:sales.paid_mean", kind="metric"),
                "difference": difference,
                "explicit": catalog_get(catalog, "metric:sales.revenue", kind="metric"),
            },
            where=november,
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[0].to_dict() == expected
    months = session.time_scope(start="2026-10-01", end="2026-12-01")
    monthly = (
        session.observe(
            {"difference": difference, "cashflow": cashflow},
            by=(months.by(mv.grain("month")),),
        )
        .compute()
        .to_pandas()
    )
    assert monthly["difference"].tolist() == [-100, 100]
    assert monthly["cashflow"].tolist() == [-25, 250]
    segment = catalog_get(catalog, "dimension:sales.customers.segment", kind="dimension")
    buyer = catalog_get(catalog, "relationship:sales.buyer", kind="relationship")
    amount = catalog_get(catalog, "measure:sales.orders.amount", kind="measure")
    paid_at = catalog_get(catalog, paid, kind="time_dimension")
    created_at = catalog_get(catalog, created, kind="time_dimension")
    anonymous = amount.sum(time=paid_at) - amount.sum(time=created_at)
    anonymous.show()
    total = session.observe({"difference": anonymous}, where=november).compute().to_pandas()
    assert total["difference"].tolist() == [100]
    grouped = (
        session.observe({"difference": anonymous}, by=(months.by(mv.grain("month")),))
        .compute()
        .to_pandas()
    )
    assert grouped["difference"].tolist() == [-100, 100]
    classified = (
        session.observe({"amount": amount.sum()}, by=(segment.via((buyer,)),), where=november)
        .compute()
        .to_pandas()
    )
    assert dict(zip(classified.iloc[:, 0], classified["amount"], strict=True)) == {
        "north": 100,
        "south": 200,
    }


def test_missing_time_stays_complete_for_totals_readiness_preview_and_ordinary_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="untimed", timezone="UTC")
    catalog = session.catalog
    count = catalog_get(catalog, "metric:sales.customer_count", kind="metric")
    mixed = catalog_get(catalog, "metric:sales.mixed", kind="metric")
    raw = catalog_get(catalog, "metric:sales.customer_time_samples", kind="metric")
    ordinary = catalog_get(catalog, "metric:sales.customer_category_samples", kind="metric")
    (fact,) = count.definition.time_bindings
    assert isinstance(fact, ms.MetricNoTimeBinding)
    assert (fact.root, fact.reason) == ("entity:sales.customers", "no_entity_default")
    assert catalog.readiness(refs=[count]).status == "ready"
    source_scope = md.unpruned(max_rows=10, timeout_seconds=10)
    assert catalog.preview(count, scope=source_scope).rows == ({"value": 2},)
    assert session.observe(
        {"mixed": mixed, "raw": raw, "ordinary": ordinary}
    ).compute().to_pandas().iloc[0].to_dict() == {"mixed": 4, "raw": 1, "ordinary": 1}

    orders = catalog_get(catalog, "entity:sales.orders", kind="entity")
    paid = catalog_get(catalog, "time_dimension:sales.orders.paid_at", kind="time_dimension")
    timed = orders.count()
    assert _clock_facts(timed * 2) == ((paid.id, "entity_default"),)
    fixed = session.members(orders).count()
    assert _clock_facts(timed + fixed) == ((paid.id, "entity_default"), (None, None))
    assert session.observe({"combined": timed + fixed}).compute().to_pandas()[
        "combined"
    ].tolist() == [4]
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    report = session.time_scope(start="2026-11-01", end="2026-12-01")
    for expression in (count, mixed, mixed * 0, raw, ordinary, timed + fixed):
        session.observe({"untimed": expression}, where=report)
        session.observe({"untimed": expression}, by=(report.by(mv.grain("day")),))
    with pytest.raises(SemanticError):
        catalog.preview(raw, scope=source_scope)


def test_closed_calendar_samples_and_windows_reject_missing_clock_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    catalog = ms.load(workspace_dir=tmp_path)
    count = catalog_get(catalog, "metric:sales.customer_count", kind="metric")
    inherited = catalog_get(catalog, "metric:sales.inherited_count", kind="metric")
    joined = catalog_get(catalog, "time_dimension:sales.customers.joined_at", kind="time_dimension")
    paid = catalog_get(catalog, "time_dimension:sales.orders.paid_at", kind="time_dimension")
    day = mv.grain("day")
    with pytest.raises(SemanticError):
        count.per(day).mean()
    count.per(joined, grain=day).mean()
    inherited.per(day).weighted_mean(weight=count)
    inherited.per(paid, grain=day).weighted_mean(weight=count)
    count.window(mv.all_history())


@pytest.mark.parametrize("body", ["customer_count.per(day).mean()"])
def test_complete_load_rejects_an_illegal_grain_or_window_in_a_named_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _project(
        tmp_path,
        extra=f"\nimport marivo.analysis as mv\nday = mv.grain('day')\nhistory = mv.all_history()\n@ms.metric()\ndef bad():\n    return {body}\n",
    )
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_native_sample_order_and_status_axis_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(
        tmp_path,
        database=True,
        extra="""
stock = ms.measure_column(name="stock", entity=orders, column="amount", unit="USD",
    additivity=ms.additive_except(paid_at), status_time_dimension=paid_at)

@ms.metric()
def last_stock():
    return stock.sum().per(paid_at).last(order_by=paid_at)
""",
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="native_status_axis", timezone="UTC")
    catalog = session.catalog
    stock = catalog_get(catalog, "metric:sales.last_stock", kind="metric")
    assert session.observe({"last": stock}).compute().to_pandas()["last"].tolist() == [200]
    paid = catalog_get(catalog, "time_dimension:sales.orders.paid_at", kind="time_dimension")
    created = catalog_get(catalog, "time_dimension:sales.orders.created_at", kind="time_dimension")
    amount = catalog_get(catalog, "measure:sales.orders.stock", kind="measure")
    with pytest.raises(SemanticError):
        consume_metric(amount.sum().per(paid).last(order_by=created))


def test_default_changes_only_for_new_definitions_and_is_deterministic_on_cold_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    old_session = mv.session.get_or_create(name="old_default", timezone="UTC")
    old = old_session.catalog
    metric = catalog_get(old, "metric:sales.inherited_amount", kind="metric")
    amount = catalog_get(old, "measure:sales.orders.amount", kind="measure")
    orders = catalog_get(old, "entity:sales.orders", kind="entity")
    customers = catalog_get(old, "entity:sales.customers", kind="entity")
    original = amount.sum()
    original_count = orders.count()
    originally_untimed = customers.count()
    old_scope = old_session.time_scope(start="2026-11-01", end="2026-12-01")
    pending = old_session.observe({"amount": metric}, where=old_scope)

    @ms.metric()
    def local_closed() -> ms.MetricExpression[Literal["numeric"]]:
        return amount.sum()

    expected = old._state._definition_version(ref.metric("sales.inherited_amount"))
    script = """
import sys
import marivo.semantic as ms
from marivo.refs import ref
catalog = ms.load(workspace_dir=sys.argv[1])
print(catalog._state._definition_version(ref.metric("sales.inherited_amount")))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == expected

    model.write_text(
        model.read_text()
        .replace(
            'default_time=ms.ref.time_dimension("sales.orders.paid_at")',
            'default_time=ms.ref.time_dimension("sales.orders.created_at")',
        )
        .replace(
            'source=md.table("customers"), primary_key=["customer_id"])',
            'source=md.table("customers"), primary_key=["customer_id"], '
            'default_time=ms.ref.time_dimension("sales.customers.joined_at"))',
        )
    )
    session = mv.session.get_or_create(name="new_default", timezone="UTC")
    current = session.catalog
    assert current._state._definition_version(ref.metric("sales.inherited_amount")) != expected
    dependency = ref.metric("sales.paid_mean")
    assert current._state._definition_version(dependency) != old._state._definition_version(
        dependency
    )
    assert _clock_facts(metric) == (("time_dimension:sales.orders.paid_at", "entity_default"),)
    assert _clock_facts(original) == (("time_dimension:sales.orders.paid_at", "entity_default"),)
    current_amount = catalog_get(current, "measure:sales.orders.amount", kind="measure")
    assert _clock_facts(current_amount.sum()) == (
        ("time_dimension:sales.orders.created_at", "entity_default"),
    )
    assert pending.compute().to_pandas()["amount"].tolist() == [300]
    november = session.time_scope(start="2026-11-01", end="2026-12-01")
    assert session.observe({"untimed": originally_untimed}, where=november).compute().to_pandas()[
        "untimed"
    ].tolist() == [2]
    current_customers = catalog_get(current, "entity:sales.customers", kind="entity")
    assert _clock_facts(current_customers.count()) == (
        ("time_dimension:sales.customers.joined_at", "entity_default"),
    )
    rows = (
        session.observe(
            {
                "old": original,
                "old_count": original_count,
                "local": local_closed,
                "new": current_amount.sum(),
                "rebound": metric,
            },
            where=november,
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[0].to_dict() == {
        "old": 300,
        "old_count": 2,
        "local": 300,
        "new": 200,
        "rebound": 200,
    }


@pytest.mark.parametrize(
    ("default", "message"),
    [
        ("None", "must identify one native TimeDimension"),
        ('"sales.orders.paid_at"', "must identify one native TimeDimension"),
        ('ms.ref.dimension("sales.orders.status")', "must identify one native TimeDimension"),
        ('ms.ref.time_dimension("sales.orders.status")', "not a declared TimeDimension"),
        ('ms.ref.time_dimension("sales.orders.missing")', "not a declared TimeDimension"),
        ('ms.ref.time_dimension("sales.customers.joined_at")', "invalid default contribution time"),
    ],
)
def test_invalid_default_time_cannot_be_inferred_or_replaced(
    tmp_path: Path, default: str, message: str
) -> None:
    _project(tmp_path, default=default)
    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


@pytest.mark.parametrize(
    ("time", "message"),
    [
        ("joined_at", "another root"),
        ("status", "requires a native or closed TimeDimension"),
        ('"paid_at"', "requires a native or closed TimeDimension"),
    ],
)
def test_invalid_explicit_time_does_not_fall_back_to_the_entity_default(
    tmp_path: Path, time: str, message: str
) -> None:
    _project(
        tmp_path, extra=f"\n@ms.metric()\ndef invalid():\n    return amount.sum(time={time})\n"
    )
    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_zero_parameter_contract_rejects_retired_authoring_shapes(tmp_path: Path) -> None:
    model = _project(tmp_path)
    assert not hasattr(ms, "TimeParameter")
    catalog = ms.load(workspace_dir=tmp_path)
    metric = catalog_get(catalog, "metric:sales.inherited_amount", kind="metric")
    assert not callable(metric)
    assert not hasattr(metric, "bind_time")
    assert not hasattr(metric.definition, "time_parameters")
    model.write_text(
        model.read_text()
        + '\n@ms.metric()\ndef invalid(*, clock: "ms.TimeParameter"):\n    return amount.sum(time=clock)\n'
    )
    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_explicit_none_and_foreign_field_fail_when_constructing_a_closed_reduction(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    amount = catalog_get(catalog, "measure:sales.orders.amount", kind="measure")
    orders = catalog_get(catalog, "entity:sales.orders", kind="entity")
    foreign = catalog_get(
        catalog, "time_dimension:sales.customers.joined_at", kind="time_dimension"
    )
    assert _clock_facts(amount.sum(time=None)) == ((None, None),)
    with pytest.raises(SemanticError):
        orders.count(time=foreign)


def test_report_independent_values_broadcast_after_reduction_and_window_only_clocked_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(
        tmp_path,
        database=True,
        extra="\n@ms.metric()\ndef all_amount():\n    return amount.sum(time=None)\n",
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("mixed-report", timezone="UTC")
    catalog = session.catalog
    amount = catalog_get(catalog, "measure:sales.orders.amount", kind="measure")
    orders = catalog_get(catalog, "entity:sales.orders", kind="entity")
    customers = catalog_get(catalog, "entity:sales.customers", kind="entity")
    independent = catalog_get(catalog, "metric:sales.all_amount", kind="metric")
    timed = amount.sum()
    fixed = session.members(customers).count()
    scope = session.time_scope(start="2026-11-02", end="2026-11-05")
    axis = scope.by(mv.grain("day"))
    observed = session.observe(
        {
            "ratio": timed / fixed,
            "fixed": fixed,
            "independent": independent,
            "count": orders.count(time=None),
        },
        by=(axis,),
    ).compute()
    rows = observed.to_pandas()
    assert rows["ratio"].tolist() == [50, 100, 0]
    assert rows["fixed"].tolist() == [2, 2, 2]
    assert rows["independent"].tolist() == [300, 300, 300]
    assert rows["count"].tolist() == [2, 2, 2]
    assert session.observe(
        {"independent": independent}, where=session.time_scope(start="2030-01-01", end="2031-01-01")
    ).compute().to_pandas()["independent"].tolist() == [300]
    assert session.observe(
        {"independent": independent.window(mv.to_date(grain=mv.grain("month")))}, where=scope
    ).compute().to_pandas()["independent"].tolist() == [300]
    rule = mv.trailing_periods(count=2)
    windowed = (
        session.observe(
            {
                "ratio": (timed / fixed).window(rule),
                "difference": (timed - independent).window(rule),
                "independent": independent.window(rule),
                "fixed": fixed.window(rule),
            },
            by=(axis,),
        )
        .compute()
        .to_pandas()
    )
    assert windowed["ratio"].tolist() == [50, 150, 100]
    assert windowed["difference"].tolist() == [-200, 0, -100]
    assert windowed["independent"].tolist() == [300, 300, 300]
    assert windowed["fixed"].tolist() == [2, 2, 2]
    samples = (
        session.observe({"mean": (timed / fixed).per(mv.grain("day")).mean()}, where=scope)
        .compute()
        .to_pandas()
    )
    assert samples["mean"].tolist() == [75]


def test_captured_time_facts_survive_fixed_derivation_and_cold_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("time-facts", timezone="UTC")
    amount = catalog_get(session.catalog, "measure:sales.orders.amount", kind="measure")
    created = catalog_get(
        session.catalog, "time_dimension:sales.orders.created_at", kind="time_dimension"
    )
    scope = session.time_scope(start="2026-11-02", end="2026-11-05")
    fixed = amount.sum(time=None)
    pending = session.observe(
        {
            "mixed": amount.sum() / fixed,
            "clocks": amount.sum() - amount.sum(time=created),
            "fixed": fixed,
        },
        by=(scope.by(mv.grain("day")),),
    )
    facts = pending.schema.value_time_semantics
    assert isinstance(facts["mixed"], mv.FrameObservedTimeSemantics)
    assert [item.kind for item in facts["mixed"].contributions] == ["clocked", "independent"]
    assert facts["mixed"].contributions[1].source == "explicit_none"
    observed = pending.compute()
    assert observed.schema.value_time_semantics == facts
    derived = observed.select(
        {"ratio": observed["mixed"] * 2, "renamed": observed["fixed"]}
    ).persist()
    assert isinstance(derived.schema.value_time_semantics["ratio"], mv.FrameDerivedTimeSemantics)
    assert derived.schema.value_time_semantics["renamed"] == facts["fixed"]
    from json import loads
    from threading import Event
    from time import monotonic

    from marivo.server.frames import FrameReads
    from marivo.server.wire import Budget, Limits

    budget = Budget(Limits(), "frame", monotonic() + 30, Event())
    wire = loads(FrameReads(tmp_path).report(session.id, derived.artifact_id, budget))
    wire_fact = wire["schema"]["value_time_semantics"]["ratio"]
    assert wire_fact["kind"] == "derived"
    assert wire_fact["contributions"][0]["report_axis"]["grain"]["unit"] == "day"
    assert len(wire_fact["contributions"][0]["report_cells"]) == 3
    assert [item["kind"] for item in wire_fact["contributions"]] == ["clocked", "independent"]
    producer = """
import sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2])
assert isinstance(frame.schema.value_time_semantics['ratio'], mv.FrameDerivedTimeSemantics)
assert [x.kind for x in frame.schema.value_time_semantics['ratio'].contributions] == ['clocked', 'independent']
assert frame.to_pandas()['renamed'].tolist() == [300, 300, 300]
fact = frame.schema.value_time_semantics['ratio'].contributions[0]
assert fact.report_axis.grain.unit == 'day'
assert len(fact.report_cells) == 3
"""
    # Reopening the leaf must not need definitions or the source database.
    import shutil

    shutil.rmtree(tmp_path / "models")
    (tmp_path / "facts.duckdb").unlink()
    result = subprocess.run(
        [sys.executable, "-c", producer, session.id, derived.artifact_id],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_independent_classifications_and_null_clocks_keep_their_own_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    with duckdb.connect(str(tmp_path / "facts.duckdb")) as connection:
        connection.execute("INSERT INTO orders VALUES (3, 'paid', NULL, NULL, 50, 1, 11)")
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("independent-keys", timezone="UTC")
    amount = catalog_get(session.catalog, "measure:sales.orders.amount", kind="measure")
    customer = catalog_get(session.catalog, "dimension:sales.orders.customer_id", kind="dimension")
    clock = catalog_get(
        session.catalog, "time_dimension:sales.orders.paid_at", kind="time_dimension"
    )
    scope = session.time_scope(start="2026-11-02", end="2026-11-05")
    timed = amount.sum().where(customer == 11)
    independent = amount.sum(time=None).where(customer == 12)
    rows = (
        session.observe(
            {"timed": timed, "independent": independent}, by=(customer, scope.by(mv.grain("day")))
        )
        .compute()
        .to_pandas()
    )
    assert rows["timed"].tolist() == [100, 0, 0, 0, 0, 0]
    assert rows["independent"].tolist() == [0, 0, 0, 200, 200, 200]
    all_rows = (
        session.observe({"all": amount.sum(time=None), "timed": amount.sum()}, where=scope)
        .compute()
        .to_pandas()
    )
    assert all_rows["all"].tolist() == [350]
    assert all_rows["timed"].tolist() == [300]
    filtered = amount.sum(time=None).where((clock >= scope.start) & (clock < scope.end))
    assert session.observe({"filtered": filtered}, where=scope).compute().to_pandas()[
        "filtered"
    ].tolist() == [300]


def test_fixed_temporal_population_matches_existing_keys_and_explicit_internal_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("fixed-time-keys", timezone="UTC")
    customers = catalog_get(session.catalog, "entity:sales.customers", kind="entity")
    axis = session.time_scope(start="2026-11-02", end="2026-11-04").by(mv.grain("day"))
    one = mv.coordinates(by=(mv.identity(customers),), keys=(((11,),),))
    two = mv.coordinates(by=(mv.identity(customers),), keys=(((11,),), ((12,),)))
    population = mv.product(axis, two)
    assert isinstance(population, mv.Population)
    frame = session.observe({"members": population.count()}, by=(axis,)).compute()
    assert frame.to_pandas()["members"].tolist() == [2, 2]
    samples = mv.product(axis, one)
    # Explicit identity samples must be compatible with the contribution root;
    # a fixed source-independent count provides a compatible complete domain here.
    count = one.count().per(samples).mean()
    assert session.observe({"mean": count}).compute().to_pandas()["mean"].tolist() == [1]


def test_independent_windows_still_validate_endpoints_and_calendar_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError

    _project(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("independent-window-errors", timezone="UTC")
    customers = catalog_get(session.catalog, "entity:sales.customers", kind="entity")
    count = mv.coordinates(by=(mv.identity(customers),), keys=(((11,),),)).count()
    scope = session.time_scope(start="2026-11-02", end="2026-11-03")
    with pytest.raises(AnalysisError):
        session.observe({"v": count.window(mv.all_history())})
    with pytest.raises(AnalysisError):
        session.observe({"v": count.window(mv.since(start=scope.end))}, where=scope)
    with pytest.raises(AnalysisError):
        session.observe(
            {"v": count.window(mv.trailing_periods(count=2))}, by=(scope.by(mv.grain("hour")),)
        )
    with pytest.raises(AnalysisError):
        session.observe(
            {"v": count.window(mv.to_date(grain=mv.grain("hour")))}, by=(scope.by(mv.grain("day")),)
        )


def test_mixed_nested_samples_do_not_depend_on_operand_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("nested-independent", timezone="UTC")
    amount = catalog_get(session.catalog, "measure:sales.orders.amount", kind="measure")
    customer = catalog_get(session.catalog, "dimension:sales.orders.customer_id", kind="dimension")
    timed, independent = amount.sum(), amount.sum(time=None)
    scope = session.time_scope(start="2026-11-02", end="2026-11-05")
    rows = (
        session.observe(
            {
                "left": (independent + timed).per(customer).sum().per(mv.grain("day")).mean(),
                "right": (timed + independent).per(customer).sum().per(mv.grain("day")).mean(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    # The actual daily/customer pairs are (Nov 2, 11) and (Nov 3, 12).
    # Their mixed values are 100 + 100 and 200 + 200; the daily mean is 300.
    assert rows.iloc[0].to_dict() == {"left": 300, "right": 300}


def test_removed_calendar_axis_retains_window_grain_and_frozen_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, database=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("frozen-window-grain", timezone="UTC")
    amount = catalog_get(session.catalog, "measure:sales.orders.amount", kind="measure")
    scope = session.time_scope(start="2026-11-01", end="2026-12-01")
    cases: tuple[tuple[Literal["day", "month"], int, int], ...] = (
        ("day", 600, 30),
        ("month", 300, 1),
    )
    for grain, expected, cells in cases:
        original = session.observe(
            {"v": amount.sum().window(mv.trailing_periods(count=2))},
            by=(scope.by(mv.grain(grain)),),
        ).compute()
        reduced = original.reduce({"total": original["v"].sum()}).persist()
        reopened = session.persisted_frame(reduced.artifact_id)
        assert reopened.to_pandas()["total"].tolist() == [expected]
        assert reopened.schema.axes == ()
        fact = reopened.schema.value_time_semantics["total"].contributions[0]
        assert isinstance(fact.report_axis, mv.FrameCalendarEndpoint)
        assert isinstance(fact.report_axis.grain, mv.FrameBuiltinGrain)
        assert fact.report_axis.grain.unit == grain
        assert len(fact.report_cells) == cells
        assert fact.report_cells[0][0] == scope.start
        assert fact.report_cells[-1][3] == scope.end
