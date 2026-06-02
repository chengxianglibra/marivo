"""Tests for marivo.semantic.authoring — decorator and builder implementation.

Tests cover:
- Outside-loader-context guard
- Model name resolution (explicit > default_model > MissingModelError)
- All decorator signatures (keyword-only enforcement)
- name defaults to function __name__
- Ref types returned by decorators
- DecompositionBuilder from ms.sum(), ms.ratio(), ms.weighted_average()
- Duplicate name detection
- Provenance fields on metric
- ms.ref() builder
- ms.derived_metric() body-free registration
- Derived metric form validation: ratio/weighted_average only
- Derived additivity validation: only non_additive or None
"""

from __future__ import annotations

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.authoring import (
    DecompositionBuilder,
    _compute_decomposition_ast_hash,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, SemanticLoadError
from marivo.semantic.ir import (
    AiContextIR,
    DatasetRef,
    FieldRef,
    MetricRef,
    RelationshipRef,
    TimeFieldRef,
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
        ms.model(name="sales")
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_dataset_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.field(dataset="orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_time_field_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.time_field(dataset="orders", data_type="date", granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_metric_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:

        @ms.metric(decomposition=ms.sum())
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


def test_relationship_outside_context_raises() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.relationship(
            from_dataset="orders",
            to_dataset="items",
            from_fields=["id"],
            to_fields=["order_id"],
        )
    assert exc_info.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT


# ---------------------------------------------------------------------------
# ms.model() call
# ---------------------------------------------------------------------------


def test_model_creates_model_ir() -> None:
    ctx = _enter_ctx()
    try:
        ms.model(name="sales", default=True, description="Sales model")
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


def test_model_sets_default_model_on_context() -> None:
    ctx = _enter_ctx()
    try:
        assert ctx.default_model is None
        ms.model(name="sales", default=True)
        assert ctx.default_model == "sales"
    finally:
        _exit_ctx()


def test_model_default_false_does_not_set_context() -> None:
    ctx = _enter_ctx(default_model="existing")
    try:
        ms.model(name="other", default=False)
        assert ctx.default_model == "existing"
    finally:
        _exit_ctx()


def test_model_requires_keyword_args() -> None:
    _enter_ctx()
    try:
        with pytest.raises(TypeError):
            ms.model("sales")  # type: ignore[misc]
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ms.dataset() decorator
# ---------------------------------------------------------------------------


def test_dataset_returns_ref() -> None:
    _enter_ctx(default_model="sales")
    try:
        orders = ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

        assert isinstance(orders, DatasetRef)
        assert orders.semantic_id == "sales.orders"
    finally:
        _exit_ctx()


def test_dataset_requires_name_without_body() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(TypeError):
            ms.dataset(datasource="wh", source=ms.table("orders"))  # type: ignore[call-arg]
    finally:
        _exit_ctx()


def test_dataset_explicit_name() -> None:
    _enter_ctx(default_model="sales")
    try:
        _orders_impl = ms.dataset(
            name="orders_tbl",
            datasource="wh",
            source=ms.table("orders"),
        )

        assert isinstance(_orders_impl, DatasetRef)
        assert _orders_impl.semantic_id == "sales.orders_tbl"
    finally:
        _exit_ctx()


def test_dataset_pushes_ir_without_callable() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ref = ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))
        ir, callable_ = ctx.pending_objects[-1]
        assert ref.semantic_id == "sales.orders"
        assert ir.semantic_id == "sales.orders"
        assert ir.model == "sales"
        assert ir.name == "orders"
        assert ir.datasource == "wh"
        assert ir.source == ms.table("orders")
        assert callable_ is None
    finally:
        _exit_ctx()


def test_dataset_datasource_as_string() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_datasource_as_datasource_ref() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        warehouse = md.ref("wh")
        ms.dataset(name="orders", datasource=warehouse, source=ms.table("orders"))

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_primary_key() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.dataset(
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
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.dataset(
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
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.dataset(
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


def test_dataset_decorator_body_is_rejected() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(TypeError):

            @ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))
            def orders(backend: object) -> object:
                return backend
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.field() decorator
# ---------------------------------------------------------------------------


def test_field_returns_ref() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.field(dataset="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(amount, FieldRef)
        assert amount.semantic_id == "sales.orders.amount"
    finally:
        _exit_ctx()


def test_field_name_defaults_to_function_name() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.field(dataset="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.name == "amount"
        assert ir.is_time_field is False
        assert ir.data_type is None
        assert ir.granularity is None
    finally:
        _exit_ctx()


def test_field_explicit_name() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.field(name="order_amount", dataset="sales.orders")
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.name == "order_amount"
        assert ir.semantic_id == "sales.orders.order_amount"
    finally:
        _exit_ctx()


def test_field_with_dataset_ref() -> None:
    _enter_ctx(default_model="sales")
    try:
        ds_ref = DatasetRef("sales.orders")

        @ms.field(dataset=ds_ref)
        def amount(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(amount, FieldRef)
    finally:
        _exit_ctx()


def test_field_pushes_callable() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        def amount_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.field(dataset="sales.orders")(amount_fn)
        ir, callable_ = ctx.pending_objects[-1]
        assert callable_ is amount_fn
        assert ir.dataset == "sales.orders"
    finally:
        _exit_ctx()


def test_field_body_rejects_lambda() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.field(dataset="sales.orders")
            def amount(table: object) -> object:
                fn = lambda value: value
                return fn(table)

        assert exc_info.value.kind == ErrorKind.METRIC_BODY_NOT_SINGLE_RETURN
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.time_field() decorator
# ---------------------------------------------------------------------------


def test_time_field_returns_ref() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.time_field(dataset="sales.orders", data_type="date", granularity="day")
        def order_date(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(order_date, TimeFieldRef)
        assert order_date.semantic_id == "sales.orders.order_date"
    finally:
        _exit_ctx()


def test_time_field_ir_has_time_metadata() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.time_field(
            dataset="sales.orders",
            data_type="timestamp",
            granularity="hour",
            required_prefix="order_date",
        )
        def order_hour(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.is_time_field is True
        assert ir.data_type == "timestamp"
        assert ir.granularity == "hour"
        assert ir.required_prefix == "order_date"
    finally:
        _exit_ctx()


def test_time_field_requires_data_type_and_granularity() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(TypeError):

            @ms.time_field(dataset="sales.orders")  # type: ignore[call-arg]
            def order_date(table: object) -> object:
                return None  # type: ignore[unreachable]
    finally:
        _exit_ctx()


def test_time_field_body_rejects_sql_escape_hatch() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.time_field(dataset="sales.orders", data_type="date", granularity="day")
            def order_date(backend: object) -> object:
                return backend.sql("select current_date")

        assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH
        assert exc_info.value.constraint_id == "ast_sql_escape_hatch"
    finally:
        _exit_ctx()


def test_time_field_accepts_timezone_metadata() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.time_field(
            dataset="sales.orders",
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
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_field(
                dataset="sales.orders",
                data_type="timestamp",
                granularity="hour",
                timezone="Mars/Olympus",
            )
            def created_at(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert "timezone" in exc_info.value.message
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.metric() decorator
# ---------------------------------------------------------------------------


def test_metric_returns_ref() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(revenue, MetricRef)
        assert revenue.semantic_id == "sales.revenue"
    finally:
        _exit_ctx()


def test_metric_base_with_datasets() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.is_derived is False
        assert ir.datasets == ("sales.orders",)
        assert ir.decomposition.kind == "sum"
    finally:
        _exit_ctx()


def test_metric_with_dataset_ref() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        orders_ref = DatasetRef("sales.orders")

        @ms.metric(datasets=[orders_ref], decomposition=ms.sum())
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasets == ("sales.orders",)
    finally:
        _exit_ctx()


def test_metric_provenance_fields() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=["sales.orders"],
            decomposition=ms.sum(),
            verification_mode="sql_parity",
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="ansi",
            source_document="docs/revenue.md",
            source_notes="Excludes refunds",
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        prov = ir.provenance
        assert prov.source_sql == "SELECT SUM(amount) FROM orders"
        assert prov.source_dialect == "ansi"
        assert prov.source_document == "docs/revenue.md"
        assert prov.source_notes == "Excludes refunds"
        assert prov.verification_mode == "sql_parity"
    finally:
        _exit_ctx()


def test_metric_body_ast_hash() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        # body_ast_hash should be a non-empty string
        assert isinstance(ir.body_ast_hash, str)
        assert len(ir.body_ast_hash) > 0
    finally:
        _exit_ctx()


def test_metric_decomposition_ratio() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=["sales.orders"],
            decomposition=ms.ratio(
                numerator=ms.ref("sales.gross_revenue"),
                denominator=ms.ref("sales.total_revenue"),
            ),
        )
        def margin(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.decomposition.kind == "ratio"
        assert "numerator" in ir.decomposition.components
        assert "denominator" in ir.decomposition.components
    finally:
        _exit_ctx()


def test_metric_decomposition_weighted_average() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=["sales.orders"],
            decomposition=ms.weighted_average(
                value=ms.ref("sales.revenue"),
                weight=ms.ref("sales.count"),
            ),
        )
        def aov(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.decomposition.kind == "weighted_average"
        assert "numerator" in ir.decomposition.components
        assert "weight" in ir.decomposition.components
    finally:
        _exit_ctx()


def test_metric_pushes_callable() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.metric(datasets=["sales.orders"], decomposition=ms.sum())(revenue_fn)
        ir, callable_ = ctx.pending_objects[-1]
        assert callable_ is revenue_fn
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# ms.relationship() call
# ---------------------------------------------------------------------------


def test_relationship_returns_ref() -> None:
    _enter_ctx(default_model="sales")
    try:
        rel = ms.relationship(
            name="orders_to_items",
            from_dataset="sales.orders",
            to_dataset="sales.items",
            from_fields=["sales.orders.id"],
            to_fields=["sales.items.order_id"],
        )
        assert isinstance(rel, RelationshipRef)
        assert rel.semantic_id == "sales.orders_to_items"
    finally:
        _exit_ctx()


def test_relationship_pushes_ir() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.relationship(
            name="orders_to_items",
            from_dataset="sales.orders",
            to_dataset="sales.items",
            from_fields=["sales.orders.id"],
            to_fields=["sales.items.order_id"],
        )
        ir, callable_ = ctx.pending_objects[-1]
        assert ir.name == "orders_to_items"
        assert ir.from_dataset == "sales.orders"
        assert ir.to_dataset == "sales.items"
        assert ir.from_fields == ("sales.orders.id",)
        assert ir.to_fields == ("sales.items.order_id",)
        assert callable_ is None
    finally:
        _exit_ctx()


def test_relationship_with_ref_objects() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        orders_ref = DatasetRef("sales.orders")
        items_ref = DatasetRef("sales.items")
        id_ref = FieldRef("sales.orders.id")
        oid_ref = FieldRef("sales.items.order_id")

        ms.relationship(
            name="orders_to_items",
            from_dataset=orders_ref,
            to_dataset=items_ref,
            from_fields=[id_ref],
            to_fields=[oid_ref],
        )
        ir, _ = ctx.pending_objects[-1]
        assert ir.from_dataset == "sales.orders"
        assert ir.to_dataset == "sales.items"
        assert ir.from_fields == ("sales.orders.id",)
        assert ir.to_fields == ("sales.items.order_id",)
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# DecompositionBuilder
# ---------------------------------------------------------------------------


def test_sum_builder() -> None:
    builder = ms.sum()
    assert builder.kind == "sum"
    assert builder.components == {}


def test_ratio_builder() -> None:
    builder = ms.ratio(
        numerator=ms.ref("sales.gross"),
        denominator=ms.ref("sales.total"),
    )
    assert builder.kind == "ratio"
    assert "numerator" in builder.components
    assert "denominator" in builder.components


def test_weighted_average_builder() -> None:
    builder = ms.weighted_average(
        value=ms.ref("sales.revenue"),
        weight=ms.ref("sales.count"),
    )
    assert builder.kind == "weighted_average"
    assert "numerator" in builder.components
    assert "weight" in builder.components


def test_decomposition_builder_is_frozen() -> None:
    import dataclasses

    builder = ms.sum()
    assert dataclasses.is_dataclass(builder)
    assert getattr(builder, "__dataclass_params__", None) is not None
    assert builder.__dataclass_params__.frozen  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ms.ref()
# ---------------------------------------------------------------------------


def test_ref_returns_string() -> None:
    result = ms.ref("sales.revenue")
    assert isinstance(result, str)
    assert result == "sales.revenue"


# ---------------------------------------------------------------------------
# ms.derived_metric() direct registration
# ---------------------------------------------------------------------------


def test_derived_metric_returns_ref_and_pushes_body_free_ir() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ref = ms.derived_metric(
            name="margin",
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.cost",
            ),
            additivity="non_additive",
            source_document="metric-catalog.md",
            ai_context={"business_definition": "Revenue divided by cost."},
        )

        assert isinstance(ref, MetricRef)
        assert ref.semantic_id == "sales.margin"
        ir, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is None
        assert ir.semantic_id == "sales.margin"
        assert ir.is_derived is True
        assert ir.datasets == ()
        assert ir.python_symbol == "margin"
        assert ir.additivity == "non_additive"
        assert ir.decomposition.kind == "ratio"
        assert ir.decomposition.components == {
            "numerator": "sales.revenue",
            "denominator": "sales.cost",
        }
        assert ir.provenance.verification_mode is None
        assert ir.provenance.source_document == "metric-catalog.md"
        assert ir.body_ast_hash == _compute_decomposition_ast_hash(
            ms.ratio(
                numerator="sales.revenue",
                denominator="sales.cost",
            )
        )
    finally:
        _exit_ctx()


def test_derived_metric_decomposition_hash_is_component_order_stable() -> None:
    forward = DecompositionBuilder(
        kind="ratio",
        components={
            "numerator": "sales.revenue",
            "denominator": "sales.orders",
        },
    )
    reversed_order = DecompositionBuilder(
        kind="ratio",
        components={
            "denominator": "sales.orders",
            "numerator": "sales.revenue",
        },
    )

    assert _compute_decomposition_ast_hash(forward) == _compute_decomposition_ast_hash(
        reversed_order
    )


def test_derived_metric_weighted_average_keeps_numerator_weight_keys() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        ms.derived_metric(
            name="aov",
            decomposition=ms.weighted_average(
                value="sales.revenue",
                weight="sales.order_count",
            ),
        )

        ir, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is None
        assert ir.is_derived is True
        assert ir.additivity is None
        assert ir.decomposition.kind == "weighted_average"
        assert ir.decomposition.components == {
            "numerator": "sales.revenue",
            "weight": "sales.order_count",
        }
    finally:
        _exit_ctx()


def test_derived_metric_rejects_sum_decomposition() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.derived_metric(name="orphan", decomposition=ms.sum())

        assert exc_info.value.kind == ErrorKind.INVALID_DECOMPOSITION
        assert exc_info.value.constraint_id == "decomposition_shape"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()


@pytest.mark.parametrize("additivity", ["additive", "semi_additive"])
def test_derived_metric_rejects_additive_additivity(additivity: str) -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.derived_metric(
                name="margin",
                decomposition=ms.ratio(
                    numerator="sales.revenue",
                    denominator="sales.cost",
                ),
                additivity=additivity,  # type: ignore[arg-type]
            )

        assert exc_info.value.kind == ErrorKind.INVALID_DECOMPOSITION
        assert exc_info.value.constraint_id == "decomposition_shape"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()


def test_metric_rejects_empty_datasets_after_derived_split() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                datasets=[],
                decomposition=ms.ratio(
                    numerator="sales.revenue",
                    denominator="sales.cost",
                ),
            )
            def margin() -> object:
                return 1

        assert exc_info.value.kind == ErrorKind.MISSING_DATASETS
        assert exc_info.value.constraint_id == "metric_datasets_required"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()


def test_metric_rejects_empty_datasets_with_sum_after_derived_split() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(datasets=[], decomposition=ms.sum())
            def orphan_metric() -> object:
                return 1

        assert exc_info.value.kind == ErrorKind.MISSING_DATASETS
        assert exc_info.value.constraint_id == "metric_datasets_required"
        assert ctx.pending_objects == []
    finally:
        _exit_ctx()


def test_base_metric_rejects_component_body_at_definition_time() -> None:
    """ms.component() cannot be hidden inside a dataset-backed metric."""
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticLoadError) as exc_info:

            @ms.metric(
                datasets=["sales.orders"],
                decomposition=ms.ratio(
                    numerator="sales.failed_count",
                    denominator="sales.total_count",
                ),
            )
            def failure_rate(orders):
                return ms.component("numerator") / ms.component("denominator")

        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    finally:
        _exit_ctx()


def test_base_metric_sidecar_stores_callable() -> None:
    """Base metric stores the raw callable in sidecar."""
    ctx = _enter_ctx(default_model="sales")
    try:

        def revenue_fn(table: object) -> object:
            return None  # type: ignore[unreachable]

        ms.metric(datasets=["sales.orders"], decomposition=ms.sum())(revenue_fn)
        _, sidecar_entry = ctx.pending_objects[-1]
        assert sidecar_entry is revenue_fn
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# Duplicate name detection
# ---------------------------------------------------------------------------


def test_duplicate_dataset_name_raises() -> None:
    _enter_ctx(default_model="sales")
    try:
        ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


def test_duplicate_metric_name_raises() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(backend: object) -> object:
            return None  # type: ignore[unreachable]

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
            def revenue(backend: object) -> object:  # type: ignore[misc]
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.DUPLICATE_NAME
    finally:
        _exit_ctx()


def test_dataset_and_metric_same_name_no_collision() -> None:
    """A dataset and a metric with the same model.name should coexist — kind-scoped uniqueness."""
    ctx = _enter_ctx(default_model="sales")
    try:
        ds = ms.dataset(
            name="dau_7d_portrait",
            datasource="warehouse",
            source=ms.table("dau_7d_portrait"),
        )
        assert ds.semantic_id == "sales.dau_7d_portrait"

        @ms.metric(
            datasets=[ds],
            additivity="additive",
            decomposition=ms.sum(),
            name="dau_7d_portrait",
        )
        def dau_7d_portrait(table):
            return table.dau.sum()

        assert dau_7d_portrait.semantic_id == "sales.dau_7d_portrait"
    finally:
        _exit_ctx()


def test_field_and_time_field_same_name_same_dataset_collides() -> None:
    """A field and a time_field with the same name on the same dataset share the fields namespace."""
    ctx = _enter_ctx(default_model="sales")
    try:
        ds = ms.dataset(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )

        @ms.field(dataset=ds, name="log_date")
        def log_date_field(table):
            return table.log_date

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.time_field(dataset=ds, name="log_date", data_type="string", granularity="day")
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
            ms.model("sales")  # type: ignore[misc]
    finally:
        _exit_ctx()


def test_dataset_keyword_only() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(TypeError):
            ms.dataset("wh")  # type: ignore[misc]
    finally:
        _exit_ctx()


def test_metric_keyword_only() -> None:
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(TypeError):
            ms.metric(ms.sum())  # type: ignore[misc]
    finally:
        _exit_ctx()


# ---------------------------------------------------------------------------
# AiContext handling
# ---------------------------------------------------------------------------


def test_metric_with_ai_context() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=["sales.orders"],
            decomposition=ms.sum(),
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
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=["sales.orders"],
            decomposition=ms.sum(),
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
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                datasets=["sales.orders"],
                decomposition=ms.sum(),
                ai_context={"invalid_key": "oops"},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


def test_ai_context_with_wrong_type_for_guardrails_raises() -> None:
    """ai_context with wrong type for guardrails should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                datasets=["sales.orders"],
                decomposition=ms.sum(),
                ai_context={"guardrails": "not a list"},
            )
            def revenue(table: object) -> object:
                return None  # type: ignore[unreachable]

        assert exc_info.value.kind == ErrorKind.INVALID_AI_CONTEXT
    finally:
        _exit_ctx()


def test_ai_context_with_wrong_type_for_business_definition_raises() -> None:
    """ai_context with wrong type for business_definition should raise INVALID_AI_CONTEXT."""
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                datasets=["sales.orders"],
                decomposition=ms.sum(),
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
    _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(
                datasets=["sales.orders"],
                decomposition=ms.sum(),
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
    ctx = _enter_ctx(default_model="sales")
    try:
        orders_ds = ms.dataset(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )
        portrait_ds = ms.dataset(
            name="portrait",
            datasource="warehouse",
            source=ms.table("portrait"),
        )

        @ms.field(dataset=orders_ds, name="region")
        def orders_region(table):
            return table.region

        @ms.field(dataset=portrait_ds, name="region")
        def portrait_region(table):
            return table.region

        assert orders_region.semantic_id == "sales.orders.region"
        assert portrait_region.semantic_id == "sales.portrait.region"
    finally:
        _exit_ctx()


def test_field_model_mismatch_with_dataset_raises() -> None:
    """A field whose model_name disagrees with the dataset's model must raise."""
    ctx = _enter_ctx(default_model="sales")
    try:
        ds = ms.dataset(
            name="orders",
            datasource="warehouse",
            source=ms.table("orders"),
        )

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.field(dataset=ds, name="region", model_name="inventory")
            def region(table):
                return table.region

        assert exc_info.value.kind == "invalid_ref"
    finally:
        _exit_ctx()
