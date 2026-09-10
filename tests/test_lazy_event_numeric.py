"""Independent Event assignment references against native DuckDB expressions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo._compat import UTC
from marivo.analysis.compiler.event import (
    EventStepRelation,
    canonical_event_rows,
    compile_event_match,
    event_output_proof,
)
from marivo.analysis.event import EveryStart, FirstPerSubject

_BASE = datetime(2026, 2, 1, tzinfo=UTC)
_END = _BASE + timedelta(seconds=10)
_THROUGH = _BASE + timedelta(seconds=20)
_DIGEST = "sha256:" + "a" * 64
_POLICIES = (
    FirstPerSubject(),
    EveryStart(completion_assignment="shared"),
    EveryStart(completion_assignment="exclusive"),
)


@dataclass(frozen=True)
class _Occurrence:
    event: str
    subject: tuple[str, int]
    identity: tuple[int, int]
    second: int

    @property
    def order(self) -> tuple[int, tuple[int, int]]:
        return self.second, self.identity


def _relations(
    backend: Backend, rows: list[_Occurrence], pattern: tuple[str, ...]
) -> tuple[EventStepRelation, ...]:
    by_event: dict[str, ir.Table] = {}
    schema = pa.schema(
        [
            ("tenant", pa.string()),
            ("subject", pa.int64()),
            ("first", pa.int64()),
            ("second", pa.int64()),
            ("stamp", pa.timestamp("us", tz="UTC")),
        ]
    )
    for event in dict.fromkeys(pattern):
        selected = [row for row in rows if row.event == event]
        raw = backend.create_table(
            event,
            pa.Table.from_pylist(
                [
                    {
                        "tenant": row.subject[0],
                        "subject": row.subject[1],
                        "first": row.identity[0],
                        "second": row.identity[1],
                        "stamp": _BASE + timedelta(seconds=row.second),
                    }
                    for row in selected
                ],
                schema=schema,
            ),
        )
        by_event[event] = raw.select(
            entity_identity=ibis.struct({"tenant": raw.tenant, "id": raw.subject}),
            event_identity=ibis.struct({"k0": raw.first, "k1": raw.second}),
            occurred_at=raw.stamp,
        )
    return tuple(
        EventStepRelation(event, f"step_{index}", by_event[event])
        for index, event in enumerate(pattern)
    )


def _reference(
    rows: list[_Occurrence],
    pattern: tuple[str, ...],
    matching: FirstPerSubject | EveryStart,
) -> list[tuple[_Occurrence | None, ...]]:
    attempts: list[tuple[_Occurrence | None, ...]] = []
    subjects = sorted({row.subject for row in rows})
    for subject in subjects:
        eligible = sorted(
            [row for row in rows if row.subject == subject and 0 <= row.second < 20],
            key=lambda row: row.order,
        )
        anchors = [row for row in eligible if row.event == pattern[0] and row.second < 10]
        if isinstance(matching, FirstPerSubject):
            anchors = anchors[:1]
        reserved: set[tuple[str, tuple[int, int]]] = set()
        for anchor in anchors:
            assignment: list[_Occurrence | None] = [anchor]
            used = {(anchor.event, anchor.identity)}
            for index, event in enumerate(pattern[1:], start=1):
                previous = assignment[-1]
                exclusive = (
                    index == len(pattern) - 1
                    and isinstance(matching, EveryStart)
                    and matching.completion_assignment == "exclusive"
                )
                candidates = [
                    row
                    for row in eligible
                    if previous is not None
                    and row.event == event
                    and row.order > previous.order
                    and (event, row.identity) not in used
                    and (not exclusive or (event, row.identity) not in reserved)
                ]
                selected = candidates[0] if candidates else None
                assignment.append(selected)
                if selected is not None:
                    used.add((event, selected.identity))
                    if exclusive:
                        reserved.add((event, selected.identity))
            attempts.append(tuple(assignment))
    return attempts


def _scalar(backend: Backend, table: ir.Table, column: str) -> int:
    value = backend.to_pyarrow(table)[column][0].as_py()
    assert isinstance(value, int)
    return value


@pytest.mark.parametrize("matching", _POLICIES)
@pytest.mark.parametrize(
    "pattern", [("a",), ("a", "b"), ("a", "b", "c"), ("a", "b", "a"), ("a", "a", "a")]
)
def test_all_policies_match_independent_occurrence_reference(
    matching: FirstPerSubject | EveryStart, pattern: tuple[str, ...]
) -> None:
    rows = [
        _Occurrence(event, ("tenant", subject), (subject, ordinal), second)
        for subject in (1, 2)
        for event, seconds in (
            ("a", (-1, 0, 1, 3, 8, 10, 17)),
            ("b", (2, 4, 9, 15)),
            ("c", (6, 14, 20)),
        )
        for ordinal, second in enumerate(seconds)
        if not (subject == 2 and event == "b")
    ]
    Random(120).shuffle(rows)
    backend = ibis.duckdb.connect()
    try:
        steps = _relations(backend, rows, pattern)
        expression, checks, proof = compile_event_match(
            steps,
            matching=matching,
            cohort_start=_BASE,
            cohort_end=_END,
            completion_through=_THROUGH,
            definition_digest=_DIGEST,
            coverage_complete=True,
        )
        actual = backend.to_pyarrow(expression).to_pylist()
        reference = _reference(rows, pattern, matching)
        assert len(actual) == len(reference) * len(pattern)
        for index, expected in enumerate(reference):
            anchor = expected[0]
            assert anchor is not None
            group = actual[index * len(pattern) : (index + 1) * len(pattern)]
            assert len({row["journey_id"] for row in group}) == 1
            for step_index, (row, occurrence) in enumerate(zip(group, expected, strict=True)):
                assert row["step_key"] == f"step_{step_index}"
                assert row["entity_identity"] == {"tenant": "tenant", "id": anchor.subject[1]}
                assert row["completion_status"] == (
                    "complete" if expected[-1] is not None else "incomplete"
                )
                if occurrence is None:
                    assert all(
                        row[name] is None
                        for name in (
                            "event_identity",
                            "occurred_at",
                            "elapsed_from_start",
                            "elapsed_from_previous",
                        )
                    )
                else:
                    previous = expected[max(step_index - 1, 0)]
                    assert previous is not None
                    assert row["event_identity"] == {
                        "k0": occurrence.identity[0],
                        "k1": occurrence.identity[1],
                    }
                    assert row["occurred_at"] == _BASE + timedelta(seconds=occurrence.second)
                    assert (
                        row["elapsed_from_start"] == (occurrence.second - anchor.second) * 1_000_000
                    )
                    assert (
                        row["elapsed_from_previous"]
                        == (occurrence.second - previous.second) * 1_000_000
                    )
        assert all(_scalar(backend, check.expression, "violations") == 0 for check in checks)
        assert _scalar(backend, proof, "journey_count") == len(reference)
        retained = backend.create_table("retained", expression)
        validation = event_output_proof(
            retained,
            step_keys=tuple(step.step_key for step in steps),
            event_refs=pattern,
            matching=matching,
            cohort_start=_BASE,
            cohort_end=_END,
            completion_through=_THROUGH,
            definition_digest=_DIGEST,
            coverage_complete=True,
        )
        assert _scalar(backend, validation, "violations") == 0
        assert (
            backend.to_pyarrow(
                canonical_event_rows(retained, tuple(step.step_key for step in steps))
            ).to_pylist()
            == actual
        )
    finally:
        backend.disconnect()


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_empty_sources_and_missing_followup_keep_exact_dense_authority(
    complete: bool, empty: bool
) -> None:
    rows = [] if empty else [_Occurrence("a", ("tenant", 1), (1, 1), 0)]
    backend = ibis.duckdb.connect()
    try:
        steps = _relations(backend, rows, ("a", "b", "c"))
        table, _, proof = compile_event_match(
            steps,
            matching=FirstPerSubject(),
            cohort_start=_BASE,
            cohort_end=_END,
            completion_through=_THROUGH,
            definition_digest=_DIGEST,
            coverage_complete=complete,
        )
        actual = backend.to_pyarrow(table).to_pylist()
        assert len(actual) == (0 if empty else 3)
        assert _scalar(backend, proof, "journey_count") == (0 if empty else 1)
        assert _scalar(backend, proof, "missing_row_count") == (0 if empty else 2)
        if actual:
            assert {row["completion_status"] for row in actual} == {
                "incomplete" if complete else "coverage_censored"
            }
            assert actual[1]["occurred_at"] is None and actual[2]["occurred_at"] is None
    finally:
        backend.disconnect()


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("matching", _POLICIES)
def test_irregular_eligibility_thresholds_match_reference(
    seed: int, matching: FirstPerSubject | EveryStart
) -> None:
    random = Random(seed)
    pattern = ("a", "b", "a") if seed % 2 else ("a", "b", "c")
    rows = [
        _Occurrence(event, ("tenant", subject), (event_index, subject * 100 + ordinal), second)
        for subject in (1, 2)
        for event_index, event in enumerate(("a", "b", "c"))
        for ordinal, second in enumerate(random.sample(range(20), random.randrange(1, 10)))
    ]
    random.shuffle(rows)
    backend = ibis.duckdb.connect()
    try:
        expression, checks, _ = compile_event_match(
            _relations(backend, rows, pattern),
            matching=matching,
            cohort_start=_BASE,
            cohort_end=_END,
            completion_through=_THROUGH,
            definition_digest=_DIGEST,
            coverage_complete=True,
        )
        actual = backend.to_pyarrow(expression).to_pylist()
        reference = _reference(rows, pattern, matching)
        assert [row["event_identity"] for row in actual] == [
            None if row is None else {"k0": row.identity[0], "k1": row.identity[1]}
            for attempt in reference
            for row in attempt
        ]
        assert all(_scalar(backend, check.expression, "violations") == 0 for check in checks)
    finally:
        backend.disconnect()
