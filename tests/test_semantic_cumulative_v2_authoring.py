"""Authoring + IR + hash tests for cumulative metrics v2 anchors."""

import hashlib
from contextlib import contextmanager
from typing import Any

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.authoring import (
    CumulativeComposition,
    GrainToDate,
    Trailing,
    _compute_composition_hash,
)
from marivo.semantic.errors import SemanticDecoratorError
from marivo.semantic.ir import MetricIR
from marivo.semantic.loader import _LOADER_CTX, LoaderContext
from marivo.semantic.refs import EntityRef, TimeDimensionRef


@contextmanager
def _session(domain: str = "sales"):
    """Enter a LoaderContext with a default domain for cumulative authoring."""
    ctx = LoaderContext(default_domain=domain)
    _LOADER_CTX.set(ctx)
    try:
        yield ctx
    finally:
        _LOADER_CTX.set(None)


def _pending_metric(ctx: LoaderContext, semantic_id: str) -> MetricIR:
    for ir_obj, _ in ctx.pending_objects:
        if isinstance(ir_obj, MetricIR) and ir_obj.semantic_id == semantic_id:
            return ir_obj
    raise KeyError(f"no pending MetricIR with semantic_id={semantic_id!r}")


def _build_event_time_axis() -> TimeDimensionRef:
    """Declare a minimal events entity + event_time time dimension, return the ref."""
    events = ms.entity(
        name="events",
        datasource=md.ref("datasource.warehouse"),
        source=ms.table("events"),
    )

    @ms.time_dimension(entity=events, granularity="day")
    def event_time(table: Any) -> Any:
        return table["event_time"]

    return event_time  # type: ignore[return-value]


def _measure(name: str, entity: str, *, additivity: str = "additive"):
    return ms.measure_column(
        entity=EntityRef(entity), name=name, column=name, additivity=additivity
    )


def test_grain_to_date_returns_frozen_value_object():
    obj = ms.grain_to_date(grain="month")
    assert isinstance(obj, GrainToDate)
    assert obj.grain == "month"
    with pytest.raises(Exception):
        obj.grain = "quarter"  # type: ignore[misc]


def test_grain_to_date_rejects_unknown_grain():
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.grain_to_date(grain="day")
    assert "grain_to_date" in str(exc_info.value).lower()


@pytest.mark.parametrize("grain", ["week", "month", "quarter", "year"])
def test_grain_to_date_accepts_reset_grains(grain):
    assert ms.grain_to_date(grain=grain).grain == grain


def test_trailing_returns_frozen_value_object():
    obj = ms.trailing(count=7, unit="day")
    assert isinstance(obj, Trailing)
    assert obj.count == 7
    assert obj.unit == "day"
    with pytest.raises(Exception):
        obj.count = 30  # type: ignore[misc]


def test_trailing_rejects_non_positive_count():
    with pytest.raises(SemanticDecoratorError):
        ms.trailing(count=0, unit="day")


@pytest.mark.parametrize("count", [True, False])
def test_trailing_rejects_bool_count(count):
    """``bool`` is a subclass of ``int`` but must not be accepted as a count.

    ``isinstance(True, int)`` is True, so without an explicit rejection
    ``count=True`` would silently become ``count=1``. Mirrors the
    ``SampleIntervalIR`` guard in ``marivo/semantic/ir.py``.
    """
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.trailing(count=count, unit="day")
    assert "positive integer count" in str(exc_info.value)


@pytest.mark.parametrize("unit", ["second", "minute", "hour", "day", "week"])
def test_trailing_accepts_fixed_size_units(unit):
    assert ms.trailing(count=3, unit=unit).unit == unit


def test_trailing_rejects_calendar_variable_unit():
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.trailing(count=3, unit="month")
    msg = str(exc_info.value).lower()
    assert "trailing" in msg
    assert "grain_to_date" in msg or "fixed" in msg


