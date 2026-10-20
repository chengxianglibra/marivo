"""Tests for marivo.semantic.authoring — decorator and builder implementation.

Tests cover current physical values, pure local declarations, and typed retirement.

Historical coverage groups:
- Outside-loader-context guard
- Model name resolution (explicit > default_domain > MissingModelError)
- All decorator signatures (keyword-only enforcement)
- name defaults to function __name__
- Ref types returned by decorators
- ms.ratio(), ms.linear() derived registration; ms.weighted_mean() aggregation
- Duplicate name detection
- Provenance fields on metric
- exact ``ms.ref.<kind>()`` factories
- Derived metric validation: ratio/linear
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from typing import get_type_hints

import ibis.expr.types as ibis_ir
import pytest
from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.deferred import root_declaration
from marivo._definition.expressions import (
    ContributionFilter,
    EntityCount,
    MetricExpression,
    PerReduce,
    PredicateExpression,
    RowExpression,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import computation_fingerprint
from marivo.refs import MeasureKind, Ref
from marivo.refs import ref as ref_factory
from marivo.semantic._authoring_context import DefinitionIR, PendingDefinition
from marivo.semantic._deferred_semantics import (
    consume_field,
    consume_metric,
    consume_predicate,
    consume_row,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticExpressionError,
    SemanticLoadError,
)
from marivo.semantic.ir import (
    AiContextIR,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    MeasureIR,
    PeriodCalendarIR,
    RelationshipIR,
    TableSourceIR,
    WorkScheduleIR,
)
from marivo.semantic.loader import _LOADER_CTX, LoaderContext, loader_context
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import in_memory_datasource_ir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enter_ctx(**kwargs: object) -> LoaderContext:
    """Create a LoaderContext and set it as the current context."""
    kwargs.setdefault("datasource_irs", (in_memory_datasource_ir("wh"),))
    ctx = LoaderContext(**kwargs)  # type: ignore[arg-type]
    _LOADER_CTX.set(ctx)
    return ctx


def _exit_ctx() -> None:
    """Reset the loader context."""
    _LOADER_CTX.set(None)


def _pending_objects(
    ctx: LoaderContext | None,
) -> list[tuple[DefinitionIR, Callable[..., object] | None]]:
    assert ctx is not None
    return [
        (
            pending.definition,
            pending.expression_body.callable if pending.expression_body is not None else None,
        )
        for pending in ctx.pending_definitions
    ]


def _pending_refs(ctx: LoaderContext) -> list[object]:
    return [pending.ref for pending in ctx.pending_definitions]


def _is_ref(value: object, kind: ms.SemanticKind) -> bool:
    return type(value) is ms.Ref and value.kind is kind


def _leaf(unit: str | None = None) -> Ref[MeasureKind]:
    orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
    return ms.measure_column(
        name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit=unit
    )


def _closed_leaf(unit: str | None = None) -> Deferred:
    captured = _leaf(unit)
    return ms.finalize().resolve(captured)


class _FakeTable:
    def __init__(self) -> None:
        self.columns_requested: list[str] = []

    def __getitem__(self, column: str) -> str:
        self.columns_requested.append(column)
        return f"column:{column}"


@pytest.fixture(autouse=True)
def _clean_ctx() -> Iterator[None]:
    """Ensure loader context is clean before and after each test."""
    _exit_ctx()
    yield
    _exit_ctx()


# ---------------------------------------------------------------------------
# Outside-loader-context guard
# ---------------------------------------------------------------------------


def test_model_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.domain(name="sales", owner="Mina Zhang")
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_dataset_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.dimension(entity="orders")  # type: ignore[arg-type]  # intentional rejection input
        def amount(table: object) -> object:
            return None

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_time_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.time_dimension(entity="orders", granularity="day")  # type: ignore[arg-type]  # intentional rejection input
        def order_date(table: object) -> object:
            return None

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_metric_does_not_require_context() -> None:
    _enter_ctx(default_domain="sales")
    amount = _closed_leaf()
    _exit_ctx()

    @ms.metric()
    def revenue() -> Deferred:
        return amount.sum()

    assert isinstance(revenue, Deferred)
    assert _LOADER_CTX.get() is None


def test_relationship_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.relationship(
            name="orders_to_items",
            from_entity="orders",  # type: ignore[arg-type]  # intentional rejection input
            to_entity="items",  # type: ignore[arg-type]  # intentional rejection input
            keys=[
                ms.join_on(
                    ref_factory.dimension("sales.orders.path"),
                    ref_factory.dimension("sales.items.order_id"),
                )
            ],
        )
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


# ---------------------------------------------------------------------------
# ms.domain() call
# ---------------------------------------------------------------------------


def test_model_creates_model_ir() -> None:
    ctx = _enter_ctx()
    try:
        ref = ms.domain(name="sales", owner="Mina Zhang", default=True)
        # Should have one pending object
        assert len(_pending_objects(ctx)) == 1
        ir, callable_ = _pending_objects(ctx)[0]
        assert isinstance(ir, DomainIR)
        assert _is_ref(ref, ms.SemanticKind.DOMAIN)
        assert ref.path == "sales"
        assert ir.name == "sales"
        assert ir.owner == "Mina Zhang"
        assert ir.default is True
        assert ctx.default_domain == "sales"
        # model() is not a decorator — no callable
        assert callable_ is None
    finally:
        _exit_ctx()


def test_model_default_false_does_not_set_context() -> None:
    ctx = _enter_ctx(default_domain="existing")
    try:
        ms.domain(name="other", owner="Alex Chen", default=False)
        assert ctx.default_domain == "existing"
    finally:
        _exit_ctx()


def test_model_requires_owner_keyword() -> None:
    _enter_ctx()
    try:
        with pytest.raises(TypeError):
            ms.domain(name="sales")  # type: ignore[call-arg]
    finally:
        _exit_ctx()


@pytest.mark.parametrize("owner", [42, None, "", "   "])
def test_model_owner_must_be_non_empty_string(owner: object) -> None:
    _enter_ctx()
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.domain(name="sales", owner=owner)  # type: ignore[arg-type]
        assert exc_info.value.kind == ErrorKind.INVALID_DOMAIN_OWNER
        assert "owner must be a non-empty string" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_field_accepts_model_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        sales_ref = ms.domain(name="sales", owner="Mina Zhang", default=True)
        ds = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )

        @ms.dimension(entity=ds, domain=sales_ref)
        def region(table: ibis_ir.Table) -> ibis_ir.Value:
            return table.region

        assert region.path == "sales.orders.region"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.entity() decorator
# ---------------------------------------------------------------------------


def test_entity_without_name_returns_expression_decorator() -> None:
    _enter_ctx(default_domain="sales")
    try:
        decorator = ms.entity(datasource=ms.ref.datasource("wh"), source=md.table("orders"))
        assert callable(decorator)
    finally:
        _exit_ctx()


def test_dataset_rejects_bare_string_datasource() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource="wh", source=md.table("orders"))  # type: ignore[call-overload]  # intentional rejection input

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert 'ms.ref.datasource("warehouse")' in str(exc_info.value)
    finally:
        _exit_ctx()


def test_dimension_rejects_bare_string_domain() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
        )
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.dimension(entity=ds, domain="inventory")  # type: ignore[arg-type]
            def region(table: ibis_ir.Table) -> ibis_ir.Value:
                return table.region

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "ms.domain(name=...)" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_entity_returns_value_and_pushes_ir_without_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.entity(
            name="orders_tbl",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders", database="sales_mart"),
            primary_key=["order_id"],
        )
        ir, callable_ = _pending_objects(ctx)[-1]
        assert isinstance(ir, EntityIR)
        assert _is_ref(ref, ms.SemanticKind.ENTITY)
        assert ref.path == "sales.orders_tbl"
        assert ir.semantic_id == "sales.orders_tbl"
        assert ir.domain == "sales"
        assert ir.name == "orders_tbl"
        assert ir.datasource == "wh"
        assert ir.source == md.table("orders", database="sales_mart")
        assert isinstance(ir.source, TableSourceIR)
        assert ir.source.kind == "table"
        assert ir.source.table == "orders"
        assert ir.source.database == "sales_mart"
        assert ir.primary_key == ("order_id",)
        assert callable_ is None
    finally:
        _exit_ctx()


def test_dataset_records_parquet_and_csv_sources() -> None:
    from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR

    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="parquet_orders",
            datasource=ms.ref.datasource("wh"),
            source=md.parquet("/data/orders/*.parquet", hive_partitioning=True),
        )

        ms.entity(
            name="csv_orders",
            datasource=ms.ref.datasource("wh"),
            source=md.csv(
                "/data/orders.csv", columns={"order_id": "order_id"}, header=False, delimiter="|"
            ),
        )

        parquet_ir, _ = _pending_objects(ctx)[-2]
        assert isinstance(parquet_ir, EntityIR)
        csv_ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(csv_ir, EntityIR)
        assert isinstance(parquet_ir.source, ParquetSourceIR)
        assert parquet_ir.source.path == "/data/orders/*.parquet"
        assert parquet_ir.source.hive_partitioning is True
        assert isinstance(csv_ir.source, CsvSourceIR)
        assert csv_ir.source.path == "/data/orders.csv"
        assert csv_ir.source.header is False
        assert csv_ir.source.delimiter == "|"
        assert csv_ir.source.columns == (("order_id", "order_id"),)
    finally:
        _exit_ctx()


def test_entity_rejects_invalid_source_value() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=object())  # type: ignore[call-overload]  # intentional rejection input
        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "source" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_entity_accepts_json_source(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "\n".join(
                [
                    "import marivo.datasource as md",
                    "import marivo.semantic as ms",
                    "",
                    "ms.domain(name='sales', owner='Data Team')",
                    "events = ms.entity(",
                    "    name='events',",
                    "    datasource=ms.ref.datasource('warehouse'),",
                    "    source=md.json('data/events/*.json', columns={'event_id': 'event_id'}),",
                    ")",
                ]
            )
        }
    )

    assert project._registry is not None
    assert project._registry.entities["sales.events"].source.to_dict() == {
        "kind": "json",
        "path": "data/events/*.json",
        "columns": {"event_id": "event_id"},
        "format": "auto",
        "records_path": None,
        "query_params": {},
        "method": "GET",
        "body": None,
        "body_params": [],
    }


# ---------------------------------------------------------------------------
# ms.dimension() decorator
# ---------------------------------------------------------------------------


def test_field_name_defaults_to_function_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert _is_ref(amount, ms.SemanticKind.DIMENSION)
        assert amount.path == "sales.orders.amount"
        assert ir.name == "amount"
        assert ir.is_time_dimension is False
        assert ir.granularity is None
        assert ir.kind == DimensionKind.CATEGORICAL
    finally:
        _exit_ctx()


def test_field_explicit_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(name="order_amount", entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert ir.name == "order_amount"
        assert ir.semantic_id == "sales.orders.order_amount"
    finally:
        _exit_ctx()


def test_component_decorators_accept_leading_docstrings() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        amount = _leaf()

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def region(table: ibis_ir.Table) -> ibis_ir.Value:
            """Order region."""
            return table.region

        @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
        def order_date(table: ibis_ir.Table) -> ibis_ir.Value:
            """Order date."""
            return table.order_date

        finalized = ms.finalize()
        amount_value = finalized.resolve(amount)

        @ms.measure()
        def named_amount() -> Deferred:
            """Order amount."""
            return amount_value

        before = len(ctx.pending_definitions)

        @ms.metric()
        def revenue() -> Deferred:
            """Order revenue."""
            return named_amount.sum()

        assert _is_ref(region, ms.SemanticKind.DIMENSION)
        assert _is_ref(order_date, ms.SemanticKind.TIME_DIMENSION)
        assert root_declaration(named_amount).name == "named_amount"
        assert root_declaration(revenue).name == "revenue"
        assert len(ctx.pending_definitions) == before
    finally:
        _exit_ctx()


def test_field_pushes_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        def amount_fn(table: object) -> object:
            return None

        ms.dimension(entity=ref_factory.entity("sales.orders"))(amount_fn)
        ir, callable_ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert callable_ is amount_fn
        assert ir.entity == "sales.orders"
    finally:
        _exit_ctx()


def test_field_body_rejects_lambda() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.dimension(entity=ref_factory.entity("sales.orders"))
            def amount(table: object) -> object:
                fn: Callable[[object], object] = lambda value: value
                return fn(table)

        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert "Dimension body of 'amount'" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_dimension_column_pushes_ir_sidecar_and_pending_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )

        ref = ms.dimension_column(
            name="region",
            entity=orders,
            column="region",
            ai_context=ms.ai_context(
                business_definition="Sales reporting region.",
            ),
        )

        ir, sidecar = _pending_objects(ctx)[-1]
        assert _is_ref(ref, ms.SemanticKind.DIMENSION)
        assert ref.path == "sales.orders.region"
        assert isinstance(ir, DimensionIR)
        assert ir.semantic_id == "sales.orders.region"
        assert ir.domain == "sales"
        assert ir.entity == "sales.orders"
        assert ir.name == "region"
        assert ir.python_symbol == "region"
        assert ir.is_time_dimension is False
        assert ir.kind is DimensionKind.CATEGORICAL
        assert ir.ai_context.business_definition == "Sales reporting region."
        assert sidecar is not None

        fake = _FakeTable()
        assert sidecar(fake) == "column:region"
        assert fake.columns_requested == ["region"]
        assert _pending_refs(ctx)[-1] == ref
    finally:
        _exit_ctx()


def test_dimension_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.dimension_column(
                name="region",
                entity="sales.orders",  # type: ignore[arg-type]  # intentional rejection input
                column="region",
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "entity must be Ref[entity]" in str(exc_info.value)


def test_dimension_column_rejects_empty_column() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.dimension_column(name="region", entity=orders, column="")
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "column must be a non-empty string" in str(exc_info.value)


def test_measure_body_error_uses_measure_label() -> None:
    with pytest.raises(SemanticExpressionError) as caught:

        @ms.measure()  # type: ignore[arg-type]  # intentional rejection input
        def amount(table: ibis_ir.Table) -> ibis_ir.Value:
            return table.amount

    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert "amount" in str(caught.value)
    assert "zero-argument" in str(caught.value.expected)


def test_measure_column_pushes_ir_sidecar_and_pending_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )

        ref = ms.measure_column(
            name="amount",
            entity=orders,
            column="amount",
            additivity=ms.additive_all(),
            unit="CNY",
            ai_context=ms.ai_context(
                business_definition="Order amount before refunds.",
            ),
        )

        ir, sidecar = _pending_objects(ctx)[-1]
        assert _is_ref(ref, ms.SemanticKind.MEASURE)
        assert ref.path == "sales.orders.amount"
        assert isinstance(ir, MeasureIR)
        assert ir.semantic_id == "sales.orders.amount"
        assert ir.domain == "sales"
        assert ir.entity == "sales.orders"
        assert ir.name == "amount"
        assert ir.python_symbol == "amount"
        assert ir.additivity == "additive"
        assert ir.unit == "CNY"
        assert ir.ai_context.business_definition == "Order amount before refunds."
        assert sidecar is not None

        fake = _FakeTable()
        assert sidecar(fake) == "column:amount"
        assert fake.columns_requested == ["amount"]
        assert _pending_refs(ctx)[-1] == ref
    finally:
        _exit_ctx()


def test_measure_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.measure_column(
                name="amount",
                entity="sales.orders",  # type: ignore[arg-type]  # intentional rejection input
                column="amount",
                additivity=ms.additive_all(),
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "entity must be Ref[entity]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.time_dimension() decorator
# ---------------------------------------------------------------------------


def test_time_dimension_body_error_uses_time_dimension_label() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
            def order_date(table: ibis_ir.Table) -> ibis_ir.Value:
                return table.created_at
                table.created_at  # noqa: B018

        assert "Time dimension body of 'order_date'" in str(exc_info.value)
        assert "forbidden Expr statement" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_time_field_ir_has_time_metadata() -> None:
    from marivo.semantic.ir import HourPrefixParse

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
        def order_date(table: object) -> object:
            return None

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="hour",
            parse=ms.hour_prefix(order_date),
        )
        def order_hour(table: object) -> object:
            return None

        assert _is_ref(order_date, ms.SemanticKind.TIME_DIMENSION)
        assert order_date.path == "sales.orders.order_date"
        assert _is_ref(order_hour, ms.SemanticKind.TIME_DIMENSION)
        assert order_hour.path == "sales.orders.order_hour"
        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert ir.is_time_dimension is True
        assert isinstance(ir.parse, HourPrefixParse)
        assert ir.parse.prefix == "sales.orders.order_date"
        assert ir.granularity == "hour"
        assert ir.kind == DimensionKind.TIME
    finally:
        _exit_ctx()


def test_hour_prefix_rejects_string_prefix() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.hour_prefix("sales.orders.order_date")  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "prefix must be Ref[time_dimension]" in str(exc_info.value)
    assert "got str" in str(exc_info.value)


def test_time_dimension_rejects_invalid_parse_value() -> None:
    _enter_ctx(default_domain="sales")
    try:

        def order_date(table: object) -> object:
            return None

        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.time_dimension(
                entity=ref_factory.entity("sales.orders"),
                granularity="day",
                parse=object(),  # type: ignore[arg-type]
            )(order_date)
        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "parse" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_time_field_requires_data_type_and_granularity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):

            @ms.time_dimension(entity=ref_factory.entity("sales.orders"))  # type: ignore[call-arg]
            def order_date(table: object) -> object:
                return None
    finally:
        _exit_ctx()


def test_time_field_body_rejects_sql_escape_hatch() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
            def order_date(backend: ibis_ir.Table) -> ibis_ir.Value:
                return backend.sql("select current_date")

        assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH
        assert exc_info.value.constraint_id == "ast_sql_escape_hatch"
    finally:
        _exit_ctx()


def test_time_field_accepts_timezone_metadata() -> None:
    from marivo.semantic.ir import TimestampParse

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="hour",
            parse=ms.timestamp(timezone="UTC"),
        )
        def created_at(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert isinstance(ir.parse, TimestampParse)
        assert ir.parse.timezone == "UTC"
        assert ir.ai_context == AiContextIR()
    finally:
        _exit_ctx()


def test_time_field_accepts_missing_timezone_for_datetime_metadata() -> None:
    from marivo.semantic.ir import DatetimeParse

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="hour",
            parse=ms.datetime(),
        )
        def created_at(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert isinstance(ir.parse, DatetimeParse)
        assert ir.parse.timezone is None
    finally:
        _exit_ctx()


def test_time_field_accepts_missing_timezone_for_timestamp_metadata() -> None:
    from marivo.semantic.ir import TimestampParse

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="hour",
            parse=ms.timestamp(),
        )
        def created_at(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert isinstance(ir.parse, TimestampParse)
        assert ir.parse.timezone is None
    finally:
        _exit_ctx()


def test_time_field_rejects_invalid_timezone() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.orders"),
                granularity="hour",
                parse=ms.timestamp(timezone="Mars/Olympus"),
            )
            def created_at(table: object) -> object:
                return None

        assert "timezone" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_unsupported_strptime_shorthands() -> None:
    """Shorthand formats are rejected by ms.strptime()."""
    with pytest.raises(ValueError, match="%"):
        ms.strptime("yyyymmdd")
    with pytest.raises(ValueError, match="%"):
        ms.strptime("hh")


def test_time_field_rejects_date_format_on_temporal_type() -> None:
    """date_format is no longer a parameter on time_dimension; this is enforced by signature."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):

            @ms.time_dimension(
                entity=ref_factory.entity("sales.orders"),
                granularity="day",
                parse=ms.datetime(timezone="UTC"),
                date_format="%Y-%m-%d",  # type: ignore[call-arg]
            )
            def created_at(table: object) -> object:
                return None
    finally:
        _exit_ctx()


