"""Explicit rescaling preserves semantic authority and captures the asserted unit."""

from collections.abc import Callable
from decimal import Decimal

import ibis
import pytest

import marivo.semantic as ms
from marivo._definition.deferred import inspect_deferred
from marivo._definition.graph import computation_fingerprint
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic._expression_values import _as_deferred
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value

ProjectFactory = Callable[..., SemanticProject]
_DOMAIN = 'import marivo.semantic as ms\nms.domain(name="traffic", owner="Analytics")\n'
_FIELDS = """import marivo.datasource as md
import marivo.semantic as ms
flows = ms.entity(name="flows", datasource=ms.ref.datasource("warehouse"), source=md.table("flows"), primary_key=["id"])
day = ms.time_dimension_column(name="day", entity=flows, column="day", is_default=True)
category = ms.dimension_column(name="category", entity=flows, column="category")
total_in_flow_value = ms.measure_column(name="total_in_flow_value", entity=flows, column="bytes", unit="By", additivity=ms.additive_all())
total_in_p95_flow_value = ms.measure_column(name="total_in_p95_flow_value", entity=flows, column="p95_bytes", unit="By", additivity=ms.additive_all())
duration = ms.measure_column(name="duration", entity=flows, column="duration", unit="s", additivity=ms.additive_all())
blocked = ms.measure_column(name="blocked", entity=flows, column="bytes", unit="By", additivity=ms.non_additive())
fixed = ms.measure_column(name="fixed", entity=flows, column="bytes", unit="By", additivity=ms.additive_except(day), status_time_dimension=day)
unknown = ms.measure_column(name="unknown", entity=flows, column="bytes", additivity=ms.additive_all())
other = ms.entity(name="other", datasource=ms.ref.datasource("warehouse"), source=md.table("other"))
other_bytes = ms.measure_column(name="bytes", entity=other, column="bytes", unit="By", additivity=ms.additive_all())
"""
_DECLARATIONS = """
@ms.measure(unit="GiB")
def gib():
    return ms.rescale(total_in_flow_value, divisor=1024**3, unit="GiB")
@ms.metric()
def bandwidth():
    return ms.rescale(total_in_flow_value.sum(), divisor=86400 * 1024**3, unit="GiB/s")
"""


def _catalog(factory: ProjectFactory, source: str = _FIELDS + _DECLARATIONS) -> SemanticCatalog:
    project = factory({"traffic/_domain.py": _DOMAIN, "traffic/objects.py": source})
    assert not project.errors(), tuple(str(error) for error in project.errors())
    return SemanticCatalog(project)


