"""Tests for decimal precision/scale derivation rules and computed measure facts."""

from __future__ import annotations

from dataclasses import replace

import ibis
import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.lowering import _declared_cast
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.decimal_precision import (
    DecimalPrecision,
    DecimalType,
    add_sub,
    min_max,
    multiply,
    sum_of,
)
from marivo.semantic.errors import SemanticLoadError
from marivo.semantic.metric_graph_lowering import _derive_measure_result_type
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.shared_fixtures import load_inline_semantic


def test_decimal_type_rejects_out_of_range_bounds() -> None:
    with pytest.raises(ValueError, match="scale"):
        DecimalType(precision=2, scale=3)
    with pytest.raises(ValueError, match="precision"):
        DecimalType(precision=0, scale=0)
    with pytest.raises(ValueError, match="precision"):
        DecimalType(precision=39, scale=0)


def test_decimal_precision_parses_resolved_declared_strings() -> None:
    assert DecimalPrecision.from_string("decimal(24, 4)") == DecimalPrecision(24, 4)
    assert DecimalPrecision.from_string("decimal(45, 0)") == DecimalPrecision(45, 0)
    with pytest.raises(ValueError, match="resolved decimal"):
        DecimalPrecision.from_string("decimal")
    with pytest.raises(ValueError, match="resolved decimal"):
        DecimalPrecision.from_string("int64")


def _physical_decimal(precision: int, scale: int) -> ibis.expr.types.numeric.NumericValue:
    return ibis.table({"value": dt.Decimal(precision, scale)}, name="physical").value


def test_declared_cast_rejects_value_position_narrowing() -> None:
    # Integer positions narrow from 20 to 16: leading digits could be lost.
    with pytest.raises(DatasetCompilationError, match="value-exact rule"):
        _declared_cast(_physical_decimal(24, 4), "decimal", DecimalPrecision(20, 4))
    # Scale narrows from 2 to 0: fractional digits would be truncated.
    with pytest.raises(DatasetCompilationError, match="value-exact rule"):
        _declared_cast(_physical_decimal(12, 2), "decimal", DecimalPrecision(12, 0))


def test_declared_cast_normalizes_inside_declared_integer_positions() -> None:
    widened = _declared_cast(_physical_decimal(18, 4), "decimal", DecimalPrecision(24, 4))
    assert widened.type() == dt.Decimal(24, 4)
    trimmed = _declared_cast(_physical_decimal(20, 4), "decimal", DecimalPrecision(24, 4))
    assert trimmed.type() == dt.Decimal(24, 4)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (DecimalType(12, 2), DecimalType(4, 0), DecimalType(13, 2)),
        (DecimalType(20, 5), DecimalType(20, 3), DecimalType(23, 5)),
        (DecimalType(38, 2), DecimalType(37, 3), None),
        (DecimalType(35, 2), DecimalType(2, 2), DecimalType(36, 2)),
        (DecimalType(38, 2), DecimalType(2, 2), None),
    ],
)
def test_add_sub_rules(left: DecimalType, right: DecimalType, expected: DecimalType | None) -> None:
    assert add_sub(left, right) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (DecimalType(12, 2), DecimalType(4, 0), DecimalType(16, 2)),
        (DecimalType(20, 5), DecimalType(20, 3), DecimalType(38, 8)),
        (DecimalType(20, 5), DecimalType(19, 4), DecimalType(38, 9)),
        (DecimalType(20, 20), DecimalType(19, 19), None),
        (DecimalType(30, 0), DecimalType(9, 0), DecimalType(38, 0)),
        (DecimalType(20, 5), DecimalType(19, 3), DecimalType(38, 8)),
    ],
)
def test_multiply_rules(
    left: DecimalType, right: DecimalType, expected: DecimalType | None
) -> None:
    assert multiply(left, right) == expected


def test_sum_rule_uses_fixed_precision() -> None:
    assert sum_of(DecimalType(12, 2)) == DecimalType(38, 2)
    assert sum_of(DecimalType(3, 0)) == DecimalType(38, 0)


def test_min_max_rule_is_identity() -> None:
    assert min_max(DecimalType(12, 2)) == DecimalType(12, 2)


def test_derive_measure_result_type_walks_arithmetic_tree() -> None:
    table = ibis.table(
        {
            "amount": "decimal(12,2)",
            "qty": "int64",
            "rate": "decimal(4,0)",
            "price": "decimal(9,2)",
        },
        name="sales.orders",
    )
    expression = (table.amount * table.qty + table.rate * table.price).op()
    derived = _derive_measure_result_type(expression)
    # dec(12,2) * int64 -> dec(31,2) plus dec(4,0) * dec(9,2) -> dec(13,2);
    # add_sub(dec(31,2), dec(13,2)) -> dec(32,2).
    assert derived == "decimal(32, 2)"


