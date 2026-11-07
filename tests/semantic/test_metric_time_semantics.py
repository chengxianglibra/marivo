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
from marivo.analysis.errors import AnalysisError
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
    assert "default_time: " + paid in orders.render()
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
    assert "unavailable" in count.render()
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
        with pytest.raises(AnalysisError, match="contribution time"):
            session.observe({"untimed": expression}, where=report)
        with pytest.raises(AnalysisError, match="contribution time"):
            session.observe({"untimed": expression}, by=(report.by(mv.grain("day")),))
    with pytest.raises(SemanticError, match="Catalog preview does not support"):
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
    with pytest.raises(SemanticError, match=r"Metric\.per.*contribution time"):
        count.per(day)
    with pytest.raises(SemanticError, match=r"Metric\.per.*contribution time"):
        count.per(joined, grain=day)
    for samples in (inherited.per(day), inherited.per(paid, grain=day)):
        with pytest.raises(SemanticError, match=r"weight.*customer_count"):
            samples.weighted_mean(weight=count)
    with pytest.raises(SemanticError, match=r"Metric\.window.*contribution time"):
        count.window(mv.all_history())


@pytest.mark.parametrize(
    "body",
    [
        "customer_count.per(day).mean()",
        "customer_count.per(joined_at, grain=day).mean()",
        "inherited_count.per(day).weighted_mean(weight=customer_count)",
        "inherited_count.per(paid_at, grain=day).weighted_mean(weight=customer_count)",
        "(inherited_count + customer_count * 0).window(history)",
    ],
)
def test_complete_load_rejects_an_illegal_grain_or_window_in_a_named_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _project(
        tmp_path,
        extra=f"\nimport marivo.analysis as mv\nday = mv.grain('day')\nhistory = mv.all_history()\n@ms.metric()\ndef bad():\n    return {body}\n",
    )
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    with pytest.raises(SemanticLoadFailed, match="requires contribution time"):
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
    with pytest.raises(SemanticError, match="internal time axis"):
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
    with pytest.raises(AnalysisError, match="contribution time"):
        session.observe({"untimed": originally_untimed}, where=november)
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
    with pytest.raises(SemanticLoadFailed, match=message):
        ms.load(workspace_dir=tmp_path)


@pytest.mark.parametrize(
    ("time", "message"),
    [
        ("None", "time=None"),
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
    with pytest.raises(SemanticLoadFailed, match=message):
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
        + """
@ms.metric()
def invalid(*, clock: "ms.TimeParameter"):
    return amount.sum(time=clock)
"""
    )
    with pytest.raises(SemanticLoadFailed, match="zero-argument"):
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
    with pytest.raises(SemanticError, match="time=None"):
        amount.sum(time=None)  # type: ignore[arg-type]  # Exercise explicit None.
    with pytest.raises(SemanticError, match="another root"):
        orders.count(time=foreign)