def test_time_dimension_without_parse_is_valid() -> None:
    """time_dimension without parse is allowed — parse is inferred at analysis time."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="day",
        )
        def order_date(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        assert ir.is_time_dimension
        assert ir.parse is None
    finally:
        _exit_ctx()


def test_time_field_accepts_canonical_strptime() -> None:
    """Sanity: a valid %Y%m%d format goes through cleanly."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="day",
            parse=ms.strptime("%Y%m%d"),
        )
        def order_date(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        from marivo.semantic.ir import StrptimeParse

        assert isinstance(ir.parse, StrptimeParse)
        assert ir.parse.format == "%Y%m%d"
    finally:
        _exit_ctx()


def test_time_field_strips_whitespace_from_strptime() -> None:
    """normalize_strptime strips whitespace before validation."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="day",
            parse=ms.strptime("  %Y-%m-%d  "),
        )
        def order_date(table: object) -> object:
            return None

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, DimensionIR)
        from marivo.semantic.ir import StrptimeParse

        assert isinstance(ir.parse, StrptimeParse)
        assert ir.parse.format == "%Y-%m-%d"
    finally:
        _exit_ctx()


def test_time_field_rejects_invalid_strptime_directive() -> None:
    """Unknown strptime directives like %Q are rejected by ms.strptime()."""
    with pytest.raises(ValueError):
        ms.strptime("%Q%m%d")


def test_time_dimension_accepts_sample_interval() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.bandwidth_samples"),
            granularity="second",
            parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
        )
        def sample_ts(table: object) -> object:
            return None

        irs = [obj for obj, _ in _pending_objects(ctx) if isinstance(obj, DimensionIR)]
        from marivo.semantic.ir import TimestampParse

        ir = irs[-1]
        assert isinstance(ir.parse, TimestampParse)
        assert ir.parse.sample_interval is not None
        assert ir.granularity == "second"
        assert ir.parse.sample_interval.count == 5
        assert ir.parse.sample_interval.unit == "minute"
    finally:
        _exit_ctx()


def test_strptime_time_dimension_accepts_sample_interval() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.bandwidth_samples"),
            granularity="second",
            parse=ms.strptime(
                "%Y%m%d%H%M%S",
                timezone="UTC",
                sample_interval=(5, "minute"),
            ),
        )
        def sample_ts(table: object) -> object:
            return None

        irs = [obj for obj, _ in _pending_objects(ctx) if isinstance(obj, DimensionIR)]
        from marivo.semantic.ir import StrptimeParse

        ir = irs[-1]
        assert isinstance(ir.parse, StrptimeParse)
        assert ir.parse.sample_interval is not None
        assert ir.granularity == "second"
        assert ir.parse.sample_interval.count == 5
        assert ir.parse.sample_interval.unit == "minute"
    finally:
        _exit_ctx()


def test_time_dimensions_reject_invalid_sample_interval_units() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="second",
                parse=ms.timestamp(timezone="UTC", sample_interval=(1, "day")),  # type: ignore[arg-type]
            )
            def sample_ts(table: object) -> object:
                return None

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        with pytest.raises(SemanticDecoratorError) as strptime_exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="second",
                parse=ms.strptime(
                    "%Y%m%d%H%M%S",
                    timezone="UTC",
                    sample_interval=(1, "day"),  # type: ignore[arg-type]
                ),
            )
            def sample_ts(table: object) -> object:
                return None

        assert strptime_exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
    finally:
        _exit_ctx()


def test_strptime_time_dimension_rejects_sample_interval_without_date_context() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="hour",
                parse=ms.strptime(
                    "%H:%M",
                    sample_interval=(1, "hour"),
                ),
            )
            def sample_hour(table: object) -> object:
                return None

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        assert "date context" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_dimensions_coarser_granularity_error_suggests_fix() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="day",
                parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
            )
            def sample_ts(table: object) -> object:
                return None

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        message = str(exc_info.value)
        assert "Set granularity to 'minute' or finer" in message
        assert "'second', 'minute'" in message
        with pytest.raises(SemanticDecoratorError) as strptime_exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="day",
                parse=ms.strptime(
                    "%Y%m%d%H%M%S",
                    timezone="UTC",
                    sample_interval=(5, "minute"),
                ),
            )
            def sample_ts(table: object) -> object:
                return None

        assert strptime_exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        message = str(strptime_exc_info.value)
        assert "Set granularity to 'minute' or finer" in message
        assert "'second', 'minute'" in message
    finally:
        _exit_ctx()


def test_hour_prefix_accepts_sample_interval() -> None:
    from marivo.semantic.ir import HourPrefixParse

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(entity=ref_factory.entity("sales.bandwidth_samples"), granularity="day")
        def dt(table: object) -> object:
            return None

        @ms.time_dimension(
            entity=ref_factory.entity("sales.bandwidth_samples"),
            granularity="hour",
            parse=ms.hour_prefix(dt, sample_interval=(1, "hour")),
        )
        def hh(table: object) -> object:
            return None

        irs = [obj for obj, _ in _pending_objects(ctx) if isinstance(obj, DimensionIR)]
        ir = irs[-1]
        assert isinstance(ir.parse, HourPrefixParse)
        assert ir.parse.sample_interval is not None
        assert ir.parse.sample_interval.count == 1
        assert ir.parse.sample_interval.unit == "hour"
    finally:
        _exit_ctx()


def test_hour_prefix_rejects_sample_interval_day_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        dt = ref_factory.time_dimension("sales.bandwidth_samples.dt")

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="hour",
                parse=ms.hour_prefix(dt, sample_interval=(1, "day")),  # type: ignore[arg-type]
            )
            def hh(table: object) -> object:
                return None

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
    finally:
        _exit_ctx()


def test_time_dimension_column_pushes_ir_sidecar_and_pending_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )

        ref = ms.time_dimension_column(
            name="log_date",
            entity=orders,
            column="dt",
            granularity="day",
            parse=ms.strptime("%Y%m%d"),
            is_default=True,
            ai_context=ms.ai_context(
                business_definition="Default order reporting date.",
            ),
        )

        ir, sidecar = _pending_objects(ctx)[-1]
        assert _is_ref(ref, ms.SemanticKind.TIME_DIMENSION)
        assert ref.path == "sales.orders.log_date"
        assert isinstance(ir, DimensionIR)
        assert ir.semantic_id == "sales.orders.log_date"
        assert ir.domain == "sales"
        assert ir.entity == "sales.orders"
        assert ir.name == "log_date"
        assert ir.python_symbol == "log_date"
        assert ir.is_time_dimension is True
        assert ir.kind is DimensionKind.TIME
        assert ir.granularity == "day"
        assert ir.parse is not None
        assert ir.is_default is True
        assert ir.ai_context.business_definition == "Default order reporting date."
        assert sidecar is not None

        fake = _FakeTable()
        assert sidecar(fake) == "column:dt"
        assert fake.columns_requested == ["dt"]
        assert _pending_refs(ctx)[-1] == ref
    finally:
        _exit_ctx()


def test_time_dimension_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.time_dimension_column(
                name="log_date",
                entity="sales.orders",  # type: ignore[arg-type]  # intentional rejection input
                column="dt",
                granularity="day",
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "entity must be Ref[entity]" in str(exc_info.value)


def test_time_dimension_column_reuses_parse_granularity_validation() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.time_dimension_column(
                name="log_date",
                entity=orders,
                column="dt",
                granularity="hour",
                parse=ms.strptime("%Y%m%d"),
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "requires a time-bearing format" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.metric() decorator
# ---------------------------------------------------------------------------


def test_metric_returns_local_declaration() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()

        @ms.metric()
        def revenue() -> Deferred:
            return amount.sum()

        assert isinstance(revenue, Deferred)
        assert root_declaration(revenue).name == "revenue"
        assert root_declaration(revenue).domain is None
    finally:
        _exit_ctx()


def test_metric_roots_come_from_source_reductions() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()
        before = len(ctx.pending_definitions)

        @ms.metric()
        def revenue() -> Deferred:
            return amount.sum()

        assert consume_metric(revenue).roots == (consume_row(amount).root,)
        assert isinstance(consume_metric(revenue).node, SourceAggregate)
        assert len(ctx.pending_definitions) == before
    finally:
        _exit_ctx()


def test_metric_historical_sql_in_ai_context() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()

        @ms.metric(
            ai_context=ms.ai_context(
                business_definition="Historical ANSI SQL: SELECT SUM(amount) FROM orders"
            )
        )
        def revenue() -> Deferred:
            return amount.sum()

        assert root_declaration(revenue).ai_context is not None
        assert (
            root_declaration(revenue).ai_context.business_definition
            == "Historical ANSI SQL: SELECT SUM(amount) FROM orders"
        )
    finally:
        _exit_ctx()


def test_metric_computation_fingerprint() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()

        @ms.metric()
        def revenue() -> Deferred:
            return amount.sum()

        assert len(computation_fingerprint(consume_metric(revenue).node)) == 64
        assert computation_fingerprint(consume_metric(revenue).node) == computation_fingerprint(
            consume_metric(amount.sum()).node
        )
    finally:
        _exit_ctx()


def test_metric_does_not_register_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()
        before = len(ctx.pending_definitions)

        def revenue_fn() -> Deferred:
            return amount.sum()

        value = ms.metric()(revenue_fn)
        assert isinstance(value, Deferred)
        assert root_declaration(value).name == "revenue_fn"
        assert len(ctx.pending_definitions) == before
        assert all(
            pending.expression_body is None or pending.expression_body.callable is not revenue_fn
            for pending in ctx.pending_definitions
        )
    finally:
        _exit_ctx()


def test_metric_accepts_semi_additive() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="samples", datasource=ms.ref.datasource("wh"), source=md.table("samples")
        )
        sample_ts_ref = ms.time_dimension_column(
            name="sample_ts",
            entity=orders,
            column="sample_ts",
            granularity="minute",
            parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
        )
        value_ref = ms.measure_column(
            name="value",
            entity=orders,
            column="value",
            additivity=ms.additive_all(except_=(sample_ts_ref,)),
            status_time_dimension=sample_ts_ref,
        )

        finalized = ms.finalize()
        value = finalized.resolve(value_ref)
        sample_ts = finalized.resolve(sample_ts_ref)

        @ms.metric()
        def upstream_avg() -> Deferred:
            return value.sum(time=sample_ts).per(sample_ts).mean()

        assert isinstance(consume_metric(upstream_avg).node, PerReduce)
        assert consume_metric(upstream_avg).node.reducer == "mean"
    finally:
        _exit_ctx()


def test_semi_additive_rejects_string_over() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.additive_all(except_=("sales.orders.order_date",))  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "Dimension or TimeDimension refs" in str(exc_info.value)


def test_closed_additivity_and_value_policies_reject_invalid_values() -> None:
    assert ms.additive(over=(ref_factory.dimension("sales.orders.region"),)).version == 1
    assert ms.additive_all().version == 1
    assert ms.non_additive().version == 1
    for policy in (ms.nulls.ignore, ms.empty.null):
        with pytest.raises(SemanticExpressionError) as caught:
            policy()
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError, match="additivity requires"):
            ms.measure_column(
                name="amount",
                entity=ref_factory.entity("sales.orders"),
                column="amount",
                additivity="additive",  # type: ignore[arg-type]  # intentional rejection input
            )
    finally:
        _exit_ctx()


def test_retired_fold_aliases_are_not_public() -> None:
    assert "fold" not in get_type_hints(ms.metric)
    assert "status_time_fold" not in get_type_hints(ms.measure_column)
    assert "AggregateFoldInput" not in ms.__all__
    assert "AggregateFoldValue" not in ms.__all__


@pytest.mark.parametrize("fold", ["mean", "min", "max", "first", "last", ("percentile", 0.9)])
def test_retired_aggregate_folds_are_not_normalized(fold: object) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.aggregate(
                name="inventory",
                measure=ref_factory.measure("sales.inventory.quantity"),
                agg="sum",
                fold=fold,
            )
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "per(" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


@pytest.mark.parametrize(
    "fold",
    ["auto", "sum", ("percentile", 0.0), ("percentile", 1.0), ("other", 0.5), ("percentile",)],
)
def test_aggregate_rejects_values_outside_shared_fold_alias(fold: object) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.aggregate(
                name="inventory",
                measure=ref_factory.measure("sales.inventory.quantity"),
                agg="sum",
                fold=fold,
            )
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "per(" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


def test_status_time_can_be_declared_without_fold() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        inventory = ms.entity(
            name="inventory", datasource=ms.ref.datasource("wh"), source=md.table("inventory")
        )
        axis = ms.time_dimension_column(
            name="snapshot_at", entity=inventory, column="snapshot_at", granularity="day"
        )
        quantity = ms.measure_column(
            name="quantity",
            entity=inventory,
            column="quantity",
            additivity=ms.additive_all(except_=(axis,)),
            status_time_dimension=axis,
        )
        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, MeasureIR)
        assert ir.status_time_fold is None
        assert ir.status_time_dimension == axis.path
        assert consume_field(ms.finalize().resolve(quantity)).default_time is None
    finally:
        _exit_ctx()


def test_status_fold_requires_status_time() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.measure_column(  # type: ignore[call-arg]  # intentional rejection input
                name="inventory",
                entity=ref_factory.entity("sales.orders"),
                column="quantity",
                additivity=ms.additive_all(),
                status_time_fold="last",
            )
        assert caught.value.repair is not None and "per(" in caught.value.repair.action
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.aggregate() call
# ---------------------------------------------------------------------------


def test_retired_aggregate_rejects_before_registration() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.aggregate(
                name="revenue", measure=ref_factory.measure("sales.orders.amount"), agg="sum"
            )
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "reducer" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


def test_retired_aggregate_never_pushes_legacy_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.aggregate(
                name="revenue", measure=ref_factory.measure("sales.orders.amount"), agg="sum"
            )
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "reducer" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.count() call
# ---------------------------------------------------------------------------


def test_entity_count_retains_exact_root() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        finalized = ms.finalize()
        orders = finalized.resolve(orders_ref)
        before = len(ctx.pending_definitions)
        value = orders.count()
        assert isinstance(consume_metric(value).node, EntityCount)
        assert consume_metric(value).node.entity == orders.ref
        assert consume_metric(value).unit == "1"
        assert len(ctx.pending_definitions) == before
    finally:
        _exit_ctx()


def test_count_rejects_string_entity() -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.count(name="order_count", entity="sales.orders")
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert caught.value.repair is not None and "Entity.count" in caught.value.repair.action


# ---------------------------------------------------------------------------
# ms.where() filtered count / aggregate
# ---------------------------------------------------------------------------


def test_where_requires_at_least_one_condition() -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.where()
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert caught.value.repair is not None and "Metric.where" in caught.value.repair.action


def test_count_predicate_retains_exact_field() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        state_ref = ms.dimension_column(name="state", entity=orders_ref, column="state")
        finalized = ms.finalize()
        orders = finalized.resolve(orders_ref)
        state = finalized.resolve(state_ref)
        value = orders.count().where(state == "FAILED")
        assert isinstance(consume_metric(value).node, ContributionFilter)
        assert isinstance(consume_metric(value).node.predicate, PredicateExpression)
        assert consume_metric(value).node.predicate == consume_predicate(state == "FAILED").node
        field_condition = consume_metric(value).node.predicate.inputs[0]
        assert isinstance(field_condition, RowExpression)
        assert field_condition.inputs[0].ref == consume_field(state).reference
    finally:
        _exit_ctx()


@pytest.mark.parametrize("membership", [(2, 4), [2, 4]])
def test_explicit_membership_comparisons_normalize_inputs(
    membership: tuple[int, ...] | list[int],
) -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        kind_ref = ms.dimension_column(name="type", entity=orders_ref, column="type")
        finalized = ms.finalize()
        orders = finalized.resolve(orders_ref)
        kind = finalized.resolve(kind_ref)
        predicate = (kind == membership[0]) | (kind == membership[1])
        value = orders.count().where(predicate)
        assert isinstance(consume_metric(value).node, ContributionFilter)
        assert isinstance(consume_metric(value).node.predicate, PredicateExpression)
        assert (
            consume_metric(value).node.predicate
            == consume_predicate((kind == 2) | (kind == 4)).node
        )

    finally:
        _exit_ctx()


@pytest.mark.parametrize(
    "bad_value",
    [None, (), [], {2, 4}, {"op": "in", "value": [2, 4]}, (2, object())],
)
def test_where_rejects_invalid_condition_values(bad_value: object) -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.where(**{"type": bad_value})
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert caught.value.repair is not None and "Metric.where" in caught.value.repair.action


def test_where_rejects_qualified_dimension_keys() -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.where(**{"sales.orders.type": 2})
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert caught.value.repair is not None and "Metric.where" in caught.value.repair.action


@pytest.mark.parametrize("bad_filter", [{"state": "FAILED"}, [("state", "FAILED")], "FAILED"])
def test_count_rejects_non_where_filter(bad_filter: object) -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        finalized = ms.finalize()
        orders = finalized.resolve(orders_ref)
        with pytest.raises(SemanticExpressionError) as caught:
            consume_metric(orders.count().where(bad_filter))  # type: ignore[arg-type]  # intentional rejection input
        assert caught.value.kind == (
            ErrorKind.INVALID_EXPRESSION_VALUE
            if isinstance(bad_filter, dict)
            else ErrorKind.INVALID_EXPRESSION_STAGE
        )
        assert caught.value.repair is not None
    finally:
        _exit_ctx()


def test_aggregate_rejects_non_where_filter() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()
        with pytest.raises(SemanticExpressionError) as caught:
            consume_metric(amount.sum().where({"state": "FAILED"}))  # type: ignore[arg-type]  # intentional rejection input
        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.ratio() derived registration
# ---------------------------------------------------------------------------


def test_metric_division_keeps_source_nodes_and_local_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf("CNY")
        before = len(ctx.pending_definitions)

        @ms.metric()
        def margin() -> Deferred:
            return amount.sum() / amount.count()

        assert isinstance(consume_metric(margin).node, MetricExpression)
        assert consume_metric(margin).node.operands[0] == consume_metric(amount.sum()).node
        assert consume_metric(margin).node.operands[1] == consume_metric(amount.count()).node
        assert root_declaration(margin).name == "margin"
        assert len(ctx.pending_definitions) == before
    finally:
        _exit_ctx()


def test_weighted_mean_keeps_measure_inputs() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount_ref = _leaf("CNY")
        weight_ref = ms.measure_column(
            name="quantity",
            entity=ref_factory.entity("sales.orders"),
            column="quantity",
            additivity=ms.additive_all(),
            unit="1",
        )
        finalized = ms.finalize()
        amount = finalized.resolve(amount_ref)
        weight = finalized.resolve(weight_ref)
        value = amount.weighted_mean(weight=weight)
        assert isinstance(consume_metric(value).node, SourceWeightedMean)
        assert isinstance(consume_metric(value).node.value, RowField)
        assert isinstance(consume_metric(value).node.weight, RowField)
        assert consume_metric(value).node.value.ref == consume_field(amount).reference
        assert consume_metric(value).node.weight.ref == consume_field(weight).reference
    finally:
        _exit_ctx()


def test_metric_subtract_keeps_ordered_source_nodes() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf("CNY")

        @ms.metric()
        def net_revenue() -> Deferred:
            return amount.sum() - amount.mean()

        assert isinstance(consume_metric(net_revenue).node, MetricExpression)
        assert consume_metric(net_revenue).node.operands[0] == consume_metric(amount.sum()).node
        assert consume_metric(net_revenue).node.operands[1] == consume_metric(amount.mean()).node
    finally:
        _exit_ctx()


def test_linear_requires_at_least_two_terms() -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.linear(name="lonely", add=[ref_factory.metric("sales.revenue")])
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert caught.value.repair is not None and "+ and -" in caught.value.repair.action


def test_metric_rejects_empty_entities() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.metric(entities=[], additivity=ms.additive_all())  # type: ignore[call-arg]  # intentional rejection input
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert "entities" in str(caught.value.received)
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.relationship() call
# ---------------------------------------------------------------------------


def test_relationship_returns_resolved_value() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
            primary_key=["path"],
        )
        items = ms.entity(
            name="items",
            datasource=ms.ref.datasource("wh"),
            source=md.table("items"),
            primary_key=["order_id"],
        )
        ms.dimension_column(name="path", entity=orders, column="path")
        ms.dimension_column(name="order_id", entity=items, column="order_id")
        rel = ms.relationship(
            name="orders_to_items",
            from_entity=ref_factory.entity("sales.orders"),
            to_entity=ref_factory.entity("sales.items"),
            keys=[
                ms.join_on(
                    ref_factory.dimension("sales.orders.path"),
                    ref_factory.dimension("sales.items.order_id"),
                )
            ],
        )
        assert _is_ref(rel, ms.SemanticKind.RELATIONSHIP)
        assert rel.path == "sales.orders_to_items"
    finally:
        _exit_ctx()


def test_relationship_pushes_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
            primary_key=["path"],
        )
        items = ms.entity(
            name="items",
            datasource=ms.ref.datasource("wh"),
            source=md.table("items"),
            primary_key=["order_id"],
        )
        ms.dimension_column(name="path", entity=orders, column="path")
        ms.dimension_column(name="order_id", entity=items, column="order_id")
        ms.relationship(
            name="orders_to_items",
            from_entity=ref_factory.entity("sales.orders"),
            to_entity=ref_factory.entity("sales.items"),
            keys=[
                ms.join_on(
                    ref_factory.dimension("sales.orders.path"),
                    ref_factory.dimension("sales.items.order_id"),
                )
            ],
        )
        ir, callable_ = _pending_objects(ctx)[-1]
        assert isinstance(ir, RelationshipIR)
        assert ir.name == "orders_to_items"
        assert ir.from_entity == "sales.orders"
        assert ir.to_entity == "sales.items"
        assert ir.keys[0].from_key == "sales.orders.path"
        assert ir.keys[0].to_key == "sales.items.order_id"
        assert callable_ is None
    finally:
        _exit_ctx()


def test_relationship_rejects_non_join_key_entries() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.relationship(
                name="orders_to_items",
                from_entity=ref_factory.entity("sales.orders"),
                to_entity=ref_factory.entity("sales.items"),
                keys=[object()],  # type: ignore[list-item]
            )
        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "join_on" in str(exc_info.value)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Ref factories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: ms.aggregate(name="revenue", measure="sales.orders.amount", agg="sum"), "measure"),
        (
            lambda: ms.ratio(
                name="margin",
                numerator="sales.revenue",
                denominator=ref_factory.metric("sales.cost"),
            ),
            "numerator",
        ),
        (
            lambda: ms.weighted_mean(
                name="aov",
                value=ref_factory.measure("sales.orders.unit_price"),
                weight="sales.orders",
            ),
            "weight",
        ),
        (
            lambda: ms.weighted_mean(
                name="aov",
                value=ref_factory.metric("sales.revenue"),
                weight=ref_factory.measure("sales.orders.requests"),
            ),
            "value",
        ),
        (
            lambda: ms.linear(
                name="net", add=[ref_factory.metric("sales.revenue")], subtract=["sales.refunds"]
            ),
            "subtract",
        ),
        (
            lambda: ms.relationship(
                name="orders_to_items",
                from_entity="sales.orders",  # type: ignore[arg-type]  # intentional rejection input
                to_entity=ref_factory.entity("sales.items"),
                keys=[],
            ),
            "from_entity",
        ),
        (
            lambda: ms.join_on("sales.orders.path", ref_factory.dimension("sales.items.order_id")),  # type: ignore[arg-type]  # intentional rejection input
            "from_key",
        ),
        (lambda: ms.snapshot(partition_field="sales.orders.dt", grain="day"), "partition_field"),  # type: ignore[arg-type]  # intentional rejection input
        (
            lambda: ms.validity(
                valid_from="sales.orders.valid_from",  # type: ignore[arg-type]  # intentional rejection input
                valid_to=ref_factory.dimension("sales.orders.valid_to"),
                interval="closed_open",
                open_end=(None,),
            ),
            "valid_from",
        ),
    ],
)
def test_authoring_reference_parameters_reject_naked_strings(
    call: Callable[[], object], expected: str
) -> None:
    _enter_ctx(default_domain="sales")
    try:
        retired = set(call.__code__.co_names) & {"aggregate", "ratio", "weighted_mean", "linear"}
        if retired:
            with pytest.raises(SemanticExpressionError) as caught:
                call()
            assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
            assert caught.value.expected and caught.value.received and caught.value.repair
            assert expected in str(caught.value.received)
        else:
            with pytest.raises(SemanticDecoratorError) as invalid:
                call()
            assert invalid.value.kind == ErrorKind.INVALID_REF
            assert invalid.value.constraint_id == ConstraintId.REF_SHAPE
            assert expected in str(invalid.value)
    finally:
        _exit_ctx()


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda: ms.aggregate(
                name="revenue", measure=ref_factory.metric("sales.revenue"), agg="sum"
            ),
            "Ref[measure]",
        ),
        (
            lambda: ms.ratio(
                name="margin",
                numerator=ref_factory.measure("sales.orders.amount"),
                denominator=ref_factory.metric("sales.cost"),
            ),
            "Ref[metric]",
        ),
        (
            lambda: ms.relationship(
                name="orders_to_items",
                from_entity=ref_factory.metric("sales.orders"),  # type: ignore[arg-type]  # intentional rejection input
                to_entity=ref_factory.entity("sales.items"),
                keys=[],
            ),
            "Ref[entity]",
        ),
        (
            lambda: ms.join_on(
                ref_factory.entity("sales.orders"),  # type: ignore[arg-type]  # intentional rejection input
                ref_factory.dimension("sales.items.order_id"),
            ),
            "Ref[dimension]",
        ),
        (
            lambda: ms.snapshot(partition_field=ref_factory.metric("sales.revenue"), grain="day"),  # type: ignore[arg-type]  # intentional rejection input
            "Ref[dimension]",
        ),
        (
            lambda: ms.validity(
                valid_from=ref_factory.metric("sales.valid_from"),  # type: ignore[arg-type]  # intentional rejection input
                valid_to=ref_factory.dimension("sales.orders.valid_to"),
                interval="closed_open",
                open_end=(None,),
            ),
            "Ref[dimension]",
        ),
    ],
)
def test_authoring_reference_parameters_reject_wrong_ref_kind(
    call: Callable[[], object], expected: str
) -> None:
    _enter_ctx(default_domain="sales")
    try:
        retired = set(call.__code__.co_names) & {"aggregate", "ratio", "weighted_mean", "linear"}
        if retired:
            with pytest.raises(SemanticExpressionError) as caught:
                call()
            assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
            assert caught.value.expected and caught.value.received and caught.value.repair
            assert "Ref[" in str(caught.value.received)
            assert next(iter(retired)) in str(caught.value.received)
        else:
            with pytest.raises(SemanticDecoratorError) as invalid:
                call()
            assert invalid.value.kind == ErrorKind.INVALID_REF
            assert invalid.value.constraint_id == ConstraintId.REF_SHAPE
            assert expected in str(invalid.value)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Duplicate name detection
# ---------------------------------------------------------------------------


def test_duplicate_dataset_name_raises() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))

        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))

        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


def test_local_names_do_not_publish_registry_collisions() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf()
        before = len(ctx.pending_definitions)

        def revenue() -> Deferred:
            return amount.sum()

        first = ms.metric()(revenue)
        second = ms.metric()(revenue)
        assert first is not second
        assert root_declaration(first).name == root_declaration(second).name == "revenue"
        assert computation_fingerprint(consume_metric(first).node) == computation_fingerprint(
            consume_metric(second).node
        )
        assert len(ctx.pending_definitions) == before
    finally:
        _exit_ctx()


def test_dataset_and_metric_same_name_no_collision() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount_ref = _leaf()
        orders = ms.entity(
            name="dau_7d_portrait", datasource=ms.ref.datasource("wh"), source=md.table("portraits")
        )

        amount = ms.finalize().resolve(amount_ref)

        @ms.metric()
        def dau_7d_portrait() -> Deferred:
            return amount.sum()

        assert orders.path == "sales.dau_7d_portrait"
        assert root_declaration(dau_7d_portrait).name == "dau_7d_portrait"
    finally:
        _exit_ctx()


def test_field_and_time_field_same_name_same_dataset_collides() -> None:
    """A field and a time_field with the same name on the same dataset share the fields namespace."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )

        @ms.dimension(entity=ds, name="log_date")
        def log_date_field(table: ibis_ir.Table) -> ibis_ir.Value:
            return table.log_date

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ds,
                name="log_date",
                granularity="day",
                parse=ms.strptime("%Y%m%d"),
            )
            def log_date_tf(table: ibis_ir.Table) -> ibis_ir.Value:
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
            ms.domain("sales")  # type: ignore[call-arg]  # intentional rejection input
    finally:
        _exit_ctx()


