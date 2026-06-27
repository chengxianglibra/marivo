"""Tests for Phase 1: typed datasource source variants and semantic IR value objects.

Phase 1a tests verify that the generic FileSourceIR has been replaced with typed
ParquetSourceIR and CsvSourceIR dataclasses, with dedicated constructors on
both the md and ms modules, and that the legacy file() builder is removed.

Phase 1b tests verify the semantic IR value objects: MeasureRef, MeasureIR,
parse variants, SqlProvenance, JoinKey, ValidityVersioningIR.open_end, and
the DimensionKind categorical/time-only enum.
"""

from __future__ import annotations

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR
from marivo.semantic.errors import SemanticDecoratorError
from marivo.semantic.ir import (
    AiContextIR,
    DateParse,
    DatetimeParse,
    DimensionKind,
    HourPrefixParse,
    JoinKey,
    MeasureIR,
    SampleIntervalIR,
    SemanticParse,
    SourceLocation,
    SqlProvenance,
    StrptimeParse,
    SymbolKind,
    TimestampParse,
    ValidityVersioningIR,
    source_from_dict,
)
from marivo.semantic.loader import LoaderContext, loader_context
from marivo.semantic.refs import MeasureRef


def test_typed_source_builders_have_no_options_bag() -> None:
    table = ms.table("orders", database=("warehouse", "sales"))
    parquet = ms.parquet("/tmp/orders/*.parquet", hive_partitioning=True, columns=("id", "amount"))
    csv = ms.csv("/tmp/orders.csv", header=False, delimiter="|", columns=("id", "amount"))

    assert isinstance(table, TableSourceIR)
    assert table.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["warehouse", "sales"],
    }
    assert isinstance(parquet, ParquetSourceIR)
    assert parquet.to_dict() == {
        "kind": "parquet",
        "path": "/tmp/orders/*.parquet",
        "hive_partitioning": True,
        "columns": ["id", "amount"],
    }
    assert isinstance(csv, CsvSourceIR)
    assert csv.to_dict() == {
        "kind": "csv",
        "path": "/tmp/orders.csv",
        "header": False,
        "delimiter": "|",
        "columns": ["id", "amount"],
    }


