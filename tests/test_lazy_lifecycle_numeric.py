"""Independent replay expectations against native interval and trace projections."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

from marivo.analysis.compiler.event import EventStepRelation
from marivo.analysis.compiler.lifecycle import compile_replay
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import EventCoverageResolution, resolve_event_coverage
from marivo.analysis.domains.lifecycle import ROLES, LifecyclePayload
from tests.lazy_lifecycle_fixtures import END, START, history, sources_without_io

Occurrence = tuple[int, int, datetime]


def evaluate(
    started: list[Occurrence],
    finished: list[Occurrence],
    *,
    model: str = "terminal",
    coverage: str = "complete",
) -> tuple[dict[str, list[dict[str, object]]], dict[str, int]]:
    logical = history(sources_without_io())
    assert isinstance(logical._root, LogicalRootHandle)
    payload = logical._root.payload
    assert isinstance(payload, LifecyclePayload)
    semantics = payload.semantics
    if model == "cycle":
        semantics = replace(
            semantics,
            _token=d._CORE_TOKEN,
            terminals=(),
            transitions=(("open", "trigger_1", "done"), ("done", "trigger_1", "open")),
        )
    elif model == "self":
        semantics = replace(
            semantics,
            _token=d._CORE_TOKEN,
            terminals=(),
            transitions=(("open", "trigger_1", "open"),),
        )
    resolved = resolve_event_coverage(payload.definition, require_source_origin=True)
    if coverage != "complete":
        resolved = EventCoverageResolution(
            False,
            "observed" if coverage == "prefix" else "unknown",
            tuple(
                replace(
                    f,
                    complete=False,
                    basis="observed" if coverage == "prefix" else "unknown",
                    complete_through=(START + timedelta(hours=4)).isoformat()
                    if coverage == "prefix"
                    else None,
                )
                for f in resolved.events
            ),
        )
    backend = ibis.duckdb.connect()
    backend.raw_sql("SET threads=1")
    counter = 0

    def freeze(table: ir.Table) -> ir.Table:
        nonlocal counter
        counter += 1
        backend.create_table(f"replay_fence_{counter}", table, temp=True)
        return ibis.table(table.schema(), name=f"replay_fence_{counter}")

    try:
        streams: list[EventStepRelation] = []
        for key, name, values in (
            ("trigger_0", "sales.started", started),
            ("trigger_1", "sales.finished", finished),
        ):
            arrow = pa.table(
                {
                    "subject": pa.array([v[0] for v in values], type=pa.int64()),
                    "identity": pa.array([v[1] for v in values], type=pa.int64()),
                    "instant": pa.array([v[2] for v in values], type=pa.timestamp("us", "UTC")),
                }
            )
            raw = backend.create_table(key, arrow, temp=True)
            table = raw.select(
                entity_identity=ibis.struct({"id": raw.subject}),
                event_identity=ibis.struct({"k0": raw.identity}),
                occurred_at=raw.instant,
            )
            streams.append(EventStepRelation(name, key, table))
        membership = backend.create_table("members", pa.table({"id": [1, 2, 3]}), temp=True)
        primary, parts, checks = compile_replay(
            tuple(streams), membership, semantics, resolved, freeze=freeze
        )
        diagnostics = {
            check.name: int(backend.execute(check.expression).violations.iloc[0])
            for check in checks
        }
        output = {
            "history": backend.to_pyarrow(primary).to_pylist(),
            **{role: backend.to_pyarrow(table).to_pylist() for role, table in parts},
        }
        return output, diagnostics
    finally:
        backend.disconnect()


def test_inception_lookback_clipping_and_no_trigger_subjects() -> None:
    rows, checks = evaluate(
        [(1, 1, START - timedelta(days=2))], [(1, 2, START + timedelta(hours=3))]
    )
    assert not any(checks.values())
    assert [
        (r["model_state"], r["valid_from"], r["valid_to"], r["interval_status"], r["left_clipped"])
        for r in rows["history"]
    ] == [
        ("open", START, START + timedelta(hours=3), "completed", True),
        ("done", START + timedelta(hours=3), END, "right_censored", False),
    ]
    assert [r["classification"] for r in rows[ROLES[1]]] == [
        "seeded",
        "not_incepted",
        "not_incepted",
    ]
    assert len(rows[ROLES[0]]) == 1
    assert rows[ROLES[2]] == []


@pytest.mark.parametrize("model", ["cycle", "self"])
def test_same_time_legal_transitions_are_lossless(model: str) -> None:
    rows, checks = evaluate(
        [(1, 1, START)],
        [(1, 2, START + timedelta(hours=2)), (1, 3, START + timedelta(hours=2))],
        model=model,
    )
    assert not any(checks.values())
    assert [r["model_state"] for r in rows["history"]] == ["open", "open"]
    assert [r["transition_ordinal"] for r in rows[ROLES[0]]] == [1, 2]
    assert [r["from_model_state"] for r in rows[ROLES[0]]] == (
        ["open", "done"] if model == "cycle" else ["open", "open"]
    )
    assert all(
        isinstance(r["valid_from"], datetime)
        and isinstance(r["valid_to"], datetime)
        and r["valid_from"] < r["valid_to"]
        for r in rows["history"]
    )


def test_missing_inception_requires_complete_origin_proof() -> None:
    _, complete = evaluate([], [(1, 2, START)])
    assert complete["lifecycle.insufficient_state_history"] == 1
    rows, unknown = evaluate([], [(1, 2, START)], coverage="unknown")
    assert not any(unknown.values())
    assert rows["history"] == []
    assert [r["classification"] for r in rows[ROLES[1]]] == ["coverage_censored"] * 3
    assert all(r["inception_at"] is None and r["known_through"] is None for r in rows[ROLES[1]])


def test_prefix_retains_known_inception_without_full_window_claim() -> None:
    rows, checks = evaluate(
        [(1, 1, START)], [(1, 2, START + timedelta(hours=4))], coverage="prefix"
    )
    assert not any(checks.values())
    assert [r["interval_status"] for r in rows["history"]] == [
        "coverage_censored",
        "coverage_censored",
    ]
    ledger = rows[ROLES[1]]
    assert all(
        r["classification"] == "coverage_censored"
        and r["known_through"] == START + timedelta(hours=4)
        for r in ledger
    )
    assert ledger[0]["inception_at"] == START
    assert ledger[1]["inception_at"] is None


@pytest.mark.parametrize("identities", [(1, 1), (1, 2), (2, 1)])
def test_order_dependent_cross_event_tie_is_rejected(identities: tuple[int, int]) -> None:
    _, checks = evaluate([(1, identities[0], START)], [(1, identities[1], START)])
    assert checks["lifecycle.ambiguous_event_order"] == 1


@pytest.mark.parametrize("finish_identity", [6, 7, 8])
def test_equivalent_same_time_terminal_violations_are_admitted(finish_identity: int) -> None:
    rows, checks = evaluate(
        [(1, 1, START), (1, 7, START + timedelta(hours=3))],
        [(1, 2, START + timedelta(hours=1)), (1, finish_identity, START + timedelta(hours=3))],
    )
    assert not any(checks.values())
    assert len(rows[ROLES[2]]) == 2
    assert all(r["violation_kind"] == "transition_from_terminal" for r in rows[ROLES[2]])


def test_pre_inception_and_illegal_triggers_do_not_change_state() -> None:
    rows, checks = evaluate(
        [(1, 2, START + timedelta(hours=1)), (1, 3, START + timedelta(hours=2))], [(1, 1, START)]
    )
    assert not any(checks.values())
    assert len(rows["history"]) == 1
    assert rows["history"][0]["valid_from"] == START + timedelta(hours=1)
    assert [r["violation_kind"] for r in rows[ROLES[2]]] == ["illegal_transition"]
    assert rows[ROLES[0]] == []


def reference_cycle(
    started: list[Occurrence], finished: list[Occurrence]
) -> dict[str, list[dict[str, object]]]:
    """A scalar reference with explicit entries, independent of native window/CTE logic."""
    events = sorted(
        [(time, identity, "event:sales.started") for _, identity, time in started]
        + [(time, identity, "event:sales.finished") for _, identity, time in finished]
    )
    state: str | None = None
    entered: tuple[datetime, int, str] | None = None
    intervals: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []

    def interval(exit: tuple[datetime, int, str] | None) -> None:
        if entered is None:
            return
        left = max(START, entered[0])
        right = END if exit is None else min(END, exit[0])
        if left >= right:
            return
        intervals.append(
            {
                "entity_identity": {"id": 1},
                "model_state": state,
                "valid_from": left,
                "valid_to": right,
                "entered_by_event_ref": entered[2],
                "entered_by_event_identity": {"k0": entered[1]},
                "exited_by_event_ref": None if exit is None else exit[2],
                "exited_by_event_identity": None if exit is None else {"k0": exit[1]},
                "interval_status": "right_censored" if exit is None else "completed",
                "left_clipped": entered[0] < START,
            }
        )

    for event in events:
        time, identity, event_ref = event
        if state is None:
            if event_ref == "event:sales.started":
                state, entered = "open", event
            continue
        if event_ref == "event:sales.started":
            if time >= START:
                violations.append(
                    {
                        "entity_identity": {"id": 1},
                        "trigger_event_ref": event_ref,
                        "trigger_event_identity": {"k0": identity},
                        "occurred_at": time,
                        "model_state_at_event": state,
                        "violation_kind": "illegal_transition",
                    }
                )
            continue
        interval(event)
        next_state = "done" if state == "open" else "open"
        if time >= START:
            transitions.append(
                {
                    "entity_identity": {"id": 1},
                    "transition_ordinal": len(transitions) + 1,
                    "occurred_at": time,
                    "from_model_state": state,
                    "to_model_state": next_state,
                    "trigger_event_ref": event_ref,
                    "trigger_event_identity": {"k0": identity},
                }
            )
        state, entered = next_state, event
    interval(None)
    return {
        "history": intervals,
        ROLES[0]: transitions,
        ROLES[2]: sorted(violations, key=lambda row: str(row["trigger_event_identity"])),
    }


@pytest.mark.parametrize("seed", range(10))
def test_complete_native_rows_match_independent_reference(seed: int) -> None:
    import random

    generator = random.Random(seed)
    started = [(1, 100, START - timedelta(hours=1))]
    finished: list[Occurrence] = []
    for ordinal in range(12):
        item = (1, 101 + ordinal, START + timedelta(hours=generator.randrange(23)))
        if generator.randrange(3) == 0:
            # Keep this complete-output oracle free of cross-Event ambiguity;
            # same-Event ties still exercise ordered cycles and transient states.
            started.append((item[0], item[1], item[2] + timedelta(microseconds=1)))
        else:
            finished.append(item)
    actual, checks = evaluate(started, finished, model="cycle")
    assert not any(checks.values())
    expected = reference_cycle(started, finished)
    for role, rows in expected.items():
        assert actual[role] == rows
