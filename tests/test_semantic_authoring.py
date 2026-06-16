"""Tests for marivo.semantic.authoring — decorator and builder implementation.

Tests cover:
- Outside-loader-context guard
- Model name resolution (explicit > default_domain > MissingModelError)
- All decorator signatures (keyword-only enforcement)
- name defaults to function __name__
- Ref types returned by decorators
- ms.ratio(), ms.weighted_average(), ms.linear() derived registration
- Duplicate name detection
- Provenance fields on metric
- ms.ref() builder
- Derived metric validation: ratio/weighted_average/linear
"""

from __future__ import annotations

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, SemanticLoadError
from marivo.semantic.ir import (
    AiContextIR,
    DimensionIR,
    DimensionKind,
    DimensionRef,
    EntityRef,
    MetricIR,
    MetricRef,
    RelationshipRef,
    TimeDimensionRef,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enter_ctx(**kwargs: object) -> LoaderContext:
    """Create a LoaderContext and set it as the current context."""
    ctx = LoaderContext(**kwargs)  # type: ignore[arg-type]
    _LOADER_CTX.set(ctx)
    return ctx


def _exit_ctx() -> None:
    """Reset the loader context."""
    _LOADER_CTX.set(None)


@pytest.fixture(autouse=True)
def _clean_ctx():
    """Ensure loader context is clean before and after each test."""
    _exit_ctx()
    yield
    _exit_ctx()


# ---------------------------------------------------------------------------
# Outside-loader-context guard
# ---------------------------------------------------------------------------


def test_model_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.domain(name="sales")
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_dataset_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.dimension(entity="orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_time_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.time_dimension(entity="orders", data_type="date", granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_simple_metric_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_relationship_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.relationship(
            from_entity="orders",
            to_entity="items",
            from_dimensions=["id"],
            to_dimensions=["order_id"],
        )
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


# ---------------------------------------------------------------------------
# ms.domain() call
# ---------------------------------------------------------------------------


def test_model_creates_model_ir() -> None:
    ctx = _enter_ctx()
    try:
        ms.domain(name="sales", default=True, description="Sales model")
        # Should have one pending object
        assert len(ctx.pending_objects) == 1
        ir, callable_ = ctx.pending_objects[0]
        assert ir.name == "sales"
        assert ir.default is True
        assert ir.description == "Sales model"
        # model() is not a decorator — no callable
        assert callable_ is None
    finally:
        _exit_ctx()


def test_model_sets_default_domain_on_context() -> None:
    ctx = _enter_ctx()
    try:
        assert ctx.default_domain is None
        ms.domain(name="sales", default=True)
        assert ctx.default_domain == "sales"
    finally:
        _exit_ctx()


def test_model_default_false_does_not_set_context() -> None:
    ctx = _enter_ctx(default_domain="existing")
    try:
        ms.domain(name="other", default=False)
        assert ctx.default_domain == "existing"
    finally:
        _exit_ctx()


def test_model_requires_keyword_args() -> None:
    _enter_ctx()
    try:
        with pytest.raises(TypeError):
            ms.domain("sales")  # type: ignore[misc]
    finally:
        _exit_ctx()


def test_model_returns_model_ref() -> None:
    _enter_ctx()
    try:
        ref = ms.domain(name="sales", default=True)
        assert isinstance(ref, ms.DomainRef)
        assert ref.semantic_id == "sales"
    finally:
        _exit_ctx()


def test_field_accepts_model_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        sales_ref = ms.domain(name="sales", default=True)
        ds = ms.entity(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )

        @ms.dimension(entity=ds, domain=sales_ref)
        def region(table):
            return table.region

        assert region.semantic_id == "sales.orders.region"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.entity() decorator
# ---------------------------------------------------------------------------


def test_dataset_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

        assert isinstance(orders, EntityRef)
        assert orders.semantic_id == "sales.orders"
    finally:
        _exit_ctx()


def test_dataset_requires_name_without_body() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.entity(datasource="wh", source=ms.table("orders"))  # type: ignore[call-arg]
    finally:
        _exit_ctx()


def test_dataset_explicit_name() -> None:
    _enter_ctx(default_domain="sales")
    try:
        _orders_impl = ms.entity(
            name="orders_tbl",
            datasource="wh",
            source=ms.table("orders"),
        )

        assert isinstance(_orders_impl, EntityRef)
        assert _orders_impl.semantic_id == "sales.orders_tbl"
    finally:
        _exit_ctx()


def test_dataset_pushes_ir_without_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))
        ir, callable_ = ctx.pending_objects[-1]
        assert ref.semantic_id == "sales.orders"
        assert ir.semantic_id == "sales.orders"
        assert ir.domain == "sales"
        assert ir.name == "orders"
        assert ir.datasource == "wh"
        assert ir.source == ms.table("orders")
        assert callable_ is None
    finally:
        _exit_ctx()


