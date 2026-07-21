"""Tests for marivo.semantic.authoring — decorator and builder implementation.

Tests cover:
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

from collections.abc import Callable
from typing import get_type_hints

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.refs import ref as ref_factory
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, SemanticLoadError
from marivo.semantic.ir import (
    AiContextIR,
    DimensionIR,
    DimensionKind,
    MeasureIR,
    MetricIR,
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


def _pending_objects(ctx: LoaderContext | None) -> list[tuple[object, object | None]]:
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


class _FakeTable:
    def __init__(self) -> None:
        self.columns_requested: list[str] = []

    def __getitem__(self, column: str) -> str:
        self.columns_requested.append(column)
        return f"column:{column}"


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
        ms.domain(name="sales", owner="Mina Zhang")
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_dataset_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.dimension(entity="orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_time_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.time_dimension(entity="orders", granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_metric_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_relationship_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.relationship(
            name="orders_to_items",
            from_entity="orders",
            to_entity="items",
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
        ms.domain(name="sales", owner="Mina Zhang", default=True)
        # Should have one pending object
        assert len(_pending_objects(ctx)) == 1
        ir, callable_ = _pending_objects(ctx)[0]
        assert ir.name == "sales"
        assert ir.owner == "Mina Zhang"
        assert ir.default is True
        # model() is not a decorator — no callable
        assert callable_ is None
    finally:
        _exit_ctx()


def test_model_sets_default_domain_on_context() -> None:
    ctx = _enter_ctx()
    try:
        assert ctx.default_domain is None
        ms.domain(name="sales", owner="Mina Zhang", default=True)
        assert ctx.default_domain == "sales"
    finally:
        _exit_ctx()


def test_model_default_false_does_not_set_context() -> None:
    ctx = _enter_ctx(default_domain="existing")
    try:
        ms.domain(name="other", owner="Alex Chen", default=False)
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


def test_model_requires_owner_keyword() -> None:
    _enter_ctx()
    try:
        with pytest.raises(TypeError):
            ms.domain(name="sales")  # type: ignore[call-arg]
    finally:
        _exit_ctx()


@pytest.mark.parametrize("owner", [42, None])
def test_model_owner_must_be_string(owner: object) -> None:
    _enter_ctx()
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.domain(name="sales", owner=owner)  # type: ignore[arg-type]
        assert exc_info.value.kind == ErrorKind.INVALID_DOMAIN_OWNER
        assert "owner must be a non-empty string" in str(exc_info.value)
    finally:
        _exit_ctx()


@pytest.mark.parametrize("owner", ["", "   "])
def test_model_owner_must_be_non_empty(owner: str) -> None:
    _enter_ctx()
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.domain(name="sales", owner=owner)
        assert exc_info.value.kind == ErrorKind.INVALID_DOMAIN_OWNER
        assert "owner must be a non-empty string" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_model_returns_model_ref() -> None:
    _enter_ctx()
    try:
        ref = ms.domain(name="sales", owner="Mina Zhang", default=True)
        assert _is_ref(ref, ms.SemanticKind.DOMAIN)
        assert ref.path == "sales"
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
        def region(table):
            return table.region

        assert region.path == "sales.orders.region"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.entity() decorator
# ---------------------------------------------------------------------------


def test_dataset_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )

        assert _is_ref(orders, ms.SemanticKind.ENTITY)
        assert orders.path == "sales.orders"
    finally:
        _exit_ctx()


def test_dataset_requires_name_without_body() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.entity(datasource=ms.ref.datasource("wh"), source=md.table("orders"))  # type: ignore[call-arg]
    finally:
        _exit_ctx()


def test_dataset_rejects_bare_string_datasource() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource="wh", source=md.table("orders"))  # type: ignore[arg-type]

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
            def region(table):
                return table.region

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "ms.domain(name=...)" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_dataset_explicit_name() -> None:
    _enter_ctx(default_domain="sales")
    try:
        _orders_impl = ms.entity(
            name="orders_tbl",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
        )

        assert _is_ref(_orders_impl, ms.SemanticKind.ENTITY)
        assert _orders_impl.path == "sales.orders_tbl"
    finally:
        _exit_ctx()


def test_dataset_pushes_ir_without_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
        )
        ir, callable_ = _pending_objects(ctx)[-1]
        assert ref.path == "sales.orders"
        assert ir.semantic_id == "sales.orders"
        assert ir.domain == "sales"
        assert ir.name == "orders"
        assert ir.datasource == "wh"
        assert ir.source == md.table("orders")
        assert callable_ is None
    finally:
        _exit_ctx()


def test_dataset_datasource_as_string() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_datasource_as_datasource_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        warehouse = ms.ref.datasource("wh")
        ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_primary_key() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders"),
            primary_key=["order_id"],
        )

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.primary_key == ("order_id",)
    finally:
        _exit_ctx()


def test_dataset_source_records_table_database() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.table("orders", database="sales_mart"),
        )

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.source.kind == "table"
        assert ir.source.table == "orders"
        assert ir.source.database == "sales_mart"
    finally:
        _exit_ctx()


def test_dataset_source_records_parquet_source() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.parquet("/data/orders/*.parquet", hive_partitioning=True),
        )

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.source.kind == "parquet"
        assert ir.source.path == "/data/orders/*.parquet"
        assert ir.source.hive_partitioning is True
    finally:
        _exit_ctx()


def test_dataset_source_records_csv_source() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.entity(
            name="orders",
            datasource=ms.ref.datasource("wh"),
            source=md.csv(
                "/data/orders.csv", schema={"order_id": "string"}, header=False, delimiter="|"
            ),
        )

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.source.kind == "csv"
        assert ir.source.path == "/data/orders.csv"
        assert ir.source.header is False
        assert ir.source.delimiter == "|"
    finally:
        _exit_ctx()


def test_entity_rejects_invalid_source_value() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=object())  # type: ignore[arg-type]
        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "source" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_entity_accepts_json_source(semantic_project_factory) -> None:
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
                    "    source=md.json('data/events/*.json', schema={'event_id': 'string'}),",
                    ")",
                ]
            )
        }
    )

    assert project._registry is not None
    assert project._registry.entities["sales.events"].source.to_dict() == {
        "kind": "json",
        "path": "data/events/*.json",
        "schema": {"event_id": "string"},
        "format": "auto",
    }


def test_table_source_constructor() -> None:
    """md.table, md.parquet, and md.csv produce correct IR objects."""
    from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR

    tbl = md.table("orders", database="sales_mart")
    assert isinstance(tbl, TableSourceIR)
    assert tbl.table == "orders"
    assert tbl.database == "sales_mart"

    pq = md.parquet("/data/orders.parquet", hive_partitioning=True)
    assert isinstance(pq, ParquetSourceIR)
    assert pq.path == "/data/orders.parquet"
    assert pq.hive_partitioning is True

    cs = md.csv("/data/orders.csv", schema={"order_id": "string"}, delimiter=",")
    assert isinstance(cs, CsvSourceIR)
    assert cs.path == "/data/orders.csv"
    assert cs.delimiter == ","


def test_entity_is_not_a_decorator() -> None:
    """ms.entity() is a plain call returning Ref[entity], not a decorator."""
    _enter_ctx(default_domain="sales")
    try:
        result = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        assert _is_ref(result, ms.SemanticKind.ENTITY)
        # It does not accept a function body — it returns a ref, not a decorator.
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.dimension() decorator
# ---------------------------------------------------------------------------


def test_field_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert _is_ref(amount, ms.SemanticKind.DIMENSION)
        assert amount.path == "sales.orders.amount"
    finally:
        _exit_ctx()


def test_dimension_accepts_leading_docstring() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def region(table: object) -> object:
            """Order region."""
            return None  # type: ignore[unreachable]

        assert _is_ref(region, ms.SemanticKind.DIMENSION)
        assert region.path == "sales.orders.region"
    finally:
        _exit_ctx()


def test_field_name_defaults_to_function_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.name == "amount"
        assert ir.is_time_dimension is False
        assert ir.granularity is None
    finally:
        _exit_ctx()


def test_field_explicit_name() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(name="order_amount", entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.name == "order_amount"
        assert ir.semantic_id == "sales.orders.order_amount"
    finally:
        _exit_ctx()


def test_field_with_dataset_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ds_ref = ref_factory.entity("sales.orders")

        @ms.dimension(entity=ds_ref)
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert _is_ref(amount, ms.SemanticKind.DIMENSION)
    finally:
        _exit_ctx()


def test_field_pushes_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        def amount_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.dimension(entity=ref_factory.entity("sales.orders"))(amount_fn)
        ir, callable_ = _pending_objects(ctx)[-1]
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
                fn = lambda value: value
                return fn(table)

        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert "Dimension body of 'amount'" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_field_kind_defaults_to_dimension() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.dimension(entity=ref_factory.entity("sales.orders"))
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        irs = [obj for obj, _ in _pending_objects(ctx) if isinstance(obj, DimensionIR)]
        assert len(irs) == 1
        assert irs[0].kind == DimensionKind.CATEGORICAL
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
        assert _pending_refs(ctx)[-1] is ref
    finally:
        _exit_ctx()


def test_dimension_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.dimension_column(  # type: ignore[arg-type]
                name="region",
                entity="sales.orders",
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


def test_field_kind_measure() -> None:
    from marivo.semantic.ir import MeasureIR

    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.measure(entity=ref_factory.entity("sales.orders"), additivity="additive")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        irs = [obj for obj, _ in _pending_objects(ctx) if isinstance(obj, MeasureIR)]
        assert len(irs) == 1
        assert irs[0].kind == ms.SemanticKind.MEASURE
    finally:
        _exit_ctx()


def test_measure_accepts_leading_docstring() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.measure(entity=ref_factory.entity("sales.orders"), additivity="additive")
        def amount(table: object) -> object:
            """Order amount."""
            return None  # type: ignore[unreachable]

        assert _is_ref(amount, ms.SemanticKind.MEASURE)
        assert amount.path == "sales.orders.amount"
    finally:
        _exit_ctx()


def test_measure_body_error_uses_measure_label() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.measure(entity=ref_factory.entity("sales.orders"), additivity="additive")
            def amount(table: object) -> object:
                return table.amount
                table.amount  # noqa: B018

        assert "Measure body of 'amount'" in str(exc_info.value)
        assert "forbidden Expr statement" in str(exc_info.value)
    finally:
        _exit_ctx()


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
            additivity="additive",
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
        assert _pending_refs(ctx)[-1] is ref
    finally:
        _exit_ctx()


def test_measure_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.measure_column(  # type: ignore[arg-type]
                name="amount",
                entity="sales.orders",
                column="amount",
                additivity="additive",
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "entity must be Ref[entity]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.time_dimension() decorator
# ---------------------------------------------------------------------------


def test_time_field_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert _is_ref(order_date, ms.SemanticKind.TIME_DIMENSION)
        assert order_date.path == "sales.orders.order_date"
    finally:
        _exit_ctx()


def test_time_dimension_accepts_leading_docstring() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
        def order_date(table: object) -> object:
            """Order date."""
            return None  # type: ignore[unreachable]

        assert _is_ref(order_date, ms.SemanticKind.TIME_DIMENSION)
        assert order_date.path == "sales.orders.order_date"
    finally:
        _exit_ctx()


def test_time_dimension_body_error_uses_time_dimension_label() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
            def order_date(table: object) -> object:
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
            return None  # type: ignore[unreachable]

        @ms.time_dimension(
            entity=ref_factory.entity("sales.orders"),
            granularity="hour",
            parse=ms.hour_prefix(order_date),
        )
        def order_hour(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

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
                return None  # type: ignore[unreachable]
    finally:
        _exit_ctx()


def test_time_field_body_rejects_sql_escape_hatch() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_dimension(entity=ref_factory.entity("sales.orders"), granularity="day")
            def order_date(backend: object) -> object:
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
                return None  # type: ignore[unreachable]

        assert "timezone" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_field_rejects_yyyymmdd_shorthand() -> None:
    """Shorthand aliases like 'yyyymmdd' are no longer accepted by ms.strptime()."""
    with pytest.raises(ValueError, match="%"):
        ms.strptime("yyyymmdd")


def test_time_field_rejects_hh_shorthand() -> None:
    """Shorthand 'hh' is no longer accepted by ms.strptime()."""
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
                return None  # type: ignore[unreachable]
    finally:
        _exit_ctx()


def test_time_field_rejects_date_format_on_date_type() -> None:
    """date_format is no longer a parameter on time_dimension; this is enforced by signature."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):

            @ms.time_dimension(
                entity=ref_factory.entity("sales.orders"),
                granularity="day",
                date_format="%Y-%m-%d",  # type: ignore[call-arg]
            )
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]
    finally:
        _exit_ctx()


