"""Prove encoded cutoff, injective date grouping, and window geometry contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.grouping import grouping_binding
from marivo._definition.observation_time import ObservationPeriod
from marivo._kernel.time_types import anchor_from_datetime, grain_nests
from marivo._kernel.timezones import normalize_timezone
from marivo.analysis.binding.windows import _window_start
from marivo.analysis.compiler.calendar import builtin_key
from marivo.analysis.compiler.source_time import (
    _hour_date_guard,
    endpoint_rewrite_safe,
    ordered_time_literal,
    source_time,
    source_time_predicate,
)
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.date_day_keys import deferred_day_key
from marivo.analysis.materialization.encoded_time import EncodedTime, encoded_time
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.analysis.materialization.time_filter import optimize_time
from marivo.analysis.model.errors import CoreRuleError
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.datasource.spi import localize, timestamp
from marivo.semantic.ir import DateParse, HourPrefixParse
from marivo.semantic.reader import SemanticProject
from marivo.semantic.static_check.source_timezone import DeclaredSourceTimezone
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.formal._smt import prove_unsat
from tests.formal.verify_temporal_boundaries import (
    _binding,
    _integer,
    _micros,
    _prove_selection,
    _Truth,
    _truth,
)
from tests.shared_fixtures import catalog_value

_DATE = "%Y%m%d"
_DASHED = "%Y-%m-%d"
_HOUR = "%Y%m%d%H"
_HOUR_TICK = 3_600_000_000
_DAY_TICK = 24 * _HOUR_TICK


@pytest.mark.parametrize("hourly", [False, True])
def test_canonical_encoding_comparison_is_chronological_and_injective(hourly: bool) -> None:
    component_names = ("year", "month", "day", "hour") if hourly else ("year", "month", "day")
    digits = (1000000, 10000, 100, 1) if hourly else (10000, 100, 1)
    chronology = (372 * 24, 31 * 24, 24, 1) if hourly else (372, 31, 1)
    limits = ((1, 9999), (1, 12), (1, 31), (0, 23))[: len(component_names)]
    declarations: list[str] = []
    for row in (1, 2):
        for name, (minimum, maximum) in zip(component_names, limits, strict=True):
            label = f"{name}{row}"
            declarations.extend(
                (
                    f"(declare-const {label} Int)",
                    f"(assert (and (<= {minimum} {label}) (<= {label} {maximum})))",
                )
            )

    def rank(weights: tuple[int, ...], row: int) -> str:
        parts = " ".join(
            f"(* {weight} {name}{row})"
            for name, weight in zip(component_names, weights, strict=True)
        )
        return f"(+ {parts})"

    # Decimal positional rank also models bytewise string order for a fixed
    # width and fixed separators. The civil rank needn't model elapsed time:
    # lexicographic order holds even on this superset of valid Gregorian days.
    prelude = "\n".join(declarations)
    witness = tuple(f"{name}{row}" for row in (1, 2) for name in component_names)
    for index, first_difference in enumerate(component_names):
        prefix = "\n".join(f"(assert (= {name}1 {name}2))" for name in component_names[:index])
        # Every unequal pair has a first difference. Swapping the row
        # labels covers the greater-than direction as well.
        prove_unsat(
            f"""(set-logic QF_LIA)
{prelude}
{prefix}
(assert (< {first_difference}1 {first_difference}2))
(assert (or
  (>= {rank(digits, 1)} {rank(digits, 2)})
  (>= {rank(chronology, 1)} {rank(chronology, 2)})))""",
            claim=f"Fixed-width {'hour' if hourly else 'date'} encoding, first difference {first_difference}",
            witness=witness,
        )


def _encoded_truth(
    predicate: ops.Value, table: ir.Table, *, fmt: str, composite: bool = False
) -> _Truth:
    fields = ("day", "hour") if composite else ("point",)
    symbols = {table[name].op(): (name, f"{name}_null") for name in fields}
    for literal in predicate.find(ops.Literal):
        value = literal.value
        if type(value) in (int, str):
            text = str(value)
            tick = (
                int(text) if composite and len(text) <= 2 else _micros(datetime.strptime(text, fmt))
            )
            symbols[literal] = (_integer(tick), "false")
    return _truth(predicate, symbols)


def _instant_clock(table: ir.Table, fmt: str, zone: str) -> tuple[ir.Value, SourceTimeAuthority]:
    """Return the marked physical clock used by filter lowering."""
    zone = normalize_timezone(zone).name
    parsed, authority = source_time(
        table.point,
        axis(fmt, zone),
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone(zone, "field"),
        engine="duckdb",
    )
    # DATE layout starts at business midnight. The named/fixed offset applies
    # even when the physical DATE value contains no clock fields.
    if isinstance(parsed, ir.DateValue):
        parsed = localize(zone, timestamp(parsed))
    return encoded_time(table.point, parsed, fmt, zone), authority


@pytest.mark.parametrize(
    ("fmt", "physical", "zone", "start", "end"),
    (
        (_DATE, "int64", "UTC", "2024-02-28T12:00+00:00", "2024-03-01T12:00+00:00"),
        (_DATE, "string", "+05:30", "1969-12-30T20:00+00:00", "1970-01-01T20:00+00:00"),
        (_DASHED, "string", "UTC", "1969-12-31T00:00+00:00", "1970-01-02T00:00+00:00"),
        (_HOUR, "int64", "UTC", "2024-02-29T22:45+00:00", "2024-03-01T01:15+00:00"),
        (_HOUR, "string", "-08:00", "1970-01-01T06:30+00:00", "1970-01-01T10:30+00:00"),
    ),
)
def test_authored_encoded_filter_preserves_canonical_membership(
    fmt: str, physical: str, zone: str, start: str, end: str
) -> None:
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    offset = (
        timezone.utc if zone == "UTC" else datetime.fromisoformat(f"2000-01-01T00:00{zone}").tzinfo
    )
    assert offset is not None
    first_local, last_local = (endpoint.astimezone(offset) for endpoint in (first, last))
    step = _HOUR_TICK if fmt == _HOUR else _DAY_TICK
    lower, upper = (_micros(point.replace(tzinfo=None)) for point in (first_local, last_local))
    assert first_local.utcoffset() == last_local.utcoffset()
    table = ibis.table({"point": physical}, name="canonical_events")
    marked, authority = _instant_clock(table, fmt, zone)
    authored = table.filter((ibis.literal(first) <= marked) & (marked < ibis.literal(last)))
    lowered = optimize_time(authored, "duckdb")
    (node,) = lowered.op().find(ops.Filter)
    assert not any(predicate.find(EncodedTime) for predicate in node.predicates)
    actual = tuple(_encoded_truth(predicate, table, fmt=fmt) for predicate in node.predicates)
    truth = _Truth(
        f"(and {' '.join(p.yes for p in actual)})",
        f"(or {' '.join(p.no for p in actual)})",
    )
    _, declarations, variables = _binding(table, ("point",), step=step)
    direct = source_time_predicate(
        table.point, axis(fmt, normalize_timezone(zone).name), authority, start=first, end=last
    )
    assert direct is not None
    expected = f"(and (not point_null) (<= {_integer(lower)} point) (< point {_integer(upper)}))"
    for route, selection in (
        ("authored filter rewrite", truth),
        ("direct range", _encoded_truth(direct.op(), table, fmt=fmt)),
    ):
        _prove_selection(
            declarations,
            selection,
            expected,
            claim=f"{fmt}/{physical} {zone} half-open {route}",
            witness=variables,
            known="(not point_null)",
        )
    for point in (first_local, last_local):
        civil = point.replace(tzinfo=None)
        floor = civil.replace(minute=0, second=0, microsecond=0)
        if fmt != _HOUR:
            floor = floor.replace(hour=0)
        delta = timedelta(microseconds=step)
        assert ordered_time_literal(civil, fmt) == (
            floor if civil == floor else floor + delta
        ).strftime(fmt)
        assert ordered_time_literal(civil, fmt, strict=True) == (floor + delta).strftime(fmt)


@pytest.mark.parametrize("prefix_type", ["date", "string", "int64"])
@pytest.mark.parametrize("hour_type", ["int8", "string"])
@pytest.mark.parametrize(
    ("start", "end", "same_day"),
    [
        ("2024-02-29T01:01+00:00", "2024-02-29T04:01+00:00", True),
        ("2024-02-29T23:01+00:00", "2024-03-02T00:00+00:00", False),
    ],
)
def test_date_hour_filter_ir_keeps_complete_clock_and_nullable_value(
    prefix_type: str, hour_type: str, start: str, end: str, same_day: bool
) -> None:
    table = ibis.table({"day": prefix_type, "hour": hour_type}, name="date_hour")
    axis_day = (
        replace(axis(_DATE), parse=DateParse(timezone="UTC"))
        if prefix_type == "date"
        else axis(_DATE)
    )
    day, _ = source_time(
        table.day,
        axis_day,
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone("UTC", "field"),
        engine="duckdb",
    )
    axis_hour = replace(axis(_HOUR), parse=HourPrefixParse(axis_day.ref.path))
    _, authority = source_time(
        table.hour,
        axis_hour,
        boundary_timezone="UTC",
        prefix=day,
        source_timezone=DeclaredSourceTimezone("UTC", "field", axis_day.ref.path),
        engine="duckdb",
    )
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    value, filtered = (
        source_time_predicate(
            table.hour,
            axis_hour,
            authority,
            start=first,
            end=last,
            prefix=table.day,
            prefix_axis=axis_day,
            engine="duckdb",
            use=use,
        )
        for use in ("value", "filter")
    )
    assert value is not None and filtered is not None
    assert bool(filtered.op().find(ops.IfElse)) is not same_day
    declarations = f"""(declare-const day Int)
