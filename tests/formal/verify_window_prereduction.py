"""Verify window bucket membership and dense-grid pre-reduction independently of planning."""

from collections import defaultdict
from collections.abc import Iterable
from typing import Literal, TypeAlias

import ibis
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.

from tests.formal._summary_monoids import result

WindowRule: TypeAlias = Literal["trailing", "all_history", "since", "to_date"]
Fact: TypeAlias = tuple[str, int, int | None, int | None]
Receipt: TypeAlias = tuple[str, int, int, int, int | None, bool, int, int, int | None, int | None]
_FACTS: tuple[Fact, ...] = (
    ("A", -2, 100, 1),
    ("A", -1, 11, -1),
    ("A", 0, 10, 1),
    ("A", 2, None, 2),
    ("A", 4, 30, 2),
    ("A", 4, 40, 1),
    ("B", 1, None, 1),
    ("B", 5, 17, 0),
    ("C", -2, None, 8),
)
_ORDINALS = tuple(range(-2, 7))


def test_aligned_native_cell_contains_only_included_or_excluded_rows() -> None:
    lo, hi, start, end, instant = z3.Ints("window_start window_end cell_start cell_end instant")
    intersects = z3.And(lo <= start, end <= hi)
    aligned = z3.Or(end <= lo, hi <= start, intersects)
    result(
        "a native calendar cell fits a half-open window exactly when its rows do",
        (z3.And(lo <= instant, instant < hi) == intersects),
        lo < hi,
        start < end,
        start <= instant,
        instant < end,
        aligned,
        logic="QF_LIA",
    )
    result(
        "a window can bisect even a day-shaped cell without boundary alignment",
        (z3.And(lo <= instant, instant < hi) == intersects),
        lo == start + 1,
        end == start + 23 * 60,
        hi == end,
        instant == lo,
        logic="QF_LIA",
        counterexample=True,
    )


def test_bucket_summary_completes_empty_unknown_and_strict_states() -> None:
    n, h, n_prior, h_prior = z3.Ints("rows valid earlier_rows earlier_valid")
    total, earlier_total = z3.Reals("mass earlier_mass")
    bad, earlier_bad = z3.Bools("strict_failure earlier_failure")
    combined = (n + n_prior, h + h_prior, total + earlier_total, z3.Or(bad, earlier_bad))
    empty = n == 0
    valid = h > 0
    had_rows = n_prior > 0
    had_valid = h_prior > 0
    result(
        "preserve distinct empty and all-NA window states while combining any prefix",
        z3.And(
            (combined[0] > 0) == z3.Or(z3.Not(empty), had_rows),
            (combined[1] > 0) == z3.Or(valid, had_valid),
            (combined[3] == (combined[0] != combined[1])),
            z3.Implies(z3.And(empty, had_rows), combined[0] > 0),
            z3.Implies(z3.And(combined[0] > 0, combined[1] == 0), z3.Or(bad, earlier_bad)),
        ),
        n >= h,
        h >= 0,
        n_prior >= h_prior,
        h_prior >= 0,
        bad == (n != h),
        earlier_bad == (n_prior != h_prior),
        logic="QF_LIRA",
    )


def _lower_bound(rule: WindowRule, endpoint: int) -> int:
    if rule == "trailing":
        return endpoint - 2
    if rule == "to_date":
        return -2 if endpoint < 0 else 0
    return -1 if rule == "since" else -2


def _independent_windows(facts: Iterable[Fact], rule: WindowRule) -> list[Receipt]:
    by_key: defaultdict[str, list[Fact]] = defaultdict(list)
    for item in facts:
        by_key[item[0]].append(item)
    receipts: list[Receipt] = []
    for key, rows in sorted(by_key.items()):
        for endpoint in range(7):
            qualifying = [row for row in rows if _lower_bound(rule, endpoint) <= row[1] <= endpoint]
            if not qualifying:
                continue
            values = [row[2] for row in qualifying if row[2] is not None]
            pairs = [
                (row[2], row[3]) for row in qualifying if row[2] is not None and row[3] is not None
            ]
            paired_mass = sum(value * weight for value, weight in pairs)
            paired_weight = sum(weight for _, weight in pairs)
            receipts.append(
                (
                    key,
                    endpoint,
                    len(qualifying),
                    len(values),
                    sum(values) if values else None,
                    len(values) != len(qualifying),
                    paired_mass,
                    paired_weight,
                    min(values) if values else None,
                    max(values) if values else None,
                )
            )
    return receipts


@pytest.mark.parametrize("rule", ("trailing", "all_history", "since", "to_date"))
def test_dense_native_window_keeps_sparse_support_and_payloads(rule: WindowRule) -> None:
    bound = "2 PRECEDING" if rule == "trailing" else "UNBOUNDED PRECEDING"
    partition = "source_key, (ordinal < 0)" if rule == "to_date" else "source_key"
    earliest = -1 if rule == "since" else -2
    engine = ibis.duckdb.connect()
    try:
        engine.create_table(
            "fact",
            ibis.memtable(
                _FACTS,
                schema={
                    "source_key": "string",
                    "ordinal": "int64",
                    "value": "int64",
                    "weight": "int64",
                },
            ),
        )
        engine.create_table(
            "bucket", ibis.memtable({"ordinal": _ORDINALS}, schema={"ordinal": "int64"})
        )
        native = (
            engine.sql(
                f"""
            WITH state AS (
                SELECT source_key, ordinal, COUNT(*) AS n, COUNT(value) AS h,
                       SUM(value) AS mass, BOOL_OR(value IS NULL) AS bad,
                       SUM(value * weight) AS pair_mass,
                       SUM(weight) FILTER (WHERE value IS NOT NULL) AS pair_weight,
                       MIN(value) AS low, MAX(value) AS high
                FROM fact WHERE ordinal >= {earliest} GROUP BY source_key, ordinal
            ), dense AS (
                SELECT keys.source_key, bucket.ordinal,
                       COALESCE(state.n, 0) AS n, COALESCE(state.h, 0) AS h,
                       state.mass, COALESCE(state.bad, FALSE) AS bad,
                       state.pair_mass, state.pair_weight, state.low, state.high
                FROM (SELECT DISTINCT source_key FROM fact) keys CROSS JOIN bucket
                LEFT JOIN state USING (source_key, ordinal)
                WHERE bucket.ordinal >= {earliest}
            ), folded AS (
                SELECT source_key, ordinal, SUM(n) OVER w AS n, SUM(h) OVER w AS h,
                       SUM(mass) OVER w AS mass, BOOL_OR(bad) OVER w AS bad,
                       COALESCE(SUM(pair_mass) OVER w, 0) AS pair_mass,
                       COALESCE(SUM(pair_weight) OVER w, 0) AS pair_weight,
                       MIN(low) OVER w AS low, MAX(high) OVER w AS high
                FROM dense
                WINDOW w AS (PARTITION BY {partition} ORDER BY ordinal ROWS BETWEEN {bound} AND CURRENT ROW)
            )
            SELECT * FROM folded WHERE ordinal >= 0 AND n > 0 ORDER BY source_key, ordinal
            """,
            )
            .to_pyarrow()
            .to_pylist()
        )
        rows = [tuple(row.values()) for row in native]
    finally:
        engine.disconnect()
    assert rows == _independent_windows(_FACTS, rule)
    assert any(row[:2] == ("A", 1) for row in rows)