def test_dataset_keyword_only() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.entity("wh")  # type: ignore[call-overload]  # intentional rejection input
    finally:
        _exit_ctx()


def test_metric_keyword_only() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.metric("additive")  # type: ignore[arg-type, call-arg]  # intentional rejection input
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext handling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AiContext validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ("summary", "synonyms", "examples", "instructions", "owner_notes"),
)
def test_ai_context_invalid_key_raises_type_error(key: str) -> None:
    """ms.ai_context() rejects unsupported metadata keys."""
    with pytest.raises(TypeError, match="ai_context"):
        ms.ai_context(**{key: "oops"})


def test_ai_context_with_wrong_type_for_guardrails_raises() -> None:
    """ms.ai_context() with wrong type for guardrails raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(guardrails="not a list")
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    assert "guardrails" in str(exc_info.value)


def test_ai_context_with_wrong_type_for_business_definition_raises() -> None:
    """ms.ai_context() with wrong type for business_definition raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(business_definition=42)  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    assert "business_definition" in str(exc_info.value)


def test_ai_context_with_non_string_in_list_raises() -> None:
    """ms.ai_context() with non-string items in list field raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(guardrails=[1, 2, 3])  # type: ignore[list-item]
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT


def test_ai_context_empty_returns_defaults() -> None:
    """ms.ai_context() with no args returns an empty AiContextValue."""
    val = ms.ai_context()
    assert isinstance(val, ms.typing.AiContextValue)
    assert val.business_definition is None
    assert val.guardrails == ()


def test_ai_context_raw_dict_raises_teachable_error() -> None:
    """Passing a raw dict to ai_context= raises INVALID_AI_CONTEXT with ms.ai_context() guidance."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(  # type: ignore[arg-type]  # intentional rejection input
                ai_context={"business_definition": "I should use ms.ai_context()"},  # type: ignore[arg-type]
            )
            def revenue(table: object) -> object:
                return None

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
        assert "ms.ai_context" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_ai_context_error_location_points_to_user_code() -> None:
    """ms.ai_context() type errors report the user's call site, not internal code."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(business_definition=42)  # type: ignore[arg-type]
    assert exc_info.value.location is not None
    # The location should point to this test file, not to authoring.py
    assert "test_semantic_authoring" in exc_info.value.location.file
    assert exc_info.value.location.line > 0


def test_ai_context_value_post_init_rejects_invalid_types() -> None:
    """AiContextValue.__post_init__ rejects invalid field types even when bypassing ms.ai_context()."""
    with pytest.raises(TypeError, match=r"ms\.ai_context"):
        ms.typing.AiContextValue(business_definition=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ms\.ai_context"):
        ms.typing.AiContextValue(guardrails=("ok", 1))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dataset-scoped field IDs
# ---------------------------------------------------------------------------


def test_two_datasets_same_column_name_distinct_ids() -> None:
    """Two datasets sharing a column name produce distinct dataset-scoped field IDs."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ds = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )
        portrait_ds = ms.entity(
            name="portrait",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("portrait"),
        )

        @ms.dimension(entity=orders_ds, name="region")
        def orders_region(table: ibis_ir.Table) -> ibis_ir.Value:
            return table.region

        @ms.dimension(entity=portrait_ds, name="region")
        def portrait_region(table: ibis_ir.Table) -> ibis_ir.Value:
            return table.region

        assert orders_region.path == "sales.orders.region"
        assert portrait_region.path == "sales.portrait.region"
    finally:
        _exit_ctx()


