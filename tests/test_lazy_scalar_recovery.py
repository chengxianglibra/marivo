"""Real MySQL, SQLite, Trino and ClickHouse producer death and independent Session recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.execution import Parameter, Statement
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.scalar_sql_execution import (
    ScalarBatchStream,
    ScalarExecutionAdapter,
)
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import registry_for as scalar_registry
from tests.multisource_environment import clickhouse_analysis as clickhouse
from tests.multisource_environment import mysql_analysis as mysql
from tests.multisource_environment import trino_analysis as trino
from tests.multisource_environment.postgres_analysis import password as qualification_password

pytestmark = pytest.mark.runtime
REVENUE = ref.metric("sales.revenue")


def registry_for(
    table: str, patch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    if table.endswith(".sqlite"):
        return scalar_registry(Path(table))
    if table.startswith("clickhouse_"):
        patch.setenv("MARIVO_TEST_CLICKHOUSE_PASSWORD", qualification_password())
        return scalar_registry(Path("unused"), engine="clickhouse", table=table)
    if table.startswith("trino_"):
        return scalar_registry(Path("unused"), engine="trino", table=table)
    patch.setenv("MARIVO_TEST_MYSQL_PASSWORD", mysql.password())
    return scalar_registry(Path("unused"), engine="mysql", table=table)


@pytest.fixture(params=["sqlite", "mysql", "trino", "clickhouse"])
def source_table(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "mysql" and os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in MySQL service")
    if request.param == "trino" and os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in Trino service")
    if request.param == "clickhouse" and os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in ClickHouse service")
    schema = "id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, amount DOUBLE, weight DOUBLE, region TEXT, channel TEXT, day DATE, start DATE, `end` DATE"
    if request.param == "sqlite":
        path = tmp_path / "source.sqlite"
        with sqlite3.connect(path) as con:
            con.execute(f"CREATE TABLE orders({schema})")
            con.execute("INSERT INTO orders(id,amount) VALUES (1,10.25),(2,20.50)")
        yield str(path)
    elif request.param == "clickhouse":
        name = "clickhouse_recovery_" + uuid4().hex
        with clickhouse.connection(admin=True) as con:
            ch_schema = (
                schema.replace("BIGINT", "Nullable(Int64)")
                .replace("DOUBLE", "Nullable(Float64)")
                .replace("TEXT", "Nullable(String)")
                .replace("DATE", "Nullable(Date)")
            )
            con.command(f"CREATE TABLE {name}({ch_schema}) ENGINE=MergeTree ORDER BY tuple()")
            con.command(f"INSERT INTO {name}(id,amount) VALUES (1,10.25),(2,20.50)")
            try:
                yield name
            finally:
                con.command(f"DROP TABLE {name}")
    elif request.param == "trino":
        name = "trino_recovery_" + uuid4().hex
        with trino.connection(admin=True) as trino_con:
            cur = trino_con.cursor()
            trino_schema = schema.replace("TEXT", "VARCHAR").replace("`end`", '"end"')
            cur.execute(f"CREATE TABLE {name}({trino_schema})").fetchall()
            cur.execute(f"INSERT INTO {name}(id,amount) VALUES (1,10.25),(2,20.50)").fetchall()
            try:
                yield name
            finally:
                cur.execute(f"DROP TABLE {name}").fetchall()
                cur.close()
    else:
        name = "recovery_" + uuid4().hex
        with mysql.connection(admin=True) as con, con.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {name}({schema}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin"
            )
            cur.execute(f"INSERT INTO {name}(id,amount) VALUES (1,10.25),(2,20.50)")
            try:
                yield name
            finally:
                cur.execute(f"DROP TABLE {name}")


def _produce(project: Path, table: str, point: str) -> None:
    point, _, variant = point.partition(":")
    composed = variant == "ratio"
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
                            "composed": composed,
                        }
                    )
                )
                os._exit(73)

        def event(name: str) -> None:
            if name != "source_statement":
                crash(name)

        runtime = DatasetRuntime.create(project, "scalar-recovery", event=event)
        original = ScalarExecutionAdapter.batches

        def batches(
            adapter: ScalarExecutionAdapter,
            value: Statement | ir.Expr,
            *,
            chunk_size: int,
            params: Mapping[ir.Scalar, Parameter] | None = None,
            role: str = "query",
        ) -> ScalarBatchStream:
            crash("source_statement")
            stream = original(adapter, value, chunk_size=chunk_size, params=params, role=role)
            crash("source_ack")
            return stream

        patch.setattr(ScalarExecutionAdapter, "batches", batches)
        observed = runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
            ref.metric("sales.conversion_rate") if composed else REVENUE
        )
        if composed:
            observed = observed.with_dimensions(ref.dimension("sales.orders.channel"))
        observed.aggregate().execute()
    raise AssertionError("Producer did not reach requested crash point")


def _recover(project: Path, table: str) -> None:
    metadata = json.loads((project / "crash.json").read_text())
    runtime = DatasetRuntime.open(project, metadata["session"])
    composed = metadata.get("composed", False)
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
        artifact = runtime.artifact(run.output_artifact_ref)
        column = "conversion_rate" if composed else "revenue"
        expected = "15.375" if composed else "30.75"
        assert artifact.to_pandas()[column].astype(str).tolist() == [expected]
    else:
        assert run.lifecycle == "failed"
        assert run.output_artifact_ref is None
        assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
    with pytest.MonkeyPatch.context() as patch:
        registry, sidecar = registry_for(table, patch)
        source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        if run.lifecycle == "succeeded":
            from marivo.analysis.materialization import admission

            def forbidden(*args: object, **kwargs: object) -> object:
                raise AssertionError("cold binding attempted source access")

            with patch.context() as cold:
                cold.setattr(admission, "_build_backend_from_effective", forbidden)
                cold.delenv("MARIVO_TEST_MYSQL_PASSWORD", raising=False)
                observed = source.observe(
                    ref.metric("sales.conversion_rate") if composed else REVENUE
                )
                if composed:
                    observed = observed.with_dimensions(ref.dimension("sales.orders.channel"))
                hit = observed.aggregate().execute()
                assert hit.state.artifact_ref.ref == run.output_artifact_ref
                assert runtime.statistics.primary_queries == 0
        if composed and run.lifecycle == "succeeded":
            from marivo.analysis.materialization import admission

            def no_source(*args: object, **kwargs: object) -> object:
                raise AssertionError("cold sufficient-state fold attempted source access")

            assert run.output_artifact_ref is not None
            with patch.context() as cold:
                cold.setattr(admission, "_build_backend_from_effective", no_source)
                retained = runtime.artifact(run.output_artifact_ref)
                assert isinstance(retained, MaterializedMetricDataset)
                folded = retained.rollup(
                    drop_dimensions=(ref.dimension("sales.orders.channel"),)
                ).execute()
                assert folded.to_pandas().conversion_rate.tolist() == [15.375]
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
            "tests.test_lazy_scalar_recovery",
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

    runtime = DatasetRuntime.create(tmp_path, "scalar-cleanup", event=event)
    logical = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    finish = ScalarExecutionAdapter.finish

    def lost_cancel(adapter: ScalarExecutionAdapter) -> None:
        raise OSError("cancel acknowledgement unavailable")

    def lost_finish(adapter: ScalarExecutionAdapter) -> None:
        finish(adapter)
        raise OSError("close acknowledgement unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ScalarExecutionAdapter, "interrupt", lost_cancel)
        patch.setattr(ScalarExecutionAdapter, "finish", lost_finish)
        with pytest.raises(OSError) as raised:
            logical.execute()
        assert raised.value is original_error
    assert runtime.statistics.events["remote_read_status_unknown"] == 1
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    armed = False
    assert logical.execute().to_pandas()["revenue"].astype(str).tolist() == ["30.75"]


@pytest.mark.parametrize("point", ["transfer", "before_rename", "before_commit"])
def test_writer_error_is_original_and_retry_is_explicit(
    tmp_path: Path, source_table: str, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    failure = OSError("owned writer failed")
    armed = True

    def event(name: str) -> None:
        if armed and name == point:
            raise failure

    runtime = DatasetRuntime.create(tmp_path / "writer", point, event=event)
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    with pytest.raises(OSError) as caught:
        target.execute()
    assert caught.value is failure
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    armed = False
    assert target.execute().to_pandas().revenue.tolist() == [30.75]


@pytest.mark.parametrize("point", ["transfer", "before_rename", "before_commit", "after_commit"])
def test_composed_parts_have_atomic_publication_and_cold_fold(
    tmp_path: Path, source_table: str, point: str
) -> None:
    process = _worker("produce", tmp_path, source_table, point + ":ratio")
    assert process.returncode == 73, process.stdout + process.stderr
    recovered = _worker("recover", tmp_path, source_table)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["lifecycle"] == ("succeeded" if point == "after_commit" else "failed")
    assert result["before"]["dataset_artifacts"] == int(point == "after_commit")


if __name__ == "__main__":
    mode, root, table, point = sys.argv[1:]
    if mode == "produce":
        _produce(Path(root), table, point)
    else:
        _recover(Path(root), table)
