"""Independent small-fixture arithmetic for native retained Event reductions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from itertools import product

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo._compat import UTC
from marivo.analysis.compiler.event_axes import lower_event_axes
from marivo.analysis.compiler.event_reducers import (
    FUNNEL_COUNTS,
    canonical_time_to_event_rows,
    compile_event_funnel,
    compile_event_subject_selection,
    compile_event_time_to_event,
    funnel_output_proof,
    time_to_event_output_proof,
)
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.domains.completeness import EventCoverageFact, EventCoverageResolution
from marivo.analysis.domains.contracts import EventAxisBinding, EventJourneySemantics
from marivo.analysis.event import EveryStart, FirstPerSubject, sequence, step
from marivo.analysis.subject import DroppedBefore
from marivo.refs import ref
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity
from tests.lazy_event_fixtures import make_event_sources

_BASE = datetime(2026, 2, 1, tzinfo=UTC)
_THROUGH = _BASE + timedelta(seconds=20)
_PATTERN = sequence(
    *(
        step(participant=ParticipantRoleHandle(ref.event("events." + name), "subject"), key=name)
        for name in ("a", "b", "c")
    )
)


def _semantics() -> EventJourneySemantics:
    return EventJourneySemantics(
        _token=d._CORE_TOKEN,
        pattern_json=_PATTERN.model_dump_json(),
        matching_json=FirstPerSubject().model_dump_json(),
        subject_entity_ref="subjects",
        subject_identity_signature=(("id", "int64"),),
        occurrence_identity_types=("int64",),
        cohort_start=_BASE.isoformat(),
        cohort_end=(_BASE + timedelta(seconds=10)).isoformat(),
        completion_through=_THROUGH.isoformat(),
        population_definition="population",
        completeness_json="[]",
        source_dependency_fingerprint="source",
        step_event_fingerprints=("a", "b", "c"),
        source_origins=("source",) * 3,
    )


def _coverage(complete: tuple[bool, ...]) -> EventCoverageResolution:
    return EventCoverageResolution(
        all(complete),
        "declared" if all(complete) else "unknown",
        tuple(
            EventCoverageFact(
                event_ref=selected.event.key,
                event_fingerprint=selected.key,
                source_origin_ref="source",
                basis="declared" if value else "unknown",
                complete=value,
                complete_from=_BASE.isoformat() if value else None,
                complete_through=_THROUGH.isoformat() if value else None,
                authority=None,
                observed_at=None,
                source_revision=None,
                rationale="fixture" if value else None,
            )
            for selected, value in zip(_PATTERN.steps, complete, strict=True)
        ),
    )


@dataclass(frozen=True)
class _Journey:
    subject: int
    times: tuple[int | None, ...]
    axis: str | None = None


def _relation(
    backend: Backend, journeys: tuple[_Journey, ...], *, complete: bool = False
) -> ir.Table:
    schema = pa.schema(
        [
            ("journey_id", pa.string()),
            ("completion_status", pa.string()),
            ("entity_identity", pa.struct([("id", pa.int64())])),
            ("step_key", pa.string()),
            ("event_identity", pa.struct([("k0", pa.int64())])),
            ("occurred_at", pa.timestamp("us", tz="UTC")),
            ("elapsed_from_start", pa.int64()),
            ("elapsed_from_previous", pa.int64()),
            ("region", pa.string()),
        ]
    )
    records: list[dict[str, object]] = []
    for attempt, journey in enumerate(journeys):
        status = (
            "complete"
            if all(value is not None for value in journey.times)
            else ("incomplete" if complete else "coverage_censored")
        )
        for index, (selected, second) in enumerate(zip(_PATTERN.steps, journey.times, strict=True)):
            start, previous = journey.times[0], journey.times[max(0, index - 1)]
            records.append(
                {
                    "journey_id": f"j_{journey.subject}_{attempt}",
                    "completion_status": status,
                    "entity_identity": {"id": journey.subject},
                    "step_key": selected.key,
                    "event_identity": None if second is None else {"k0": second},
                    "occurred_at": None if second is None else _BASE + timedelta(seconds=second),
                    "elapsed_from_start": None
                    if second is None or start is None
                    else (second - start) * 1_000_000,
                    "elapsed_from_previous": None
                    if second is None or previous is None
                    else (second - previous) * 1_000_000,
                    "region": journey.axis,
                }
            )
    return backend.create_table("journeys", pa.Table.from_pylist(records, schema=schema))


def _scalar(backend: Backend, expression: ir.Table, name: str = "violations") -> int:
    result = backend.to_pyarrow(expression)[name][0].as_py()
    assert isinstance(result, int)
    return result


def _reference_counts(
    journeys: tuple[_Journey, ...], complete: tuple[bool, ...], index: int
) -> dict[str, int]:
    reached = entered = unknown = censored = 0
    for journey in journeys:
        missing = next((i for i, value in enumerate(journey.times) if value is None), None)
        target_unknown = missing is not None and index >= missing and not complete[missing]
        target_entered = index == 0 or journey.times[index - 1] is not None
        reached += journey.times[index] is not None
        entered += target_entered
        unknown += target_unknown
        censored += target_entered and target_unknown
    return dict(
        zip(
            FUNNEL_COUNTS,
            (
                len(journeys),
                len(journeys) - unknown,
                entered,
                entered - censored,
                reached,
                entered - censored - reached,
                censored,
            ),
            strict=True,
        )
    )


@pytest.mark.parametrize("complete", tuple(product((False, True), repeat=3)))
@pytest.mark.parametrize("grouped", (False, True))
def test_funnel_matches_independent_dense_counts(complete: tuple[bool, ...], grouped: bool) -> None:
    journeys = (
        _Journey(1, (0, 2, 5), "west"),
        _Journey(2, (1, None, None), None),
        _Journey(3, (0, 3, None), "west"),
    )
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, journeys, complete=all(complete))
        output, checks, proof = compile_event_funnel(
            table,
            semantics=_semantics(),
            coverage=_coverage(complete),
            axis_columns=("region",) if grouped else (),
        )
        rows = backend.to_pyarrow(output).to_pylist()
        assert len(rows) == (6 if grouped else 3)
        for row in rows:
            group = tuple(item for item in journeys if not grouped or item.axis == row["region"])
            index = ("a", "b", "c").index(row["step_key"])
            expected = _reference_counts(group, complete, index)
            assert {name: row[name] for name in FUNNEL_COUNTS} == expected
            for name, numerator, denominator in (
                ("conversion_from_first", "reached_count", "resolved_cohort_count"),
                ("conversion_from_previous", "reached_count", "resolved_entry_count"),
                ("loss_rate_from_previous", "lost_count", "resolved_entry_count"),
            ):
                value = (
                    None
                    if not expected[denominator] or (not index and name != "conversion_from_first")
                    else expected[numerator] / expected[denominator]
                )
                assert row[name] == value
        assert _scalar(backend, proof) == 0
        assert all(_scalar(backend, check.expression) == 0 for check in checks)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("grouped", (False, True))
def test_empty_funnel_has_only_realized_groups(grouped: bool) -> None:
    backend = ibis.duckdb.connect()
    try:
        output, checks, proof = compile_event_funnel(
            _relation(backend, ()),
            semantics=_semantics(),
            coverage=_coverage((True,) * 3),
            axis_columns=("region",) if grouped else (),
        )
        rows = backend.to_pyarrow(output).to_pylist()
        assert len(rows) == (0 if grouped else 3)
        assert all(row[name] == 0 for row in rows for name in FUNNEL_COUNTS)
        assert _scalar(backend, proof) == 0
        assert all(_scalar(backend, check.expression) == 0 for check in checks)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("complete", tuple(product((False, True), repeat=3)))
@pytest.mark.parametrize("pair", ((0, 1), (0, 2), (1, 2)))
def test_time_to_event_classifies_selected_pair(
    complete: tuple[bool, ...], pair: tuple[int, int]
) -> None:
    journeys = (_Journey(1, (0, 2, 5)), _Journey(2, (1, None, None)), _Journey(3, (0, 3, None)))
    start_index, end_index = pair
    backend = ibis.duckdb.connect()
    try:
        output, _, proof = compile_event_time_to_event(
            _relation(backend, journeys),
            semantics=_semantics(),
            coverage=_coverage(complete),
            from_step=_PATTERN.steps[start_index],
            to_step=_PATTERN.steps[end_index],
        )
        rows = backend.to_pyarrow(output).to_pylist()
        assert len(rows) == len(journeys)
        for row, journey in zip(rows, journeys, strict=True):
            missing = next((i for i, value in enumerate(journey.times) if value is None), None)
            unknown = missing is not None and not complete[missing]
            start, end = journey.times[start_index], journey.times[end_index]
            status = (
                ("entry_unknown" if unknown else "not_entered")
                if start is None
                else (
                    "complete"
                    if end is not None
                    else ("coverage_censored" if unknown else "incomplete")
                )
            )
            assert row["completion_status"] == status
            assert row["duration"] == (
                None if end is None or start is None else (end - start) * 1_000_000
            )
            followup = (
                None
                if start is None
                else end
                if end is not None
                else 20
                if status == "incomplete"
                else start
            )
            assert row["followup_until"] == (
                None if followup is None else _BASE + timedelta(seconds=followup)
            )
        assert _scalar(backend, proof) == 0
    finally:
        backend.disconnect()


def test_partial_followup_uses_common_contiguous_coverage_prefix() -> None:
    coverage = _coverage((True, False, False))
    facts = tuple(
        replace(
            fact,
            complete_from=_BASE.isoformat(),
            complete_through=(_BASE + timedelta(seconds=end)).isoformat(),
        )
        for fact, end in zip(coverage.events, (20, 12, 8), strict=True)
    )
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, (_Journey(1, (2, None, None)),))
        output, _, _ = compile_event_time_to_event(
            table,
            semantics=_semantics(),
            coverage=replace(coverage, events=facts),
            from_step=_PATTERN.steps[0],
            to_step=_PATTERN.steps[2],
        )
        row = backend.to_pyarrow(output).to_pylist()[0]
        assert row["followup_until"] == _BASE + timedelta(seconds=8)
        assert row["observed_duration"] == 6_000_000
        facts = (
            *facts[:1],
            replace(facts[1], complete_from=(_BASE + timedelta(seconds=3)).isoformat()),
            facts[2],
        )
        output, _, _ = compile_event_time_to_event(
            table,
            semantics=_semantics(),
            coverage=replace(coverage, events=facts),
            from_step=_PATTERN.steps[0],
            to_step=_PATTERN.steps[2],
        )
        assert backend.to_pyarrow(output).to_pylist()[0]["observed_duration"] == 0
    finally:
        backend.disconnect()


@pytest.mark.parametrize("complete", tuple(product((False, True), repeat=3)))
def test_selection_matches_exact_funnel_loss_and_fences_all_unknowns(
    complete: tuple[bool, ...],
) -> None:
    journeys = (_Journey(1, (0, 2, 5)), _Journey(2, (1, None, None)), _Journey(3, (0, 3, None)))
    backend = ibis.duckdb.connect()
    try:
        output, checks, proof = compile_event_subject_selection(
            _relation(backend, journeys),
            semantics=_semantics(),
            coverage=_coverage(complete),
            selection=DroppedBefore(step=_PATTERN.steps[2]),
        )
        actual = backend.to_pyarrow(output).to_pylist()
        assert actual == ([{"entity_identity": {"id": 3}}] if complete[2] else [])
        expected_unknown = int(not complete[1]) + int(not complete[2])
        assert _scalar(backend, proof, "unknown_subject_count") == expected_unknown
        assert _scalar(backend, checks[-1].expression) == expected_unknown
    finally:
        backend.disconnect()


def test_result_proofs_reject_corruption_and_admit_filtered_cells() -> None:
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, (_Journey(1, (0, 2, 5)),))
        funnel, _, _ = compile_event_funnel(
            table, semantics=_semantics(), coverage=_coverage((True,) * 3)
        )
        bad = funnel.mutate(reached_count=funnel.reached_count + 1)
        assert _scalar(backend, funnel_output_proof(bad, step_keys=("a", "b", "c"))) > 0
        subset = funnel.filter(funnel.step_key == "b")
        assert (
            _scalar(
                backend, funnel_output_proof(subset, step_keys=("a", "b", "c"), require_dense=False)
            )
            == 0
        )
        tte, _, _ = compile_event_time_to_event(
            table,
            semantics=_semantics(),
            coverage=_coverage((True,) * 3),
            from_step=_PATTERN.steps[0],
            to_step=_PATTERN.steps[2],
        )
        bad_tte = tte.mutate(duration=tte.duration + 1)
        assert (
            _scalar(backend, time_to_event_output_proof(bad_tte, completion_through=_THROUGH)) > 0
        )
        false_coverage = subset.mutate(
            cohort_count=ibis.literal(3, type="int64"),
            resolved_cohort_count=ibis.literal(3, type="int64"),
            entry_count=ibis.literal(2, type="int64"),
            resolved_entry_count=ibis.literal(1, type="int64"),
            reached_count=ibis.literal(1, type="int64"),
            lost_count=ibis.literal(0, type="int64"),
            coverage_censored_count=ibis.literal(1, type="int64"),
            conversion_from_first=ibis.literal(1 / 3),
            conversion_from_previous=ibis.literal(1.0),
            loss_rate_from_previous=ibis.literal(0.0),
        )
        assert (
            _scalar(
                backend,
                funnel_output_proof(false_coverage, step_keys=("a", "b", "c"), require_dense=False),
            )
            > 0
        )
    finally:
        backend.disconnect()


def test_repeated_attempts_recover_canonical_order_without_auxiliary_columns() -> None:
    backend = ibis.duckdb.connect()
    try:
        table = _relation(
            backend,
            (_Journey(1, (3, None, None)), _Journey(1, (1, 2, 5)), _Journey(1, (0, 2, None))),
        )
        semantics = replace(
            _semantics(),
            _token=d._CORE_TOKEN,
            matching_json=EveryStart(completion_assignment="shared").model_dump_json(),
        )
        output, _, _ = compile_event_time_to_event(
            table,
            semantics=semantics,
            coverage=_coverage((True,) * 3),
            from_step=_PATTERN.steps[1],
            to_step=_PATTERN.steps[2],
        )
        actual = backend.to_pyarrow(output).to_pylist()
        retained = backend.create_table(
            "retained", backend.to_pyarrow(output.order_by(ibis.desc("journey_id")))
        )
        assert backend.to_pyarrow(canonical_time_to_event_rows(retained)).to_pylist() == actual
        assert actual[-1]["completion_status"] == "not_entered"
    finally:
        backend.disconnect()


def test_partial_event_receipt_proves_absence_after_reached_predecessor() -> None:
    coverage = _coverage((False, False, False))
    proven = replace(
        coverage.events[1],
        basis="observed",
        complete=False,
        complete_from=(_BASE + timedelta(seconds=1)).isoformat(),
        complete_through=_THROUGH.isoformat(),
    )
    coverage = replace(coverage, events=(coverage.events[0], proven, coverage.events[2]))
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, (_Journey(1, (2, None, None)), _Journey(2, (0, None, None))))
        output, _, proof = compile_event_time_to_event(
            table,
            semantics=_semantics(),
            coverage=coverage,
            from_step=_PATTERN.steps[0],
            to_step=_PATTERN.steps[2],
        )
        rows = backend.to_pyarrow(output).to_pylist()
        assert [row["completion_status"] for row in rows] == ["incomplete", "coverage_censored"]
        assert rows[0]["followup_until"] == _THROUGH
        assert _scalar(backend, proof) == 0
        output, _, _ = compile_event_funnel(table, semantics=_semantics(), coverage=coverage)
        last = backend.to_pyarrow(output).to_pylist()[-1]
        assert last["resolved_cohort_count"] == 1
        assert last["coverage_censored_count"] == 0
    finally:
        backend.disconnect()


@pytest.mark.parametrize("entity_name", ("customers", "snapshots", "validity"))
@pytest.mark.parametrize("failure", (None, "missing", "overlap"))
def test_governed_axis_native_join_preserves_null_and_checks_entry_version(
    entity_name: str, failure: str | None
) -> None:
    owner = make_event_sources()._owner
    subject = normalize_target_entity(owner.semantic_registry, "sales." + entity_name)
    dimension = replace(
        normalize_target_dimension(owner.semantic_registry, "sales.customers.region"),
        entity_ref=subject.ref,
        nullable=True,
    )
    binding = EventAxisBinding(dimension, subject, (), "fixture")
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, (_Journey(1, (0, 2, 5)), _Journey(2, (1, None, None)))).drop(
            "region"
        )
        records: list[dict[str, object]] = [
            {
                "id": value,
                "region": "correct" if value == 1 else None,
                "day": date(2026, 2, 1),
                "start": date(2026, 2, 1),
                "end": None,
            }
            for value in (1, 2)
        ]
        if failure == "missing":
            records.pop()
        elif failure == "overlap":
            records.append(dict(records[0]))
        if entity_name != "customers":
            records.append(
                {
                    "id": 1,
                    "region": "old",
                    "day": date(2026, 1, 31),
                    "start": date(2026, 1, 1),
                    "end": date(2026, 2, 1),
                }
            )
        source = backend.create_table(
            "subjects",
            pa.Table.from_pylist(
                records,
                schema=pa.schema(
                    [
                        ("id", pa.int64()),
                        ("region", pa.string()),
                        ("day", pa.date32()),
                        ("start", pa.date32()),
                        ("end", pa.date32()),
                    ]
                ),
            ),
        )
        checks: list[CompiledValidation] = []
        output = lower_event_axes(
            table,
            (binding,),
            owner,
            {subject.ref.path: source},
            step_key="a",
            freeze=lambda value: value,
            add_validation=checks.append,
        )
        if failure:
            assert sum(_scalar(backend, check.expression) for check in checks) > 0
        else:
            rows = backend.to_pyarrow(output).to_pylist()
            assert len(rows) == 6
            assert [row["region"] for row in rows] == ["correct"] * 3 + [None] * 3
            assert all(_scalar(backend, check.expression) == 0 for check in checks)
    finally:
        backend.disconnect()


def test_axis_enrichment_follows_complete_governed_two_hop_path() -> None:
    owner = make_event_sources()._owner
    subject = normalize_target_entity(owner.semantic_registry, "sales.lines")
    dimension = replace(
        normalize_target_dimension(owner.semantic_registry, "sales.customers.region"),
        nullable=True,
    )
    binding = EventAxisBinding(
        dimension, subject, ("sales.line_order", "sales.order_customer"), "fixture"
    )
    backend = ibis.duckdb.connect()
    try:
        table = _relation(backend, (_Journey(1, (0, 2, 5)), _Journey(2, (1, None, None)))).drop(
            "region"
        )
        sources = {
            "sales.lines": backend.create_table(
                "lines", pa.table({"id": [1, 2], "order_id": [10, 20]})
            ),
            "sales.orders": backend.create_table(
                "orders", pa.table({"id": [10, 20], "customer_id": [100, 200]})
            ),
            "sales.customers": backend.create_table(
                "customers", pa.table({"id": [100, 200], "region": ["north", None]})
            ),
        }
        checks: list[CompiledValidation] = []
        output = lower_event_axes(
            table,
            (binding,),
            owner,
            sources,
            step_key="a",
            freeze=lambda value: value,
            add_validation=checks.append,
        )
        actual = backend.to_pyarrow(output).to_pylist()
        assert len(actual) == 6
        assert [row["region"] for row in actual] == ["north"] * 3 + [None] * 3
        assert all(_scalar(backend, check.expression) == 0 for check in checks)
    finally:
        backend.disconnect()