def test_field_model_mismatch_with_dataset_raises() -> None:
    """A field whose model disagrees with the dataset's model must raise."""
    ctx = _enter_ctx(default_domain="sales")
    try:
        ds = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
        )

        inventory_ref = ms.ref.domain("inventory")
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.dimension(entity=ds, name="region", domain=inventory_ref)
            def region(table: ibis_ir.Table) -> ibis_ir.Value:
                return table.region

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()


# metric unit field


def test_metric_inherits_row_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf("USD")

        @ms.metric()
        def revenue() -> Deferred:
            return amount.sum()

        assert consume_metric(revenue).unit == "USD"
    finally:
        _exit_ctx()


def test_metric_unit_defaults_to_none() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf(None)

        @ms.metric()
        def revenue() -> Deferred:
            return amount.sum()

        assert consume_metric(revenue).unit is None
    finally:
        _exit_ctx()


def test_metric_division_derives_dimensionless_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = _closed_leaf("CNY")
        value = amount.sum() / amount.mean()
        assert consume_metric(value).unit == "1"
    finally:
        _exit_ctx()


@pytest.mark.parametrize("bad", ("", "C N Y", "CNY\t", "µs"))
def test_metric_unit_rejects_whitespace_and_empty(bad: str) -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.metric(unit=bad)  # type: ignore[call-arg]  # intentional rejection input
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert "unit" in str(caught.value.received)