def test_cumulative_accepts_grain_to_date_anchor():
    with _session(domain="sales"):
        event_time = _build_event_time_axis()
        amt = _measure("amt", "sales.events")
        gmv = ms.aggregate(name="gmv", measure=amt, agg="sum")
        cum = ms.cumulative(
            name="mtd_gmv", base=gmv, over=event_time, anchor=ms.grain_to_date(grain="month")
        )
        assert cum is not None


def test_cumulative_accepts_trailing_anchor():
    with _session(domain="sales"):
        event_time = _build_event_time_axis()
        uid = _measure("uid", "sales.events", additivity="non_additive")
        active = ms.aggregate(name="active", measure=uid, agg="count_distinct")
        cum = ms.cumulative(
            name="rolling7_active",
            base=active,
            over=event_time,
            anchor=ms.trailing(count=7, unit="day"),
        )
        assert cum is not None


def test_cumulative_anchor_defaults_to_all_history():
    with _session(domain="sales") as ctx:
        event_time = _build_event_time_axis()
        amt = _measure("amt", "sales.events")
        gmv = ms.aggregate(name="gmv", measure=amt, agg="sum")
        ms.cumulative(name="all_gmv", base=gmv, over=event_time)
        # Anchor defaults to all_history: read back the authored MetricIR and
        # assert the resolved CumulativeComposition.anchor directly.
        m = _pending_metric(ctx, "sales.all_gmv")
        assert isinstance(m.composition, CumulativeComposition)
        assert m.composition.anchor == "all_history"


def test_v1_all_history_hash_is_byte_identical():
    """The forward-compatibility promise: v1 all_history objects hash identically under v2 code."""
    v1_composition = CumulativeComposition(base="sales.gmv", over="sales.events.event_time")
    v1_hash = _compute_composition_hash(v1_composition)
    # The v1 hash text is repr(("cumulative", base, over, "all_history")).
    expected_text = repr(("cumulative", "sales.gmv", "sales.events.event_time", "all_history"))
    expected = hashlib.sha256(expected_text.encode()).hexdigest()[:16]
    assert v1_hash == expected


def test_grain_to_date_hash_differs_from_all_history():
    base = "sales.gmv"
    over = "sales.events.event_time"
    all_history_hash = _compute_composition_hash(CumulativeComposition(base=base, over=over))
    gtd_hash = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("grain_to_date", "month"))
    )
    assert all_history_hash != gtd_hash


def test_trailing_hash_encodes_count_and_unit():
    base = "sales.gmv"
    over = "sales.events.event_time"
    h7d = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 7, "day"))
    )
    h30d = _compute_composition_hash(
        CumulativeComposition(base=base, over=over, anchor=("trailing", 30, "day"))
    )
    assert h7d != h30d


def test_grain_to_date_hash_distinguishes_reset_grains():
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


def test_trailing_hash_distinguishes_unit():
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


def test_cumulative_anchor_hashes_are_deterministic():
    """Recomputing the same anchor composition must yield the same hash bytes
    across calls (forward-compatibility / cache-key stability)."""
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
# Task 2: validator + loader behavior under the new anchors.
#
# The v1 validator/loader were written for the single all_history anchor.
# These tests prove the base-whitelist (sum/count/count_distinct), the
# cumulative-over-derived rejection, and the over-omission resolution all
# key on ``composition.kind == "cumulative"`` and run unchanged for the
# grain_to_date and trailing anchors.
# ---------------------------------------------------------------------------

_CUMULATIVE_PROJECT = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = md.ref("datasource.wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))
event_time = ms.time_dimension_column(
    name="event_time", entity=orders, column="created_at", granularity="day")
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity="additive")
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""


