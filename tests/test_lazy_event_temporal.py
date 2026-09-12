"""Real Event participant versions remain independent of membership authority."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import time_scope
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.event import every_start, first_per_subject, sequence, step
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.contracts import scope_payload
from marivo.analysis.session._lazy_sources import LazySources
from marivo.datasource.ir import TableColumnBindingIR, TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody
from marivo.semantic.event import participant_role
from marivo.semantic.ir import DateParse, EventParticipantIR, SnapshotVersioningIR
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_event_runtime_fixtures import (
    OCCURRENCE_CANARY,
    START,
    THROUGH,
    journey,
    setup_event,
)

pytestmark = pytest.mark.runtime

_MEMBERSHIP = time_scope(start="2026-01-01", end="2026-02-01")
_Version = Literal["snapshots", "validity"]


def _temporal_sources(project: Path, version: _Version) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, original_sources, database = setup_event(project, engine=True)
    original = original_sources._owner.semantic_registry
    registry = replace(
        original,
        relationships={
            **original.relationships,
            **{
                f"sales.{name}_customer": replace(
                    original.relationships[f"sales.{name}_customer"],
                    to_entity=f"sales.{version}",
                )
                for name in ("started", "finished")
            },
        },
    )
    registry.freeze()
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute(f"DELETE FROM {version}")
        if version == "snapshots":
            connection.execute(
                "INSERT INTO snapshots (id, day) VALUES "
                "(1, DATE '2026-01-31'), "
                "(1, DATE '2026-02-01'), (1, DATE '2026-02-02'), "
                "(2, DATE '2026-02-01'), (2, DATE '2026-02-02'), "
                "(3, DATE '2026-02-02')"
            )
        else:
            connection.execute(
                'INSERT INTO validity (id, start, "end") VALUES '
                "(1, DATE '2026-01-01', DATE '2026-02-01'), "
                "(1, DATE '2026-02-01', DATE '2026-02-02'), "
                "(1, DATE '2026-02-02', NULL), "
                "(2, DATE '2026-02-01', NULL), "
                "(3, DATE '2026-02-01', NULL)"
            )
    return (
        runtime,
        runtime.sources(semantic_registry=registry, sidecar=original_sources._owner.sidecar),
        database,
    )


@pytest.mark.parametrize("version", ["snapshots", "validity"])
@pytest.mark.parametrize("retained", [False, True])
def test_occurrences_resolve_current_versions_independently_of_membership_scope(
    tmp_path: Path, version: _Version, retained: bool
) -> None:
    runtime, sources, database = _temporal_sources(tmp_path, version)
    membership = sources.population(ref.entity(f"sales.{version}"), time_scope=_MEMBERSHIP)
    population = membership.execute() if retained else membership
    if retained:
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            if version == "snapshots":
                connection.execute("DELETE FROM snapshots WHERE day = DATE '2026-01-31'")
            else:
                connection.execute("DELETE FROM validity WHERE start = DATE '2026-01-01'")
    logical = journey(
        sources,
        matching=every_start(completion_assignment="exclusive"),
        population=population,
    )
    semantics = logical.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    assert semantics.cohort_start == START.isoformat()
    assert semantics.completion_through == THROUGH.isoformat()
    result = logical.execute()
    rows = result.to_pandas()
    assert rows.entity_identity.tolist() == [(1,)] * 4
    assert rows.step_key.tolist() == ["start", "finish", "start", "finish"]
    assert rows.event_identity.tolist() == [
        (OCCURRENCE_CANARY,),
        (OCCURRENCE_CANARY + 10,),
        (OCCURRENCE_CANARY + 1,),
        (OCCURRENCE_CANARY + 11,),
    ]
    assert rows.completion_status.tolist() == ["complete"] * 4
    assert [value.isoformat() for value in rows.occurred_at] == [
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T03:00:00+00:00",
        "2026-02-01T02:00:00+00:00",
        "2026-02-02T01:00:00+00:00",
    ]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert record.descriptor.population_authority.membership_scope == scope_payload(_MEMBERSHIP)
    assert runtime.statistics.local_handoffs == ()
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("version", ["snapshots", "validity"])
@pytest.mark.parametrize("failure", ["missing", "overlap"])
def test_missing_or_overlapping_occurrence_versions_fail_atomically(
    tmp_path: Path, version: _Version, failure: str
) -> None:
    runtime, sources, database = _temporal_sources(tmp_path, version)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        if version == "snapshots":
            if failure == "missing":
                connection.execute("DELETE FROM snapshots WHERE id = 1 AND day = DATE '2026-02-01'")
            else:
                connection.execute("INSERT INTO snapshots (id, day) VALUES (1, DATE '2026-02-01')")
        elif failure == "missing":
            connection.execute("DELETE FROM validity WHERE id = 1 AND start = DATE '2026-02-01'")
        else:
            connection.execute(
                'INSERT INTO validity (id, start, "end") VALUES '
                "(1, DATE '2026-02-01', DATE '2026-02-03')"
            )
    population = sources.population(ref.entity(f"sales.{version}"), time_scope=_MEMBERSHIP)
    with pytest.raises(MaterializationError) as caught:
        journey(sources, population=population).execute()
    assert str(OCCURRENCE_CANARY) not in str(caught.value)
    counts = snapshot(runtime)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()


def test_retained_local_membership_cannot_cross_the_event_source_boundary(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path)
    population = sources.population(ref.entity("sales.customers")).execute()
    before = snapshot(runtime)
    runtime.target = LocalTarget()
    with pytest.raises(DatasetCompilationError, match="source-required"):
        journey(sources, population=population).execute()
    after = snapshot(runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()


def _self_subject_sources(project: Path) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, original_sources, database = setup_event(project, engine=True)
    original = original_sources._owner.semantic_registry
    entity_ref = ref.entity("sales.started_rows")
    event_ref = ref.event("sales.started")
    snapshot_ref = ref.time_dimension("sales.started_rows.snapshot_at")
    entity = original.entities[entity_ref.path]
    assert isinstance(entity.source, TableSourceIR)
    body = ExpressionBody.for_column("snapshot_day")
    registry = replace(
        original,
        entities={
            **original.entities,
            entity_ref.path: replace(
                entity,
                source=replace(
                    entity.source,
                    columns=(
                        *entity.source.columns,
                        ("snapshot_day", TableColumnBindingIR("snapshot_day", "date")),
                    ),
                ),
                versioning=SnapshotVersioningIR("snapshot", snapshot_ref.path, "day"),
            ),
        },
        dimensions={
            **original.dimensions,
            "sales.started_rows.occurred_at": replace(
                original.dimensions["sales.started_rows.occurred_at"], is_default=False
            ),
            snapshot_ref.path: replace(
                original.dimensions["sales.started_rows.occurred_at"],
                semantic_id=snapshot_ref.path,
                name="snapshot_at",
                granularity="day",
                parse=DateParse(),
                is_default=True,
                body_ast_hash=body.body_ast_hash,
                source_column="snapshot_day",
            ),
        },
        events={
            **original.events,
            event_ref.path: replace(
                original.events[event_ref.path],
                participants=(EventParticipantIR("buyer", (), "one"),),
            ),
        },
    )
    registry.freeze()
    old_sidecar = original_sources._owner.sidecar
    sidecar = CompiledExpressionSidecar(
        bodies={**old_sidecar.bodies, snapshot_ref: body},
        field_owners={**old_sidecar.field_owners, snapshot_ref: entity_ref},
        catalog_refs=old_sidecar.catalog_refs | {snapshot_ref},
    )
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("ALTER TABLE started_rows ADD COLUMN snapshot_day DATE")
        connection.execute("UPDATE started_rows SET snapshot_day = CAST(occurred_at AS DATE)")
        connection.execute(
            "INSERT INTO started_rows VALUES (?, 1, TIMESTAMP '2026-01-31 12:00:00', DATE '2026-01-31')",
            [OCCURRENCE_CANARY],
        )
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


@pytest.mark.parametrize("missing_version", [False, True])
def test_versioned_event_source_self_subject_obeys_its_occurrence_instant(
    tmp_path: Path, missing_version: bool
) -> None:
    runtime, sources, database = _self_subject_sources(tmp_path)
    if missing_version:
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute(
                "UPDATE started_rows SET snapshot_day = DATE '2026-02-02' "
                "WHERE occurrence_id = ? AND snapshot_day = DATE '2026-02-01'",
                [OCCURRENCE_CANARY],
            )
    event_ref = ref.event("sales.started")
    population = sources.population(ref.entity("sales.started_rows"), time_scope=_MEMBERSHIP)
    logical = sources.events.match(
        sequence(step(participant=participant_role(event=event_ref, name="buyer"), key="start")),
        cohort_window=time_scope(start=START.isoformat(), end="2026-02-02T00:00:00+00:00"),
        completion_through=THROUGH,
        matching=first_per_subject(),
        population=population,
        completeness=(
            BoundedCompletenessDeclarationV1(
                inputs=(event_ref,),
                complete_from=START,
                complete_through=THROUGH,
                rationale="The source fixture covers the full occurrence range.",
            ),
        ),
    )
    if missing_version:
        with pytest.raises(MaterializationError) as caught:
            logical.execute()
        assert str(OCCURRENCE_CANARY) not in str(caught.value)
        counts = snapshot(runtime)
        assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    else:
        result = logical.execute()
        rows = result.to_pandas()
        assert rows.entity_identity.tolist() == [(OCCURRENCE_CANARY,)]
        assert rows.event_identity.tolist() == [(OCCURRENCE_CANARY,)]
        assert rows.occurred_at.iloc[0].isoformat() == START.isoformat()
        assert rows.completion_status.tolist() == ["complete"]
