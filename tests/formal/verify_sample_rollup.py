"""Isolated preconditions for associating sample reduction and native Attribute groups."""

from dataclasses import dataclass
from typing import Literal

import duckdb
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional solver.

from tests.analysis.materialization.test_attribute_grouping_sets import (
    test_attribute_grouping_separates_native_null_class_from_parent as test_attribute_grouping_separates_native_null_class_from_parent,
)
from tests.analysis.materialization.test_query_optimization import (
    optimization_session as optimization_session,
)
from tests.analysis.materialization.test_query_optimization import (
    test_implicit_per_collapse_keeps_native_view_sample_failure as test_implicit_per_collapse_keeps_native_view_sample_failure,
)
from tests.analysis.materialization.test_query_optimization import (
    test_implicit_per_collapse_keeps_physical_extremum_carrier_failure as test_implicit_per_collapse_keeps_physical_extremum_carrier_failure,
)
from tests.analysis.materialization.test_query_optimization import (
    test_implicit_per_collapse_removes_only_the_sample_group as test_implicit_per_collapse_removes_only_the_sample_group,
)
from tests.formal._summary_monoids import result


@dataclass(frozen=True)
class Cell:
    payload: z3.ArithRef
    present: z3.BoolRef
    valid: z3.BoolRef


def _total(rows: tuple[Cell, ...]) -> z3.ArithRef:
    return z3.Sum(*(z3.If(row.present & row.valid, row.payload, 0) for row in rows))


def _extreme(left: Cell, right: Cell, reducer: Literal["min", "max"]) -> Cell:
    left_valid = left.present & left.valid
    right_valid = right.present & right.valid
    compare = left.payload <= right.payload if reducer == "min" else left.payload >= right.payload
    keep_left = left_valid & (~right_valid | compare)
    return Cell(
        z3.If(keep_left, left.payload, right.payload),
        left.present | right.present,
        left_valid | right_valid,
    )


@pytest.fixture
def bags() -> tuple[Cell, Cell, Cell, Cell]:
    first, second, third, fourth = (
        Cell(z3.Real(f"x_{i}"), z3.Bool(f"present_{i}"), z3.Bool(f"valid_{i}")) for i in range(4)
    )
    return first, second, third, fourth


def test_implicit_field_sum_preserves_outer_empty_and_all_null(
    bags: tuple[Cell, Cell, Cell, Cell],
) -> None:
    child = tuple(
        Cell(
            _total(rows),
            z3.Or(*(row.present for row in rows)),
            z3.Or(*(row.present & row.valid for row in rows)),
        )
        for rows in (bags[:2], bags[2:])
    )
    direct_has = z3.Or(*(row.present for row in bags))
    direct_valid = z3.Or(*(row.present & row.valid for row in bags))
    child_has = z3.Or(*(row.present for row in child))
    child_valid = z3.Or(*(row.present & row.valid for row in child))
    result(
        "Implicit single-valued sample SUM can roll up exact child states",
        z3.And(
            _total(child) == _total(bags),
            (child_has & ~child_valid) == (direct_has & ~direct_valid),
            child_has == direct_has,
        ),
        logic="QF_LRA",
    )


@pytest.mark.parametrize("counts_entities", (False, True))
def test_implicit_field_counts_compose_by_exact_addition(
    bags: tuple[Cell, Cell, Cell, Cell], counts_entities: bool
) -> None:
    count = tuple(
        z3.If(row.present if counts_entities else row.present & row.valid, 1, 0) for row in bags
    )
    result(
        "Partition counts sum to the original exact count",
        z3.Sum(*count) == z3.Sum(*count[:2]) + z3.Sum(*count[2:]),
        logic="QF_LIA",
    )


@pytest.mark.parametrize("reducer", ("min", "max"))
def test_implicit_field_extrema_keep_the_exact_validity_and_carrier(
    bags: tuple[Cell, Cell, Cell, Cell], reducer: Literal["min", "max"]
) -> None:
    a, b, c, d = bags
    nested = _extreme(_extreme(a, b, reducer), _extreme(c, d, reducer), reducer)
    direct = _extreme(_extreme(_extreme(a, b, reducer), c, reducer), d, reducer)
    result(
        "Partition extrema preserve any represented value and empty state",
        z3.And(
            nested.present == direct.present,
            nested.valid == direct.valid,
            z3.Implies(nested.valid, nested.payload == direct.payload),
        ),
        logic="QF_LRA",
    )


def test_mixed_extrema_explicit_empty_samples_and_multivalue_fanout_refuse() -> None:
    first, second = z3.Reals("first second")
    minimum = z3.If(first < second, first, second)
    maximum = z3.If(first < second, second, first)
    result(
        "min then max need not mean max",
        minimum == maximum,
        first < second,
        logic="QF_LRA",
        counterexample=True,
    )
    x = z3.Int("fact_once")
    result(
        "A fact with two assigned labels can be counted twice",
        x + x == x,
        x > 0,
        logic="QF_LIA",
        counterexample=True,
    )
    with duckdb.connect() as conn:  # noqa: TID251 - Isolated literal SQL oracle.
        observed = conn.execute(
            "SELECT sum(amount) FROM (VALUES (NULL::INTEGER)) t(amount)"
        ).fetchone()
        explicit = conn.execute(
            "SELECT SUM(child) FROM (VALUES (NULL::INTEGER), (0)) samples(child)"
        ).fetchone()
    assert observed == (None,)
    assert explicit == (0,)


def test_grouping_bits_distinguish_real_null_categories_from_rolled_up_parents() -> None:
    with duckdb.connect() as conn:  # noqa: TID251 - Isolated literal SQL oracle.
        rows = conn.execute(
            """
            WITH source (parent, fine, amount) AS
                (VALUES ('P', NULL, 12), ('P', 'A', 4), ('Q', NULL, 7))
            SELECT parent, fine, GROUPING(fine) AS rolled_up, SUM(amount)
            FROM source GROUP BY GROUPING SETS ((parent, fine), (parent))
            """
        ).fetchall()
    assert set(rows) == {
        ("P", None, 0, 12),
        ("P", "A", 0, 4),
        ("P", None, 1, 16),
        ("Q", None, 0, 7),
        ("Q", None, 1, 7),
    }
    assert sum(fine is None for _, fine, _, _ in rows) == 4
    assert sum(rolled_up == 1 for _, _, rolled_up, _ in rows) == 2
