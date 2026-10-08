"""Remote prefix reads retain actual inputs without cross-read snapshot guarantees."""

import json
import os
from contextlib import nullcontext
from pathlib import Path

import pytest

import marivo.datasource.domain_snapshot as snapshots
from tests.datasource.source_cases import source_case
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_remote_preparation_records_independent_reads(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
) -> None:
    from contextlib import closing

    from marivo.datasource.adapters import PhysicalRequirement
    from marivo.datasource.capabilities import provider_statement_log

    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        binding = case.session.bind(case.source, source_identity="facts")
        qualified = case.session.qualify(
            binding, PhysicalRequirement("capture", 1, frozenset({"scan"}))
        )
        table = binding.relation.order_by("id", "tenant")
        read = case.session.compile(
            qualified, table, purpose="capture", expected_schema=table.schema().to_pyarrow()
        )
        before = len(source_trace.native_sql)
        with snapshots.capture(
            case.session, checkpoint=lambda: None, guard=lambda _: nullcontext()
        ) as authority:
            assert authority["kind"] == "independent_reads"
            assert authority["files"] == []
            for iteration in range(2):
                with closing(case.session.batches(read, chunk_size=1)) as batches:
                    assert [
                        value for batch in batches for value in batch.column("amount").to_pylist()
                    ] == (
                        [99, 99, 99] if backend == "postgres" and iteration == 1 else [2, None, 4]
                    )
                if backend == "postgres" and iteration == 0:
                    from psycopg import sql

                    from marivo.datasource.ir import TableSourceIR
                    from tests.datasource.environment import postgres_analysis as pg

                    assert isinstance(case.source, TableSourceIR)
                    with pg.connection(admin=True) as writer:
                        writer.execute(
                            sql.SQL("UPDATE {}.{} SET amount=99").format(
                                sql.Identifier("public"), sql.Identifier(case.source.table)
                            )
                        )
        native = source_trace.native_sql[before:]
        assert len(case.session.submissions) == 2
        assert all(item.state == "succeeded" for item in case.session.submissions)
        assert not any(
            item.purpose == "analysis.domain_capture.snapshot"
            for item in provider_statement_log(case.session._backend)
        )
        assert not any(
            text.strip().upper().startswith(("BEGIN", "START TRANSACTION", "ROLLBACK"))
            for text in native
        )
        if backend == "postgres":
            assert int(case.session._backend.con.info.transaction_status) == 0
        observed = {
            "backend": backend,
            "authority": authority,
            "source_changes_permitted": True,
            "domain_snapshot": False,
            "actual_reads": len(case.session.submissions),
        }
    assert case.session._closed
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"{backend}-independent-reads.json").write_text(
            json.dumps(observed, sort_keys=True)
        )
