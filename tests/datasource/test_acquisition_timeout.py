"""Native acquisition deadlines on test-owned blocking views."""

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.datasource.authoring_store import AuthoringStore
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import Case, source_case
from tests.support.source_trace import SourceTrace


@contextmanager
def _blocking_view(case: Case, backend: str, statement: str) -> Iterator[TableSourceIR]:
    assert isinstance(case.source, TableSourceIR)
    name = case.source.table + "_blocking"
    database = case.source.database
    parts = (*database, name) if isinstance(database, tuple) else (database, name)
    names = [part for part in parts if part is not None]
    assert all(part.replace("_", "").isalnum() for part in names)
    qualified = ".".join(names)
    create = "CREATE VIEW " + qualified + " AS " + statement
    drop = "DROP VIEW " + qualified
    if backend in ("duckdb", "sqlite"):
        path = case.session.datasource.fields["path"]
        assert isinstance(path, str)
        if backend == "duckdb":
            case.session.close()
        admin = ibis.duckdb.connect(path) if backend == "duckdb" else ibis.sqlite.connect(path)
        try:
            admin.raw_sql(create)
            if backend == "sqlite":
                admin.con.commit()
        finally:
            admin.disconnect()
        try:
            yield TableSourceIR(name, database=database)
        finally:
            admin = ibis.duckdb.connect(path) if backend == "duckdb" else ibis.sqlite.connect(path)
            try:
                admin.raw_sql(drop)
                if backend == "sqlite":
                    admin.con.commit()
            finally:
                admin.disconnect()
    elif backend == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection(admin=True) as admin:
            admin.execute(create)
            try:
                admin.execute("GRANT SELECT ON " + qualified + " TO analysis_reader")
                yield TableSourceIR(name, database=database)
            finally:
                admin.execute(drop)
    elif backend == "trino":
        from tests.datasource.environment import trino_analysis as trino

        with trino.connection(admin=True, catalog="iceberg") as admin:
            cursor = admin.cursor()
            try:
                cursor.execute(create).fetchall()
                try:
                    yield TableSourceIR(name, database=database)
                finally:
                    cursor.execute(drop).fetchall()
            finally:
                cursor.close()
    else:
        from tests.datasource.environment import clickhouse_analysis as ch

        with ch.connection(admin=True) as admin:
            admin.command(create)
            try:
                yield TableSourceIR(name, database=database)
            finally:
                admin.command(drop)


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("duckdb", "sqlite", "postgres", "clickhouse", "trino"))
def test_native_sample_timeout_prevents_snapshot_publication(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Timed-out acquisition must not publish a snapshot")

    monkeypatch.setattr(AuthoringStore, "write_snapshot", forbidden)
    owners: list[SourceSession] = []
    original = SourceSession.batches

    def batches(owner: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        owners.append(owner)
        return original(owner, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", batches)
    profile = {"clickhouse": "mergetree", "trino": "iceberg"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        bound = case.session.bind(case.source, source_identity="source_deadline.sample.timeout")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        if backend == "trino":
            expression = expression.mutate(noise=ibis.random()).aggregate(
                value=lambda t: t.noise.sum()
            )
        else:
            # Preserve a declared column type so metadata does not infer aggregate values.
            expression = expression.order_by(ibis.random()).select(value=expression.id)
        statement = str(ibis.to_sql(expression, dialect=backend))
        with _blocking_view(case, backend, statement) as source:
            inspection = md.inspect(
                ms.ref.datasource("warehouse"),
                md.table(source.table, database=source.database),
            )
            before = len(source_trace.native_sql)
            started = time.monotonic()
            with pytest.raises(DatasourceAuthoringError) as refused:
                inspection.sample(
                    scope=md.unpruned(max_rows=1, timeout_seconds=1),
                    columns=("value",),
                    persist_values=True,
                )
            elapsed = time.monotonic() - started
            assert 0.9 < elapsed < 5
            assert refused.value.code == "acquisition_execution_failed"
            assert refused.value.effect_observed is not None
            assert refused.value.effect_observed.query_executed is True
            submissions = [asdict(item) for owner in owners for item in owner.submissions]
            assert owners and submissions and all(owner._closed for owner in owners)
            assert any(item["state"] == "failed" for item in submissions)
            assert all(item["connection_disconnected"] is True for item in submissions)
            native = source_trace.native_sql[before:]
            assert native and all(item["sql"] in native for item in submissions)
            receipt = {
                "backend": backend,
                "environment": case.environment,
                "source": source.table,
                "timeout_seconds": 1,
                "elapsed_seconds": elapsed,
                "code": refused.value.code,
                "received": refused.value.received,
                "actual_native_submissions": native,
                "submissions": submissions,
                "snapshot_publication_forbidden": True,
                "boundary": "Native acquisition timeout; independent server/fetch/resource race coverage belongs to cancellation tests",
            }
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"acquisition-timeout-{backend}.json").write_text(
            json.dumps(receipt, sort_keys=True)
        )
