"""Statement observations preserve actual submissions and failure identity."""

from pathlib import Path

import ibis
import pytest
import sqlglot

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter


@pytest.mark.parametrize("kind", ["primary", "engine_check.primary_schema"])
def test_recording_preserves_raw_sql_without_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    events: list[str] = []
    runtime = DatasetRuntime.create(tmp_path, "statement-statistics", event=events.append)
    events.clear()
    sql = "SELECT 'literal-value', 42 /* original formatting */\n"
    adapter = DuckDBExecutionAdapter(ibis.duckdb.connect())
    adapter.observe(runtime._observe_submission, "source")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Recording a diagnostic statement must not parse SQL")

    monkeypatch.setattr(sqlglot, "parse_one", forbidden)
    try:
        adapter.submit(adapter.statement(sql, role=kind))
        assert runtime.statistics.statements == [(kind, sql)]
        assert runtime.statistics.validation_queries == int(kind.startswith("engine_check."))
        assert runtime.statistics.submissions[0].state == "succeeded"
        assert events == ["source_statement"]
    finally:
        adapter.disconnect()


def test_failed_submission_keeps_original_error_and_one_receipt(tmp_path: Path) -> None:
    runtime = DatasetRuntime.create(tmp_path, "failed-submission")
    adapter = DuckDBExecutionAdapter(ibis.duckdb.connect())
    adapter.observe(runtime._observe_submission, "local")
    try:
        with pytest.raises(Exception) as failure:
            adapter.submit(
                adapter.statement("SELECT * FROM missing_c3a_table", role="validation_batch")
            )
        assert len(runtime.statistics.submissions) == 1
        receipt = runtime.statistics.submissions[0]
        assert receipt.domain == "local"
        assert receipt.state == "failed"
        assert receipt.error_type == type(failure.value).__name__
        assert runtime.statistics.events["local_statement"] == 1
        assert runtime.statistics.events.get("source_statement", 0) == 0
    finally:
        adapter.disconnect()


def test_compilation_failure_submits_nothing(tmp_path: Path) -> None:
    from ibis.backends.trino import Backend

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    runtime = DatasetRuntime.create(tmp_path, "compile-only")
    adapter = TrinoExecutionAdapter(Backend())
    adapter.observe(runtime._observe_submission, "source")
    with pytest.raises(MaterializationError):
        adapter.prepare(ibis.memtable({"value": [1]}))
    assert runtime.statistics.submissions == []
    assert runtime.statistics.primary_queries == 0


def test_stream_failure_updates_the_submitted_receipt(tmp_path: Path) -> None:
    from collections.abc import Iterator

    import pyarrow as pa

    from marivo.analysis.materialization.duckdb_execution import DuckDBBatchStream

    cause = RuntimeError("injected stream failure")
    schema = pa.schema([("value", pa.int64())])

    def batches() -> Iterator[pa.RecordBatch]:
        yield pa.record_batch([[1]], schema=schema)
        raise cause

    runtime = DatasetRuntime.create(tmp_path, "failed-stream")
    adapter = DuckDBExecutionAdapter(ibis.duckdb.connect())
    adapter.observe(runtime._observe_submission, "source")
    try:
        adapter.submit(adapter.statement("SELECT 1 AS value", role="primary"))
        stream = DuckDBBatchStream(
            pa.RecordBatchReader.from_batches(schema, batches()),
            schema,
            runtime.statistics.submissions[0],
        )
        with pytest.raises(RuntimeError) as failure:
            list(stream)
        assert failure.value is cause
        assert len(runtime.statistics.submissions) == runtime.statistics.primary_queries == 1
        assert runtime.statistics.submissions[0].state == "failed"
        stream.close()
    finally:
        adapter.disconnect()


@pytest.mark.parametrize("domain", ["source", "local"])
@pytest.mark.parametrize(
    "role,expected",
    [
        ("validation_batch", 1),
        ("sampling_validation", 1),
        ("engine_check.temporal_rules", 1),
        ("source_schema", 0),
        ("source_timezone", 0),
        ("primary", 0),
        ("retained_part", 0),
    ],
)
def test_validation_counter_uses_physical_validation_roles(
    tmp_path: Path, domain: str, role: str, expected: int
) -> None:
    from marivo.analysis.materialization.submissions import Submission

    runtime = DatasetRuntime.create(tmp_path, "validation-roles")
    assert domain in {"source", "local"}
    receipt = Submission("source" if domain == "source" else "local", role, "SELECT 1")
    runtime._observe_submission(receipt)
    receipt.fail(RuntimeError("injected after submission"))
    assert runtime.statistics.validation_queries == expected
    assert len(runtime.statistics.submissions) == 1