def test_validator_rejects_cumulative_over_derived_base() -> None:
    """cumulative-over-derived is rejected for all anchors (v1 rule carries over).

    The cumulative-over-derived rejection lives in
    ``_validate_cumulative_metric`` and keys on
    ``base.metric_type == "derived"`` with no anchor branch. Authoring a
    cumulative metric with a ratio (derived) base under each anchor must fail
    load identically with the cumulative-base-derived teaching error.
    """
    from tests.shared_fixtures import load_inline_semantic

    anchors = [
        None,  # all_history
        "ms.grain_to_date(grain='month')",
        "ms.trailing(count=7, unit='day')",
    ]
    for anchor_expr in anchors:
        anchor_arg = "" if anchor_expr is None else f", anchor={anchor_expr}"
        source = _CUMULATIVE_PROJECT + (
            "order_count = ms.count(name='order_count', entity=orders)\n"
            "aov = ms.ratio(name='aov', numerator=revenue, denominator=order_count)\n"
            f"bad = ms.cumulative(name='bad', base=aov, over=event_time{anchor_arg})\n"
        )
        with load_inline_semantic(source) as result:
            assert result.status == "errored", f"expected load to fail for anchor={anchor_expr!r}"
            messages = " ".join(str(e) for e in result.errors)
            assert "cumulative base" in messages
            assert "derived" in messages
            assert "ratio of two cumulative metrics" in messages


def test_loader_resolves_grain_to_date_anchor_over_from_single_time_entity() -> None:
    """over= omission resolves from the single base-root-entity time dimension
    for grain_to_date and trailing anchors exactly as for all_history.

    The over-omission resolver (``_resolve_cumulative_over``) keys on
    ``isinstance(comp, CumulativeComposition)`` and ``comp.over is None`` with
    no anchor branch, so all three anchors resolve identically. Each metric
    omits ``over=``; load must succeed and the resolved
    ``MetricIR.composition.over`` must equal the single time dimension id,
    while ``anchor`` preserves the authored payload.
    """
    from tests.shared_fixtures import load_inline_semantic

    cases = [
        (None, "all_history"),
        ("ms.grain_to_date(grain='month')", ("grain_to_date", "month")),
        ("ms.trailing(count=7, unit='day')", ("trailing", 7, "day")),
    ]
    for anchor_expr, expected_anchor in cases:
        anchor_arg = "" if anchor_expr is None else f", anchor={anchor_expr}"
        metric_name = f"cum_{abs(hash(expected_anchor)) % 100000}"
        source = _CUMULATIVE_PROJECT + (
            f"cum = ms.cumulative(name='{metric_name}', base=revenue{anchor_arg})\n"
        )
        with load_inline_semantic(source) as result:
            assert result.status == "ready", (
                f"expected load to succeed for anchor={anchor_expr!r}; errors={result.errors}"
            )
            reg = result.registry
            assert reg is not None
            metric_ir = reg.metrics[f"test.{metric_name}"]
            comp = metric_ir.composition
            assert isinstance(comp, CumulativeComposition)
            assert comp.anchor == expected_anchor
            assert comp.over == "test.orders.event_time"


def test_loader_smoke_accepts_week_grain_under_month_reset_at_load_time() -> None:
    """Grain-compat (week under month reset) is plan-time (Task 4), not load-time.

    The loader has no grain-compatibility branch: a grain_to_date(month)
    cumulative must load successfully even though a week query grain would
    later be incompatible at observe/plan time. This is the loader-level
    smoke test the brief asks for here; the rejection test belongs in Task 4.
    """
    from tests.shared_fixtures import load_inline_semantic

    source = _CUMULATIVE_PROJECT + (
        "mtd = ms.cumulative(name='mtd_revenue', base=revenue, over=event_time, "
        "anchor=ms.grain_to_date(grain='month'))\n"
    )
    with load_inline_semantic(source) as result:
        assert result.status == "ready", (
            f"loader must not enforce grain-compat; errors={result.errors}"
        )
        reg = result.registry
        assert reg is not None
        comp = reg.metrics["test.mtd_revenue"].composition
        assert isinstance(comp, CumulativeComposition)
        assert comp.anchor == ("grain_to_date", "month")
