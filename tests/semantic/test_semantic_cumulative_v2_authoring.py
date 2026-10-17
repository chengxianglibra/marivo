"""Authoring + IR + hash tests for cumulative metrics v2 anchors."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Literal

import ibis.expr.types as ibis_ir
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import MetricWindow, SourceAggregate
from marivo._definition.windows import AllHistory, ToDate, TrailingDuration
from marivo._temporal import Grain
from marivo.refs import MeasureKind, Ref, TimeDimensionKind
from marivo.semantic._authoring_metrics import (
    _GRAIN_TO_DATE_INTERNAL,
    GrainToDate,
    Trailing,
    _compute_composition_hash,
)
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._dsl_authoring import AdditivityPolicy
from marivo.semantic.errors import SemanticDecoratorError, SemanticExpressionError
from marivo.semantic.ir import CumulativeComposition
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


def _grain_to_date(*, grain: Grain) -> GrainToDate:
    """Build the retained internal Runtime value, without retired public authoring."""
    token = _GRAIN_TO_DATE_INTERNAL.set(True)
    try:
        return GrainToDate(grain=grain)
    finally:
        _GRAIN_TO_DATE_INTERNAL.reset(token)


def test_grain_to_date_returns_frozen_value_object() -> None:
    obj = _grain_to_date(grain=mv.grain("month"))
    assert isinstance(obj, GrainToDate)
    assert obj.grain == mv.grain("month")
    with pytest.raises(Exception):
        obj.grain = mv.grain("quarter")  # type: ignore[misc]  # intentional frozen-value mutation


def test_grain_to_date_is_retired_and_not_directly_constructible() -> None:
    assert not hasattr(ms, "GrainToDate")
    assert not hasattr(ms, "Trailing")
    with pytest.raises(SemanticExpressionError, match="retired declaration factory") as caught:
        ms.grain_to_date(grain=mv.grain("month"))
    assert caught.value.expected and caught.value.received and caught.value.repair
    with pytest.raises(SemanticExpressionError, match="retired declaration factory"):
        ms.trailing(count=7, unit="day")
    with pytest.raises(TypeError, match="direct construction is not supported"):
        GrainToDate(grain=mv.grain("month"))


def test_grain_to_date_rejects_unknown_grain() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        _grain_to_date(grain=mv.grain("day"))
    assert "grain_to_date" in str(exc_info.value).lower()


@pytest.mark.parametrize("unit", ["week", "month", "quarter", "year"])
def test_grain_to_date_accepts_reset_grains(
    unit: Literal["week", "month", "quarter", "year"],
) -> None:
    value = mv.grain(unit)
    assert _grain_to_date(grain=value).grain == value


def test_grain_to_date_preserves_semantic_grain_identity() -> None:
    fiscal_month = ms.calendar_grain(
        calendar=ms.ref.period_calendar("sales.fiscal"),
        level="fiscal_month",
    )
    authored = _grain_to_date(grain=fiscal_month)

    assert authored.grain is fiscal_month
    same_calendar = _compute_composition_hash(
        CumulativeComposition(
            base="sales.gmv",
            over="sales.events.event_time",
            anchor=("grain_to_date", fiscal_month),
        )
    )
    other_calendar = _compute_composition_hash(
        CumulativeComposition(
            base="sales.gmv",
            over="sales.events.event_time",
            anchor=(
                "grain_to_date",
                ms.calendar_grain(
                    calendar=ms.ref.period_calendar("sales.retail"),
                    level="fiscal_month",
                ),
            ),
        )
    )
    assert same_calendar != other_calendar


def test_trailing_returns_frozen_value_object() -> None:
    obj = Trailing(count=7, unit="day")
    assert isinstance(obj, Trailing)
    assert obj.count == 7
    assert obj.unit == "day"
    with pytest.raises(Exception):
        obj.count = 30  # type: ignore[misc]


def test_trailing_rejects_non_positive_count() -> None:
    with pytest.raises(SemanticDecoratorError):
        Trailing(count=0, unit="day")


@pytest.mark.parametrize("count", [True, False])
def test_trailing_rejects_bool_count(count: bool) -> None:
    """``bool`` is a subclass of ``int`` but must not be accepted as a count.

    ``isinstance(True, int)`` is True, so without an explicit rejection
    ``count=True`` would silently become ``count=1``. Mirrors the
    ``SampleIntervalIR`` guard in ``marivo/semantic/ir.py``.
    """
    with pytest.raises(SemanticDecoratorError) as exc_info:
        Trailing(count=count, unit="day")
    assert "positive integer count" in str(exc_info.value)


@pytest.mark.parametrize("unit", ["second", "minute", "hour", "day", "week"])
def test_trailing_accepts_fixed_size_units(unit: str) -> None:
    assert Trailing(count=3, unit=unit).unit == unit


def test_trailing_rejects_calendar_variable_unit() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        Trailing(count=3, unit="month")
    msg = str(exc_info.value).lower()
    assert "trailing" in msg
    assert "grain_to_date" in msg or "fixed" in msg


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


def test_grain_to_date_hash_differs_from_all_history() -> None:
    base = "sales.gmv"
    over = "sales.events.event_time"
    all_history_hash = _compute_composition_hash(CumulativeComposition(base=base, over=over))
    gtd_hash = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("grain_to_date", "month"))
    )
    assert all_history_hash != gtd_hash


def test_trailing_hash_encodes_count_and_unit() -> None:
    base = "sales.gmv"
    over = "sales.events.event_time"
    h7d = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 7, "day"))
    )
    h30d = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 30, "day"))
    )
    assert h7d != h30d


def test_grain_to_date_hash_distinguishes_reset_grains() -> None:
    """MTD vs QTD vs YTD must hash distinctly — the reset grain is part of the
    anchor tuple and must feed the composition hash so cross-grain collisions
    cannot silently misidentify a cumulative metric."""
    base = "sales.gmv"
    over = "sales.events.event_time"
    h_month = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("grain_to_date", "month"))
    )
    h_quarter = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("grain_to_date", "quarter"))
    )
    h_year = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("grain_to_date", "year"))
    )
    assert len({h_month, h_quarter, h_year}) == 3


def test_trailing_hash_distinguishes_unit() -> None:
    """trailing(7, day) vs trailing(7, week) must hash distinctly — the unit is
    part of the anchor tuple and must feed the composition hash."""
    base = "sales.gmv"
    over = "sales.events.event_time"
    h_day = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 7, "day"))
    )
    h_week = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 7, "week"))
    )
    assert h_day != h_week


def test_cumulative_anchor_hashes_are_deterministic() -> None:
    """Recomputing the same current anchor composition yields the same hash."""
    base = "sales.gmv"
    over = "sales.events.event_time"
    for anchor in (
        "all_history",
        ("grain_to_date", "month"),
        ("grain_to_date", "quarter"),
        ("trailing", 7, "day"),
        ("trailing", 30, "day"),
    ):
        comp = CumulativeComposition(base=base, over=over, anchor=anchor)
        assert _compute_composition_hash(comp) == _compute_composition_hash(comp)


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
