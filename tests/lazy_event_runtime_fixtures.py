"""Small real Event sources shared by isolated Runtime acceptance phases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from marivo.analysis import time_scope
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    EventCoverageProvider,
)
from marivo.analysis.domains.event import LogicalEventDataset
from marivo.analysis.event import EveryStart, FirstPerSubject, first_per_subject, sequence, step
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import EngineTarget
from marivo.analysis.observation.metric import PopulationInput
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from marivo.semantic.event import participant_role
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_execution_fixtures import seed_execution_database

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 2, tzinfo=timezone.utc)
THROUGH = datetime(2026, 2, 3, tzinfo=timezone.utc)
OCCURRENCE_CANARY = 981730041


def setup_event(
    project: Path,
    *,
    engine: bool = False,
    event: Callable[[str], None] | None = None,
    provider: EventCoverageProvider | None = None,
) -> tuple[DatasetRuntime, LazySources, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        for name in ("started_rows", "finished_rows"):
            connection.execute(
                f"CREATE TABLE {name} (occurrence_id BIGINT, customer_id BIGINT, occurred_at TIMESTAMP)"
            )
        connection.execute(
            "INSERT INTO started_rows VALUES (?,1,'2026-02-01 00:00:00'), (?,1,'2026-02-01 02:00:00'), (?,2,'2026-02-01 12:00:00'), (?,3,'2026-02-02 00:00:00')",
            [OCCURRENCE_CANARY + i for i in range(4)],
        )
        connection.execute(
            "INSERT INTO finished_rows VALUES (?,1,'2026-02-01 03:00:00'), (?,1,'2026-02-02 01:00:00'), (?,2,'2026-02-03 00:00:00')",
            [OCCURRENCE_CANARY + 10 + i for i in range(3)],
        )
    runtime = DatasetRuntime.create(
        project, "event-runtime", event=event, event_coverage_provider=provider
    )
    if engine:
        runtime.target = EngineTarget("warehouse")
    registry, sidecar = make_event_registry(database)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


def journey(
    sources: LazySources,
    *,
    complete: bool = True,
    matching: FirstPerSubject | EveryStart | None = None,
    population: PopulationInput | None = None,
) -> LogicalEventDataset:
    started, finished = ref.event("sales.started"), ref.event("sales.finished")
    pattern = sequence(
        step(participant=participant_role(event=started, name="buyer"), key="start"),
        step(participant=participant_role(event=finished, name="buyer"), key="finish"),
    )
    return sources.events.match(
        pattern,
        cohort_window=time_scope(start=START.isoformat(), end=END.isoformat()),
        completion_through=THROUGH,
        matching=first_per_subject() if matching is None else matching,
        population=population,
        completeness=(
            BoundedCompletenessDeclarationV1(
                inputs=(started, finished),
                complete_from=START,
                complete_through=THROUGH,
                rationale="Fixture provider covers the complete requested range.",
            ),
        )
        if complete
        else (),
    )
