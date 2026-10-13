"""Prepared inputs record their source identity without temporary file copies."""

import json
import os
from collections.abc import Iterable
from pathlib import Path

import ibis.expr.types as ir
import pytest

import marivo.datasource.domain_snapshot as snapshots
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.ir import ParquetSourceIR
from tests.datasource.source_cases import source_case, source_params
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize("backend", source_params(("postgres", "mysql", "trino", "clickhouse")))
def test_remote_preparation_records_independent_reads(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
) -> None:
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
        with snapshots.capture(case.session) as authority:
            assert authority["kind"] == "independent_reads"
            assert authority["files"] == []
            for iteration in range(2):
                frame = case.session.execute(read)
                expected = [99, 99] if backend == "postgres" and iteration == 1 else [2, 4]
                assert frame["amount"].isna().tolist() == [False, True, False]
                assert frame.loc[frame["amount"].notna(), "amount"].tolist() == expected
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


def test_local_parquet_preparation_uses_declared_file_and_detects_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source_case("duckdb", "parquet", tmp_path, monkeypatch) as case:
        assert isinstance(case.source, ParquetSourceIR)
        path = Path(case.source.path).resolve()
        case.session.bind(case.source, source_identity="facts")
        backend = case.session._backend
        read_parquet = backend.read_parquet
        read_paths: list[str] = []

        def track_read(paths: str | Path | Iterable[str | Path], /, **kwargs: object) -> ir.Table:
            assert isinstance(paths, (str, Path))
            read_paths.append(str(paths))
            return read_parquet(paths, **kwargs)

        monkeypatch.setattr(backend, "read_parquet", track_read)
        with (
            pytest.raises(DatasourceSourceCapabilityError) as failure,
            snapshots.capture(case.session) as authority,
        ):
            assert authority["kind"] == "file_manifest"
            assert read_paths == [str(path)]
            path.write_bytes(path.read_bytes() + b"changed")
        assert failure.value.received == "Parquet changed during prepared analysis"