def test_source_value_objects_reject_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"TableSourceIR\.table"):
        TableSourceIR(table=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"TableSourceIR\.database"):
        TableSourceIR(table="orders", database=("warehouse", 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.columns"):
        ParquetSourceIR(path="/tmp/orders.parquet", columns=("id", 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"CsvSourceIR\.header"):
        CsvSourceIR(path="/tmp/orders.csv", header="yes")  # type: ignore[arg-type]


def test_source_builders_reject_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"TableSourceIR\.table"):
        ms.table(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.hive_partitioning"):
        ms.parquet("/tmp/orders.parquet", hive_partitioning="yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.columns"):
        md.parquet("/tmp/orders.parquet", columns="id")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"CsvSourceIR\.delimiter"):
        md.csv("/tmp/orders.csv", delimiter=123)  # type: ignore[arg-type]


def test_datasource_source_builders_match_semantic_builders() -> None:
    assert md.table("orders").to_dict() == ms.table("orders").to_dict()
    assert (
        md.parquet("/tmp/orders.parquet").to_dict() == ms.parquet("/tmp/orders.parquet").to_dict()
    )
    assert md.csv("/tmp/orders.csv").to_dict() == ms.csv("/tmp/orders.csv").to_dict()


def test_source_from_dict_reads_typed_file_variants() -> None:
    assert source_from_dict({"kind": "parquet", "path": "/tmp/orders.parquet"}).to_dict() == {
        "kind": "parquet",
        "path": "/tmp/orders.parquet",
        "hive_partitioning": False,
        "columns": None,
    }
    assert source_from_dict(
        {"kind": "csv", "path": "/tmp/orders.csv", "delimiter": "\t"}
    ).to_dict() == {
        "kind": "csv",
        "path": "/tmp/orders.csv",
        "header": True,
        "delimiter": "\t",
        "columns": None,
    }


def test_file_source_builder_is_removed_from_public_surface() -> None:
    assert not hasattr(ms, "file")
    assert not hasattr(md, "file")
    with pytest.raises(AttributeError):
        _ = ms.file
    with pytest.raises(AttributeError):
        _ = md.file


# ---------------------------------------------------------------------------
# Phase 1b: Semantic IR value objects
# ---------------------------------------------------------------------------


def test_measure_ref_and_kind_are_first_class() -> None:
    ref = MeasureRef("sales.orders.amount")
    assert ref.id == "sales.orders.amount"
    assert ref.kind == SymbolKind.MEASURE
    assert SymbolKind.MEASURE.value == "measure"
    assert {item.value for item in DimensionKind} == {"categorical", "time"}


def test_measure_ir_holds_measure_only_fields() -> None:
    ir = MeasureIR(
        semantic_id="sales.orders.amount",
        domain="sales",
        entity="sales.orders",
        name="amount",
        ai_context=AiContextIR(),
        additivity="additive",
        unit="USD",
        python_symbol="amount",
        location=SourceLocation(file="/tmp/_domain.py", line=10),
    )

    assert ir.kind == SymbolKind.MEASURE
    assert ir.additivity == "additive"
    assert ir.unit == "USD"


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


def test_metric_provenance_and_join_key_value_objects() -> None:
    assert (
        SqlProvenance(sql="select sum(amount) from orders", dialect="duckdb").verification_mode
        == "sql_parity"
    )
    assert JoinKey(from_key="sales.orders.customer_id", to_key="sales.customers.id").to_tuple() == (
        "sales.orders.customer_id",
        "sales.customers.id",
    )


def test_metric_provenance_and_join_key_reject_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"SqlProvenance\.sql"):
        SqlProvenance(sql=42, dialect="duckdb")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"SqlProvenance\.dialect"):
        SqlProvenance(sql="select 1", dialect="")
    with pytest.raises(TypeError, match=r"JoinKey\.from_key"):
        JoinKey(from_key=42, to_key="sales.customers.id")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"JoinKey\.to_key"):
        JoinKey(from_key="sales.orders.customer_id", to_key="")


def test_validity_open_end_has_no_any_payload() -> None:
    ir = ValidityVersioningIR(
        kind="validity",
        valid_from="start_at",
        valid_to="end_at",
        interval="closed_open",
        open_end=("9999-12-31", None),
    )
    assert ir.open_end == ("9999-12-31", None)


# ---------------------------------------------------------------------------
# Phase 1c: Semantic Authoring Surface (measure, metric, from_sql, join_on)
# ---------------------------------------------------------------------------


def test_measure_dimension_metric_and_aggregate_authoring() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
        )

        @ms.dimension(entity=orders)
        def region(orders_table):
            return orders_table.region

        @ms.measure(entity=orders, additivity="additive", unit="USD")
        def amount(orders_table):
            return orders_table.amount

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="select sum(amount) from orders", dialect="duckdb"),
        )
        def revenue(orders_table):
            return orders_table.amount.sum()

        average_amount = ms.aggregate(name="average_amount", measure=amount, agg="mean")

    kinds = {
        ir.semantic_id: type(ir).__name__
        for ir, _callable in ctx.pending_objects
        if hasattr(ir, "semantic_id")
    }
    assert kinds["sales.orders.region"] == "DimensionIR"
    assert kinds["sales.orders.amount"] == "MeasureIR"
    assert kinds["sales.revenue"] == "MetricIR"
    assert average_amount.id == "sales.average_amount"


def test_dimension_rejects_measure_only_arguments_by_signature() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
        )
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, additivity="additive")
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, unit="USD")
        with pytest.raises(TypeError):
            ms.dimension(entity=orders, kind="measure")


def test_multi_entity_metric_requires_root_entity_at_decorator_time() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
        )
        refunds = ms.entity(
            name="refunds", datasource="warehouse", source=ms.table("refunds"), domain=sales
        )

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(entities=[orders, refunds], additivity="additive")
            def net_revenue(orders_table, refunds_table):
                return orders_table.amount.sum() - refunds_table.amount.sum()

        assert "root_entity" in str(exc_info.value)


