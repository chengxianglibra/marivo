"""Tests for marivo.semantic_py.authoring — decorator and builder implementation.

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
- ms.component() sentinel system
- ms.component() outside derived metric body -> OutsideDerivedMetricBodyError
- ms.component("unknown") -> InvalidComponentNameError
- Derived metric form classification: empty datasets + ratio/weighted_average decomposition = derived
"""

from __future__ import annotations

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.authoring import (
    _ACTIVE_DECOMPOSITION,
    _BinOpSentinel,
    _ComponentSentinel,
    _UnaryNegSentinel,
)
from marivo.semantic_py.errors import ErrorKind, SemanticDecoratorError
from marivo.semantic_py.ir import (
    DatasetRef,
    FieldRef,
    MetricRef,
    RelationshipRef,
    TimeFieldRef,
)
from marivo.semantic_py.loader import _LOADER_CTX, LoaderContext

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

        @ms.dataset(datasource="wh")
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

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

        @ms.dataset(datasource="wh")
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(orders, DatasetRef)
        assert orders.semantic_id == "sales.orders"
    finally:
        _exit_ctx()


def test_dataset_name_defaults_to_function_name() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.dataset(datasource="wh")
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

        assert orders.semantic_id == "sales.orders"
    finally:
        _exit_ctx()


def test_dataset_explicit_name() -> None:
    _enter_ctx(default_model="sales")
    try:

        @ms.dataset(name="orders_tbl", datasource="wh")
        def _orders_impl(backend: object) -> object:
            return None  # type: ignore[unreachable]

        assert isinstance(_orders_impl, DatasetRef)
        assert _orders_impl.semantic_id == "sales.orders_tbl"
    finally:
        _exit_ctx()


def test_dataset_pushes_ir_and_callable() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        def orders_fn(backend: object) -> object:
            return None  # type: ignore[unreachable]

        ref = ms.dataset(datasource="wh")(orders_fn)
        # Should be the second pending object (after datasource)
        ir, callable_ = ctx.pending_objects[-1]
        assert ir.semantic_id == "sales.orders_fn"
        assert ir.model == "sales"
        assert ir.name == "orders_fn"
        assert ir.datasource == "wh"
        assert callable_ is orders_fn
    finally:
        _exit_ctx()


def test_dataset_datasource_as_string() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.dataset(datasource="wh")
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.datasource == "wh"
    finally:
        _exit_ctx()


def test_dataset_primary_key() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.dataset(datasource="wh", primary_key=["order_id"])
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.primary_key == ("order_id",)
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
        assert amount.semantic_id == "sales.amount"
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
        assert ir.semantic_id == "sales.order_amount"
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
        assert order_date.semantic_id == "sales.order_date"
    finally:
        _exit_ctx()


def test_time_field_ir_has_time_metadata() -> None:
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.time_field(
            dataset="sales.orders",
            data_type="timestamp",
            granularity="hour",
            required_prefix="sales.order_date",
        )
        def order_hour(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        assert ir.is_time_field is True
        assert ir.data_type == "timestamp"
        assert ir.granularity == "hour"
        assert ir.required_prefix == "sales.order_date"
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
            source_sql="SELECT SUM(amount) FROM orders",
            source_dialect="ansi",
            source_document="docs/revenue.md",
            source_notes="Excludes refunds",
            declared_status="python_native",
        )
        def revenue(table: object) -> object:
            return None  # type: ignore[unreachable]

        ir, _ = ctx.pending_objects[-1]
        prov = ir.provenance
        assert prov.source_sql == "SELECT SUM(amount) FROM orders"
        assert prov.source_dialect == "ansi"
        assert prov.source_document == "docs/revenue.md"
        assert prov.source_notes == "Excludes refunds"
        assert prov.declared_status == "python_native"
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
# ms.component() sentinel system
# ---------------------------------------------------------------------------


def test_component_outside_derived_metric_body_raises() -> None:
    """ms.component() outside derived metric body should raise OutsideDerivedMetricBodyError."""
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.component("numerator")
    assert exc_info.value.kind == ErrorKind.OUTSIDE_DERIVED_METRIC_BODY


def test_component_returns_sentinel_inside_derived() -> None:
    """ms.component() inside a derived metric body returns _ComponentSentinel."""
    ctx = _enter_ctx(default_model="sales")
    try:
        from marivo.semantic_py.ir import DecompositionIR

        decomp = DecompositionIR(
            kind="ratio",
            components={"numerator": "sales.revenue", "denominator": "sales.cost"},
        )
        token = _ACTIVE_DECOMPOSITION.set(decomp)
        try:
            result = ms.component("numerator")
            assert isinstance(result, _ComponentSentinel)
            assert result.name == "numerator"
        finally:
            _ACTIVE_DECOMPOSITION.reset(token)
    finally:
        _exit_ctx()