def test_derive_measure_result_type_rejects_float_arithmetic_on_decimal_inputs() -> None:
    table = ibis.table({"rate": "decimal(4,0)", "weight": "float64"}, name="sales.orders")
    expression = (table.rate * table.weight).op()
    with pytest.raises(SemanticLoadError, match="cannot be derived"):
        _derive_measure_result_type(expression)


def test_derive_measure_result_type_rejects_cast_targets_over_decimal_bound() -> None:
    table = ibis.table({"amount": "decimal(12,2)", "qty": "int64"}, name="sales.orders")
    # Both shapes take one structured path: a decimal-operand cast must not
    # crash with a raw ValueError, and an integer-operand cast must not derive
    # an out-of-bound type that only fails later at admission.
    for expression in (
        table.amount.cast("decimal(45,0)").op(),
        table.qty.cast("decimal(45,0)").op(),
    ):
        with pytest.raises(SemanticLoadError, match="38-digit decimal bound"):
            _derive_measure_result_type(expression)


def test_derive_measure_result_type_keeps_resolved_cast_targets() -> None:
    table = ibis.table({"amount": "decimal(12,2)", "qty": "int64"}, name="sales.orders")
    assert _derive_measure_result_type(table.amount.cast("decimal(16,4)").op()) == "decimal(16, 4)"
    assert _derive_measure_result_type(table.qty.cast("decimal(16,4)").op()) == "decimal(16, 4)"
    assert _derive_measure_result_type(table.qty.cast("string").op()) == "string"


def test_derive_measure_result_type_rejects_reduction_and_window() -> None:
    table = ibis.table({"amount": "decimal(12,2)"}, name="sales.orders")
    with pytest.raises(SemanticLoadError, match="cross-row aggregation"):
        _derive_measure_result_type(table.amount.sum().op())
    with pytest.raises(SemanticLoadError, match="window function"):
        _derive_measure_result_type(table.amount.sum().over().op())


_COMPUTED_COLUMNS = (
    "md.table('orders', columns={"
    "'id': md.source_column('id', data_type='int64'), "
    "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
    "'qty': md.source_column('qty', data_type='int64'), "
    "'wide': md.source_column('wide', data_type='decimal(38,20)')"
    "})"
)


def _project(measure_body: str) -> str:
    return (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "import ibis\n"
        "warehouse = ms.ref.datasource('wh')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, primary_key=['id'], "
        f"source={_COMPUTED_COLUMNS})\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def net(orders):\n"
        f"    return {measure_body}\n"
        "gross = ms.aggregate(name='gross', measure=net, agg='sum')\n"
    )


def _normalize(measure_body: str) -> str:
    with load_inline_semantic(_project(measure_body), domain="sales") as result:
        assert result.status == "ready", result.errors
        assert result.registry is not None
        from marivo.semantic.metric_graph_lowering import normalize_target_metric

        contract = normalize_target_metric(
            result.registry, "sales.gross", sidecar=result.expression_sidecar
        )
        return contract.logical_type


def test_computed_measure_normalizes_to_generic_decimal() -> None:
    # dec(12,2) * int64 promotes the integer operand and derives by rule.
    assert _normalize("orders.amount * orders.qty") == "decimal"


