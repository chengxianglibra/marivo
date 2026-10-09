"""Metric additivity resolution and composition validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest

from marivo.refs import ref as ref_factory
from marivo.semantic import authoring, ir, loader
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError
from marivo.semantic.validator import Registry, assembly_validate
from tests.shared_fixtures import load_inline_semantic

# ---------------------------------------------------------------------------
# IR — shared helpers
# ---------------------------------------------------------------------------


def test_additivity_bucket_maps_all_three() -> None:
    assert ir.additivity_bucket("additive") == "additive"
    assert ir.additivity_bucket("non_additive") == "non_additive"
    sa = ir.SemiAdditive(over="d.e.t", fold=ir.TimeFoldIR(kind="last"))
    assert ir.additivity_bucket(sa) == "semi_additive"


def test_composition_components_per_kind() -> None:
    assert ir.composition_components(ir.RatioComposition(numerator="d.a", denominator="d.b")) == {
        "numerator": "d.a",
        "denominator": "d.b",
    }
    lin = ir.LinearComposition(terms=(ir.LinearTerm("+", "d.a"), ir.LinearTerm("-", "d.b")))
    assert ir.composition_components(lin) == {"term0": "d.a", "term1": "d.b"}


# ---------------------------------------------------------------------------
# errors + constraints — composition rename + measure kinds
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# loader — _resolve_metric_additivity resolution pass
# ---------------------------------------------------------------------------

_INLINE_METRICS = """\
import marivo.datasource as md
import marivo.semantic as ms
import marivo.datasource as md

wh = ms.ref.datasource("wh")
orders = ms.entity(name="orders", datasource=wh, source=md.table("orders"))

@ms.measure(entity=orders, additivity=ms.additive_all())
def amount(orders): return orders.amount

@ms.measure(entity=orders, additivity=ms.non_additive())
def unit_price(orders): return orders.unit_price

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
avg_price = ms.aggregate(measure=unit_price, agg="mean", name="avg_price")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
query_count = ms.count(entity=orders, name="query_count")
aov = ms.ratio(name="aov", numerator=revenue, denominator=order_count)
gross_plus = ms.linear(name="gross_plus", add=[revenue, revenue])
weighted_price = ms.weighted_mean(name="weighted_price", value=unit_price, weight=amount)
"""


def test_resolution_fills_additivity() -> None:
    with load_inline_semantic(_INLINE_METRICS) as result:
        reg = result.registry
        assert reg.metrics["test.revenue"].additivity == "additive"
        assert reg.metrics["test.avg_price"].additivity == "non_additive"
        assert reg.metrics["test.order_count"].additivity == "additive"
        assert reg.metrics["test.query_count"].additivity == "additive"
        assert reg.metrics["test.query_count"].unit == "1"
        assert reg.metrics["test.aov"].additivity == "non_additive"
        assert reg.metrics["test.gross_plus"].additivity == "additive"
        assert reg.metrics["test.weighted_price"].additivity == "non_additive"


_INLINE_TARGET_RESOLUTION = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
region = ms.dimension_column(name="region", entity=orders, column="region")
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY"
)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
candidate = ms.aggregate(name="candidate", measure=amount, agg="sum")
mixed = ms.linear(name="mixed", add=[candidate, revenue])
"""


@pytest.mark.parametrize("is_dimension", [True, False], ids=["dimension", "unknown"])
@pytest.mark.parametrize("aggregation", ["count", "sum"])
def test_direct_ir_invalid_measure_target_preserves_ordered_errors(
    is_dimension: bool, aggregation: Literal["count", "sum"]
) -> None:
    with load_inline_semantic(_INLINE_TARGET_RESOLUTION) as result:
        assert result.registry is not None
        registry = Registry(
            domains=dict(result.registry.domains),
            datasources=dict(result.registry.datasources),
            entities=dict(result.registry.entities),
            dimensions=dict(result.registry.dimensions),
            measures=dict(result.registry.measures),
            metrics=dict(result.registry.metrics),
        )
    target = "test.orders.region" if is_dimension else "test.orders.missing"
    registry.metrics["test.candidate"] = replace(
        registry.metrics["test.candidate"],
        measure=target,
        aggregation_target=target,
        aggregation=aggregation,
        additivity=None,
        dsl_additivity=None,
        unit=None,
    )
    registry.metrics["test.mixed"] = replace(
        registry.metrics["test.mixed"], additivity=None, unit=None
    )

    loader._resolve_metric_additivity(registry)
    loader._resolve_metric_unit(registry)
    errors, warnings = assembly_validate(registry)

    assert registry.metrics["test.candidate"].additivity is None
    expected_kind = (
        ErrorKind.MISSING_MEASURE_ADDITIVITY if is_dimension else ErrorKind.UNKNOWN_MEASURE
    )
    unit_conflict = is_dimension and aggregation == "count"
    assert registry.metrics["test.candidate"].unit == ("1" if unit_conflict else None)
    assert [error.kind for error in errors] == (
        [expected_kind, ErrorKind.INCOMMENSURABLE_LINEAR_UNITS]
        if unit_conflict
        else [expected_kind]
    )
    error = errors[0]
    assert error.semantic_refs == ("test.candidate", target)
    assert error.expected is None
    assert error.received is None
    assert error.repair is None
    if is_dimension:
        assert error.message == (
            "Measure 'test.orders.region' used by 'test.candidate' must declare additivity."
        )
        assert error.constraint_id == ConstraintId.MEASURE_ADDITIVITY_REQUIRED
        assert error.hint == (
            "Set additivity with ms.additive(...), ms.additive_all(...), or ms.non_additive()."
        )
        assert error.details == {"metric": "test.candidate", "measure": target}
    else:
        assert error.message == (
            "Metric 'test.candidate' references unknown measure 'test.orders.missing'."
        )
        assert error.constraint_id is None
        assert error.hint is None
        assert error.details == {
            "metric": "test.candidate",
            "measure": target,
            "did_you_mean": ["test.orders.amount", "test.orders.region"],
        }
    if unit_conflict:
        conflict = errors[1]
        assert conflict.message == (
            "Metric 'test.mixed' adds incommensurable units ['1', 'CNY']; "
            "linear terms must share one unit."
        )
        assert conflict.semantic_refs == ("test.mixed",)
        assert conflict.constraint_id == ConstraintId.LINEAR_UNIT_COMMENSURABLE
        assert conflict.details == {
            "metric": "test.mixed",
            "units": {"test.candidate": "1", "test.revenue": "CNY"},
        }
    assert not warnings