def test_dataset_datasource_as_string() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_datasource_as_datasource_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        warehouse = md.ref("wh")
        ms.entity(name="orders", datasource=warehouse, source=ms.table("orders"))

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_primary_key() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource="wh",
            source=ms.table("orders"),
            primary_key=["order_id"],
        )

        ir, _ = ctx.pending_objects[-1]
        assert ir.primary_key == ("order_id",)
    finally:
        _exit_ctx()


def test_dataset_source_records_table_database() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource="wh",
            source=ms.table("orders", database="sales_mart"),
        )

        ir, _ = ctx.pending_objects[-1]
        assert ir.source.kind == "table"
        assert ir.source.table == "orders"
        assert ir.source.database == "sales_mart"
    finally:
        _exit_ctx()


def test_dataset_source_records_file_source() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource="wh",
            source=ms.file("/data/orders/*.parquet", format="parquet", hive_partitioning=True),
        )

        ir, _ = ctx.pending_objects[-1]
        assert ir.source.kind == "file"
        assert ir.source.path == "/data/orders/*.parquet"
        assert ir.source.format == "parquet"
        assert ir.source.options == {"hive_partitioning": True}
    finally:
        _exit_ctx()


def test_file_source_rejects_unsupported_format() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.file("/data/orders.json", format="json")  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "format must be 'parquet' or 'csv'" in exc_info.value.message


def test_table_source_constructor() -> None:
    """ms.table and ms.file produce correct IR objects."""
    from marivo.semantic.ir import FileSourceIR, TableSourceIR

    tbl = ms.table("orders", database="sales_mart")
    assert isinstance(tbl, TableSourceIR)
    assert tbl.table == "orders"
    assert tbl.database == "sales_mart"

    fl = ms.file("/data/orders.csv", format="csv", delimiter=",")
    assert isinstance(fl, FileSourceIR)
    assert fl.path == "/data/orders.csv"
    assert fl.format == "csv"
    assert fl.options == {"delimiter": ","}


def test_entity_is_not_a_decorator() -> None:
    """ms.entity() is a plain call returning EntityRef, not a decorator."""
    _enter_ctx(default_domain="sales")
    try:
        result = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))
        assert isinstance(result, EntityRef)
        # It does not accept a function body — it returns a ref, not a decorator.
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.dimension() decorator
# ---------------------------------------------------------------------------


def test_field_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(amount, DimensionRef)
        assert amount.semantic_id == "sales.orders.amount"
    finally:
        _exit_ctx()


def test_field_name_defaults_to_function_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.name == "amount"
        assert ir.is_time_dimension is False
        assert ir.data_type is None
        assert ir.granularity is None
    finally:
        _exit_ctx()


