"""Remote Event and Lifecycle admission is exact and precedes source access."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ibis
import pytest
from ibis.backends.clickhouse import Backend as ClickHouseBackend
from ibis.backends.mysql import Backend as MySQLBackend
from ibis.backends.postgres import Backend as PostgresBackend
from ibis.backends.sqlite import Backend as SQLiteBackend
from ibis.backends.trino import Backend as TrinoBackend

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.event import EventStepRelation, compile_event_match
from marivo.analysis.compiler.placement import place
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import EventPayload
from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.event import every_start, first_per_subject
from marivo.analysis.operators.registry import BackendName, implementation
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.analysis.subject import dropped_before
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import journey
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, lifecycle_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def _sources(backend: BackendName, *, lifecycle: bool) -> LazySources:
    registry, sidecar = (
        lifecycle_registry(Path("/nonexistent/c9-lifecycle.duckdb"))
        if lifecycle
        else make_event_registry(Path("/nonexistent/c9-event.duckdb"))
    )
    registry = replace(
        registry,
        datasources={
            name: replace(value, backend_type=backend)
            for name, value in registry.datasources.items()
        },
    )
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="c9-admission",
        store_id="c9-admission",
    )


@pytest.mark.parametrize(
    ("backend", "lifecycle", "reason"),
    [
        *(
            (backend, False, "source-side occurrence identity")
            for backend in ("sqlite", "mysql", "trino", "clickhouse")
        ),
        *(
            (backend, True, "recursive replay")
            for backend in ("sqlite", "mysql", "trino", "clickhouse")
        ),
    ],
)
def test_remote_event_lifecycle_reject_before_source_access(
    backend: BackendName, lifecycle: bool, reason: str
) -> None:
    sources = _sources(backend, lifecycle=lifecycle)
    dataset = history(sources) if lifecycle else journey(sources)
    with pytest.raises(DatasetCompilationError, match=reason) as rejected:
        place(dataset)
    assert rejected.value.location == "dataset.compiler"
    assert rejected.value.received is not None and f"backend={backend}" in rejected.value.received


def test_postgres_two_step_event_is_placed_without_source_access() -> None:
    dataset = journey(_sources("postgres", lifecycle=False))
    assert place(dataset).steps


def test_postgres_every_start_is_placed_without_source_access() -> None:
    dataset = journey(
        _sources("postgres", lifecycle=False),
        matching=every_start(completion_assignment="exclusive"),
    )
    assert place(dataset).steps


def test_postgres_lifecycle_rejects_before_source_access() -> None:
    with pytest.raises(DatasetCompilationError, match="recursive replay"):
        place(history(_sources("postgres", lifecycle=True)))


@pytest.mark.parametrize("backend", ["postgres", "sqlite", "mysql", "trino", "clickhouse"])
def test_derived_c9_cells_remain_closed_with_their_source(backend: BackendName) -> None:
    event = journey(_sources(backend, lifecycle=False))
    assert isinstance(event._root, LogicalRootHandle)
    assert isinstance(event._root.payload, EventPayload)
    from_step, to_step = event._root.payload.definition.pattern.steps
    lifecycle = history(_sources(backend, lifecycle=True))
    derived = (
        event.funnel(),
        event.time_to_event(from_step=from_step, to_step=to_step),
        event.select_subjects(dropped_before(step=to_step)),
        lifecycle.distribution(at=(START,)),
        lifecycle.transitions(),
        lifecycle.dwell(),
        lifecycle.violations(),
        lifecycle.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END)),
    )
    for dataset in derived:
        assert implementation(dataset).for_backend(backend) is None
        with pytest.raises(DatasetCompilationError, match="backend="):
            place(dataset)


@pytest.mark.parametrize(
    ("backend_type", "missing_lowering"),
    [
        (PostgresBackend, "HexDigest"),
        (SQLiteBackend, "Struct types aren't supported"),
        (MySQLBackend, "StructColumn"),
        (TrinoBackend, "HexDigest"),
        (ClickHouseBackend, "HexDigest"),
    ],
)
def test_event_match_compiler_probe_requires_backend_work(
    backend_type: type[PostgresBackend]
    | type[SQLiteBackend]
    | type[MySQLBackend]
    | type[TrinoBackend]
    | type[ClickHouseBackend],
    missing_lowering: str,
) -> None:
    """A successful validation compile cannot authorize the primary result."""

    def occurrence(name: str) -> EventStepRelation:
        table = ibis.table(
            {"subject": "int64", "event_id": "int64", "at": 'timestamp("UTC")'},
            name=name,
        )
        return EventStepRelation(
            name,
            name,
            table.select(
                entity_identity=ibis.struct({"id": table.subject}),
                event_identity=ibis.struct({"id": table.event_id}),
                occurred_at=table.at,
            ),
        )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows, checks, _ = compile_event_match(
        (occurrence("started"), occurrence("finished")),
        matching=first_per_subject(),
        cohort_start=start,
        cohort_end=start + timedelta(days=1),
        completion_through=start + timedelta(days=2),
        definition_digest="c9-probe",
        coverage_complete=True,
    )
    backend = backend_type()
    if backend_type in (PostgresBackend, TrinoBackend, ClickHouseBackend):
        assert backend.compile(checks[-1].expression)
    with pytest.raises(Exception, match=missing_lowering):
        backend.compile(rows)