def test_relationship_uses_join_key_pairs() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
        )
        customers = ms.entity(
            name="customers", datasource="warehouse", source=ms.table("customers"), domain=sales
        )

        @ms.dimension(entity=orders)
        def customer_id(orders_table):
            return orders_table.customer_id

        @ms.dimension(entity=customers)
        def id(customers_table):
            return customers_table.id

        ref = ms.relationship(
            name="orders_to_customers",
            from_entity=orders,
            to_entity=customers,
            keys=[ms.join_on(customer_id, id)],
        )

    relationship_ir = next(
        ir
        for ir, _callable in ctx.pending_objects
        if getattr(ir, "semantic_id", "") == "sales.orders_to_customers"
    )
    assert ref.id == "sales.orders_to_customers"
    assert relationship_ir.keys[0].to_tuple() == ("sales.orders.customer_id", "sales.customers.id")


# ---------------------------------------------------------------------------
# Phase 1d: Time parse variant constructors
# ---------------------------------------------------------------------------


def test_time_dimension_uses_parse_value_object() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
        )

        @ms.time_dimension(entity=orders, granularity="day", parse=ms.strptime("%Y%m%d"))
        def dt(orders_table):
            return orders_table.dt

    time_ir = next(
        ir
        for ir, _callable in ctx.pending_objects
        if getattr(ir, "semantic_id", "") == "sales.orders.dt"
    )
    assert time_ir.parse.kind == "strptime"
    assert time_ir.parse.format == "%Y%m%d"


def test_datetime_and_timestamp_accept_optional_timezone() -> None:
    assert ms.datetime().timezone is None
    assert ms.timestamp().timezone is None
    assert ms.datetime(timezone="UTC").timezone == "UTC"
    assert ms.timestamp(timezone="UTC").timezone == "UTC"


def test_time_dimension_parse_invalid_combinations_are_unconstructable() -> None:
    # data_type is no longer a parameter on strptime or hour_prefix
    with pytest.raises(TypeError):
        ms.strptime(data_type="string")
    with pytest.raises(TypeError):
        ms.hour_prefix("dt", data_type="date")
    # ms.date has been removed — native temporal columns don't need parse
    assert not hasattr(ms, "date")


def test_hour_prefix_requires_hour_granularity_at_decorator_time() -> None:
    ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
    with loader_context(ctx):
        sales = ms.domain(name="sales", default=True)
        orders = ms.entity(
            name="orders", datasource="warehouse", source=ms.table("orders"), domain=sales
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
# Phase 1e: Registry and error vocabulary cutover
# ---------------------------------------------------------------------------


def test_registry_uses_canonical_names() -> None:
    from marivo.semantic.validator import Registry

    registry = Registry()
    assert hasattr(registry, "domains")
    assert hasattr(registry, "entities")
    assert hasattr(registry, "dimensions")
    assert hasattr(registry, "measures")
    assert not hasattr(registry, "models")
    assert not hasattr(registry, "datasets")
    assert not hasattr(registry, "fields")


def test_error_kinds_use_entity_vocabulary() -> None:
    from marivo.semantic.errors import ErrorKind

    assert ErrorKind.MISSING_ENTITIES.value == "missing_entities"
    assert ErrorKind.MISSING_METRIC_ROOT_ENTITY.value == "missing_metric_root_entity"
    assert ErrorKind.INVALID_METRIC_ROOT_ENTITY.value == "invalid_metric_root_entity"
    assert not hasattr(ErrorKind, "MISSING_DATASETS")
    assert not hasattr(ErrorKind, "MISSING_METRIC_ROOT_DATASET")


# ---------------------------------------------------------------------------
# Phase 1f: Measure ref kind for internal resolution
# ---------------------------------------------------------------------------


def test_resolver_accepts_measure_for_internal_resolution() -> None:
    from marivo.semantic.refs import make_ref

    measure_ref = make_ref("sales.orders.amount", ms.SemanticKind.MEASURE)
    assert measure_ref.kind == ms.SemanticKind.MEASURE
