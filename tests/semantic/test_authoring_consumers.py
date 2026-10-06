"""Native authoring metadata and bounded acquisition observations."""

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.secrets import LocalPlaintextCache
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import ROWS, source_case
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_public_metadata_and_bounded_sample(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        LocalPlaintextCache,
        "default",
        classmethod(lambda cls: LocalPlaintextCache(tmp_path / "secrets.toml")),
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    owners: list[SourceSession] = []
    original = SourceSession.batches

    def batches(owner: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        owners.append(owner)
        return original(owner, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", batches)
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_AUTHORING_USER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_AUTHORING_USER"
        semantic_project_factory(
            {
                "datasources/authoring.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='authoring',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            }
        )
        before = len(source_trace.native_sql)
        datasource = ms.ref.datasource("authoring")
        source = md.table(case.source.table, database=case.source.database)
        assert len(source_trace.native_sql) == before and not owners
        connection = md.test(datasource, timeout_seconds=5)
        assert connection.ok, connection
        inspection = md.inspect(datasource, source)
        assert {column.name for column in inspection.schema} == set(ROWS[0])
        assert not owners
        before_sample = len(source_trace.native_sql)
        snapshot = inspection.sample(
            scope=md.unpruned(max_rows=3, timeout_seconds=5),
            columns=("id", "amount", "tenant", "revision"),
            persist_values=True,
        )
        rows = sorted(
            snapshot.retained_values,
            key=lambda row: (
                str(row["tenant"]),
                int(str(row["id"])),
                int(str(row["revision"])),
            ),
        )
        assert rows == ROWS
        assert all(type(row["id"]) is int for row in rows)
        assert snapshot.coverage.retained_row_count == 3
        assert len(owners) == 1 and owners[0]._closed
        submissions = [asdict(item) for item in owners[0].submissions]
        assert submissions and all(
            item["state"] == "succeeded" and item["connection_disconnected"] is True
            for item in submissions
        )
        native = source_trace.native_sql[before_sample:]
        assert native and all(item["sql"] in native for item in submissions)
    assert case.session._closed
