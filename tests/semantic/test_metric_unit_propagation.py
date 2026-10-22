"""Unit inference and declaration boundaries for published R1 expressions."""

from __future__ import annotations

import pytest

from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import ErrorKind
from tests.shared_fixtures import load_inline_semantic

_FIELDS = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
latency_ref = ms.measure_column(name="latency", entity=orders, column="latency", additivity=ms.non_additive())
requests_ref = ms.measure_column(name="requests", entity=orders, column="requests", additivity=ms.additive_all(), unit="{request}")
duration_ref = ms.measure_column(name="duration", entity=orders, column="duration", additivity=ms.additive_all(), unit="s")
opaque_ref = ms.measure_column(name="opaque", entity=orders, column="opaque", additivity=ms.additive_all(), unit="CNY/(request)")
amount = amount_ref
latency = latency_ref
orders_value = orders
requests = requests_ref
duration = duration_ref
opaque = opaque_ref
"""

_METRICS = """\
@ms.metric()
def revenue():
    return amount.sum()
@ms.metric()
def average():
    return latency.mean()
@ms.metric()
def order_count():
    return amount.count()
@ms.metric()
def entity_count():
    return orders_value.count()
@ms.metric()
def distinct_count():
    return amount.count_distinct()
@ms.metric()
def count_share():
    return order_count / entity_count
@ms.metric()
def margin():
    return revenue / revenue
@ms.metric()
def arpu():
    return revenue / order_count
@ms.metric()
def net():
    return revenue + revenue
@ms.metric()
def cny_per_request():
    return revenue / requests.sum()
@ms.metric()
def seconds_per_request():
    return duration.sum() / requests.sum()
@ms.metric()
def cny_per_second():
    return cny_per_request / seconds_per_request
@ms.metric()
def opaque_total():
    return opaque.sum()
@ms.metric()
def unknown_parent():
    return opaque_total / duration.sum()
"""


def test_measure_unit_is_stored_on_physical_definition() -> None:
    with load_inline_semantic(_FIELDS) as result:
        assert result.registry is not None, result.errors
        assert result.registry.measures["test.orders.amount"].unit == "CNY"


@pytest.mark.parametrize(
    ("name", "unit"),
    (
        ("revenue", "CNY"),
        ("average", None),
        ("order_count", "1"),
        ("entity_count", "1"),
        ("distinct_count", "1"),
        ("margin", "1"),
        ("count_share", "1"),
        ("arpu", "CNY"),
        ("net", "CNY"),
        ("cny_per_request", "CNY/{request}"),
        ("seconds_per_request", "s/{request}"),
        ("cny_per_second", "CNY/s"),
        ("opaque_total", "CNY/(request)"),
        ("unknown_parent", None),
    ),
)
def test_published_metric_infers_unit_from_its_expression(name: str, unit: str | None) -> None:
    with load_inline_semantic(_FIELDS + _METRICS) as result:
        assert result.registry is not None, result.errors
        assert consume_metric(result.registry.declared_metrics[f"test.{name}"]).unit == unit


@pytest.mark.parametrize("unit", ("USD", "%", "{order}"))
def test_metric_unit_override_is_rejected(unit: str) -> None:
    source = _FIELDS + f"@ms.metric(unit={unit!r})\ndef invalid():\n    return amount.sum()\n"
    with load_inline_semantic(source) as result:
        (error,) = result.errors
        assert error.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert error.received is not None and "unit" in error.received
        assert error.repair is not None
        assert "unit" in error.repair.action


def test_categorical_unit_is_rejected() -> None:
    source = _FIELDS.split("finalized =", 1)[0] + 'ms.dimension(entity=orders, unit="CNY")\n'
    with load_inline_semantic(source) as result:
        assert result.errors
        assert "unit" in str(result.errors[0])


def test_incommensurable_metric_addition_is_rejected() -> None:
    source = _FIELDS + "@ms.metric()\ndef invalid():\n    return amount.sum() + requests.sum()\n"
    with load_inline_semantic(source) as result:
        (error,) = result.errors
        assert error.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS
        assert error.expected and error.received and error.repair


def test_same_unit_subtraction_is_published() -> None:
    source = _FIELDS + "@ms.metric()\ndef net():\n    return amount.sum() - amount.sum()\n"
    with load_inline_semantic(source) as result:
        assert result.registry is not None, result.errors
        assert consume_metric(result.registry.declared_metrics["test.net"]).unit == "CNY"