def test_component_arithmetic_returns_binop() -> None:
    """Arithmetic on _ComponentSentinel returns _BinOpSentinel."""
    from marivo.semantic_py.ir import DecompositionIR

    decomp = DecompositionIR(
        kind="ratio",
        components={"numerator": "sales.revenue", "denominator": "sales.cost"},
    )
    token = _ACTIVE_DECOMPOSITION.set(decomp)
    try:
        num = ms.component("numerator")
        den = ms.component("denominator")

        # Division
        result = num / den
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "/"
        assert isinstance(result.left, _ComponentSentinel)
        assert isinstance(result.right, _ComponentSentinel)

        # Addition
        result = num + den
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "+"

        # Subtraction
        result = num - den
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "-"

        # Multiplication
        result = num * den
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "*"
    finally:
        _ACTIVE_DECOMPOSITION.reset(token)


def test_component_arithmetic_with_numeric_literal() -> None:
    """Arithmetic with numeric literals should produce _BinOpSentinel."""
    from marivo.semantic_py.ir import DecompositionIR

    decomp = DecompositionIR(
        kind="ratio",
        components={"a": "sales.metric_a"},
    )
    token = _ACTIVE_DECOMPOSITION.set(decomp)
    try:
        a = ms.component("a")

        # Component * 2
        result = a * 2
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "*"
        assert isinstance(result.left, _ComponentSentinel)
        assert result.right == 2

        # 2 * Component (reverse op)
        result = 2 * a
        assert isinstance(result, _BinOpSentinel)
        assert result.op == "*"
        assert result.left == 2
        assert isinstance(result.right, _ComponentSentinel)
    finally:
        _ACTIVE_DECOMPOSITION.reset(token)


def test_component_negation_returns_unary_neg() -> None:
    """Unary negation on _ComponentSentinel returns _UnaryNegSentinel."""
    from marivo.semantic_py.ir import DecompositionIR

    decomp = DecompositionIR(
        kind="sum",
        components={"x": "sales.metric_x"},
    )
    token = _ACTIVE_DECOMPOSITION.set(decomp)
    try:
        x = ms.component("x")
        result = -x
        assert isinstance(result, _UnaryNegSentinel)
        assert isinstance(result.operand, _ComponentSentinel)
    finally:
        _ACTIVE_DECOMPOSITION.reset(token)


def test_component_invalid_name_raises() -> None:
    """ms.component() with name not in decomposition should raise InvalidComponentNameError."""
    from marivo.semantic_py.ir import DecompositionIR

    decomp = DecompositionIR(
        kind="ratio",
        components={"numerator": "sales.revenue", "denominator": "sales.cost"},
    )
    token = _ACTIVE_DECOMPOSITION.set(decomp)
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.component("unknown")
        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_NAME
    finally:
        _ACTIVE_DECOMPOSITION.reset(token)


def test_component_empty_name_raises() -> None:
    """ms.component('') with empty name should raise INVALID_COMPONENT_BODY."""
    from marivo.semantic_py.ir import DecompositionIR

    decomp = DecompositionIR(
        kind="ratio",
        components={"numerator": "sales.revenue"},
    )
    token = _ACTIVE_DECOMPOSITION.set(decomp)
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:
            ms.component("")
        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    finally:
        _ACTIVE_DECOMPOSITION.reset(token)


def test_derived_metric_form_classification() -> None:
    """Derived metric: empty datasets + ratio/weighted_average decomposition = derived."""
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=[],
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.cost",
            ),
        )
        def margin():
            return ms.component("numerator") / ms.component("denominator")

        ir, _ = ctx.pending_objects[-1]
        assert ir.is_derived is True
        assert ir.datasets == ()
    finally:
        _exit_ctx()


def test_metric_rejects_empty_datasets_without_components() -> None:
    """Empty datasets only make sense for derived metrics with components."""
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.metric(datasets=[], decomposition=ms.sum())
            def orphan_metric():
                return 1

        assert exc_info.value.kind == ErrorKind.INVALID_COMPONENT_BODY
    finally:
        _exit_ctx()


def test_base_metric_rejects_component_body_at_definition_time() -> None:
    """ms.component() cannot be hidden inside a dataset-backed metric."""
    ctx = _enter_ctx(default_model="sales")
    try:
        with pytest.raises(SemanticDecoratorError) as exc_info:

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


def test_derived_metric_sidecar_stores_sentinel_tree() -> None:
    """Derived metric stores sentinel tree (not raw callable) in sidecar."""
    ctx = _enter_ctx(default_model="sales")
    try:

        @ms.metric(
            datasets=[],
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.cost",
            ),
        )
        def margin():
            return ms.component("numerator") / ms.component("denominator")

        _, sidecar_entry = ctx.pending_objects[-1]
        # sidecar_entry should be a _BinOpSentinel, not a callable
        assert isinstance(sidecar_entry, _BinOpSentinel)
        assert sidecar_entry.op == "/"
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

        @ms.dataset(datasource="wh")
        def orders(backend: object) -> object:
            return None  # type: ignore[unreachable]

        with pytest.raises(SemanticDecoratorError) as exc_info:

            @ms.dataset(datasource="wh")
            def orders(backend: object) -> object:  # type: ignore[misc]
                return None  # type: ignore[unreachable]

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
