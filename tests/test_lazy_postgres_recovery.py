"""Opt-in real PostgreSQL producer death and independent Session recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pytest
from psycopg import sql

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.execution import Parameter, Statement
from marivo.analysis.materialization.postgres_execution import (
    PostgresBatchStream,
    PostgresExecutionAdapter,
)
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_acceptance_capture import counts
from tests.lazy_postgres_fixtures import registry_for
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
    ),
]
REVENUE = ref.metric("sales.revenue")


@pytest.fixture
def source_table() -> Iterator[str]:
    name = "recovery_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
                "amount NUMERIC(18,2), weight DOUBLE PRECISION, region TEXT, channel TEXT, "
                'day DATE, start DATE, "end" DATE)'
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL("INSERT INTO {} (id, amount) VALUES (1,10.25),(2,20.50)").format(
                sql.Identifier(name)
            )
        )
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def _produce(project: Path, table: str, point: str) -> None:
    with pytest.MonkeyPatch.context() as patch:
        registry, sidecar = registry_for(table, patch)

        def crash(name: str) -> None:
            if name == point:
                assert runtime.last_run_ref is not None
                (project / "crash.json").write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "session": runtime.session_ref,
                            "run": runtime.last_run_ref,
                            "point": point,
                        }
                    )
                )
                os._exit(73)

        def event(name: str) -> None:
            if name != "source_statement":
                crash(name)

        runtime = DatasetRuntime.create(project, "postgres-recovery", event=event)
        original = PostgresExecutionAdapter.batches

        def batches(
            adapter: PostgresExecutionAdapter,
            value: Statement | ir.Expr,
            *,
            chunk_size: int,
            params: Mapping[ir.Scalar, Parameter] | None = None,
            role: str = "query",
            record: Callable[[str, str], None] | None = None,
        ) -> PostgresBatchStream:
            crash("source_statement")
            stream = original(
                adapter, value, chunk_size=chunk_size, params=params, role=role, record=record
            )
            crash("source_ack")
            return stream

        patch.setattr(PostgresExecutionAdapter, "batches", batches)
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
            REVENUE
        ).aggregate().execute()
    raise AssertionError("Producer did not reach requested crash point")


def _recover(project: Path, table: str) -> None:
    metadata = json.loads((project / "crash.json").read_text())
    runtime = DatasetRuntime.open(project, metadata["session"])
    with session_writer_guard(runtime.store.layout.lock_path(runtime.session_ref)):
        reconcile_session(runtime.store, runtime.session_ref, event=runtime._event)
    run = runtime.store.run(metadata["run"])
    assert run is not None
    assert runtime.store.resources(runtime.session_ref) == ()
    before = counts(runtime)
    assert runtime.statistics.primary_queries == 0
    assert runtime.statistics.validation_queries == 0
    if run.lifecycle == "succeeded":
        assert run.output_artifact_ref is not None
        assert runtime.artifact(run.output_artifact_ref).to_pandas()["revenue"].astype(
            str
        ).tolist() == ["30.75"]
    else:
        assert run.lifecycle == "failed"
        assert run.output_artifact_ref is None
        assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
    with pytest.MonkeyPatch.context() as patch:
        registry, sidecar = registry_for(table, patch)
        source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        safe = source.observe(REVENUE).where(gt(REVENUE, 0)).aggregate().execute()
        assert safe.to_pandas()["revenue"].astype(str).tolist() == ["30.75"]
        assert runtime.statistics.primary_queries == 1
        assert runtime.store.resources(runtime.session_ref) == ()
    print(json.dumps({"pid": os.getpid(), "lifecycle": run.lifecycle, "before": before}))


def _worker(
    mode: str, project: Path, table: str, point: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.test_lazy_postgres_recovery",
            mode,
            str(project),
            table,
            point,
        ],
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    "point",
    [
        "source_statement",
        "source_ack",
        "transfer",
        "before_rename",
        "before_commit",
        "after_commit",
    ],
)
def test_producer_death_has_atomic_publication_and_cold_recovery(
    tmp_path: Path, source_table: str, point: str
) -> None:
    process = _worker("produce", tmp_path, source_table, point)
    assert process.returncode == 73, process.stdout + process.stderr
    crashed = json.loads((tmp_path / "crash.json").read_text())
    recovered = _worker("recover", tmp_path, source_table)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["pid"] != crashed["pid"]
    committed = point == "after_commit"
    assert result["lifecycle"] == ("succeeded" if committed else "failed")
    assert result["before"]["dataset_artifacts"] == int(committed)
    assert result["before"]["analysis_action_runs"] == 1


def test_cleanup_unknown_preserves_failure_and_allows_safe_next_run(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    original_error = OSError("real transfer interrupted")
    armed = True

    def event(name: str) -> None:
        if name == "transfer" and armed:
            raise original_error

    runtime = DatasetRuntime.create(tmp_path, "postgres-cleanup", event=event)
    logical = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    finish = PostgresExecutionAdapter.finish

    def lost_cancel(adapter: PostgresExecutionAdapter) -> None:
        raise OSError("cancel acknowledgement unavailable")

    def lost_finish(adapter: PostgresExecutionAdapter) -> None:
        finish(adapter)
        raise OSError("close acknowledgement unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(PostgresExecutionAdapter, "interrupt", lost_cancel)
        patch.setattr(PostgresExecutionAdapter, "finish", lost_finish)
        with pytest.raises(OSError) as raised:
            logical.execute()
        assert raised.value is original_error
    assert runtime.statistics.events["remote_read_status_unknown"] == 1
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    armed = False
    assert logical.execute().to_pandas()["revenue"].astype(str).tolist() == ["30.75"]


if __name__ == "__main__":
    mode, root, table, point = sys.argv[1:]
    if mode == "produce":
        _produce(Path(root), table, point)
    else:
        _recover(Path(root), table)
