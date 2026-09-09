"""Independent row-method oracles, without serializing local intermediate frames."""

import time
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from marivo.analysis.materialization.local import (
    LocalBudget,
    LocalPolicy,
    execute_suffix,
)
from marivo.analysis.observation.predicates import all_of, any_of, eq, gt, is_in, is_null, not_
from marivo.analysis.operators.row import execute_row
from tests.lazy_local_fixtures import REVENUE, primary_frame, row_call, setup_local


@pytest.mark.parametrize("order", ["ascending", "descending"])
@pytest.mark.parametrize("ties", ["ordinal", "dense", "min", "max"])
def test_rank_has_independent_finite_tie_oracle(
    tmp_path: Path,
    order: Literal["ascending", "descending"],
    ties: Literal["ordinal", "dense", "min", "max"],
) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(
        source, [20, 20, 10, None, float("nan"), float("inf"), -float("inf"), 10, 30]
    )
    before = frame.copy(deep=True)
    ranked = source.rank(source.fields.metric(REVENUE), order=order, ties=ties)
    result = execute_row(frame, row_call(ranked))
    expected_ids = (
        [3, 8, 1, 2, 9, 4, 5, 6, 7] if order == "ascending" else [9, 1, 2, 3, 8, 4, 5, 6, 7]
    )
    ranks = {
        "ascending": {
            "ordinal": [1, 2, 3, 4, 5],
            "dense": [1, 1, 2, 2, 3],
            "min": [1, 1, 3, 3, 5],
            "max": [2, 2, 4, 4, 5],
        },
        "descending": {
            "ordinal": [1, 2, 3, 4, 5],
            "dense": [1, 2, 2, 3, 3],
            "min": [1, 2, 2, 4, 4],
            "max": [1, 3, 3, 5, 5],
        },
    }
    assert result["entity_identity"].tolist() == [(i,) for i in expected_ids]
    assert result["rank"].iloc[:5].tolist() == ranks[order][ties]
    assert result["rank"].iloc[5:].isna().all()
    pd.testing.assert_frame_equal(frame, before)


def test_predicates_preserve_unknown_through_not_and_nested_boolean(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(source, [10, 20, None, 30])
    predicates = [
        (not_(is_in(REVENUE, [10, 20])), [4]),
        (any_of(is_null(REVENUE), eq(REVENUE, 20)), [2, 3]),
        (all_of(not_(eq(REVENUE, 10)), gt(REVENUE, 15)), [2, 4]),
    ]
    for predicate, expected in predicates:
        result = execute_row(frame, row_call(source.where(predicate)))
        assert result["entity_identity"].tolist() == [(i,) for i in expected]


def test_direct_handoffs_and_consecutive_limit_prefixes(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(source, [10, 20, None, 30])
    first = source.rank(source.fields.metric(REVENUE))
    second = first.limit(3)
    third = second.limit(2)
    budget = LocalBudget(LocalPolicy(), time.monotonic() + 60, live_bytes=1024)
    result, handoffs = execute_suffix(
        frame, tuple(row_call(value) for value in (first, second, third)), budget
    )
    assert result["revenue"].tolist() == [30, 20]
    assert (
        handoffs[0][0] == id(frame)
        and handoffs[0][1] == handoffs[1][0]
        and handoffs[1][1] == handoffs[2][0]
    )


def test_nan_comparison_matches_the_admitted_source_adapter(tmp_path: Path) -> None:
    import duckdb

    from marivo.analysis.observation.predicates import gte, lt, lte, not_eq

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    frame = primary_frame(source, [10.0, float("nan"), float("inf"), -float("inf"), None])
    for builder, sql in ((eq, "="), (not_eq, "<>"), (lt, "<"), (lte, "<="), (gt, ">"), (gte, ">=")):
        with duckdb.connect() as connection:
            expected = connection.execute(
                f"SELECT id FROM (VALUES (1,10.0),(2,'NaN'::DOUBLE),(3,'Infinity'::DOUBLE),(4,'-Infinity'::DOUBLE),(5,NULL)) AS t(id,value) WHERE value {sql} 10 ORDER BY id"
            ).fetchall()
        result = execute_row(frame, row_call(source.where(builder(REVENUE, 10))))
        assert result["entity_identity"].tolist() == expected


@pytest.mark.parametrize("direction", ["ascending", "descending"])
@pytest.mark.parametrize("nulls", ["first", "last"])
def test_sort_and_validation_share_direction_and_null_contract(
    tmp_path: Path,
    direction: Literal["ascending", "descending"],
    nulls: Literal["first", "last"],
) -> None:
    from dataclasses import replace

    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.local import validate_frame
    from marivo.analysis.operators.row import ordered

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    ranked = source.rank(source.fields.metric(REVENUE))
    frame = execute_row(primary_frame(source, [30.0, None, 10.0]), row_call(ranked))
    ordering = ranked.row_set_contract.ordering
    assert isinstance(ordering, d._OrderedOrdering)
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=ranked.row_set_contract.cardinality,
        ordering=d._ordered_ordering(
            (
                replace(ordering.terms[0], _token=d._CORE_TOKEN, direction=direction, nulls=nulls),
                *ordering.terms[1:],
            )
        ),
    )
    result = ordered(frame, ranked.row_contract, rows)
    finite = [(1,), (3,)] if direction == "ascending" else [(3,), (1,)]
    expected = [(2,), *finite] if nulls == "first" else [*finite, (2,)]
    assert result["entity_identity"].tolist() == expected
    validate_frame(result, ranked.row_contract, rows)
    with pytest.raises(MaterializationError, match="invalid local order"):
        validate_frame(result.iloc[::-1], ranked.row_contract, rows)


def test_metric_rejects_duplicate_sampling_only_parts(tmp_path: Path) -> None:
    import pyarrow as pa

    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.materialization.local import execute_retained_suffix
    from marivo.analysis.operators.row import PartFrame

    _, sources, _ = setup_local(tmp_path)
    metric = sources.observe(REVENUE)
    frame = primary_frame(metric, [10, 20])
    part = PartFrame(
        "population_sampling_state",
        "population_sampling_state",
        1,
        pa.schema([]),
        (),
        pd.DataFrame(),
    )
    budget = LocalBudget(LocalPolicy(), time.monotonic() + 60)
    with pytest.raises(DatasetCompilationError, match="duplicate retained role"):
        execute_retained_suffix(
            frame, (part, part), (row_call(metric.rank(metric.fields.metric(REVENUE))),), budget
        )