(declare-const day_null Bool)
(declare-const hour Int)
(declare-const hour_null Bool)
(assert (and (= (mod day {_DAY_TICK}) 0) (<= 0 hour) (< hour 24)))
(define-fun clock () Int (+ day (* hour {_HOUR_TICK})))"""
    known = "(not (or day_null hour_null))"
    expected = (
        f"(and {known} (<= {_integer(_micros(first))} clock) (< clock {_integer(_micros(last))}))"
    )
    witness = ("day", "day_null", "hour", "hour_null", "clock")
    for label, actual, knownness in (("value", value, known), ("filter", filtered, None)):
        _prove_selection(
            declarations,
            _encoded_truth(actual.op(), table, fmt=_DATE, composite=True),
            expected,
            claim=f"{prefix_type}+hour {label}/{start}/{end}",
            witness=witness,
            known=knownness,
        )
    if not same_day:
        guard = _hour_date_guard(
            table.hour,
            axis_hour,
            authority,
            start=first,
            end=last,
            prefix=table.day,
            prefix_axis=axis_day,
            engine="duckdb",
        )
        assert guard is not None
        truth = _encoded_truth(guard.op(), table, fmt=_DATE, composite=True)
        prove_unsat(
            f"(set-logic QF_LIA)\n{declarations}\n(assert (and {expected} (not {truth.yes})))",
            claim="Date-only guard cannot suppress a passing complete clock",
            witness=witness,
        )


@pytest.mark.parametrize(
    "transition",
    (
        "2026-03-08T07:00+00:00",  # New York gap begins.
        "2026-11-01T06:00+00:00",  # The second 01:00 begins.
    ),
)
def test_named_zone_requires_the_complete_endpoint_stability_neighborhood(transition: str) -> None:
    point = datetime.fromisoformat(transition)
    zone = "America/New_York"
    assert endpoint_rewrite_safe(point - timedelta(hours=49), zone, "duckdb")
    assert not endpoint_rewrite_safe(point - timedelta(hours=48), zone, "duckdb")
    assert not endpoint_rewrite_safe(point, zone, "duckdb")
    assert not endpoint_rewrite_safe(point + timedelta(hours=47), zone, "duckdb")
    assert endpoint_rewrite_safe(point + timedelta(hours=49), zone, "duckdb")
    assert endpoint_rewrite_safe(point, "UTC-05:00", "duckdb")


def test_ambiguous_encoded_endpoint_retains_the_duckdb_native_witness() -> None:
    zone = "America/New_York"
    first = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)
    last = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    encoded = ibis.memtable(
        {"id": [0, 1, 2, 3], "point": ["2026110100", "2026110101", "2026110102", None]},
        schema={"id": "int64", "point": "string"},
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("fold_hours", encoded)
        # The engine's approved parser chooses one fixed interpretation of
        # 01:00. The earlier folded endpoint cannot be replaced with a civil
        # string cutoff merely because its wall-clock spelling is ordered.
        raw_sql = (
            "SELECT id FROM fold_hours WHERE "
            "timezone('America/New_York', try_strptime(point, '%Y%m%d%H')) >= ? AND "
            "timezone('America/New_York', try_strptime(point, '%Y%m%d%H')) < ? ORDER BY id"
        )
        expected = [row[0] for row in backend.con.execute(raw_sql, [first, last]).fetchall()]
        assert expected == [0]
        clock, authority = _instant_clock(table, _HOUR, zone)
        authored = table.filter((clock >= ibis.literal(first)) & (clock < ibis.literal(last)))
        bound = lower_temporal(authored.order_by("id"), "duckdb")
        assert backend.execute(bound)["id"].tolist() == expected
        contract = axis(_HOUR, zone)
        direct = source_time_predicate(table.point, contract, authority, start=first, end=last)
        assert direct is not None
        result = backend.execute(lower_temporal(table.filter(direct).order_by("id"), "duckdb"))
        assert result["id"].tolist() == expected
        assert last.astimezone(ZoneInfo(zone)).fold == 0
        assert not endpoint_rewrite_safe(last, zone, "duckdb")
        assert not endpoint_rewrite_safe(last + timedelta(hours=1), zone, "duckdb")
    finally:
        backend.disconnect()


def test_injective_reindexing_preserves_each_contribution_occurrence_and_null_key() -> None:
    prove_unsat(
        """(set-logic QF_UFLIA)
