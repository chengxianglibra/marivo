"""Public acquisition deadline, owned cancellation and independent termination proof."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import adapters
from marivo.datasource.adapters import SourceSession
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.source_cases import source_case
from tests.support.execution_logs import execution_records


@pytest.mark.runtime
def test_mysql_sample_deadline_releases_owned_server_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("mysql", "table", tmp_path, monkeypatch) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        monkeypatch.setenv("MARIVO_DEADLINE_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_DEADLINE_READER"
        semantic_project_factory(
            {
                "datasources/warehouse.py": 'import marivo.datasource as md\nmd.mysql(name="warehouse",'
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n"
            }
        )
        view = case.source.table + "_slow"
        with mysql.connection(admin=True) as writer, writer.cursor() as cursor:
            columns = ",".join(
                f"MIN(a.{field}) AS {field}" for field in ("id", "amount", "tenant", "revision")
            )
            joins = " ".join(f"CROSS JOIN {case.source.table} b{i}" for i in range(18))
            cursor.execute(
                f"CREATE VIEW {view} AS SELECT {columns} FROM {case.source.table} a {joins} WHERE RAND() < 2"
            )
        try:
            inspection = md.inspect(ms.ref.datasource("warehouse"), md.table(view))
            native = adapters._native_cursor
            owners: list[SourceSession] = []
            original = SourceSession.batches

            def batches(
                owner: SourceSession, read: adapters.CompiledRead, *, chunk_size: int
            ) -> adapters.SourceBatchStream:
                owners.append(owner)
                return original(owner, read, chunk_size=chunk_size)

            monkeypatch.setattr(SourceSession, "batches", batches)
            started = time.monotonic()
            with pytest.raises(DatasourceAuthoringError) as error:
                inspection.sample(
                    scope=md.unpruned(max_rows=1, timeout_seconds=1),
                    columns=("id", "amount", "tenant", "revision"),
                    persist_values=True,
                )
            elapsed = time.monotonic() - started
            assert 0.9 < elapsed < 5
            assert (
                error.value.effect_observed is not None
                and error.value.effect_observed.query_executed
            )
            assert owners and all(owner._closed for owner in owners)
            identities = [owner._backend._marivo_authoring_thread_id for owner in owners]
            controls = [
                item
                for owner in owners
                for item in owner._backend._marivo_authoring_cancel_submissions
            ]
            assert len(controls) == 1
            assert controls[0].sql == f"KILL QUERY {identities[0]}"
            assert (
                controls[0].purpose == "datasource.authoring.deadline"
                and controls[0].state == "succeeded"
            )
            records = execution_records(tmp_path)
            logged = [r for r in records if r.get("purpose") == "datasource.authoring.deadline"]
            assert [r["event"] for r in logged] == ["query.submitted", "query.completed"]
            assert logged[0]["sql"] == controls[0].sql
            source_sql = {submission.sql for owner in owners for submission in owner.submissions}
            source_records = [
                r for r in records if r["event"] == "query.submitted" and r["sql"] in source_sql
            ]
            assert source_records and all(
                r["operation_id"] == logged[0]["operation_id"] for r in source_records
            )
            with mysql.connection(admin=True) as observer, observer.cursor() as cursor:
                for identity in identities:
                    until = time.monotonic() + 3
                    while True:
                        cursor.execute(
                            "SELECT ID FROM information_schema.PROCESSLIST WHERE ID=%s", (identity,)
                        )
                        if not cursor.fetchall():
                            break
                        assert time.monotonic() < until, (
                            "Owned remote reader remained after timeout cleanup"
                        )
                        time.sleep(0.02)
            assert not list((tmp_path / ".marivo/authoring/snapshots").glob("*.json"))
            receipt = {
                "native_reader_ids": identities,
                "elapsed_seconds": elapsed,
                "query_executed": True,
                "independent_server_termination": True,
                "owners_closed": True,
                "snapshot_published": False,
                "code": error.value.code,
            }
        finally:
            with mysql.connection(admin=True) as writer, writer.cursor() as cursor:
                cursor.execute(f"DROP VIEW IF EXISTS {view}")
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "mysql-sample-deadline.json").write_text(
            json.dumps(receipt, sort_keys=True)
        )