def test_computed_integer_measure_keeps_ibis_inferred_type() -> None:
    int_columns = (
        "md.table('orders', columns={"
        "'id': md.source_column('id', data_type='int64'), "
        "'amount': md.source_column('amount', data_type='int64'), "
        "'qty': md.source_column('qty', data_type='int32')"
        "})"
    )
    source = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('wh')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, primary_key=['id'], "
        f"source={int_columns})\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def net(orders):\n"
        "    return orders.amount * orders.qty\n"
        "gross = ms.aggregate(name='gross', measure=net, agg='sum')\n"
    )
    with load_inline_semantic(source, domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        from marivo.semantic.metric_graph_lowering import normalize_target_metric

        contract = normalize_target_metric(
            result.registry, "sales.gross", sidecar=result.expression_sidecar
        )
        assert contract.logical_type == "int64"


def test_computed_measure_loads_into_lazy_sources() -> None:
    with load_inline_semantic(_project("orders.amount * orders.qty"), domain="sales") as result:
        assert result.registry is not None and result.expression_sidecar is not None
        sources = make_lazy_sources(
            semantic_registry=result.registry,
            sidecar=result.expression_sidecar,
            action_port=NoIoActionPort(),
            session_id="c4-computed-measure",
            store_id="c4-computed-measure",
        )
        dataset = sources.observe(ref.metric("sales.gross"))
        assert dataset.fields.get("gross") is not None


def test_computed_measure_with_reduction_is_rejected_at_normalization() -> None:
    from marivo.semantic.metric_graph_lowering import normalize_target_metric

    with load_inline_semantic(_project("orders.amount.sum()"), domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        with pytest.raises(SemanticLoadError, match="cross-row aggregation"):
            normalize_target_metric(
                result.registry, "sales.gross", sidecar=result.expression_sidecar
            )


def test_computed_measure_with_window_is_rejected_at_normalization() -> None:
    from marivo.semantic.metric_graph_lowering import normalize_target_metric

    with load_inline_semantic(_project("orders.amount.sum().over()"), domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        with pytest.raises(SemanticLoadError, match="window function"):
            normalize_target_metric(
                result.registry, "sales.gross", sidecar=result.expression_sidecar
            )


def test_computed_measure_with_foreign_table_is_rejected_at_normalization() -> None:
    from marivo.semantic.metric_graph_lowering import normalize_target_metric

    with load_inline_semantic(
        _project("ibis.table({'x': 'decimal(12,2)'}, name='customers').x + orders.amount"),
        domain="sales",
    ) as result:
        assert result.status == "ready" and result.registry is not None
        with pytest.raises(SemanticLoadError, match="other ibis tables"):
            normalize_target_metric(
                result.registry, "sales.gross", sidecar=result.expression_sidecar
            )


def test_computed_measure_with_undeclared_column_is_rejected() -> None:
    from marivo.semantic.metric_graph_lowering import normalize_target_metric

    source = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('wh')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, "
        "source=md.table('orders', columns={"
        "'amount': md.source_column('amount', data_type='decimal(12,2)')}))\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def net(orders):\n"
        "    return orders.amount * orders.missing_qty\n"
        "gross = ms.aggregate(name='gross', measure=net, agg='sum')\n"
    )
    with load_inline_semantic(source, domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        with pytest.raises(SemanticLoadError, match="undeclared column"):
            normalize_target_metric(
                result.registry, "sales.gross", sidecar=result.expression_sidecar
            )


def test_computed_measure_with_derivation_overflow_is_rejected() -> None:
    # dec(38,20) * dec(38,20): scale 40 > 38 rejects instead of truncating.
    with pytest.raises(SemanticLoadError, match="exceeds 38 digits"):
        _normalize("orders.wide * orders.wide")


def test_computed_measure_with_unresolved_precision_is_rejected() -> None:
    # A bare "decimal" declared column carries no precision and scale, so the
    # multiply cannot derive by rule; it must fail with the structured error,
    # never with a raw TypeError from DecimalType construction.
    from marivo.semantic.metric_graph_lowering import normalize_target_metric

    with load_inline_semantic(_project("orders.amount * orders.qty"), domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        entities = dict(result.registry.entities)
        orders = entities["sales.orders"]
        assert orders.source is not None and orders.source.columns is not None
        entities["sales.orders"] = replace(
            orders,
            source=replace(
                orders.source,
                columns=tuple(
                    (name, replace(binding, data_type="decimal") if name == "amount" else binding)
                    for name, binding in orders.source.columns
                ),
            ),
        )
        registry = replace(result.registry, entities=entities)
        registry.freeze()
        with pytest.raises(
            SemanticLoadError,
            match="resolved precision and scale",
        ):
            normalize_target_metric(registry, "sales.gross", sidecar=result.expression_sidecar)


def test_linear_metric_over_int64_measure_keeps_int64_type() -> None:
    source = (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('wh')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders', "
        "columns={'amount': md.source_column('amount', data_type='int64')}))\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
        "additivity='additive')\n"
        "gross = ms.aggregate(name='gross', measure=amount, agg='sum')\n"
        "counted = ms.aggregate(name='counted', measure=amount, agg='count')\n"
        "net = ms.linear(name='net', add=[gross], subtract=[counted])\n"
    )
    with load_inline_semantic(source, domain="sales") as result:
        assert result.status == "ready" and result.registry is not None
        from marivo.semantic.metric_graph_lowering import normalize_target_metric

        contract = normalize_target_metric(
            result.registry, "sales.net", sidecar=result.expression_sidecar
        )
        assert contract.logical_type == "int64"