def test_time_field_rejects_date_format_on_hour_only_field() -> None:
    """date_format is no longer a parameter on time_dimension; this is enforced by signature."""
    _enter_ctx(default_domain="sales")
    try:
        order_date = ref_factory.time_dimension("sales.orders.order_date")

        with pytest.raises(TypeError):

            @ms.time_dimension(
                entity=ref_factory.entity("sales.orders"),
                granularity="hour",
                parse=ms.hour_prefix(order_date),
                date_format="%H",  # type: ignore[call-arg]
            )
            def order_hour(table: object) -> object:
                return None  # type: ignore[unreachable]
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
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
            return None  # type: ignore[unreachable]

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
            return None  # type: ignore[unreachable]

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


def test_time_dimension_rejects_invalid_sample_interval_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="second",
                parse=ms.timestamp(timezone="UTC", sample_interval=(1, "day")),  # type: ignore[arg-type]
            )
            def sample_ts(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
    finally:
        _exit_ctx()


def test_strptime_time_dimension_rejects_invalid_sample_interval_unit() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

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
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
    finally:
        _exit_ctx()


@pytest.mark.parametrize("fmt", ["%H", "%H:%M"])
def test_strptime_time_dimension_rejects_sample_interval_without_date_context(
    fmt: str,
) -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="hour",
                parse=ms.strptime(
                    fmt,
                    sample_interval=(1, "hour"),
                ),
            )
            def sample_hour(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        assert "date context" in exc_info.value.message
    finally:
        _exit_ctx()


def test_time_dimension_coarser_granularity_error_suggests_fix() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ref_factory.entity("sales.bandwidth_samples"),
                granularity="day",
                parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
            )
            def sample_ts(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        message = str(exc_info.value)
        assert "Set granularity to 'minute' or finer" in message
        assert "'second', 'minute'" in message
    finally:
        _exit_ctx()


def test_strptime_time_dimension_coarser_granularity_error_suggests_fix() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

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
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_SAMPLE_INTERVAL
        message = str(exc_info.value)
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
            return None  # type: ignore[unreachable]

        @ms.time_dimension(
            entity=ref_factory.entity("sales.bandwidth_samples"),
            granularity="hour",
            parse=ms.hour_prefix(dt, sample_interval=(1, "hour")),
        )
        def hh(table: object) -> object:
            return None  # type: ignore[unreachable]

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
                return None  # type: ignore[unreachable]

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
        assert _pending_refs(ctx)[-1] is ref
    finally:
        _exit_ctx()


def test_time_dimension_column_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.time_dimension_column(  # type: ignore[arg-type]
                name="log_date",
                entity="sales.orders",
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


def test_metric_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert _is_ref(revenue, ms.SemanticKind.METRIC)
        assert revenue.path == "sales.revenue"
    finally:
        _exit_ctx()


def test_metric_accepts_leading_docstring() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            """Order revenue."""
            return None  # type: ignore[unreachable]

        assert _is_ref(revenue, ms.SemanticKind.METRIC)
        assert revenue.path == "sales.revenue"
    finally:
        _exit_ctx()


def test_metric_with_entities() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.metric_type == "simple"
        assert ir.entities == ("sales.orders",)
        assert ir.composition is None
    finally:
        _exit_ctx()


def test_metric_with_entity_ref() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ref = ref_factory.entity("sales.orders")

        @ms.metric(entities=[orders_ref], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.entities == ("sales.orders",)
    finally:
        _exit_ctx()


def test_metric_provenance_fields() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(
            entities=[ref_factory.entity("sales.orders")],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="ansi"),
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        prov = ir.provenance
        assert prov is not None
        assert prov.sql == "SELECT SUM(amount) FROM orders"
        assert prov.dialect == "ansi"
        assert prov.verification_mode == "sql_parity"
    finally:
        _exit_ctx()


def test_metric_rejects_invalid_provenance_value() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                entities=[ref_factory.entity("sales.orders")],
                additivity="additive",
                provenance=object(),  # type: ignore[arg-type]
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "provenance" in str(exc_info.value)
    finally:
        _exit_ctx()


def test_metric_body_ast_hash() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        # body_ast_hash should be a non-empty string
        assert isinstance(ir.body_ast_hash, str)
        assert len(ir.body_ast_hash) > 0
    finally:
        _exit_ctx()


def test_metric_pushes_callable() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")(revenue_fn)
        ir, callable_ = _pending_objects(ctx)[-1]
        assert callable_ is revenue_fn
    finally:
        _exit_ctx()


def test_metric_accepts_semi_additive() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.time_dimension(
            entity=ref_factory.entity("sales.bandwidth_samples"), granularity="minute"
        )
        def sample_ts(table: object) -> object:
            return None  # type: ignore[unreachable]

        @ms.metric(
            entities=[ref_factory.entity("sales.bandwidth_samples")],
            additivity=ms.semi_additive(
                over=sample_ts,
                fold="mean",
            ),
        )
        def upstream_avg(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert isinstance(ir, MetricIR)
        assert ir.additivity is not None
    finally:
        _exit_ctx()


def test_semi_additive_rejects_string_over() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.semi_additive(over="sales.orders.order_date", fold="last")  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "over must be Ref[time_dimension]" in str(exc_info.value)
    assert "got str" in str(exc_info.value)


def test_fold_surfaces_publish_shared_closed_aliases() -> None:
    assert get_type_hints(ms.aggregate)["fold"] is ms.AggregateFoldInput
    assert get_type_hints(ms.semi_additive)["fold"] is ms.AggregateFoldValue


@pytest.mark.parametrize("fold", ["mean", "min", "max", "first", "last", ("percentile", 0.9)])
def test_aggregate_and_semi_additive_share_fold_normalization(fold: object) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.aggregate(
            name="inventory",
            measure=ref_factory.measure("sales.inventory.quantity"),
            agg="sum",
            fold=fold,  # type: ignore[arg-type]
        )
        metric_ir, _ = _pending_objects(ctx)[-1]
        semi_additive = ms.semi_additive(
            over=ref_factory.time_dimension("sales.inventory.snapshot_at"),
            fold=fold,  # type: ignore[arg-type]
        )
    finally:
        _exit_ctx()

    assert metric_ir.fold_override == semi_additive.fold


@pytest.mark.parametrize(
    "fold",
    ["auto", "sum", ("percentile", 0.0), ("percentile", 1.0), ("other", 0.5), ("percentile",)],
)
def test_aggregate_rejects_values_outside_shared_fold_alias(fold: object) -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.aggregate(
                name="inventory",
                measure=ref_factory.measure("sales.inventory.quantity"),
                agg="sum",
                fold=fold,  # type: ignore[arg-type]
            )
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_TIME_FOLD


def test_semi_additive_rejects_null_fold() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.semi_additive(
            over=ref_factory.time_dimension("sales.inventory.snapshot_at"),
            fold=None,  # type: ignore[arg-type]
        )

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "requires a fold" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.aggregate() call
# ---------------------------------------------------------------------------


def test_aggregate_returns_ref() -> None:
    _enter_ctx(default_domain="sales")
    try:
        amount = ref_factory.measure("sales.orders.amount")
        ref = ms.aggregate(name="amount", measure=amount, agg="sum")
        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.amount"
    finally:
        _exit_ctx()


def test_aggregate_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(
            name="revenue", measure=ref_factory.measure("sales.orders.amount"), agg="sum"
        )
        ir, sidecar = _pending_objects(ctx)[-1]
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
        ref = ms.aggregate(
            name="amount", measure=ref_factory.measure("sales.orders.amount"), agg="sum"
        )
        assert ref.path == "sales.amount"
    finally:
        _exit_ctx()


def test_aggregate_explicit_name() -> None:
    _enter_ctx(default_domain="sales")
    try:
        ref = ms.aggregate(
            name="revenue", measure=ref_factory.measure("sales.orders.amount"), agg="sum"
        )
        assert ref.path == "sales.revenue"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.count() call
# ---------------------------------------------------------------------------


def test_count_requires_entity_ref() -> None:
    _enter_ctx(default_domain="ignored")
    try:
        orders = ref_factory.entity("sales.orders")
        ref = ms.count(name="order_count", entity=orders)
        ir, sidecar = _pending_objects(_LOADER_CTX.get())[-1]  # type: ignore[union-attr]
        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.order_count"
        assert sidecar is None
        assert ir.metric_type == "simple"
        assert ir.aggregation == "count"
        assert ir.measure is None
        assert ir.aggregation_target == "sales.orders"
        assert ir.aggregation_target_kind == "entity"
        assert ir.domain == "sales"
        assert ir.entities == ("sales.orders",)
        assert ir.root_entity == "sales.orders"
    finally:
        _exit_ctx()


def test_count_rejects_string_entity() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.count(name="order_count", entity="sales.orders")  # type: ignore[arg-type]
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "entity must be Ref[entity]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.where() filtered count / aggregate
# ---------------------------------------------------------------------------


def test_where_requires_at_least_one_condition() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.where()  # type: ignore[call-arg]
    finally:
        _exit_ctx()
    assert exc_info.value.kind == ErrorKind.INVALID_REF


def test_count_with_filter_records_equality_predicates_on_ir() -> None:
    _enter_ctx(default_domain="sales")
    try:
        orders = ref_factory.entity("sales.orders")
        ref = ms.count(name="failed_count", entity=orders, filter=ms.where(state="FAILED"))
        ir, _sidecar = _pending_objects(_LOADER_CTX.get())[-1]  # type: ignore[union-attr]
        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.failed_count"
        assert ir.filter == (("state", "FAILED"),)
    finally:
        _exit_ctx()


@pytest.mark.parametrize("bad_filter", [{"state": "FAILED"}, [("state", "FAILED")], "FAILED"])
def test_count_rejects_non_where_filter(bad_filter: object) -> None:
    """A filter that is not a WhereFilter must raise a typed error pointing at
    ms.where(...), not a generic load failure. See MR !29 review P2.
    """
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.count(
                name="failed_count",  # type: ignore[arg-type]
                entity=ref_factory.entity("sales.orders"),
                filter=bad_filter,  # type: ignore[arg-type]
            )
    finally:
        _exit_ctx()
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "ms.where" in str(exc_info.value)


def test_aggregate_rejects_non_where_filter() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.aggregate(
                name="failed_amount",
                measure=ref_factory.measure("sales.orders.amount"),
                agg="sum",
                filter={"state": "FAILED"},  # type: ignore[arg-type]
            )
    finally:
        _exit_ctx()
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "ms.where" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ms.ratio() derived registration
# ---------------------------------------------------------------------------


def test_ratio_returns_ref_and_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.ratio(
            name="margin",
            numerator=ref_factory.metric("sales.revenue"),
            denominator=ref_factory.metric("sales.cost"),
        )

        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.margin"
        ir, sidecar_entry = _pending_objects(ctx)[-1]
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


def test_weighted_mean_keeps_measure_inputs() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.weighted_mean(
            name="aov",
            value=ref_factory.measure("sales.orders.unit_price"),
            weight=ref_factory.measure("sales.orders.quantity"),
        )

        ir, sidecar_entry = _pending_objects(ctx)[-1]
        assert sidecar_entry is None
        assert ir.metric_type == "simple"
        assert ir.composition is None
        assert ir.weighted_mean is not None
        assert ir.weighted_mean.kind == "weighted_mean"
        assert ir.weighted_mean.value == "sales.orders.unit_price"
        assert ir.weighted_mean.weight == "sales.orders.quantity"
    finally:
        _exit_ctx()


def test_linear_returns_ref_and_pushes_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ref = ms.linear(
            name="net_revenue",
            add=[ref_factory.metric("sales.gross_revenue")],
            subtract=[ref_factory.metric("sales.refunds")],
        )
        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.net_revenue"
        ir, _ = _pending_objects(ctx)[-1]
        assert ir.metric_type == "derived"
        assert ir.composition is not None
        assert ir.composition.kind == "linear"
    finally:
        _exit_ctx()


def test_linear_requires_at_least_two_terms() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.linear(name="lonely", add=[ref_factory.metric("sales.revenue")])

        assert exc_info.value.kind == ErrorKind.INVALID_REF
    finally:
        _exit_ctx()


def test_metric_rejects_empty_entities() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(entities=[], additivity="additive")
            def margin() -> object:
                return 1

        assert exc_info.value.kind == ErrorKind.MISSING_ENTITIES
        assert exc_info.value.constraint_id == "metric_entities_required"
        assert _pending_objects(ctx) == []
    finally:
        _exit_ctx()


def test_metric_sidecar_stores_callable() -> None:
    """Simple metric stores the raw callable in sidecar."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")(revenue_fn)
        _, sidecar_entry = _pending_objects(ctx)[-1]
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


def test_relationship_with_ref_objects() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders_ref = ref_factory.entity("sales.orders")
        items_ref = ref_factory.entity("sales.items")
        id_ref = ref_factory.dimension("sales.orders.path")
        oid_ref = ref_factory.dimension("sales.items.order_id")

        ms.relationship(
            name="orders_to_items",
            from_entity=orders_ref,
            to_entity=items_ref,
            keys=[ms.join_on(id_ref, oid_ref)],
        )
        ir, _ = _pending_objects(ctx)[-1]
        assert ir.from_entity == "sales.orders"
        assert ir.to_entity == "sales.items"
        assert ir.keys[0].from_key == "sales.orders.path"
        assert ir.keys[0].to_key == "sales.items.order_id"
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Ref factories
# ---------------------------------------------------------------------------


def test_ref_factory_namespace_replaces_factories_on_ref_type() -> None:
    assert hasattr(ms, "ref")
    assert not hasattr(ms.Ref, "metric")
    assert not hasattr(ms, "Ref[metric]")
    assert ms.ref.metric("sales.revenue").path == "sales.revenue"


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
                from_entity="sales.orders",
                to_entity=ref_factory.entity("sales.items"),
                keys=[],
            ),
            "from_entity",
        ),
        (
            lambda: ms.join_on("sales.orders.path", ref_factory.dimension("sales.items.order_id")),
            "from_key",
        ),
        (lambda: ms.snapshot(partition_field="sales.orders.dt", grain="day"), "partition_field"),
        (
            lambda: ms.validity(
                valid_from="sales.orders.valid_from",
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
        with pytest.raises(SemanticDecoratorError) as exc_info:
            call()
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert exc_info.value.constraint_id == ConstraintId.REF_SHAPE
    assert expected in str(exc_info.value)


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
                from_entity=ref_factory.metric("sales.orders"),
                to_entity=ref_factory.entity("sales.items"),
                keys=[],
            ),
            "Ref[entity]",
        ),
        (
            lambda: ms.join_on(
                ref_factory.entity("sales.orders"), ref_factory.dimension("sales.items.order_id")
            ),
            "Ref[dimension]",
        ),
        (
            lambda: ms.snapshot(partition_field=ref_factory.metric("sales.revenue"), grain="day"),
            "Ref[dimension]",
        ),
        (
            lambda: ms.validity(
                valid_from=ref_factory.metric("sales.valid_from"),
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
        with pytest.raises(SemanticDecoratorError) as exc_info:
            call()
    finally:
        _exit_ctx()

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert exc_info.value.constraint_id == ConstraintId.REF_SHAPE
    assert expected in str(exc_info.value)


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


def test_duplicate_metric_name_raises() -> None:
    _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(backend: object) -> object:
            return None  # type: ignore[unreachable]

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
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
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("dau_7d_portrait"),
        )
        assert ds.path == "sales.dau_7d_portrait"

        @ms.metric(
            entities=[ds],
            additivity="additive",
            name="dau_7d_portrait",
        )
        def dau_7d_portrait(table):
            return table.dau.sum()

        assert dau_7d_portrait.path == "sales.dau_7d_portrait"
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
        def log_date_field(table):
            return table.log_date

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_dimension(
                entity=ds,
                name="log_date",
                granularity="day",
                parse=ms.strptime("%Y%m%d"),
            )
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


def test_metric_keyword_only() -> None:
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(TypeError):
            ms.metric("additive")  # type: ignore[misc]
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext handling
# ---------------------------------------------------------------------------


def test_metric_with_ai_context() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(
            entities=[ref_factory.entity("sales.orders")],
            additivity="additive",
            ai_context=ms.ai_context(
                business_definition="Total revenue",
                guardrails=["Must be positive"],
            ),
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.ai_context.business_definition == "Total revenue"
        assert ir.ai_context.guardrails == ("Must be positive",)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext validation
# ---------------------------------------------------------------------------


def test_ai_context_with_valid_keys_works() -> None:
    """ai_context with all supported keys should work."""
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(
            entities=[ref_factory.entity("sales.orders")],
            additivity="additive",
            ai_context=ms.ai_context(
                business_definition="Revenue",
                guardrails=["Must be positive"],
            ),
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.ai_context.business_definition == "Revenue"
        assert ir.ai_context.guardrails == ("Must be positive",)
    finally:
        _exit_ctx()


@pytest.mark.parametrize(
    "key",
    ("summary", "synonyms", "examples", "instructions", "owner_notes"),
)
def test_ai_context_invalid_key_raises_type_error(key: str) -> None:
    """ms.ai_context() rejects unsupported metadata keys."""
    with pytest.raises(TypeError, match="ai_context"):
        ms.ai_context(**{key: "oops"})  # type: ignore[arg-type]


def test_ai_context_with_wrong_type_for_guardrails_raises() -> None:
    """ms.ai_context() with wrong type for guardrails raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(guardrails="not a list")  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    assert "guardrails" in str(exc_info.value)


def test_ai_context_with_wrong_type_for_business_definition_raises() -> None:
    """ms.ai_context() with wrong type for business_definition raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(business_definition=42)  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    assert "business_definition" in str(exc_info.value)


def test_ambiguous_reference_error_kind_exists() -> None:
    """ErrorKind.AMBIGUOUS_REFERENCE must exist with value 'ambiguous_reference'."""
    assert hasattr(ErrorKind, "AMBIGUOUS_REFERENCE")
    assert ErrorKind.AMBIGUOUS_REFERENCE == "ambiguous_reference"


def test_ambiguous_reference_constraint_id_exists() -> None:
    """ConstraintId.AMBIGUOUS_REFERENCE must exist with value 'ambiguous_reference'."""
    assert hasattr(ConstraintId, "AMBIGUOUS_REFERENCE")
    assert ConstraintId.AMBIGUOUS_REFERENCE == "ambiguous_reference"


def test_ai_context_with_non_string_in_list_raises() -> None:
    """ms.ai_context() with non-string items in list field raises INVALID_AI_CONTEXT."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.ai_context(guardrails=[1, 2, 3])  # type: ignore[list-item]
    assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT


def test_ai_context_empty_returns_defaults() -> None:
    """ms.ai_context() with no args returns an empty AiContextValue."""
    val = ms.ai_context()
    assert isinstance(val, ms.AiContextValue)
    assert val.business_definition is None
    assert val.guardrails == ()


def test_ai_context_raw_dict_raises_teachable_error() -> None:
    """Passing a raw dict to ai_context= raises INVALID_AI_CONTEXT with ms.ai_context() guidance."""
    _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                entities=[ref_factory.entity("sales.orders")],
                additivity="additive",
                ai_context={"business_definition": "I should use ms.ai_context()"},  # type: ignore[arg-type]
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

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
        ms.AiContextValue(business_definition=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ms\.ai_context"):
        ms.AiContextValue(guardrails=("ok", 1))  # type: ignore[arg-type]


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
        def orders_region(table):
            return table.region

        @ms.dimension(entity=portrait_ds, name="region")
        def portrait_region(table):
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
            def region(table):
                return table.region

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()


# metric unit field


def test_metric_unit_lands_on_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive", unit="CNY")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.unit == "CNY"
    finally:
        _exit_ctx()


def test_metric_unit_defaults_to_none() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:

        @ms.metric(entities=[ref_factory.entity("sales.orders")], additivity="additive")
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = _pending_objects(ctx)[-1]
        assert ir.unit is None
    finally:
        _exit_ctx()


def test_ratio_unit_lands_on_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        ms.ratio(
            name="aov",
            numerator=ref_factory.metric("sales.revenue"),
            denominator=ref_factory.metric("sales.order_count"),
            unit="1",
        )
        ir, _ = _pending_objects(ctx)[-1]
        assert ir.unit == "1"
    finally:
        _exit_ctx()


@pytest.mark.parametrize("bad", ("", "C N Y", "CNY\t", "µs"))
def test_metric_unit_rejects_whitespace_and_empty(bad: str) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                entities=[ref_factory.entity("sales.orders")], additivity="additive", unit=bad
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()


def test_date_parse_no_longer_exported() -> None:
    # ms.date() has been removed — native temporal columns don't need parse
    assert not hasattr(ms, "date")


def test_date_only_strptime_rejects_timezone() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.strptime("%Y%m%d", timezone="UTC")

    assert "timezone" in exc_info.value.message
    assert "date-only" in exc_info.value.message


@pytest.mark.parametrize("bad", ("", "C N Y"))
def test_ratio_unit_rejects_whitespace_and_empty(bad: str) -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.ratio(
                name="margin",
                numerator=ref_factory.metric("sales.revenue"),
                denominator=ref_factory.metric("sales.cost"),
                unit=bad,
            )

        assert exc_info.value.kind == "invalid_ref"
        assert _pending_objects(ctx) == []
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.cumulative() derived registration
# ---------------------------------------------------------------------------


def test_cumulative_returns_ref_and_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        event_time = ms.time_dimension_column(
            name="event_time", entity=orders, column="created_at", granularity="day"
        )
        active_users = ms.count(name="active_users", entity=orders)

        ref = ms.cumulative(name="cumulative_active_users", base=active_users, over=event_time)

        assert _is_ref(ref, ms.SemanticKind.METRIC)
        assert ref.path == "sales.cumulative_active_users"
        metric_ir = next(
            ir for ir, _ in _pending_objects(ctx) if getattr(ir, "semantic_id", None) == ref.path
        )
        assert metric_ir.metric_type == "derived"
        assert metric_ir.entities == ()
        assert metric_ir.additivity is None
        assert metric_ir.composition.kind == "cumulative"
        assert metric_ir.composition.base == "sales.active_users"
        assert metric_ir.composition.over == "sales.orders.event_time"
        assert metric_ir.composition.anchor == "all_history"
    finally:
        _exit_ctx()


def test_cumulative_allows_omitted_over_for_loader_resolution() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders")
        )
        active_users = ms.count(name="active_users", entity=orders)

        ref = ms.cumulative(name="cumulative_active_users", base=active_users)

        metric_ir = next(
            ir for ir, _ in _pending_objects(ctx) if getattr(ir, "semantic_id", None) == ref.path
        )
        assert metric_ir.composition.over is None
    finally:
        _exit_ctx()


def test_cumulative_rejects_string_refs() -> None:
    ctx = _enter_ctx(default_domain="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.cumulative(name="bad", base="sales.active_users")  # type: ignore[arg-type]

        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert "base= accepts Ref[metric]" in str(exc_info.value)
    finally:
        _exit_ctx()