def test_rescale_row_and_metric_units_compose_without_mutating_sources(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog_value(catalog, "traffic.flows.total_in_flow_value", kind="field")
    duration = catalog_value(catalog, "traffic.flows.duration", kind="field")
    source = consume_row(amount)
    scaled = ms.rescale(amount, divisor=Decimal("1024"), unit="s.GiB/s.s")
    facts = consume_row(scaled)
    assert facts.unit == "GiB/s"
    assert facts.root == source.root
    assert facts.permission == source.permission
    assert facts.default_time == source.default_time
    assert facts.coordinates == source.coordinates
    assert consume_row(amount).unit == "By"
    assert consume_row(amount / 1024).unit == "By"
    assert consume_row(scaled + ms.rescale(amount, divisor=1024, unit="GiB/s")).unit == "GiB/s"
    metric = ms.rescale(amount.sum(), divisor=1, unit="GiB")
    assert consume_metric(metric).roots == consume_metric(amount.sum()).roots
    assert consume_metric(metric / duration.sum()).unit == "GiB/s"
    assert consume_metric(scaled.sum()).unit == "GiB/s"
    assert consume_row(ms.rescale(amount, divisor=1.0, unit="By/(request)")).unit == "By/(request)"


def test_rescale_preserves_sum_denials_fixed_time_and_contribution_roots(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    for name in ("blocked", "fixed"):
        value = catalog_value(catalog, f"traffic.flows.{name}", kind="field")
        scaled = ms.rescale(value, divisor=2, unit="GiB")
        assert consume_row(scaled).permission == consume_row(value).permission
        assert consume_row(scaled).default_time == consume_row(value).default_time
        if name == "blocked":
            with pytest.raises(SemanticExpressionError) as original:
                consume_metric(value.sum())
            with pytest.raises(SemanticExpressionError) as rescaled:
                consume_metric(scaled.sum())
            assert rescaled.value.kind == original.value.kind
            assert rescaled.value.expected and rescaled.value.received and rescaled.value.repair
    local = catalog_value(catalog, "traffic.flows.total_in_flow_value", kind="field")
    foreign = catalog_value(catalog, "traffic.other.bytes", kind="field")
    with pytest.raises(SemanticExpressionError) as cross_root:
        consume_row(ms.rescale(local + foreign, divisor=1, unit="GiB"))
    assert cross_root.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    with pytest.raises(SemanticExpressionError) as mixed:
        consume_metric(ms.rescale(local + local.sum(), divisor=1, unit="GiB"))
    assert mixed.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


@pytest.mark.parametrize(
    "divisor",
    [
        True,
        False,
        0,
        -1,
        float("inf"),
        float("nan"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-1"),
        "2",
        None,
        ms.ref.measure("traffic.flows.duration"),
    ],
)
def test_rescale_rejects_invalid_or_dynamic_divisors(divisor: object) -> None:
    with pytest.raises(SemanticExpressionError) as failure:
        ms.rescale(ms.ref.measure("traffic.flows.total_in_flow_value"), divisor=divisor, unit="GiB")
    assert failure.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert failure.value.expected and failure.value.received and failure.value.repair
    assert "divisor" in failure.value.repair.action


@pytest.mark.parametrize("unit", ["", "GiB / s", "字节", None, 1])
def test_rescale_rejects_invalid_target_units(unit: object) -> None:
    with pytest.raises(SemanticExpressionError) as failure:
        ms.rescale(ms.ref.measure("traffic.flows.total_in_flow_value"), divisor=1, unit=unit)
    assert failure.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert failure.value.expected and failure.value.received and failure.value.repair


def test_rescale_rejects_unknown_units_and_nonnumeric_or_unfinished_inputs(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    unknown = catalog_value(catalog, "traffic.flows.unknown", kind="field")
    with pytest.raises(SemanticExpressionError) as failure:
        consume_row(ms.rescale(unknown, divisor=1, unit="GiB"))
    assert failure.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert failure.value.received == "unknown input unit"
    assert failure.value.expected and failure.value.repair
    amount = catalog_value(catalog, "traffic.flows.total_in_flow_value", kind="field")
    category = catalog_value(catalog, "traffic.flows.category", kind="field")
    day = catalog_value(catalog, "traffic.flows.day", kind="field")
    for value in (category, day, amount > 1, amount.sum() > 1, amount.sum().per(category)):
        with pytest.raises(SemanticExpressionError) as invalid:
            consume_row(ms.rescale(value, divisor=1, unit="GiB"))
        assert invalid.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
        assert invalid.value.expected and invalid.value.received and invalid.value.repair
    with pytest.raises(SemanticExpressionError) as literal:
        ms.rescale(ibis.literal(3), divisor=1, unit="GiB")
    assert literal.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def test_rescale_binds_forward_references_and_retains_fingerprint_and_disclosure(
    semantic_project_factory: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    source = (
        """import marivo.semantic as ms
future = ms.ref.measure("traffic.flows.total_in_flow_value")
@ms.metric()
def bandwidth():
    return ms.rescale(future.sum(), divisor=1024, unit="GiB/s")
"""
        + _FIELDS
        + """
@ms.metric()
def nested():
    return ms.rescale(ms.rescale(future.sum(), divisor=1024, unit="GiB"), divisor=2, unit="GiB/s")
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    amount = catalog_value(catalog, "traffic.flows.total_in_flow_value", kind="field")
    original = ms.rescale(amount.sum(), divisor=1024, unit="GiB/s")
    assert (
        inspect_deferred(_as_deferred(catalog.get("metric:traffic.bandwidth"))).fingerprint
        == inspect_deferred(_as_deferred(original)).fingerprint
    )
    variants = (
        original,
        ms.rescale(amount.sum(), divisor=1, unit="GiB/s"),
        ms.rescale(amount.sum(), divisor=1024, unit="GiB"),
        amount.sum() / 1024,
    )
    assert len({inspect_deferred(_as_deferred(value)).fingerprint for value in variants}) == 4
    assert len({computation_fingerprint(consume_metric(value).node) for value in variants}) == 4
    contract = catalog.get("metric:traffic.bandwidth")
    assert ("rescale", "input_unit='By', divisor=1024, unit='GiB/s'") in contract.definition.facts
    contract.show()
    shown = capsys.readouterr().out
    assert "rescale" in shown and "divisor=1024" in shown and "GiB/s" in shown
    nested = catalog.get("metric:traffic.nested")
    assert nested.definition.facts == (
        ("rescale", "input_unit='GiB', divisor=2, unit='GiB/s'"),
        ("rescale", "input_unit='By', divisor=1024, unit='GiB'"),
    )


def test_rescale_does_not_change_measure_unit_assertions(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _FIELDS
        + """
@ms.measure(unit="GiB")
def invalid():
    return total_in_flow_value / 1024
"""
    )
    project = semantic_project_factory(
        {"traffic/_domain.py": _DOMAIN, "traffic/objects.py": source}
    )
    assert project.errors()
    error = project.errors()[0]
    assert error.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert error.expected and error.received and error.repair


def test_rescale_cannot_hide_fixed_time_from_measure_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _FIELDS
        + """
@ms.measure(additivity=ms.additive_all())
def invalid():
    return ms.rescale(fixed, divisor=1024, unit="GiB")
"""
    )
    project = semantic_project_factory(
        {"traffic/_domain.py": _DOMAIN, "traffic/objects.py": source}
    )
    assert project.errors()
    error = project.errors()[0]
    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert error.received == "traffic.flows.day"
    assert error.expected and "time axes fixed" in error.expected
    assert error.repair


@pytest.mark.parametrize("divisor", ["duration", "duration.sum()", "helper()"])
def test_rescale_declaration_divisor_does_not_admit_dynamic_values_or_callbacks(
    semantic_project_factory: ProjectFactory, divisor: str
) -> None:
    source = (
        _FIELDS
        + f"""
def helper():
    raise AssertionError("author callback was executed")
@ms.metric()
def invalid():
    return ms.rescale(total_in_flow_value.sum(), divisor={divisor}, unit="GiB/s")
"""
    )
    project = semantic_project_factory(
        {"traffic/_domain.py": _DOMAIN, "traffic/objects.py": source}
    )
    assert project.errors()
    error = project.errors()[0]
    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert error.expected and error.received and error.repair
    assert "author callback was executed" not in str(error)