def test_field_explicit_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(name="order_amount", entity="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.name == "order_amount"
        assert ir.semantic_id == "sales.orders.order_amount"
    finally:
        _exit_ctx()


def test_field_with_dataset_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ds_ref = EntityRef("sales.orders")

        @ms.dimension(entity=ds_ref)
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(amount, DimensionRef)
    finally:
        _exit_ctx()


def test_field_pushes_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        def amount_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.dimension(entity="sales.orders")(amount_fn)
        ir, callable_ = ctx.pending_objects[-1]
        assert callable_ is amount_fn
        assert ir.entity == "sales.orders"
    finally:
        _exit_ctx()


def test_field_body_rejects_lambda() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.dimension(entity="sales.orders")
            def amount(table: object) -> object:
                fn = lambda value: value
                return fn(table)

        assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN
    finally:
        _exit_ctx()


def test_field_kind_defaults_to_dimension() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        irs = [obj for obj, _ in ctx.pending_objects if isinstance(obj, DimensionIR)]
        assert len(irs) == 1
        assert irs[0].kind == DimensionKind.CATEGORICAL
    finally:
        _exit_ctx()


def test_field_kind_measure() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity="sales.orders", kind="measure")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        irs = [obj for obj, _ in ctx.pending_objects if isinstance(obj, DimensionIR)]
        assert len(irs) == 1
        assert irs[0].kind == DimensionKind.MEASURE
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.time_dimension() decorator
# ---------------------------------------------------------------------------


def test_time_field_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(entity="sales.orders", data_type="date", granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(order_date, TimeDimensionRef)
        assert order_date.semantic_id == "sales.orders.order_date"
    finally:
        _exit_ctx()


def test_time_field_ir_has_time_metadata() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity="sales.orders",
            data_type="timestamp",
            granularity="hour",
            required_prefix="order_date",
        )
        def order_hour(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.is_time_dimension is True
        assert ir.data_type == "timestamp"
        assert ir.granularity == "hour"
        assert ir.required_prefix == "order_date"
        assert ir.kind == DimensionKind.TIME
    finally:
        _exit_ctx()


def test_time_field_requires_data_type_and_granularity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):

            @ms.time_dimension(entity="sales.orders")  # type: ignore[call-arg]
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]
    finally:
        _exit_ctx()


def test_time_field_body_rejects_sql_escape_hatch() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_dimension(entity="sales.orders", data_type="date", granularity="day")
            def order_date(backend: object) -> object:
                return backend.sql("select current_date")

        assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH
        assert exc_info.value.constraint_id == "ast_sql_escape_hatch"
    finally:
        _exit_ctx()


def test_time_field_accepts_timezone_metadata() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity="sales.orders",
            data_type="timestamp",
            granularity="hour",
            timezone="UTC",
        )
        def created_at(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.timezone == "UTC"
        assert ir.ai_context == AiContextIR()
    finally:
        _exit_ctx()


def test_time_field_rejects_invalid_timezone() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="timestamp",
                granularity="hour",
                timezone="Mars/Olympus",
            )
            def created_at(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert "timezone" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_yyyymmdd_shorthand() -> None:
    """Shorthand aliases like 'yyyymmdd' are no longer accepted."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="string",
                granularity="day",
                date_format="yyyymmdd",
            )
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "sales.orders.order_date" in exc_info.value.semantic_refs
    finally:
        _exit_ctx()


def test_time_field_rejects_hh_shorthand() -> None:
    """Shorthand 'hh' is no longer accepted."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="string",
                granularity="hour",
                date_format="hh",
            )
            def order_hour(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
    finally:
        _exit_ctx()


def test_time_field_rejects_date_format_on_temporal_type() -> None:
    """data_type='date'/'datetime'/'timestamp' must not carry date_format."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="datetime",
                granularity="day",
                date_format="%Y-%m-%d",
            )
            def created_at(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "date_format" in exc_info.value.message
        assert "already temporal" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_date_format_on_date_type() -> None:
    """data_type='date' must not carry date_format either."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="date",
                granularity="day",
                date_format="%Y-%m-%d",
            )
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "already temporal" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_date_format_on_hour_only_field() -> None:
    """Fields with required_prefix must not carry date_format."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="string",
                granularity="hour",
                required_prefix="order_date",
                date_format="%H",
            )
            def order_hour(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "hour-only" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_missing_date_format_on_string() -> None:
    """string/integer data_type without date_format or required_prefix is rejected."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="string",
                granularity="day",
            )
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "requires" in exc_info.value.message
        assert "date_format" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_accepts_canonical_strptime() -> None:
    """Sanity: a valid %Y%m%d format goes through cleanly."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity="sales.orders",
            data_type="string",
            granularity="day",
            date_format="%Y%m%d",
        )
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.format == "%Y%m%d"
    finally:
        _exit_ctx()


def test_time_field_strips_whitespace_from_strptime() -> None:
    """normalize_strptime strips whitespace before validation."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity="sales.orders",
            data_type="string",
            granularity="day",
            date_format="  %Y-%m-%d  ",
        )
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.format == "%Y-%m-%d"
    finally:
        _exit_ctx()


