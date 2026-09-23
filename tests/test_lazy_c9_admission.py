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
        *((backend, False, "source-side occurrence identity") for backend in ("sqlite", "mysql")),
        *((backend, True, "recursive replay") for backend in ("sqlite", "mysql")),
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


@pytest.mark.parametrize("backend", ["postgres", "trino", "clickhouse"])
def test_lifecycle_is_placed_without_source_access(backend: BackendName) -> None:
    assert place(history(_sources(backend, lifecycle=True))).steps


@pytest.mark.parametrize("backend", ["clickhouse", "trino"])
def test_remote_two_step_event_is_placed_without_source_access(backend: BackendName) -> None:
    assert place(journey(_sources(backend, lifecycle=False))).steps


@pytest.mark.parametrize("backend", ["sqlite", "mysql", "trino", "clickhouse"])
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
    closed = derived[3:] if backend == "trino" else derived
    for dataset in closed:
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


def test_trino_grouped_funnel_and_attribution_reject_before_source_access() -> None:
    from marivo.analysis.funnel import funnel_loss_rate
    from marivo.refs import ref

    event = journey(_sources("trino", lifecycle=False))
    assert isinstance(event._root, LogicalRootHandle)
    assert isinstance(event._root.payload, EventPayload)
    finish = event._root.payload.definition.pattern.steps[-1]
    axis = ref.dimension("sales.customers.region")
    funnel = event.funnel()
    for dataset in (
        event.funnel(axes=(axis,)),
        funnel.compare(funnel).attribute(target=funnel_loss_rate(step=finish), axes=(axis,)),
    ):
        with pytest.raises(DatasetCompilationError, match="stage budget"):
            place(dataset)


def test_trino_direct_repeated_time_to_event_is_not_qualified() -> None:
    event = journey(
        _sources("trino", lifecycle=False),
        matching=every_start(completion_assignment="exclusive"),
    )
    assert isinstance(event._root, LogicalRootHandle)
    assert isinstance(event._root.payload, EventPayload)
    start, finish = event._root.payload.definition.pattern.steps
    with pytest.raises(DatasetCompilationError, match="first_per_subject"):
        place(event.time_to_event(from_step=start, to_step=finish))


@pytest.mark.parametrize("backend", ["trino", "clickhouse"])
@pytest.mark.parametrize("identity", ["subject", "occurrence"])
def test_remote_lifecycle_composite_identity_remains_unqualified(
    backend: BackendName, identity: str
) -> None:
    from marivo.analysis.domains.lifecycle import LifecyclePayload
    from marivo.analysis.operators.registry import _lifecycle_reason

    dataset = history(_sources(backend, lifecycle=True))
    assert isinstance(dataset._root, LogicalRootHandle)
    assert isinstance(dataset._root.payload, LifecyclePayload)
    definition = dataset._root.payload.definition
    if identity == "subject":
        definition = replace(
            definition,
            entity=replace(
                definition.entity, identity_signature=(("id", "int64"), ("second_id", "int64"))
            ),
        )
    else:
        first, *rest = definition.steps
        definition = replace(
            definition, steps=(replace(first, identity=(*first.identity, *first.identity)), *rest)
        )
    reason = _lifecycle_reason(definition, backend)
    assert reason is not None and "one int64 component" in reason
