"""Projection-only datasource source variants and semantic IR value objects."""

from __future__ import annotations

import pytest
from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.deferred import root_declaration
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import SemanticDecoratorError, SemanticExpressionError
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    DimensionKind,
    HourPrefixParse,
    JoinKey,
    SampleIntervalIR,
    SemanticKind,
    SemanticParse,
    StrptimeParse,
    TimestampParse,
)
from marivo.semantic.loader import LoaderContext, loader_context
from tests.shared_fixtures import in_memory_datasource_ir

# ---------------------------------------------------------------------------
# Semantic IR value objects
# ---------------------------------------------------------------------------


def test_measure_ref_and_kind_are_first_class() -> None:
    ref = ms.ref.measure("sales.orders.amount")
    assert ref.path == "sales.orders.amount"
    assert ref.kind == SemanticKind.MEASURE
    assert SemanticKind.MEASURE.value == "measure"
    assert {item.value for item in DimensionKind} == {"categorical", "time"}


def test_time_parse_value_objects_are_closed_variants() -> None:
    assert isinstance(DateParse(), SemanticParse)
    assert DatetimeParse(timezone="Asia/Shanghai").kind == "datetime"
    assert (
        TimestampParse(timezone="UTC", sample_interval=SampleIntervalIR(5, "minute")).kind
        == "timestamp"
    )
    assert StrptimeParse(format="%Y%m%d").kind == "strptime"
    assert HourPrefixParse(prefix="dt").kind == "hour_prefix"


def test_time_parse_value_objects_reject_invalid_payloads() -> None:
    with pytest.raises(ValueError, match=r"SampleIntervalIR\.count"):
        SampleIntervalIR(0, "minute")
    with pytest.raises(ValueError, match=r"SampleIntervalIR\.unit"):
        SampleIntervalIR(1, "day")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"DatetimeParse\.timezone"):
        DatetimeParse(timezone=42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"DatetimeParse\.timezone"):
        DatetimeParse(timezone="not/a-zone")
    with pytest.raises(TypeError, match=r"TimestampParse\.sample_interval"):
        TimestampParse(sample_interval=(1, "hour"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"StrptimeParse\.format"):
        StrptimeParse(format="yyyymmdd")
    with pytest.raises(TypeError, match=r"HourPrefixParse\.prefix"):
        HourPrefixParse(prefix=42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"DateParse\.kind"):
        DateParse(kind="datetime")  # type: ignore[arg-type]


def test_join_key_value_object() -> None:
    key = JoinKey(from_key="sales.orders.customer_id", to_key="sales.customers.id")
    assert key.from_key == "sales.orders.customer_id"
    assert key.to_key == "sales.customers.id"
    assert not hasattr(key, "to_tuple")


def test_join_key_rejects_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"JoinKey\.from_key"):
        JoinKey(from_key=42, to_key="sales.customers.id")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"JoinKey\.to_key"):
        JoinKey(from_key="sales.orders.customer_id", to_key="")


# ---------------------------------------------------------------------------
# Semantic Authoring Surface (measure, metric, join_on)
# ---------------------------------------------------------------------------


def test_measure_dimension_metric_and_aggregate_authoring() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )

        @ms.dimension(entity=orders)
        def region(orders_table):
            return orders_table.region

        amount = ms.measure_column(
            name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD"
        )

        finalized = ms.finalize()
        amount_value = finalized.resolve(amount)
        orders_value = finalized.resolve(orders)

        @ms.measure()
        def named_amount() -> Deferred:
            return amount_value

        @ms.metric(
            ai_context=ms.ai_context(
                business_definition="Historical SQL: select sum(amount) from orders"
            )
        )
        def revenue() -> Deferred:
            return named_amount.sum()

        average_amount = amount_value.mean()
    kinds = {
        pending.ref.path: type(pending.definition).__name__ for pending in ctx.pending_definitions
    }
    assert kinds["sales.orders.region"] == "DimensionIR"
    assert kinds["sales.orders.amount"] == "MeasureIR"
    assert "sales.revenue" not in kinds
    assert root_declaration(named_amount).name == "named_amount"
    assert root_declaration(revenue).name == "revenue"
    assert consume_metric(average_amount).roots == (orders_value.ref,)


def test_dimension_rejects_measure_only_arguments_by_signature() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, additivity=ms.additive_all())
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, unit="USD")
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, kind="measure")


def test_multi_entity_metric_requires_root_entity_at_decorator_time() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )
        refunds = ms.entity(
            name="refunds",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("refunds"),
            domain=sales,
        )

        with pytest.raises(SemanticExpressionError) as exc_info:

            @ms.metric(entities=[orders, refunds], additivity=ms.additive_all())
            def net_revenue(orders_table, refunds_table):
                return orders_table.amount.sum() - refunds_table.amount.sum()

        assert "zero-argument" in exc_info.value.repair.action


def test_relationship_uses_join_key_pairs() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )
        customers = ms.entity(
            name="customers",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("customers"),
            domain=sales,
        )

        customer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
        id = ms.dimension_column(name="id", entity=customers, column="id")

        ref = ms.relationship(
            name="orders_to_customers",
            from_entity=orders,
            to_entity=customers,
            keys=[ms.join_on(customer_id, id)],
        )

    relationship_ir = next(
        pending.definition
        for pending in ctx.pending_definitions
        if pending.ref.path == "sales.orders_to_customers"
    )
    assert ref.path == "sales.orders_to_customers"
    assert relationship_ir.keys[0].from_key == "sales.orders.customer_id"
    assert relationship_ir.keys[0].to_key == "sales.customers.id"


# ---------------------------------------------------------------------------
# Time parse variant constructors
# ---------------------------------------------------------------------------


def test_time_dimension_uses_parse_value_object() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )

        @ms.time_dimension(entity=orders, granularity="day", parse=ms.strptime("%Y%m%d"))
        def dt(orders_table):
            return orders_table.dt

    time_ir = next(
        pending.definition
        for pending in ctx.pending_definitions
        if pending.ref.path == "sales.orders.dt"
    )
    assert time_ir.parse.kind == "strptime"
    assert time_ir.parse.format == "%Y%m%d"


def test_datetime_and_timestamp_accept_optional_timezone() -> None:
    assert ms.datetime().timezone is None
    assert ms.timestamp().timezone is None
    assert ms.datetime(timezone="UTC").timezone == "UTC"
    assert ms.timestamp(timezone="UTC").timezone == "UTC"


def test_hour_prefix_requires_hour_granularity_at_decorator_time() -> None:
    ctx = LoaderContext(
        model_name="sales",
        file_path="/tmp/_domain.py",
        datasource_irs=(in_memory_datasource_ir("warehouse"),),
    )
    with loader_context(ctx):
        sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            domain=sales,
        )

        @ms.time_dimension(entity=orders, granularity="day")
        def dt(orders_table):
            return orders_table.dt

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(entity=orders, granularity="day", parse=ms.hour_prefix(dt))
            def hh(orders_table):
                return orders_table.hh

    assert "hour_prefix" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Registry and error vocabulary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Measure ref kind for internal resolution
# ---------------------------------------------------------------------------