def test_time_field_rejects_invalid_strptime_directive() -> None:
    """Unknown strptime directives like %Q are rejected."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.orders",
                data_type="string",
                granularity="day",
                date_format="%Q%m%d",
            )
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
    finally:
        _exit_ctx()


def test_time_dimension_accepts_sample_interval() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity="sales.bandwidth_samples",
            data_type="timestamp",
            granularity="second",
            timezone="UTC",
            sample_interval=(5, "minute"),
        )
        def sample_ts(table: object) -> object:
            return None  # type: ignore[unreachable]

        irs = [obj for obj, _ in ctx.pending_objects if isinstance(obj, DimensionIR)]
        assert irs[-1].sample_interval is not None
        assert irs[-1].granularity == "second"
        assert irs[-1].sample_interval.count == 5
        assert irs[-1].sample_interval.unit == "minute"
    finally:
        _exit_ctx()


def test_time_dimension_rejects_invalid_sample_interval_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.bandwidth_samples",
                data_type="timestamp",
                granularity="second",
                sample_interval=(1, "day"),  # type: ignore[arg-type]
            )
            def sample_ts(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
    finally:
        _exit_ctx()


def test_time_dimension_coarser_granularity_error_suggests_fix() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity="sales.bandwidth_samples",
                data_type="timestamp",
                granularity="day",
                sample_interval=(5, "minute"),
            )
            def sample_ts(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        message = str(exc_info.value)
        assert "Set granularity to 'minute' or finer" in message
        assert "'second', 'minute'" in message
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.simple_metric() decorator
# ---------------------------------------------------------------------------


def test_simple_metric_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(revenue, MetricRef)
        assert revenue.semantic_id == "sales.revenue"
    finally:
        _exit_ctx()


def test_simple_metric_with_datasets() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.metric_type == "simple"
        assert ir.entities == ("sales.orders",)
        assert ir.composition is None
    finally:
        _exit_ctx()


def test_simple_metric_with_dataset_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ref = EntityRef("sales.orders")

        @ms.simple_metric(entities=[orders_ref], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.entities == ("sales.orders",)
    finally:
        _exit_ctx()


def test_simple_metric_provenance_fields() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(
            entities=["sales.orders"],
            additivity="additive",
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="ansi",
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        prov = ir.provenance
        assert prov.source_sql == "SELECT SUM(amount) FROM orders"
        assert prov.source_dialect == "ansi"
        assert prov.verification_mode == "sql_parity"
    finally:
        _exit_ctx()


def test_simple_metric_body_ast_hash() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        # body_ast_hash should be a non-empty string
        assert isinstance(ir.body_ast_hash, str)
        assert len(ir.body_ast_hash) > 0
    finally:
        _exit_ctx()


def test_simple_metric_pushes_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.simple_metric(entities=["sales.orders"], additivity="additive")(revenue_fn)
        ir, callable_ = ctx.pending_objects[-1]
        assert callable_ is revenue_fn
    finally:
        _exit_ctx()


def test_simple_metric_accepts_semi_additive() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(
            entities=["sales.bandwidth_samples"],
            additivity=ms.semi_additive(
                over="sales.bandwidth_samples.sample_ts",
                fold="mean",
            ),
        )
        def upstream_avg(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert isinstance(ir, MetricIR)
        assert ir.additivity is not None
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.aggregate() call
# ---------------------------------------------------------------------------


def test_aggregate_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(measure="sales.orders.amount", agg="sum")
        assert isinstance(ref, MetricRef)
        assert ref.semantic_id == "sales.amount"
    finally:
        _exit_ctx()


def test_aggregate_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(measure="sales.orders.amount", agg="sum", name="revenue")
        ir, sidecar = ctx.pending_objects[-1]
        assert sidecar is None  # body-free
        assert ir.metric_type == "simple"
        assert ir.aggregation == "sum"
        assert ir.measure == "sales.orders.amount"
        assert ir.entities == ("sales.orders",)
        assert ir.composition is None
    finally:
        _exit_ctx()


def test_aggregate_infers_name_from_measure() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(measure="sales.orders.amount", agg="sum")
        assert ref.semantic_id == "sales.amount"
    finally:
        _exit_ctx()


def test_aggregate_explicit_name() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(measure="sales.orders.amount", agg="sum", name="revenue")
        assert ref.semantic_id == "sales.revenue"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.ratio() derived registration
# ---------------------------------------------------------------------------


def test_ratio_returns_ref_and_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.ratio(
            name="margin",
            numerator="sales.revenue",
            denominator="sales.cost",
        )

        assert isinstance(ref, MetricRef)
        assert ref.semantic_id == "sales.margin"
        ir, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is None
        assert ir.semantic_id == "sales.margin"
        assert ir.metric_type == "derived"
        assert ir.entities == ()
        assert ir.python_symbol == "margin"
        assert ir.composition is not None
        assert ir.composition.kind == "ratio"
        assert ir.composition.numerator == "sales.revenue"
        assert ir.composition.denominator == "sales.cost"
    finally:
        _exit_ctx()


def test_weighted_average_keeps_value_weight_keys() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.weighted_average(
            name="aov",
            value="sales.revenue",
            weight="sales.order_count",
        )

        ir, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is None
        assert ir.metric_type == "derived"
        assert ir.composition is not None
        assert ir.composition.kind == "weighted_average"
        assert ir.composition.value == "sales.revenue"
        assert ir.composition.weight == "sales.order_count"
    finally:
        _exit_ctx()


def test_linear_returns_ref_and_pushes_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.linear(
            name="net_revenue",
            add=["sales.gross_revenue"],
            subtract=["sales.refunds"],
        )
        assert isinstance(ref, MetricRef)
        assert ref.semantic_id == "sales.net_revenue"
        ir, _ = ctx.pending_objects[-1]
        assert ir.metric_type == "derived"
        assert ir.composition is not None
        assert ir.composition.kind == "linear"
    finally:
        _exit_ctx()


def test_linear_requires_at_least_two_terms() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.linear(name="lonely", add=["sales.revenue"])

        assert exc_info.value.kind == ErrorKind.INVALID_REF
    finally:
        _exit_ctx()


def test_simple_metric_rejects_empty_datasets() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(entities=[], additivity="additive")
            def margin() -> object:
                return 1

        assert exc_info.value.kind == ErrorKind.MISSING_DATASETS
        assert exc_info.value.constraint_id == "metric_datasets_required"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()


def test_simple_metric_sidecar_stores_callable() -> None:
    """Simple metric stores the raw callable in sidecar."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.simple_metric(entities=["sales.orders"], additivity="additive")(revenue_fn)
        _, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is revenue_fn
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.relationship() call
# ---------------------------------------------------------------------------


