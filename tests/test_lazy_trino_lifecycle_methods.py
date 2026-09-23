"""Read-only Iceberg Lifecycle fold qualification through the public runtime."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from tests.lazy_lifecycle_fixtures import history, lifecycle_registry
from tests.lazy_remote_lifecycle_fixtures import assert_cold_history, assert_complete_history
from tests.lazy_trino_event_fixtures import event_registry, event_source_tables
from tests.multisource_environment import trino_analysis as trino

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
    ),
]


def test_complete_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import Counter

    from requests import Response
    from trino.client import TrinoRequest

    observed: list[str] = []
    transactions: list[str] = []
    original = TrinoRequest.post

    def capture(
        request: TrinoRequest, sql: str, additional_http_headers: dict[str, str] | None = None
    ) -> Response:
        if request.http_headers.get("X-Trino-User") == "analysis_reader":
            observed.append(sql)
            if sql.startswith("WITH "):
                transactions.append(request.http_headers["X-Trino-Transaction-Id"])
        response: Response = original(request, sql, additional_http_headers)
        return response

    monkeypatch.setattr(TrinoRequest, "post", capture)
    with event_source_tables() as names:
        with trino.connection(admin=True) as admin:
            c = admin.cursor()
            for key in ("started_rows", "finished_rows"):
                c.execute(f"DELETE FROM {names[key]}").fetchall()
            c.execute(
                f"INSERT INTO {names['started_rows']} VALUES (11,1,TIMESTAMP '2026-01-31 23:00:00'),(21,2,TIMESTAMP '2026-02-01 02:00:00')"
            ).fetchall()
            c.execute(
                f"INSERT INTO {names['finished_rows']} VALUES (12,1,TIMESTAMP '2026-02-01 03:00:00'),(13,1,TIMESTAMP '2026-02-01 04:00:00')"
            ).fetchall()
            c.close()
        base, sidecar = event_registry(names)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "history", "trino-lifecycle")
        result = history(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute()
        assert_complete_history(runtime, result)
        submitted = [item.sql for item in runtime.statistics.submissions if item.domain == "source"]
        assert "START TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in submitted
        assert Counter(sql for sql in submitted if sql.startswith("WITH ")) == Counter(
            sql for sql in observed if sql.startswith("WITH ")
        )
        assert len(set(transactions)) == 1 and transactions[0] != "NONE"
        assert not any(
            sql.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))
            for sql in observed
        )
    assert_cold_history(tmp_path / "history", runtime.session_ref, result.state.artifact_ref.ref)


@pytest.mark.parametrize(
    "case",
    [
        "long",
        "ambiguous",
        "outcome_ambiguous",
        "illegal",
        "null_time",
        "missing_participant",
        "microsecond",
        "compatible",
        "empty",
        "empty_duplicate",
        "duplicate",
        "null",
        "no_inception",
        "unknown",
        "stream_failure",
    ],
)
def test_replay_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    import sqlite3
    from collections.abc import Iterator

    import pyarrow as pa

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.scalar_sql_execution import ScalarBatchStream

    with event_source_tables() as names:
        with trino.connection(admin=True) as admin:
            c = admin.cursor()
            for key in ("started_rows", "finished_rows"):
                c.execute(f"DELETE FROM {names[key]}").fetchall()
            if case == "empty_duplicate":
                c.execute(f"INSERT INTO {names['customers']} VALUES (1,'EU')").fetchall()
            elif case not in ("empty", "no_inception"):
                key = "NULL" if case == "null" else "1"
                subject = "NULL" if case == "missing_participant" else "1"
                instant = "NULL" if case == "null_time" else "TIMESTAMP '2026-02-01 00:00:00'"
                c.execute(
                    f"INSERT INTO {names['started_rows']} VALUES ({key},{subject},{instant})"
                ).fetchall()
            if case in ("duplicate", "illegal", "outcome_ambiguous"):
                c.execute(
                    f"INSERT INTO {names['started_rows']} VALUES ({1 if case == 'duplicate' else 3},1,TIMESTAMP '2026-02-01 01:00:00')"
                ).fetchall()
            if case in (
                "ambiguous",
                "outcome_ambiguous",
                "compatible",
                "no_inception",
                "unknown",
                "long",
                "microsecond",
            ):
                hour = "00" if case in ("ambiguous", "microsecond") else "01"
                suffix = "00.000002" if case == "microsecond" else "00"
                c.execute(
                    f"INSERT INTO {names['finished_rows']} VALUES (2,1,TIMESTAMP '2026-02-01 {hour}:00:{suffix}')"
                ).fetchall()
            if case == "compatible":
                for key, identity in (("started_rows", 3), ("finished_rows", 4)):
                    c.execute(
                        f"INSERT INTO {names[key]} VALUES ({identity},1,TIMESTAMP '2026-02-01 02:00:00')"
                    ).fetchall()
            if case == "long":
                values = ",".join(
                    f"({100 + i},1,TIMESTAMP '2026-02-01 02:00:{i:02d}.000001')" for i in range(24)
                )
                c.execute(f"INSERT INTO {names['finished_rows']} VALUES {values}").fetchall()
            c.close()
        base, sidecar = event_registry(names)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "boundary", "trino-lifecycle")
        logical = history(
            runtime.sources(semantic_registry=registry, sidecar=sidecar), complete=case != "unknown"
        )
        if case == "stream_failure":
            original = ScalarBatchStream.__iter__

            def broken(stream: ScalarBatchStream) -> Iterator[pa.RecordBatch]:
                for batch in original(stream):
                    yield batch
                    if "valid_from" in stream.schema.names:
                        raise RuntimeError("injected late history failure")

            monkeypatch.setattr(ScalarBatchStream, "__iter__", broken)
        if case in (
            "ambiguous",
            "outcome_ambiguous",
            "null_time",
            "missing_participant",
            "empty_duplicate",
            "duplicate",
            "null",
            "no_inception",
            "stream_failure",
        ):
            expected = RuntimeError if case == "stream_failure" else MaterializationError
            with pytest.raises(expected, match=r"injected late|validation failed"):
                logical.execute()
            with sqlite3.connect(runtime.store.db_path) as local:
                assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
        else:
            result = logical.execute()
            frame = result.to_pandas()
            assert frame.model_state.tolist() == (
                [] if case == "empty" else ["open"] if case == "illegal" else ["open", "done"]
            )
            if case == "unknown":
                assert frame.interval_status.tolist() == ["coverage_censored", "coverage_censored"]
            if case == "microsecond":
                from datetime import timedelta

                assert frame.valid_to.iloc[0] - frame.valid_from.iloc[0] == timedelta(
                    microseconds=2
                )
            if case in ("long", "compatible", "illegal"):
                violations = result.violations().execute().to_pandas()
                assert len(violations) == (
                    24 if case == "long" else 2 if case == "compatible" else 1
                )


def test_native_governed_tie_order() -> None:
    from marivo.analysis.compiler.lifecycle_array import trino_confluence, trino_replay
    from marivo.analysis.domains.lifecycle import LifecycleSemantics
    from tests.lazy_lifecycle_fixtures import sources_without_io

    semantics = history(sources_without_io()).row_contract.family_semantics
    assert isinstance(semantics, LifecycleSemantics)
    with trino.connection() as reader:
        for event_ref, second_identity, expected in (
            ("other", 2, 1),
            ("same", 2, 0),
            ("same", 1, 1),
        ):
            source = f"SELECT * FROM (VALUES (ROW(BIGINT '1'),BIGINT '1','trigger_0','same',ROW(BIGINT '1'),TIMESTAMP '2026-02-01 00:00:00'),(ROW(BIGINT '1'),BIGINT '2','trigger_1','{event_ref}',ROW(BIGINT '{second_identity}'),TIMESTAMP '2026-02-01 00:00:00')) AS input(entity_identity,ordinal,trigger_key,trigger_event_ref,event_identity,occurred_at)"
            query = f"WITH source AS ({source}), replay AS ({trino_replay('source', semantics)}) SELECT * FROM ({trino_confluence('source', 'replay', semantics)})"
            cursor = reader.cursor()
            try:
                assert cursor.execute(query).fetchall() == [[expected]]
            finally:
                cursor.close()
