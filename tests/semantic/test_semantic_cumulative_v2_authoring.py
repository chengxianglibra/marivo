"""Current Metric window authoring and definition contracts."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import ibis.expr.types as ibis_ir

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import MetricWindow, SourceAggregate
from marivo._definition.windows import AllHistory, ToDate, TrailingDuration
from marivo.refs import MeasureKind, Ref, TimeDimensionKind
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._dsl_authoring import AdditivityPolicy
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from tests.shared_fixtures import in_memory_datasource_ir


@contextmanager
def _session(domain: str = "sales") -> Iterator[LoaderContext]:
    """Enter a LoaderContext with a default domain for cumulative authoring."""
    ctx = LoaderContext(
        default_domain=domain, datasource_irs=(in_memory_datasource_ir("warehouse"),)
    )
    token = _LOADER_CTX.set(ctx)
    try:
        yield ctx
    finally:
        _LOADER_CTX.reset(token)


def _build_event_time_axis() -> Ref[TimeDimensionKind]:
    """Declare a minimal events entity + event_time time dimension, return the ref."""
    events = ms.entity(
        name="events",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("events"),
    )

    @ms.time_dimension(entity=events, granularity="day")
    def event_time(table: ibis_ir.Table) -> ibis_ir.Value:
        return table["event_time"]

    return event_time


def _measure(
    name: str, entity: str, *, additivity: AdditivityPolicy | None = None
) -> Ref[MeasureKind]:
    policy = ms.additive_all() if additivity is None else additivity
    return ms.measure_column(
        entity=ms.ref.entity(entity), name=name, column=name, additivity=policy
    )


def test_cumulative_accepts_grain_to_date_anchor() -> None:
    with _session(domain="sales"):
        event_time = _build_event_time_axis()
        amt = _measure("amt", "sales.events")
        frozen = ms.finalize()
        gmv = frozen.resolve(amt).sum(time=frozen.resolve(event_time))
        node = consume_metric(gmv.window(mv.to_date(grain=mv.grain("month")))).node
        assert isinstance(node, MetricWindow)
        assert isinstance(node.rule, ToDate)
        assert node.rule.grain == mv.grain("month")


def test_cumulative_accepts_trailing_anchor() -> None:
    with _session(domain="sales"):
        event_time = _build_event_time_axis()
        uid = _measure("uid", "sales.events", additivity=ms.non_additive())
        frozen = ms.finalize()
        active = frozen.resolve(uid).count_distinct(time=frozen.resolve(event_time))
        node = consume_metric(active.window(mv.trailing_duration(duration=timedelta(days=7)))).node
        assert isinstance(node, MetricWindow)
        assert isinstance(node.rule, TrailingDuration)
        assert node.rule.duration == timedelta(days=7)


def test_cumulative_explicit_all_history_anchor() -> None:
    with _session(domain="sales"):
        event_time = _build_event_time_axis()
        amt = _measure("amt", "sales.events")
        frozen = ms.finalize()
        gmv = frozen.resolve(amt).sum(time=frozen.resolve(event_time))
        node = consume_metric(gmv.window(mv.all_history())).node
        assert isinstance(node, MetricWindow)
        assert isinstance(node.rule, AllHistory)


# ---------------------------------------------------------------------------
# Native declaration loading retains complete formulas and explicit time
# roles under each window rule. Output-grain compatibility belongs to the
# consuming observation, after its actual calendar is available.
# ---------------------------------------------------------------------------

_CUMULATIVE_PROJECT = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("wh")
orders = ms.entity(name="orders", datasource=wh, source=md.table("orders"))
event_time = ms.time_dimension_column(
    name="event_time", entity=orders, column="created_at", granularity="day", is_default=True)
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all())
frozen = ms.finalize()
amount_value = frozen.resolve(amount)
time_value = frozen.resolve(event_time)
orders_value = frozen.resolve(orders)
@ms.metric()
def revenue():
    return amount_value.sum(time=time_value)
"""


def test_validator_accepts_complete_derived_formula_for_all_window_rules() -> None:
    from tests.shared_fixtures import load_inline_semantic

    rules = (
        "mv.all_history()",
        "mv.to_date(grain=mv.grain('month'))",
        "mv.trailing_duration(duration=timedelta(days=7))",
    )
    for rule_expr in rules:
        source = _CUMULATIVE_PROJECT + (
            "from datetime import timedelta\nimport marivo.analysis as mv\n"
            "@ms.metric()\ndef aov():\n    return revenue / orders_value.count(time=time_value)\n"
            f"rule = {rule_expr}\n"
            "@ms.metric()\ndef cumulative_aov():\n    return aov.window(rule)\n"
        )
        with load_inline_semantic(source) as result:
            assert result.status == "ready", result.errors
            assert result.registry is not None
            node = consume_metric(result.registry.declared_metrics["test.cumulative_aov"]).node
            assert isinstance(node, MetricWindow)
            assert node.operand == consume_metric(result.registry.declared_metrics["test.aov"]).node


def test_loader_retains_explicit_time_role_and_window_rule() -> None:
    from tests.shared_fixtures import load_inline_semantic

    cases = (
        ("mv.all_history()", AllHistory),
        ("mv.to_date(grain=mv.grain('month'))", ToDate),
        ("mv.trailing_duration(duration=timedelta(days=7))", TrailingDuration),
    )
    for rule_expr, rule_type in cases:
        source = _CUMULATIVE_PROJECT + (
            "from datetime import timedelta\nimport marivo.analysis as mv\n"
            f"rule = {rule_expr}\n"
            "@ms.metric()\ndef cumulative_revenue():\n    return revenue.window(rule)\n"
        )
        with load_inline_semantic(source) as result:
            assert result.status == "ready", result.errors
            assert result.registry is not None
            node = consume_metric(result.registry.declared_metrics["test.cumulative_revenue"]).node
            assert isinstance(node, MetricWindow)
            assert isinstance(node.rule, rule_type)
            revenue_node = consume_metric(result.registry.declared_metrics["test.revenue"]).node
            assert isinstance(revenue_node, SourceAggregate)
            assert revenue_node.time is not None
            assert revenue_node.time.field.ref.path == "test.orders.event_time"


def test_loader_defers_output_grain_compatibility_until_observation() -> None:
    from tests.shared_fixtures import load_inline_semantic

    source = _CUMULATIVE_PROJECT + (
        "import marivo.analysis as mv\nrule = mv.to_date(grain=mv.grain('month'))\n"
        "@ms.metric()\ndef mtd_revenue():\n    return revenue.window(rule)\n"
    )
    with load_inline_semantic(source) as result:
        assert result.status == "ready", result.errors
        assert result.registry is not None
        node = consume_metric(result.registry.declared_metrics["test.mtd_revenue"]).node
        assert isinstance(node, MetricWindow)
        assert isinstance(node.rule, ToDate)
        assert node.rule.grain == mv.grain("month")