def test_relationship_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        rel = ms.relationship(
            name="orders_to_items",
            from_entity="sales.orders",
            to_entity="sales.items",
            from_dimensions=["sales.orders.id"],
            to_dimensions=["sales.items.order_id"],
        )
        assert isinstance(rel, RelationshipRef)
        assert rel.semantic_id == "sales.orders_to_items"
    finally:
        _exit_ctx()


def test_relationship_pushes_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.relationship(
            name="orders_to_items",
            from_entity="sales.orders",
            to_entity="sales.items",
            from_dimensions=["sales.orders.id"],
            to_dimensions=["sales.items.order_id"],
        )
        ir, callable_ = ctx.pending_objects[-1]
        assert ir.name == "orders_to_items"
        assert ir.from_entity == "sales.orders"
        assert ir.to_entity == "sales.items"
        assert ir.from_dimensions == ("sales.orders.id",)
        assert ir.to_dimensions == ("sales.items.order_id",)
        assert callable_ is None
    finally:
        _exit_ctx()


def test_relationship_with_ref_objects() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ref = EntityRef("sales.orders")
        items_ref = EntityRef("sales.items")
        id_ref = DimensionRef("sales.orders.id")
        oid_ref = DimensionRef("sales.items.order_id")

        ms.relationship(
            name="orders_to_items",
            from_entity=orders_ref,
            to_entity=items_ref,
            from_dimensions=[id_ref],
            to_dimensions=[oid_ref],
        )
        ir, _ = ctx.pending_objects[-1]
        assert ir.from_entity == "sales.orders"
        assert ir.to_entity == "sales.items"
        assert ir.from_dimensions == ("sales.orders.id",)
        assert ir.to_dimensions == ("sales.items.order_id",)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.ref()