def test_date_only_strptime_rejects_timezone() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.strptime("%Y%m%d", timezone="UTC")

    assert "timezone" in exc_info.value.message
    assert "date-only" in exc_info.value.message


@pytest.mark.parametrize("bad", ("", "C N Y"))
def test_ratio_unit_rejects_whitespace_and_empty(bad: str) -> None:
    with pytest.raises(SemanticExpressionError) as caught:
        ms.ratio(
            name="ratio",
            numerator=ref_factory.metric("sales.revenue"),
            denominator=ref_factory.metric("sales.cost"),
            unit=bad,
        )
    assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    assert "unit" in str(caught.value.received)


# ---------------------------------------------------------------------------
# ms.cumulative() derived registration
# ---------------------------------------------------------------------------


def test_retired_cumulative_never_pushes_legacy_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.cumulative(
                name="running",
                base=ref_factory.metric("sales.revenue"),
                over=ref_factory.time_dimension("sales.orders.ordered_at"),
            )
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "base.window(rule)" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


def test_cumulative_allows_omitted_over_for_loader_resolution() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.cumulative(name="running", base=ref_factory.metric("sales.revenue"))
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "base.window(rule)" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


def test_cumulative_rejects_string_refs() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.cumulative(name="running", base="sales.revenue")
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert caught.value.repair is not None and "base.window(rule)" in caught.value.repair.action
        assert ctx.pending_definitions == []
    finally:
        _exit_ctx()


