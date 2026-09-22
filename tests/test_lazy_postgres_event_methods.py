"""Read-only PostgreSQL Event journey acceptance with independent source rows."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from psycopg import sql

from marivo.analysis import time_scope
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
from marivo.analysis.domains.contracts import (
    EventPayload,
    journey_identity_digest,
    journey_semantics,
)
from marivo.analysis.event import every_start, first_per_subject, sequence, step
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.event import participant_role
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import END, OCCURRENCE_CANARY, START, THROUGH, journey
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1",
        reason="opt-in PostgreSQL service",
    ),
]


@pytest.fixture
def event_tables() -> Iterator[dict[str, str]]:
    names = {
        logical: "c9_" + logical + "_" + uuid4().hex
        for logical in ("customers", "started_rows", "finished_rows")
    }
    with pg.connection(admin=True) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE TABLE {} (id bigint, region text)").format(
                    sql.Identifier(names["customers"])
                )
            )
            for logical in ("started_rows", "finished_rows"):
                admin.execute(
                    sql.SQL(
                        "CREATE TABLE {} (occurrence_id bigint, customer_id bigint, "
                        "occurred_at timestamp)"
                    ).format(sql.Identifier(names[logical]))
                )
            for name in names.values():
                admin.execute(
                    sql.SQL("GRANT SELECT ON {} TO analysis_reader").format(sql.Identifier(name))
                )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (1,'EU'),(2,'US'),(3,'EU')").format(
                    sql.Identifier(names["customers"])
                )
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,2,%s),(%s,1,%s),(%s,3,%s)").format(
                    sql.Identifier(names["started_rows"])
                ),
                (
                    OCCURRENCE_CANARY + 1,
                    START.replace(tzinfo=None),
                    OCCURRENCE_CANARY,
                    START.replace(tzinfo=None),
                    OCCURRENCE_CANARY + 2,
                    END.replace(tzinfo=None),
                ),
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,2,%s),(%s,1,%s)").format(
                    sql.Identifier(names["finished_rows"])
                ),
                (
                    OCCURRENCE_CANARY + 11,
                    THROUGH.replace(tzinfo=None),
                    OCCURRENCE_CANARY + 10,
                    START.replace(tzinfo=None),
                ),
            )
            yield names
        finally:
            for name in names.values():
                admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def _registry(
    names: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(Path("unused.duckdb"))
    monkeypatch.setenv("MARIVO_TEST_POSTGRES_PASSWORD", pg.password())
    entities = dict(registry.entities)
    for logical, table in names.items():
        path = "sales." + logical
        entity = entities[path]
        assert isinstance(entity.source, TableSourceIR)
        entities[path] = replace(
            entity,
            source=replace(entity.source, table=table, database="public"),
        )
    registry = replace(
        registry,
        entities=entities,
        datasources={
            name: replace(
                datasource,
                backend_type="postgres",
                fields={
                    "host": pg.HOST,
                    "port": pg.PORT,
                    "database": pg.DATABASE,
                    "user": pg.READER,
                },
                env_refs={"password": "MARIVO_TEST_POSTGRES_PASSWORD"},
            )
            for name, datasource in registry.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar


def test_event_journey_is_one_read_only_source_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-event", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = journey(sources)
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, EventPayload)
    definition_digest = journey_identity_digest(journey_semantics(logical._root.payload.definition))
    result = logical.execute()
    frame = result.to_pandas()
    assert len(frame) == 4
    assert frame.completion_status.tolist() == ["complete", "complete", "incomplete", "incomplete"]
    assert frame.occurred_at.notna().sum() == 3
    expected_ids = []
    for subject, anchor in ((1, OCCURRENCE_CANARY), (2, OCCURRENCE_CANARY + 1)):
        identity = {"entity_identity": {"id": subject}, "anchor_event_identity": {"k0": anchor}}
        encoded = (
            "event_journey@v1:"
            + definition_digest
            + ":"
            + json.dumps(identity, separators=(",", ":"))
        )
        expected_ids.append("journey_" + hashlib.sha256(encoded.encode()).hexdigest())
    assert frame.journey_id.tolist() == [
        expected_ids[0],
        expected_ids[0],
        expected_ids[1],
        expected_ids[1],
    ]
    bundles = [item for item in runtime.statistics.submissions if item.role == "event_bundle"]
    assert len(bundles) == 1
    assert bundles[0].sql.startswith("WITH ")
    assert bundles[0].state == "succeeded"
    assert not any(
        item.sql.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))
        for item in runtime.statistics.submissions
        if item.domain == "source"
    )


def test_every_start_exclusive_assigns_distinct_completions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["started_rows"])
            ),
            (OCCURRENCE_CANARY + 3, (START + timedelta(hours=2)).replace(tzinfo=None)),
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["finished_rows"])
            ),
            (OCCURRENCE_CANARY + 13, (START + timedelta(hours=3)).replace(tzinfo=None)),
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-every-start", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        journey(sources, matching=every_start(completion_assignment="exclusive"))
        .execute()
        .to_pandas()
    )
    assert len(frame) == 6
    assert frame.completion_status.tolist() == [
        "complete",
        "complete",
        "complete",
        "complete",
        "incomplete",
        "incomplete",
    ]
    assert frame.journey_id.nunique() == 3
    assert (
        len([item for item in runtime.statistics.submissions if item.role == "event_bundle"]) == 1
    )


def test_every_start_shared_may_reuse_one_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["started_rows"])
            ),
            (OCCURRENCE_CANARY + 3, (START + timedelta(hours=1)).replace(tzinfo=None)),
        )
        admin.execute(
            sql.SQL("DELETE FROM {} WHERE customer_id=1").format(
                sql.Identifier(event_tables["finished_rows"])
            )
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["finished_rows"])
            ),
            (OCCURRENCE_CANARY + 13, (START + timedelta(hours=3)).replace(tzinfo=None)),
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-every-start-shared", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        journey(sources, matching=every_start(completion_assignment="shared")).execute().to_pandas()
    )
    assert len(frame) == 6
    assert frame.completion_status.tolist() == [
        "complete",
        "complete",
        "complete",
        "complete",
        "incomplete",
        "incomplete",
    ]
    assert frame.event_identity.iloc[1] == frame.event_identity.iloc[3]
    assert frame.journey_id.nunique() == 3


def test_three_step_repeat_event_requires_distinct_occurrences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["finished_rows"])
            ),
            (OCCURRENCE_CANARY + 13, (START + timedelta(hours=1)).replace(tzinfo=None)),
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-three-step", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    started, finished = ref.event("sales.started"), ref.event("sales.finished")
    pattern = sequence(
        step(participant=participant_role(event=started, name="buyer"), key="start"),
        step(participant=participant_role(event=finished, name="buyer"), key="first_finish"),
        step(participant=participant_role(event=finished, name="buyer"), key="second_finish"),
    )
    frame = (
        sources.events.match(
            pattern,
            cohort_window=time_scope(start=START.isoformat(), end=END.isoformat()),
            completion_through=THROUGH,
            matching=first_per_subject(),
            completeness=(
                BoundedCompletenessDeclarationV1(
                    inputs=(started, finished),
                    complete_from=START,
                    complete_through=THROUGH,
                    rationale="Fixture provider covers the complete requested range.",
                ),
            ),
        )
        .execute()
        .to_pandas()
    )
    assert len(frame) == 6
    assert frame.step_key.tolist() == [
        "start",
        "first_finish",
        "second_finish",
        "start",
        "first_finish",
        "second_finish",
    ]
    assert frame.completion_status.tolist() == ["complete"] * 3 + ["incomplete"] * 3
    assert frame.event_identity.iloc[1] != frame.event_identity.iloc[2]
    assert frame.event_identity.iloc[4] is None
    assert frame.event_identity.iloc[5] is None


@pytest.mark.parametrize("assignment", ["shared", "exclusive"])
def test_three_step_every_start_uses_distinct_successive_occurrences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
    assignment: Literal["shared", "exclusive"],
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("DELETE FROM {} WHERE customer_id=1").format(
                sql.Identifier(event_tables["finished_rows"])
            )
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["started_rows"])
            ),
            (OCCURRENCE_CANARY + 3, (START + timedelta(hours=2)).replace(tzinfo=None)),
        )
        for offset, hour in ((13, 1), (14, 3), (15, 4)):
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                    sql.Identifier(event_tables["finished_rows"])
                ),
                (OCCURRENCE_CANARY + offset, (START + timedelta(hours=hour)).replace(tzinfo=None)),
            )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-three-step-every", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    started, finished = ref.event("sales.started"), ref.event("sales.finished")
    pattern = sequence(
        step(participant=participant_role(event=started, name="buyer"), key="start"),
        step(participant=participant_role(event=finished, name="buyer"), key="first_finish"),
        step(participant=participant_role(event=finished, name="buyer"), key="second_finish"),
    )
    frame = (
        sources.events.match(
            pattern,
            cohort_window=time_scope(start=START.isoformat(), end=END.isoformat()),
            completion_through=THROUGH,
            matching=every_start(completion_assignment=assignment),
            completeness=(
                BoundedCompletenessDeclarationV1(
                    inputs=(started, finished),
                    complete_from=START,
                    complete_through=THROUGH,
                    rationale="Fixture provider covers the complete requested range.",
                ),
            ),
        )
        .execute()
        .to_pandas()
    )
    assert len(frame) == 9
    assert frame.completion_status.tolist() == ["complete"] * 6 + ["incomplete"] * 3
    assert frame.event_identity.iloc[2] != frame.event_identity.iloc[5]
    assert frame.event_identity.iloc[1] != frame.event_identity.iloc[2]
    assert frame.event_identity.iloc[4] != frame.event_identity.iloc[5]


@pytest.mark.parametrize("bad", ["duplicate", "null"])
def test_invalid_occurrence_identity_rejects_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str], bad: str
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                sql.Identifier(event_tables["started_rows"])
            ),
            (OCCURRENCE_CANARY if bad == "duplicate" else None, START.replace(tzinfo=None)),
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-event-duplicate", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with pytest.raises(MaterializationError, match="Event validation failed"):
        journey(sources).execute()
    assert (
        len([item for item in runtime.statistics.submissions if item.role == "event_bundle"]) == 1
    )
    with sqlite3.connect(runtime.store.db_path) as local:
        assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)


def test_empty_journey_still_checks_membership_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("DELETE FROM {}").format(sql.Identifier(event_tables["started_rows"]))
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (1,'EU')").format(
                sql.Identifier(event_tables["customers"])
            )
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-event-empty", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with pytest.raises(MaterializationError, match="Event validation failed"):
        journey(sources).execute()
    assert (
        len([item for item in runtime.statistics.submissions if item.role == "event_bundle"]) == 1
    )
    with sqlite3.connect(runtime.store.db_path) as local:
        assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)


def test_equal_time_assignment_ambiguity_rejects_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("DELETE FROM {} WHERE customer_id=1").format(
                sql.Identifier(event_tables["finished_rows"])
            )
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,1,%s),(%s,1,%s)").format(
                sql.Identifier(event_tables["finished_rows"])
            ),
            (
                OCCURRENCE_CANARY,
                START.replace(tzinfo=None),
                OCCURRENCE_CANARY + 20,
                (START + timedelta(hours=1)).replace(tzinfo=None),
            ),
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "postgres-event-tie", "postgres-event")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with pytest.raises(MaterializationError, match=r"event\.ambiguous_event_order"):
        journey(sources).execute()
    with sqlite3.connect(runtime.store.db_path) as local:
        assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)


@pytest.mark.parametrize("variant", ["first", "every"])
def test_committed_journey_opens_in_cold_process_without_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str], variant: str
) -> None:
    if variant == "every":
        with pg.connection(admin=True) as admin:
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                    sql.Identifier(event_tables["started_rows"])
                ),
                (OCCURRENCE_CANARY + 3, (START + timedelta(hours=2)).replace(tzinfo=None)),
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                    sql.Identifier(event_tables["finished_rows"])
                ),
                (OCCURRENCE_CANARY + 13, (START + timedelta(hours=3)).replace(tzinfo=None)),
            )
    registry, sidecar = _registry(event_tables, monkeypatch)
    project = tmp_path / "postgres-event-cold"
    runtime = DatasetRuntime.create(project, "postgres-event")
    result = journey(
        runtime.sources(semantic_registry=registry, sidecar=sidecar),
        matching=(
            every_start(completion_assignment="exclusive")
            if variant == "every"
            else first_per_subject()
        ),
    ).execute()
    expected = (
        ["complete"] * 4 + ["incomplete"] * 2
        if variant == "every"
        else ["complete"] * 2 + ["incomplete"] * 2
    )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; from marivo.analysis.materialization.admission import DatasetRuntime; "
            "import json, sys; runtime = DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2]); "
            "frame = runtime.artifact(sys.argv[3]).to_pandas(); "
            "assert frame.completion_status.tolist() == json.loads(sys.argv[4]); "
            "assert not runtime.statistics.submissions",
            str(project),
            runtime.session_ref,
            result.state.artifact_ref.ref,
            json.dumps(expected),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