# ---------------------------------------------------------------------------


def test_ref_returns_string() -> None:
    result = ms.ref("sales.revenue")
    assert isinstance(result, str)
    assert result == "sales.revenue"


# ---------------------------------------------------------------------------
# Duplicate name detection
# ---------------------------------------------------------------------------


def test_duplicate_dataset_name_raises() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


def test_duplicate_metric_name_raises() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(backend: object) -> object:
            return None  # type: ignore[unreachable]

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(entities=["sales.orders"], additivity="additive")
            def revenue(backend: object) -> object:  # type: ignore[misc]
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


def test_dataset_and_metric_same_name_no_collision() -> None:
    """A dataset and a metric with the same model.name should coexist — kind-scoped uniqueness."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="dau_7d_portrait",
            datasource="warehouse",
            source=ms.table("dau_7d_portrait"),
        )
        assert ds.semantic_id == "sales.dau_7d_portrait"

        @ms.simple_metric(
            entities=[ds],
            additivity="additive",
            name="dau_7d_portrait",
        )
        def dau_7d_portrait(table):
            return table.dau.sum()

        assert dau_7d_portrait.semantic_id == "sales.dau_7d_portrait"
    finally:
        _exit_ctx()


def test_field_and_time_field_same_name_same_dataset_collides() -> None:
    """A field and a time_field with the same name on the same dataset share the fields namespace."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )

        @ms.dimension(entity=ds, name="log_date")
        def log_date_field(table):
            return table.log_date

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(entity=ds, name="log_date", data_type="string", granularity="day")
            def log_date_tf(table):
                return table.log_date

        assert exc_info.value.kind == "duplicate_name"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Keyword-only enforcement
# ---------------------------------------------------------------------------


def test_model_keyword_only() -> None:
    _enter_ctx()
    try:
        with pytest.raises(TypeError):
            ms.domain("sales")  # type: ignore[misc]
    finally:
        _exit_ctx()


def test_dataset_keyword_only() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.entity("wh")  # type: ignore[misc]
    finally:
        _exit_ctx()


def test_simple_metric_keyword_only() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.simple_metric("additive")  # type: ignore[misc]
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext handling
# ---------------------------------------------------------------------------


