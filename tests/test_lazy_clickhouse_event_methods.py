"""Live read-only ClickHouse Event qualification with independent expectations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventPayload,
    journey_identity_digest,
    journey_semantics,
)
from marivo.analysis.event import every_start
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.clickhouse_execution import ClickHouseCursor
from marivo.analysis.materialization.errors import MaterializationError
from tests.lazy_clickhouse_event_fixtures import event_registry as _registry
from tests.lazy_clickhouse_event_fixtures import event_source_tables
from tests.lazy_event_runtime_fixtures import END, OCCURRENCE_CANARY, START, THROUGH, journey
from tests.multisource_environment import clickhouse_analysis as ch

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
    ),
]


@pytest.fixture
def event_tables() -> Iterator[dict[str, str]]:
    with event_source_tables() as names:
        yield names


def test_event_journey_source_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_tables: dict[str, str]
) -> None:
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "clickhouse-event", "clickhouse-event")
    logical = journey(runtime.sources(semantic_registry=registry, sidecar=sidecar))
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, EventPayload)
    digest = journey_identity_digest(journey_semantics(logical._root.payload.definition))
    frame = logical.execute().to_pandas()
    assert frame.completion_status.tolist() == ["complete"] * 2 + ["incomplete"] * 2
    assert frame.occurred_at.notna().sum() == 3
    expected = []
    for subject, anchor in ((1, OCCURRENCE_CANARY), (2, OCCURRENCE_CANARY + 1)):
        value = {"entity_identity": {"id": subject}, "anchor_event_identity": {"k0": anchor}}
        encoded = "event_journey@v1:" + digest + ":" + json.dumps(value, separators=(",", ":"))
        expected.extend(["journey_" + hashlib.sha256(encoded.encode()).hexdigest()] * 2)
    assert frame.journey_id.tolist() == expected
    assert (
        len([item for item in runtime.statistics.submissions if item.role == "event_bundle"]) == 1
    )
    with ch.connection(admin=True) as admin:
        admin.command("SYSTEM FLUSH LOGS")
        observed = admin.query(
            "SELECT query, user FROM system.query_log WHERE type='QueryFinish' "
            "AND user='analysis_reader' AND startsWith(query, 'WITH ') "
            "AND position(query, {table:String}) > 0",
            parameters={"table": event_tables["started_rows"]},
        ).result_rows
    assert len(observed) == 1
    assert observed[0][1] == "analysis_reader"
    assert observed[0][0].startswith("WITH ")


@pytest.mark.parametrize("assignment", ["shared", "exclusive"])
def test_every_start_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
    assignment: Literal["shared", "exclusive"],
) -> None:
    with ch.connection(admin=True) as admin:
        admin.command(f"TRUNCATE TABLE {event_tables['finished_rows']}")
        admin.insert(
            event_tables["started_rows"], [(OCCURRENCE_CANARY + 3, 1, START + timedelta(hours=1))]
        )
        admin.insert(
            event_tables["finished_rows"], [(OCCURRENCE_CANARY + 13, 1, START + timedelta(hours=3))]
        )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "every-start", "clickhouse-event")
    result = journey(
        runtime.sources(semantic_registry=registry, sidecar=sidecar),
        matching=every_start(completion_assignment=assignment),
    ).execute()
    frame = result.to_pandas()
    expected = ["complete"] * (4 if assignment == "shared" else 2)
    expected += ["incomplete"] * (2 if assignment == "shared" else 4)
    assert frame.completion_status.tolist() == expected
    assert frame.journey_id.nunique() == 3
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; import json,sys; "
            "from marivo.analysis.materialization.admission import DatasetRuntime; "
            "runtime=DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2]); "
            "frame=runtime.artifact(sys.argv[3]).to_pandas(); "
            "assert frame.completion_status.tolist()==json.loads(sys.argv[4]); "
            "assert not runtime.statistics.submissions",
            str(tmp_path / "every-start"),
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


@pytest.mark.parametrize(
    "bad",
    [
        "duplicate",
        "null_identity",
        "null_time",
        "missing_participant",
        "ambiguous",
        "empty_duplicate_membership",
    ],
)
def test_invalid_sources_never_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
    bad: str,
) -> None:
    with ch.connection(admin=True) as admin:
        if bad == "empty_duplicate_membership":
            admin.command(f"TRUNCATE TABLE {event_tables['started_rows']}")
            admin.insert(event_tables["customers"], [(1, "EU")])
        elif bad == "ambiguous":
            admin.command(f"TRUNCATE TABLE {event_tables['finished_rows']}")
            admin.insert(
                event_tables["finished_rows"],
                [
                    (OCCURRENCE_CANARY, 1, START),
                    (OCCURRENCE_CANARY + 20, 1, START + timedelta(hours=1)),
                ],
            )
        else:
            identity = (
                None
                if bad == "null_identity"
                else OCCURRENCE_CANARY
                if bad == "duplicate"
                else OCCURRENCE_CANARY + 99
            )
            subject = 999 if bad == "missing_participant" else 1
            instant = None if bad == "null_time" else START
            admin.insert(event_tables["started_rows"], [(identity, subject, instant)])
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "invalid", "clickhouse-event")
    with pytest.raises(MaterializationError, match="Event validation failed"):
        journey(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute()
    with sqlite3.connect(runtime.store.db_path) as local:
        assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)


@pytest.mark.parametrize("empty", [False, True])
def test_microsecond_endpoints_and_empty_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
    empty: bool,
) -> None:
    with ch.connection(admin=True) as admin:
        for logical in ("started_rows", "finished_rows"):
            admin.command(f"TRUNCATE TABLE {event_tables[logical]}")
        if not empty:
            admin.insert(
                event_tables["started_rows"],
                [(1, 1, START + timedelta(microseconds=1)), (2, 2, END)],
            )
            admin.insert(
                event_tables["finished_rows"],
                [(11, 1, START + timedelta(microseconds=3)), (12, 2, THROUGH)],
            )
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "precision", "clickhouse-event")
    frame = (
        journey(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute().to_pandas()
    )
    if empty:
        assert frame.empty
    else:
        assert len(frame) == 2
        assert frame.elapsed_from_start.tolist() == [timedelta(0), timedelta(microseconds=2)]
        assert frame.occurred_at.iloc[0].microsecond == 1
        assert frame.occurred_at.iloc[1].microsecond == 3


def test_unknown_coverage_marks_missing_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
) -> None:
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "coverage", "clickhouse-event")
    result = journey(
        runtime.sources(semantic_registry=registry, sidecar=sidecar), complete=False
    ).execute()
    assert (
        result.to_pandas().completion_status.tolist()
        == ["complete"] * 2 + ["coverage_censored"] * 2
    )


def test_late_stream_failure_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_tables: dict[str, str],
) -> None:
    registry, sidecar = _registry(event_tables, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path / "stream-failure", "clickhouse-event")
    original = ClickHouseCursor.fetchmany
    failed: set[ClickHouseCursor] = set()

    def fetchmany(cursor: ClickHouseCursor, size: int) -> list[tuple[object, ...]]:
        if cursor in failed:
            raise OSError("injected late Event stream failure")
        rows = list(original(cursor, size))
        if rows and len(rows[0]) == 4 and rows[0][0] == 2:
            failed.add(cursor)
        return rows

    monkeypatch.setattr(ClickHouseCursor, "fetchmany", fetchmany)
    with pytest.raises(OSError, match="injected late Event stream failure"):
        journey(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute()
    assert failed
    with sqlite3.connect(runtime.store.db_path) as local:
        assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