@pytest.mark.parametrize(
    "source, expected_text",
    [
        (
            """\
import marivo.datasource as md
import marivo.semantic as ms
wh = ms.ref.datasource("wh")
o = ms.entity(name="o", datasource=wh, source=md.table("o"))
value = ms.measure_column(name="value", entity=o, column="value", additivity=ms.non_additive())
weight = ms.measure_column(name="weight", entity=o, column="weight", additivity=ms.non_additive())
ms.weighted_mean(name="bad", value=value, weight=weight)
""",
            "must be additive",
        ),
        (
            """\
import marivo.datasource as md
import marivo.semantic as ms
wh = ms.ref.datasource("wh")
left = ms.entity(name="left", datasource=wh, source=md.table("left"))
right = ms.entity(name="right", datasource=wh, source=md.table("right"))
value = ms.measure_column(name="value", entity=left, column="value", additivity=ms.non_additive())
weight = ms.measure_column(name="weight", entity=right, column="weight", additivity=ms.additive_all())
ms.weighted_mean(name="bad", value=value, weight=weight)
""",
            "same entity",
        ),
    ],
)
def test_weighted_mean_rejects_invalid_weight_grain(source: str, expected_text: str) -> None:
    with load_inline_semantic(source) as result:
        assert any(expected_text in error.message for error in result.errors)


# ---------------------------------------------------------------------------
# additivity coordinates require Dimension refs
# ---------------------------------------------------------------------------


def test_additivity_coordinate_must_be_dimension() -> None:
    entity = ref_factory.entity("test.snap")

    with pytest.raises(SemanticDecoratorError) as exc_info:
        authoring.additive_all(except_=(entity,))

    assert exc_info.value.kind == ErrorKind.INVALID_REF


# ---------------------------------------------------------------------------
# validator — sum on non-additive measure is rejected
# ---------------------------------------------------------------------------

_INLINE_SUM_ON_INTENSIVE = """\
import marivo.datasource as md
import marivo.semantic as ms
import marivo.datasource as md

wh = ms.ref.datasource("wh")
o = ms.entity(name="o", datasource=wh, source=md.table("o"))

@ms.measure(entity=o, additivity=ms.non_additive())
def unit_price(o): return o.unit_price

bad = ms.aggregate(measure=unit_price, agg="sum", name="bad")
"""


def test_sum_on_non_additive_measure_is_rejected() -> None:
    with load_inline_semantic(_INLINE_SUM_ON_INTENSIVE) as result:
        kinds = {e.kind for e in result.errors}
        assert ErrorKind.INVALID_MEASURE_AGGREGATION in kinds


# ---------------------------------------------------------------------------
# validator — composition component refs + cycle
# ---------------------------------------------------------------------------

_INLINE_BAD_COMPONENT = """\
import marivo.datasource as md
import marivo.semantic as ms
import marivo.datasource as md

wh = ms.ref.datasource("wh")
o = ms.entity(name="o", datasource=wh, source=md.table("o"))

@ms.measure(entity=o, additivity=ms.additive_all())
def amount(o): return o.amount

rev = ms.aggregate(measure=amount, agg="sum", name="rev")
bad_ratio = ms.ratio(name="bad_ratio", numerator=rev, denominator=ms.ref.metric("test.missing"))
"""


def test_unknown_composition_component_is_reported() -> None:
    with load_inline_semantic(_INLINE_BAD_COMPONENT) as result:
        assert ErrorKind.MISSING_METRIC_REF in {e.kind for e in result.errors}


_INLINE_CYCLE = """\
import marivo.datasource as md
import marivo.semantic as ms
import marivo.datasource as md

wh = ms.ref.datasource("wh")
o = ms.entity(name="o", datasource=wh, source=md.table("o"))

@ms.measure(entity=o, additivity=ms.additive_all())
def amount(o): return o.amount

base = ms.aggregate(measure=amount, agg="sum", name="base")
a = ms.linear(name="a", add=[base, ms.ref.metric("test.b")])
b = ms.linear(name="b", add=[base, ms.ref.metric("test.a")])
"""


def test_metric_cycle_detected_over_composition() -> None:
    with load_inline_semantic(_INLINE_CYCLE) as result:
        assert ErrorKind.CROSS_MODEL_CYCLE in {e.kind for e in result.errors}