(declare-const row Int)
(declare-const target Int)
(declare-const row_null Bool)
(declare-const target_null Bool)
(declare-fun bucket (Int) Int)
(define-fun same_raw () Bool
  (and (= row_null target_null) (or row_null (= row target))))
(define-fun same_bucket () Bool
  (and (= row_null target_null) (or row_null (= (bucket row) (bucket target)))))
(assert (=> (and (not row_null) (not target_null) (= (bucket row) (bucket target)))
            (= row target)))
(assert (distinct same_raw same_bucket))""",
        claim="An injective null-preserving reindex has the same multiset preimage",
        witness=("row", "target", "row_null", "target_null", "same_raw", "same_bucket"),
    )


@pytest.mark.parametrize(
    ("zone", "start", "end", "elapsed_hours"),
    (
        ("UTC", "2024-02-28", "2024-03-02", (24, 24, 24)),
        ("America/New_York", "2026-03-07", "2026-03-10", (24, 23, 24)),
        ("America/New_York", "2026-10-31", "2026-11-03", (24, 25, 24)),
    ),
)
def test_native_day_key_deferral_uses_actual_calendar_geometry(
    zone: str, start: str, end: str, elapsed_hours: tuple[int, ...]
) -> None:
    calendar = grouping_binding(exact_scope(start, end, zone).by(mv.grain("day")))
    assert (
        tuple(int((right - left).total_seconds() / 3600) for left, right, _, _ in calendar.cells)
        == elapsed_hours
    )
    days = [
        (date.fromisoformat(start) + timedelta(days=index)).isoformat()
        for index in range(len(elapsed_hours))
    ]
    contract = axis(_DASHED)
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "civil_days",
            ibis.memtable({"point": [*days, days[0], None]}, schema={"point": "string"}),
        )
        parsed, authority = source_time(
            table.point,
            contract,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone(zone, "field"),
            engine="duckdb",
        )
        deferred = deferred_day_key(
            table, table.point, calendar, zone, "duckdb", encoded=(contract, authority)
        )
        assert (deferred is not None) == all(width == 24 for width in elapsed_hours)
        assert (
            deferred_day_key(
                table,
                table.point.concat(""),
                calendar,
                zone,
                "duckdb",
                encoded=(contract, authority),
            )
            is None
        )
        assert (
            deferred_day_key(
                table,
                table.point,
                calendar,
                zone,
                "duckdb",
                encoded=(axis("%d/%m/%Y"), authority),
            )
            is None
        )
        if deferred is None:
            return
        marked = table.mutate(bucket=deferred)
        grouped = marked.group_by("bucket").aggregate(rows=marked.count()).order_by("bucket")
        result = lower_temporal(grouped, "duckdb")
        (aggregate,) = result.op().find(ops.Aggregate)
        assert aggregate.groups["bucket"].dtype.is_string()
        native_key = builtin_key(parsed, calendar)
        native = table.mutate(bucket=native_key)
        expected = native.group_by("bucket").aggregate(rows=native.count()).order_by("bucket")
        pd.testing.assert_frame_equal(
            backend.execute(result), backend.execute(lower_temporal(expected, "duckdb"))
        )
    finally:
        backend.disconnect()


def test_permissive_parser_is_a_counterexample_without_the_canonical_declaration() -> None:
    # This is a witness against dropping the theorem's domain premise.
    # Noncanonical texts are outside the authored encoding contract.
    backend = ibis.duckdb.connect()
    try:
        rows = backend.raw_sql(
            """SELECT count(DISTINCT token), count(DISTINCT try_strptime(token, '%Y-%m-%d'))
            FROM (VALUES ('2026-2-3'), ('2026-02-03')) AS source(token)"""
        ).fetchall()
    finally:
        backend.disconnect()
    assert rows == [(2, 1)]


def test_civil_grains_form_partial_order_with_a_real_cross_year_week() -> None:
    units: tuple[Literal["day", "week", "month", "quarter", "year"], ...] = (
        "day",
        "week",
        "month",
        "quarter",
        "year",
    )
    expected = {
        "day": set(units),
        "week": {"week"},
        "month": {"month", "quarter", "year"},
        "quarter": {"quarter", "year"},
        "year": {"year"},
    }
    for inner in units:
        assert {
            outer for outer in units if grain_nests(mv.grain(inner), mv.grain(outer))
        } == expected[inner]
    assert grain_nests(mv.grain("hour", count=2), mv.grain("hour", count=6))
    assert not grain_nests(mv.grain("hour", count=2), mv.grain("hour", count=3))
    grid = grouping_binding(exact_scope("2024-12-30", "2025-01-06", "UTC").by(mv.grain("week")))
    ((left, right, _, _),) = grid.cells
    assert (left.date(), right.date()) == (date(2024, 12, 30), date(2025, 1, 6))
    assert (left.year, left.month) != (right.year, right.month)


@pytest.mark.parametrize(
    ("month", "start", "hours"),
    (
        (3, "2026-03-07", (24, 23, 24)),
        (11, "2026-10-31", (24, 25, 24)),
    ),
)
def test_five_window_rules_use_exact_civil_and_elapsed_endpoints(
    month: int, start: str, hours: tuple[int, ...]
) -> None:
    zone = "America/New_York"
    tz = ZoneInfo(zone)
    civil = datetime.fromisoformat(start).replace(tzinfo=tz)
    final = (civil + timedelta(days=len(hours))).date()
    grid = grouping_binding(exact_scope(start, final.isoformat(), zone).by(mv.grain("day")))
    cells = grid.cells
    boundaries = tuple(
        (civil + timedelta(days=day)).astimezone(timezone.utc) for day in range(-1, len(hours) + 1)
    )
    assert tuple((cell[0], cell[1]) for cell in cells) == tuple(pairwise(boundaries[1:]))
    assert tuple(int((cell[1] - cell[0]).total_seconds() / 3600) for cell in cells) == hours
    period = ObservationPeriod(None, anchor_from_datetime(boundaries[-1]), zone)
    fixed = datetime(2026, 2, 1, tzinfo=timezone.utc)
    duration = timedelta(hours=24)
    expected_resets = tuple(
        datetime(end.astimezone(tz).year, end.astimezone(tz).month, 1, tzinfo=tz).astimezone(
            timezone.utc
        )
        if end.astimezone(tz).day != 1
        else datetime(2026, month - 1, 1, tzinfo=tz).astimezone(timezone.utc)
        for end in boundaries[2:]
    )
    checks = (
        (mv.all_history(), (None,) * len(cells)),
        (mv.since(start=fixed), (fixed,) * len(cells)),
        (mv.to_date(grain=mv.grain("month")), expected_resets),
        (mv.trailing_duration(duration=duration), tuple(end - duration for end in boundaries[2:])),
        (mv.trailing_periods(count=2), boundaries[:-2]),
        (mv.trailing_periods(count=1), boundaries[1:-1]),
    )
    for rule, expected in checks:
        actual = tuple(
            _window_start(rule, period, grid, cells, index, cell[1], None)
            for index, cell in enumerate(cells)
        )
        assert actual == expected
        finite = tuple(value for value in actual if value is not None)
        assert finite == tuple(sorted(finite))
        assert all(
            lower is None or lower < cell[1] for lower, cell in zip(actual, cells, strict=True)
        )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    (
        ("2024-02-29T23:59:59.999999+00:00", "2024-02-01"),
        ("2024-03-01T00:00:00+00:00", "2024-02-01"),
        ("2024-03-01T00:00:00.000001+00:00", "2024-03-01"),
        ("2024-03-31T00:00:00+00:00", "2024-03-01"),
        ("2024-04-01T00:00:00+00:00", "2024-03-01"),
    ),
)
def test_scalar_month_reset_owns_leap_day_and_complete_month_end(
    endpoint: str, expected: str
) -> None:
    end = datetime.fromisoformat(endpoint)
    period = ObservationPeriod(None, anchor_from_datetime(end), "UTC")
    actual = _window_start(mv.to_date(grain=mv.grain("month")), period, None, (), 0, end, None)
    assert actual == datetime.fromisoformat(expected).replace(tzinfo=timezone.utc)


def test_trailing_periods_rejects_selection_of_a_partial_calendar_bucket() -> None:
    grid = grouping_binding(
        exact_scope("2024-02-29T12:00:00Z", "2024-03-01", "UTC").by(mv.grain("day"))
    )
    ((_, _, _, endpoint),) = grid.cells
    period = ObservationPeriod(None, endpoint, "UTC")
    with pytest.raises(CoreRuleError) as failure:
        _window_start(mv.trailing_periods(count=2), period, grid, grid.cells, 0, endpoint, None)
    assert failure.value.expected == "complete calendar buckets for trailing_periods"


def test_window_and_per_do_not_commute_in_public_observation(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "windows.duckdb"
    connection = ibis.duckdb.connect(database=str(database))
    try:
        connection.raw_sql(
            """CREATE TABLE facts (id BIGINT, point TIMESTAMPTZ, amount BIGINT);
            INSERT INTO facts VALUES
              (1, '2026-01-02T12:00:00+00', 20), (2, '2026-01-03T12:00:00+00', 30),
              (3, '2026-01-05T12:00:00+00', 50)"""
        )
    finally:
        connection.disconnect()
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test')",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms

facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'),
                  source=md.table('facts'), primary_key=['id'])
point = ms.time_dimension_column(name='point', entity=facts, column='point', parse=ms.timestamp())
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD',
                           additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("formal-windows", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    revenue = amount.sum(time=point)
    day = mv.grain("day")
    rule = mv.trailing_duration(duration=timedelta(days=3))
    scope = session.time_scope(start="2026-01-03", end="2026-01-06")
    result = (
        session.observe(
            {
                "outer": revenue.per(day).mean().window(rule),
                "inner": revenue.window(rule).per(day).mean(),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    # Outside per: Jan 3 and Jan 5 are the two samples in the endpoint window.
    # Inside per: those same two observed report samples own separate endpoints
    # and see (20 + 30) and (30 + 50), respectively.
    assert result["outer"].tolist() == [40]
    assert result["inner"].tolist() == [65]
    reductions = (
        session.observe(
            {
                "history": revenue.window(mv.all_history()),
                "since": revenue.window(mv.since(start=session.instant("2026-01-04"))),
                "month": revenue.window(mv.to_date(grain=mv.grain("month"))),
                "elapsed": revenue.window(rule),
            },
            where=scope,
        )
        .compute()
        .to_pandas()
    )
    assert reductions.to_dict(orient="list") == {
        "history": [100],
        "since": [50],
        "month": [100],
        "elapsed": [80],
    }
    periods = (
        session.observe(
            {"periods": revenue.window(mv.trailing_periods(count=2))},
            by=(scope.by(day),),
        )
        .compute()
        .to_pandas()
    )
    assert periods["periods"].tolist() == [50, 30, 50]
