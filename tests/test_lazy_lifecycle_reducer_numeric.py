"""Independent numerical references for canonical Lifecycle continuations."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.lifecycle_reducers import reduce_lifecycle
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.lifecycle import ROLES
from marivo.analysis.domains.lifecycle_reducers import (
    LifecycleReducerPayload,
    LifecycleSelectionPayload,
    in_state,
)
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, sources_without_io


@pytest.fixture
def backend() -> Iterator[Backend]:
    value = ibis.duckdb.connect()
    value.raw_sql("SET threads=1")
    try:
        yield value
    finally:
        value.disconnect()


def primary(
    backend: Backend, rows: list[tuple[int, str, datetime, datetime, str, bool]]
) -> ir.Table:
    schema = pa.schema(
        [
            ("entity_identity", pa.struct([("id", pa.int64())])),
            ("model_state", pa.string()),
            ("valid_from", pa.timestamp("us", "UTC")),
            ("valid_to", pa.timestamp("us", "UTC")),
            ("interval_status", pa.string()),
            ("left_clipped", pa.bool_()),
        ]
    )
    values = [
        {
            "entity_identity": {"id": k},
            "model_state": state,
            "valid_from": start,
            "valid_to": end,
            "interval_status": status,
            "left_clipped": clipped,
        }
        for k, state, start, end, status, clipped in rows
    ]
    return backend.create_table(
        "history_rows", pa.Table.from_pylist(values, schema=schema), temp=True
    )


def test_dwell_completed_clipped_fragments_and_exact_quantiles(backend: Backend) -> None:
    from marivo.analysis import time_scope
    from marivo.analysis.lifecycle import FromInception

    h = sources_without_io().lifecycle.replay(
        MODEL,
        window=time_scope(start=START.isoformat(), end=(START + timedelta(days=3)).isoformat()),
        seed=FromInception(),
    )
    table = primary(
        backend,
        [
            (1, "open", START, START + timedelta(days=2), "completed", True),
            (2, "open", START, START + timedelta(days=1), "completed", False),
            (3, "open", START, END, "coverage_censored", False),
            (4, "done", START, END, "right_censored", False),
        ],
    )
    result = h.dwell()
    assert isinstance(result._root, LogicalRootHandle)
    payload = result._root.payload
    assert isinstance(payload, LifecycleReducerPayload)
    compiled, checks, _ = reduce_lifecycle(table, {}, payload, freeze=lambda t: t)
    assert checks == ()
    rows = backend.to_pyarrow(compiled).to_pylist()
    opened = next(r for r in rows if r["model_state"] == "open")
    day = 86_400_000_000
    assert opened == {
        "model_state": "open",
        "interval_count": 3,
        "completed_count": 2,
        "right_censored_count": 0,
        "coverage_censored_count": 1,
        "left_clipped_completed_count": 1,
        "mean_duration": 1.5 * day,
        "median_duration": 1.5 * day,
        "p90_duration": 1.9 * day,
    }
    done = next(r for r in rows if r["model_state"] == "done")
    assert done["mean_duration"] is None and done["completed_count"] == 0


def test_fractional_microsecond_statistics_are_not_truncated(backend: Backend) -> None:
    table = primary(
        backend,
        [
            (1, "open", START, START + timedelta(microseconds=1), "completed", False),
            (2, "open", START, START + timedelta(microseconds=2), "completed", False),
        ],
    )
    value = history(sources_without_io()).dwell()
    assert isinstance(value._root, LogicalRootHandle) and isinstance(
        value._root.payload, LifecycleReducerPayload
    )
    result, _, _ = reduce_lifecycle(table, {}, value._root.payload, freeze=lambda t: t)
    row = next(r for r in backend.to_pyarrow(result).to_pylist() if r["model_state"] == "open")
    assert (row["mean_duration"], row["median_duration"], row["p90_duration"]) == (1.5, 1.5, 1.9)


def ledger(backend: Backend) -> ir.Table:
    schema = pa.schema(
        [
            ("entity_identity", pa.struct([("id", pa.int64())])),
            ("classification", pa.string()),
            ("inception_at", pa.timestamp("us", "UTC")),
            ("known_through", pa.timestamp("us", "UTC")),
        ]
    )
    return backend.create_table(
        "coverage",
        pa.Table.from_pylist(
            [
                {
                    "entity_identity": {"id": 1},
                    "classification": "seeded",
                    "inception_at": START,
                    "known_through": END,
                },
                {
                    "entity_identity": {"id": 2},
                    "classification": "coverage_censored",
                    "inception_at": None,
                    "known_through": START + timedelta(hours=4),
                },
                {
                    "entity_identity": {"id": 3},
                    "classification": "not_incepted",
                    "inception_at": None,
                    "known_through": END,
                },
            ],
            schema=schema,
        ),
        temp=True,
    )


@pytest.mark.parametrize(
    "at,unknown,known", [(START, 0, 1), (START + timedelta(hours=4), 1, 1), (END, 1, 1)]
)
def test_no_interval_members_and_exact_coverage_boundary(
    backend: Backend, at: datetime, unknown: int, known: int
) -> None:
    h = history(sources_without_io())
    table = primary(backend, [(1, "open", START, END, "right_censored", False)])
    parts = {ROLES[1]: ledger(backend)}
    summary = h.distribution(at=(at,))
    assert isinstance(summary._root, LogicalRootHandle) and isinstance(
        summary._root.payload, LifecycleReducerPayload
    )
    result, checks, _ = reduce_lifecycle(table, parts, summary._root.payload, freeze=lambda t: t)
    assert all(backend.to_pyarrow(c.expression)["violations"][0].as_py() == 0 for c in checks)
    rows = backend.to_pyarrow(result).to_pylist()
    assert [r["known_subject_count"] for r in rows] == [known, known]
    assert [r["coverage_censored_subject_count"] for r in rows] == [unknown, unknown]
    assert sum(r["subject_count"] for r in rows) == 1
    selection = h.select_subjects(in_state(ModelStateHandle(MODEL, "open"), at=at))
    assert isinstance(selection._root, LogicalRootHandle) and isinstance(
        selection._root.payload, LifecycleSelectionPayload
    )
    _, checks, _ = reduce_lifecycle(table, parts, selection._root.payload, freeze=lambda t: t)
    complete = next(c for c in checks if c.name == "lifecycle.selection.complete_membership")
    assert backend.to_pyarrow(complete.expression)["violations"][0].as_py() == unknown


def test_same_intervals_different_same_time_transition_multiplicity(backend: Backend) -> None:
    import json
    from dataclasses import asdict

    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.domains.lifecycle_reducers import history_semantics

    value = history(sources_without_io()).transitions()
    assert isinstance(value._root, LogicalRootHandle) and isinstance(
        value._root.payload, LifecycleReducerPayload
    )
    payload = value._root.payload
    original = history_semantics(payload.semantics)
    model = replace(
        original,
        _token=d._CORE_TOKEN,
        states=("A", "B", "C", "D"),
        initial="A",
        terminals=(),
        transitions=(
            ("A", "trigger_1", "B"),
            ("B", "trigger_1", "C"),
            ("C", "trigger_1", "D"),
            ("D", "trigger_1", "B"),
        ),
    )
    semantics = replace(
        payload.semantics, _token=d._CORE_TOKEN, history_json=json.dumps(asdict(model))
    )
    payload = replace(payload, _token=d._CORE_TOKEN, semantics=semantics)
    table = primary(
        backend,
        [
            (1, "A", START, START + timedelta(hours=1), "completed", False),
            (1, "B", START + timedelta(hours=1), END, "right_censored", False),
        ],
    )
    for index, pairs in enumerate(
        [
            [("A", "B"), ("B", "C"), ("C", "D"), ("D", "B")],
            [("A", "B"), ("B", "C"), ("C", "D"), ("D", "B"), ("B", "C"), ("C", "D"), ("D", "B")],
        ]
    ):
        trace = backend.create_table(
            f"trace_{index}",
            pa.table(
                {
                    "from_model_state": [a for a, b in pairs],
                    "to_model_state": [b for a, b in pairs],
                    "occurred_at": pa.array(
                        [START + timedelta(hours=1)] * len(pairs), type=pa.timestamp("us", "UTC")
                    ),
                }
            ),
            temp=True,
        )
        result, _, _ = reduce_lifecycle(table, {ROLES[0]: trace}, payload, freeze=lambda t: t)
        rows = backend.to_pyarrow(result).to_pylist()
        assert sum(r["transition_count"] for r in rows) == len(pairs)
        assert {
            (r["from_model_state"], r["to_model_state"]): r["transition_count"] for r in rows
        } == {p: pairs.count(p) for p in set(pairs)}