def test_simple_metric_with_ai_context() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(
            entities=["sales.orders"],
            additivity="additive",
            ai_context={
                "business_definition": "Total revenue",
                "guardrails": ["Must be positive"],
            },
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.ai_context.business_definition == "Total revenue"
        assert ir.ai_context.guardrails == ("Must be positive",)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext validation
# ---------------------------------------------------------------------------


def test_ai_context_with_valid_keys_works() -> None:
    """ai_context with all valid keys should work."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(
            entities=["sales.orders"],
            additivity="additive",
            ai_context={
                "business_definition": "Revenue",
                "guardrails": ["Must be positive"],
                "synonyms": ["rev", "sales"],
                "examples": ["orders.amount.sum()"],
                "instructions": "Use with care",
                "owner_notes": "Team Data",
            },
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.ai_context.business_definition == "Revenue"
        assert ir.ai_context.guardrails == ("Must be positive",)
        assert ir.ai_context.synonyms == ("rev", "sales")
        assert ir.ai_context.examples == ("orders.amount.sum()",)
        assert ir.ai_context.instructions == "Use with care"
        assert ir.ai_context.owner_notes == "Team Data"
    finally:
        _exit_ctx()


def test_ai_context_with_invalid_key_raises() -> None:
    """ai_context with an invalid key should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(
                entities=["sales.orders"],
                additivity="additive",
                ai_context={"invalid_key": "oops"},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


def test_ai_context_with_wrong_type_for_guardrails_raises() -> None:
    """ai_context with wrong type for guardrails should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(
                entities=["sales.orders"],
                additivity="additive",
                ai_context={"guardrails": "not a list"},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


def test_ai_context_with_wrong_type_for_business_definition_raises() -> None:
    """ai_context with wrong type for business_definition should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(
                entities=["sales.orders"],
                additivity="additive",
                ai_context={"business_definition": 42},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


def test_ambiguous_reference_error_kind_exists() -> None:
    """ErrorKind.AMBIGUOUS_REFERENCE must exist with value 'ambiguous_reference'."""
    assert hasattr(ErrorKind, "AMBIGUOUS_REFERENCE")
    assert ErrorKind.AMBIGUOUS_REFERENCE == "ambiguous_reference"


def test_ambiguous_reference_constraint_id_exists() -> None:
    """ConstraintId.AMBIGUOUS_REFERENCE must exist with value 'ambiguous_reference'."""
    assert hasattr(ConstraintId, "AMBIGUOUS_REFERENCE")
    assert ConstraintId.AMBIGUOUS_REFERENCE == "ambiguous_reference"


def test_ai_context_with_non_string_in_list_raises() -> None:
    """ai_context with non-string items in list field should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(
                entities=["sales.orders"],
                additivity="additive",
                ai_context={"guardrails": [1, 2, 3]},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Dataset-scoped field IDs
# ---------------------------------------------------------------------------


def test_two_datasets_same_column_name_distinct_ids() -> None:
    """Two datasets sharing a column name produce distinct dataset-scoped field IDs."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ds = ms.entity(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )
        portrait_ds = ms.entity(
            name="portrait",
            datasource="warehouse",
            source=ms.table("portrait"),
        )

        @ms.dimension(entity=orders_ds, name="region")
        def orders_region(table):
            return table.region

        @ms.dimension(entity=portrait_ds, name="region")
        def portrait_region(table):
            return table.region

        assert orders_region.semantic_id == "sales.orders.region"
        assert portrait_region.semantic_id == "sales.portrait.region"
    finally:
        _exit_ctx()


def test_field_model_mismatch_with_dataset_raises() -> None:
    """A field whose model disagrees with the dataset's model must raise."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )

        inventory_ref = ms.DomainRef(semantic_id="inventory")
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.dimension(entity=ds, name="region", domain=inventory_ref)
            def region(table):
                return table.region

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()


# metric unit field


def test_simple_metric_unit_lands_on_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive", unit="CNY")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.unit == "CNY"
    finally:
        _exit_ctx()


def test_simple_metric_unit_defaults_to_none() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.simple_metric(entities=["sales.orders"], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.unit is None
    finally:
        _exit_ctx()


def test_ratio_unit_lands_on_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.ratio(
            name="aov",
            numerator="sales.revenue",
            denominator="sales.order_count",
            unit="1",
        )
        ir, _ = ctx.pending_objects[-1]
        assert ir.unit == "1"
    finally:
        _exit_ctx()


@pytest.mark.parametrize("bad", ("", "C N Y", "CNY\t", "µs"))
def test_simple_metric_unit_rejects_whitespace_and_empty(bad: str) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.simple_metric(entities=["sales.orders"], additivity="additive", unit=bad)
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()


@pytest.mark.parametrize("bad", ("", "C N Y"))
def test_ratio_unit_rejects_whitespace_and_empty(bad: str) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.ratio(
                name="margin",
                numerator="sales.revenue",
                denominator="sales.cost",
                unit=bad,
            )

        assert exc_info.value.kind == "invalid_ref"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()