def test_period_calendar_authoring_pushes_typed_declaration() -> None:
    ctx = LoaderContext(default_domain="sales")
    with loader_context(ctx):
        calendar = ms.period_calendar(
            name="fiscal",
            date=ref_factory.time_dimension("sales.calendar.calendar_date"),
            boundary_timezone="UTC",
            coverage=(date(2026, 1, 1), date(2027, 1, 1)),
            levels={"week": ref_factory.dimension("sales.calendar.fiscal_week")},
            correspondences={
                "prior": ms.period_correspondence(
                    level="week",
                    baseline_key=ref_factory.dimension("sales.calendar.prior_week"),
                )
            },
        )

    assert calendar == ref_factory.period_calendar("sales.fiscal")
    pending = ctx.pending_definitions[-1]
    assert isinstance(pending, PendingDefinition)
    assert isinstance(pending.definition, PeriodCalendarIR)
    assert pending.definition.levels == (("week", "sales.calendar.fiscal_week"),)
    assert pending.definition.correspondences == (("prior", "week", "sales.calendar.prior_week"),)


def test_work_schedule_authoring_pushes_typed_declaration() -> None:
    ctx = LoaderContext(default_domain="sales")
    with loader_context(ctx):
        schedule = ms.work_schedule(
            name="sales_schedule",
            date=ref_factory.time_dimension("sales.calendar.calendar_date"),
            is_working=ref_factory.dimension("sales.calendar.is_working"),
            boundary_timezone="Asia/Shanghai",
            coverage=(date(2026, 1, 1), date(2026, 1, 5)),
        )

    assert schedule == ref_factory.work_schedule("sales.sales_schedule")
    pending = ctx.pending_definitions[-1]
    assert isinstance(pending, PendingDefinition)
    assert isinstance(pending.definition, WorkScheduleIR)
    assert pending.definition.date == "sales.calendar.calendar_date"
    assert pending.definition.is_working == "sales.calendar.is_working"
    assert pending.definition.coverage == ("2026-01-01", "2026-01-05")


def test_authoring_cases_release_context_without_pytest_fixture() -> None:
    try:
        test_entity_count_retains_exact_root()
        assert _LOADER_CTX.get() is None
        test_dataset_outside_context_raises()
        test_dataset_outside_context_raises()
        test_entity_count_retains_exact_root()
        assert _LOADER_CTX.get() is None
    finally:
        _exit_ctx()
