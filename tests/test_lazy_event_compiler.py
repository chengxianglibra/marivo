"""Native Event ambiguity, identity and retained-row integrity boundaries."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo._compat import UTC
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.event import (
    EventStepRelation,
    compile_event_match,
    event_journey_id,
    event_output_proof,
)
from marivo.analysis.event import EveryStart, FirstPerSubject

_BASE = datetime(2026, 2, 1, tzinfo=UTC)
_DIGEST = "sha256:" + "b" * 64


def _relations(
    backend: Backend, groups: tuple[tuple[tuple[int | None, int], ...], ...]
) -> tuple[EventStepRelation, ...]:
    steps: list[EventStepRelation] = []
    for index, rows in enumerate(groups):
        raw = backend.create_table(
            f"event_{index}",
            pa.table(
                {
                    "id": pa.array([row[0] for row in rows], type=pa.int64()),
                    "stamp": pa.array(
                        [_BASE + timedelta(seconds=row[1]) for row in rows],
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                }
            ),
        )
        steps.append(
            EventStepRelation(
                f"event_{index}",
                f"step_{index}",
                raw.select(
                    entity_identity=ibis.struct({"subject": ibis.literal("identity_canary")}),
                    event_identity=ibis.struct({"k0": raw.id}),
                    occurred_at=raw.stamp,
                ),
            )
        )
    return tuple(steps)


def _compile(
    steps: tuple[EventStepRelation, ...], matching: FirstPerSubject | EveryStart
) -> tuple[ir.Table, tuple[tuple[str, ir.Table], ...], ir.Table]:
    result, checks, proof = compile_event_match(
        steps,
        matching=matching,
        cohort_start=_BASE,
        cohort_end=_BASE + timedelta(seconds=10),
        completion_through=_BASE + timedelta(seconds=20),
        definition_digest=_DIGEST,
        coverage_complete=True,
    )
    return result, tuple((check.name, check.expression) for check in checks), proof


def _violations(backend: Backend, expression: ir.Table) -> int:
    value = backend.to_pyarrow(expression)["violations"][0].as_py()
    assert isinstance(value, int)
    return value


@pytest.mark.parametrize(
    "matching",
    [
        FirstPerSubject(),
        EveryStart(completion_assignment="shared"),
        EveryStart(completion_assignment="exclusive"),
    ],
)
@pytest.mark.parametrize(
    ("groups", "ambiguous"),
    [
        ((((1, 0),), ((1, 0), (2, 1))), True),
        ((((1, 0),), ((2, 0),)), False),
        ((((1, 1),), ((1, 0), (2, 2))), False),
        ((((1, 0),), ((2, 1),), ((2, 1), (3, 2))), True),
    ],
)
def test_only_assignment_changing_ties_are_ambiguous(
    matching: FirstPerSubject | EveryStart,
    groups: tuple[tuple[tuple[int, int], ...], ...],
    ambiguous: bool,
) -> None:
    backend = ibis.duckdb.connect()
    try:
        steps = _relations(backend, groups)
        _, checks, _ = _compile(steps, matching)
        guard = dict(checks)["event.ambiguous_event_order"]
        assert (_violations(backend, guard) > 0) == ambiguous
        for name, expression in checks:
            if name != "event.ambiguous_event_order":
                assert _violations(backend, expression) == 0
    finally:
        backend.disconnect()


def test_reserved_tied_final_occurrence_does_not_invent_ambiguity() -> None:
    backend = ibis.duckdb.connect()
    try:
        steps = _relations(backend, (((1, 0), (2, 1)), ((2, 1), (3, 2))))
        table, checks, _ = _compile(steps, EveryStart(completion_assignment="exclusive"))
        assert _violations(backend, dict(checks)["event.ambiguous_event_order"]) == 0
        rows = backend.to_pyarrow(table).to_pylist()
        assert [row["event_identity"] for row in rows if row["step_key"] == "step_1"] == [
            {"k0": 2},
            {"k0": 3},
        ]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("rows", [((1, 0), (1, 1)), ((None, 0),)])
def test_occurrence_identity_duplicates_and_null_components_are_scalar_failures(
    rows: tuple[tuple[int | None, int], ...],
) -> None:
    backend = ibis.duckdb.connect()
    try:
        _, checks, _ = _compile(_relations(backend, (rows,)), FirstPerSubject())
        assert sum(_violations(backend, expression) for _, expression in checks) > 0
        assert all(tuple(expression.columns) == ("violations",) for _, expression in checks)
    finally:
        backend.disconnect()


def test_compilation_requires_homogeneous_occurrence_structs_without_source_reads() -> None:
    first = ibis.table(
        {
            "entity_identity": "struct<subject: string>",
            "event_identity": "struct<k0: int64>",
            "occurred_at": dt.Timestamp(timezone="UTC"),
        },
        name="first",
    )
    second = ibis.table(
        {
            "entity_identity": "struct<subject: string>",
            "event_identity": "struct<k0: string>",
            "occurred_at": dt.Timestamp(timezone="UTC"),
        },
        name="second",
    )
    with pytest.raises(DatasetCompilationError, match="incompatible Event identities"):
        _compile(
            (EventStepRelation("a", "start", first), EventStepRelation("b", "end", second)),
            FirstPerSubject(),
        )
    table, _, _ = _compile((EventStepRelation("a", "start", first),), FirstPerSubject())
    assert len(table.columns) == 8


def test_native_digest_matches_independent_json_encoding_and_normalizes_signed_zero() -> None:
    backend = ibis.duckdb.connect()
    try:
        raw = backend.create_table("identities", {"value": [-0.0, 0.0]})
        subject = ibis.struct({"subject": ibis.literal('tenant "one" \\ snow \u96ea')})
        event = ibis.struct({"k0": raw.value})
        ids = backend.to_pyarrow(
            raw.select(journey_id=event_journey_id(subject, event, definition_digest=_DIGEST))
        )["journey_id"].to_pylist()
        payload = json.dumps(
            {
                "entity_identity": {"subject": 'tenant "one" \\ snow \u96ea'},
                "anchor_event_identity": {"k0": 0.0},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        expected = (
            "journey_"
            + hashlib.sha256(("event_journey@v1:" + _DIGEST + ":" + payload).encode()).hexdigest()
        )
        assert ids == [expected, expected]
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "corruption",
    ["density", "duplicate", "digest", "status", "elapsed", "missing", "final_reuse", "identity"],
)
def test_native_recovery_proof_rejects_structural_and_assignment_corruption(
    corruption: str,
) -> None:
    backend = ibis.duckdb.connect()
    try:
        steps = _relations(backend, (((1, 0), (2, 1)), ((3, 2),), ((4, 3), (5, 4))))
        matching = EveryStart(completion_assignment="exclusive")
        expression, _, _ = _compile(steps, matching)
        retained = backend.create_table("retained", expression)
        if corruption == "density":
            damaged = retained.filter(retained.step_key != "step_1")
        elif corruption == "duplicate":
            damaged = retained.union(retained.limit(1))
        elif corruption == "digest":
            damaged = retained.mutate(journey_id=ibis.literal("journey_" + "0" * 64))
        elif corruption == "status":
            damaged = retained.mutate(completion_status=ibis.literal("incomplete"))
        elif corruption == "elapsed":
            damaged = retained.mutate(elapsed_from_start=retained.elapsed_from_start + 1)
        elif corruption == "missing":
            damaged = retained.mutate(
                occurred_at=(retained.step_key == "step_1").ifelse(
                    ibis.null().cast(retained.occurred_at.type()), retained.occurred_at
                )
            )
        elif corruption == "final_reuse":
            damaged = retained.mutate(
                event_identity=(retained.step_key == "step_2").ifelse(
                    ibis.struct({"k0": ibis.literal(4, type="int64")}), retained.event_identity
                )
            )
        else:
            damaged = retained.mutate(
                entity_identity=ibis.null().cast(retained.entity_identity.type())
            )
        proof = event_output_proof(
            damaged,
            step_keys=tuple(step.step_key for step in steps),
            event_refs=tuple(step.event_ref for step in steps),
            matching=matching,
            cohort_start=_BASE,
            cohort_end=_BASE + timedelta(seconds=10),
            completion_through=_BASE + timedelta(seconds=20),
            definition_digest=_DIGEST,
            coverage_complete=True,
        )
        assert _violations(backend, proof) > 0
        assert set(proof.columns) == {
            "row_count",
            "subject_count",
            "matched_row_count",
            "missing_row_count",
            "journey_count",
            "complete_journey_count",
            "incomplete_journey_count",
            "censored_journey_count",
            "violations",
        }
        assert "identity_canary" not in str(backend.to_pyarrow(proof).to_pylist())
    finally:
        backend.disconnect()
