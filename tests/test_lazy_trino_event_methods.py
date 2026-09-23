"""Live Trino Event matching through a read-only transaction."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventPayload,
    journey_identity_digest,
    journey_semantics,
)
from marivo.analysis.materialization.admission import DatasetRuntime
from tests.lazy_event_runtime_fixtures import journey
from tests.lazy_trino_event_fixtures import event_registry as _registry
from tests.lazy_trino_event_fixtures import event_source_tables
from tests.multisource_environment import trino_analysis as trino

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
    ),
]


@pytest.fixture
def event_tables() -> Iterator[dict[str, str]]:
    with event_source_tables() as names:
        yield names


def test_exact_microsecond_journey(
    tmp_path: Path, event_tables: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import Counter
    from datetime import timedelta

    from requests import Response
    from trino.client import TrinoRequest

    observed: list[str] = []
    transaction_ids: list[str] = []
    original = TrinoRequest.post

    def capture(
        request: TrinoRequest, sql: str, additional_http_headers: dict[str, str] | None = None
    ) -> Response:
        headers = request.http_headers
        if headers.get("X-Trino-User") == "analysis_reader":
            observed.append(sql)
            if sql.startswith("WITH "):
                transaction_ids.append(headers["X-Trino-Transaction-Id"])
        response: Response = original(request, sql, additional_http_headers)
        return response

    monkeypatch.setattr(TrinoRequest, "post", capture)
    registry, sidecar = _registry(event_tables)
    runtime = DatasetRuntime.create(tmp_path / "trino-event", "trino-event")
    logical = journey(runtime.sources(semantic_registry=registry, sidecar=sidecar))
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, EventPayload)
    digest = journey_identity_digest(journey_semantics(logical._root.payload.definition))
    result = logical.execute()
    frame = result.to_pandas()
    assert frame.completion_status.tolist() == ["complete"] * 2 + ["incomplete"] * 2
    assert frame.elapsed_from_start.iloc[1] == timedelta(microseconds=2)
    assert frame.occurred_at.iloc[0].microsecond == 1
    assert frame.occurred_at.iloc[1].microsecond == 3
    expected = []
    for subject, anchor in ((1, 11), (2, 21)):
        identity = {"entity_identity": {"id": subject}, "anchor_event_identity": {"k0": anchor}}
        encoded = "event_journey@v1:" + digest + ":" + json.dumps(identity, separators=(",", ":"))
        expected.extend(["journey_" + hashlib.sha256(encoded.encode()).hexdigest()] * 2)
    assert frame.journey_id.tolist() == expected
    source = [item for item in runtime.statistics.submissions if item.domain == "source"]
    assert sum(item.role == "primary" for item in source) == 1
    assert any(
        item.sql == "START TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        for item in source
    )
    assert not any("ASOF" in item.sql for item in source)
    assert Counter(item.sql for item in source if item.sql.startswith("WITH ")) == Counter(
        sql for sql in observed if sql.startswith("WITH ")
    )
    for item in source:
        if "?" in item.sql:
            prefix = "EXECUTE IMMEDIATE '" + item.sql.replace("'", "''") + "' USING "
            assert any(sql.startswith(prefix) for sql in observed)
        else:
            assert item.sql in observed
    assert len(set(transaction_ids)) == 1 and transaction_ids[0] != "NONE"
    assert not any(
        sql.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))
        for sql in observed
    )


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "null_identity",
        "null_time",
        "missing_participant",
        "ambiguous",
        "empty_duplicate",
        "empty",
        "unknown",
        "stream_failure",
    ],
)
def test_event_boundaries(
    tmp_path: Path, event_tables: dict[str, str], case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from marivo.analysis.materialization.errors import MaterializationError

    with trino.connection(admin=True) as admin:
        cursor = admin.cursor()
        if case in ("empty", "empty_duplicate"):
            cursor.execute(f"DELETE FROM {event_tables['started_rows']}").fetchall()
            if case == "empty_duplicate":
                cursor.execute(
                    f"INSERT INTO {event_tables['customers']} VALUES (1,'EU')"
                ).fetchall()
        elif case not in ("unknown", "stream_failure"):
            table = (
                event_tables["finished_rows"]
                if case == "ambiguous"
                else event_tables["started_rows"]
            )
            value = {
                "duplicate": "(11,1,TIMESTAMP '2026-02-01 01:00:00')",
                "null_identity": "(NULL,1,TIMESTAMP '2026-02-01 01:00:00')",
                "null_time": "(33,1,NULL)",
                "missing_participant": "(33,999,TIMESTAMP '2026-02-01 01:00:00')",
                "ambiguous": "(11,1,TIMESTAMP '2026-02-01 00:00:00.000001')",
            }[case]
            cursor.execute(f"INSERT INTO {table} VALUES {value}").fetchall()
        cursor.close()
    registry, sidecar = _registry(event_tables)
    runtime = DatasetRuntime.create(tmp_path / case, "trino-event")
    logical = journey(
        runtime.sources(semantic_registry=registry, sidecar=sidecar), complete=case != "unknown"
    )
    if case == "stream_failure":
        import pyarrow as pa

        from marivo.analysis.materialization.scalar_sql_execution import ScalarBatchStream

        original = ScalarBatchStream.__iter__

        def broken(stream: ScalarBatchStream) -> Iterator[pa.RecordBatch]:
            for batch in original(stream):
                yield batch
                if "journey_id" in stream.schema.names and "step_key" in stream.schema.names:
                    raise RuntimeError("injected late journey read failure")

        monkeypatch.setattr(ScalarBatchStream, "__iter__", broken)
        with pytest.raises(RuntimeError, match="injected late journey"):
            logical.execute()
        with sqlite3.connect(runtime.store.db_path) as local:
            assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
    elif case in ("empty", "unknown"):
        frame = logical.execute().to_pandas()
        if case == "empty":
            assert frame.empty
        else:
            assert frame.completion_status.tolist() == ["complete"] * 2 + ["coverage_censored"] * 2
    else:
        with pytest.raises(MaterializationError, match="Event validation failed"):
            logical.execute()
        with sqlite3.connect(runtime.store.db_path) as local:
            assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)


@pytest.mark.parametrize("assignment", ["shared", "exclusive"])
def test_every_start_and_cold_recovery(
    tmp_path: Path, event_tables: dict[str, str], assignment: Literal["shared", "exclusive"]
) -> None:
    import json
    import subprocess
    import sys

    from marivo.analysis.event import every_start

    assert assignment in ("shared", "exclusive")
    with trino.connection(admin=True) as admin:
        cursor = admin.cursor()
        cursor.execute(
            f"INSERT INTO {event_tables['started_rows']} VALUES (13,1,TIMESTAMP '2026-02-01 00:00:00.000002')"
        ).fetchall()
        cursor.close()
    registry, sidecar = _registry(event_tables)
    runtime = DatasetRuntime.create(tmp_path / assignment, "trino-event")
    result = journey(
        runtime.sources(semantic_registry=registry, sidecar=sidecar),
        matching=every_start(completion_assignment=assignment),
    ).execute()
    expected = ["complete"] * (4 if assignment == "shared" else 2) + ["incomplete"] * (
        2 if assignment == "shared" else 4
    )
    assert result.to_pandas().completion_status.tolist() == expected
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; import sys,json; from marivo.analysis.materialization.admission import DatasetRuntime; r=DatasetRuntime.open(Path(sys.argv[1]),sys.argv[2]); f=r.artifact(sys.argv[3]).to_pandas(); assert f.completion_status.tolist()==json.loads(sys.argv[4]); assert not r.statistics.submissions",
            str(tmp_path / assignment),
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


@pytest.mark.parametrize("method", ["funnel", "time_to_event", "selection", "compare"])
def test_event_continuations(tmp_path: Path, event_tables: dict[str, str], method: str) -> None:
    from datetime import timedelta

    from marivo.analysis.subject import dropped_before

    registry, sidecar = _registry(event_tables)
    runtime = DatasetRuntime.create(tmp_path / method, "trino-event")
    logical = journey(runtime.sources(semantic_registry=registry, sidecar=sidecar))
    assert isinstance(logical._root, LogicalRootHandle)
    assert isinstance(logical._root.payload, EventPayload)
    start, finish = logical._root.payload.definition.pattern.steps
    if method == "funnel":
        frame = logical.funnel().execute().to_pandas()
        assert frame.reached_count.tolist() == [2, 1]
        assert frame.conversion_from_first.tolist() == [1.0, 0.5]
    elif method == "time_to_event":
        frame = logical.time_to_event(from_step=start, to_step=finish).execute().to_pandas()
        assert frame.completion_status.tolist() == ["complete", "incomplete"]
        assert frame.duration.iloc[0] == timedelta(microseconds=2)
    elif method == "selection":
        frame = logical.select_subjects(dropped_before(step=finish)).execute().to_pandas()
        assert frame.entity_identity.tolist() == [(2,)]
    else:
        funnel = logical.funnel()
        assert funnel.compare(funnel).execute().to_pandas().loss_rate_delta.iloc[-1] == 0.0
