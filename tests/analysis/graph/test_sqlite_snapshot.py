"""Native SQLite capture consistency, cancellation and connection ownership."""

import sqlite3
from contextlib import closing
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import marivo.datasource.domain_snapshot as snapshots
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.domain_snapshot import capture
from marivo.datasource.ir import TableSourceIR
from tests.datasource.source_cases import source_case


def test_sqlite_capture_freezes_all_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    with source_case("sqlite", "table", tmp_path, monkeypatch) as case:
        assert isinstance(case.source, TableSourceIR)
        binding = case.session.bind(case.source, source_identity="facts")
        qualified = case.session.qualify(
            binding, PhysicalRequirement("capture", 1, frozenset({"scan"}))
        )
        table = binding.relation.order_by("id", "tenant")
        read = case.session.compile(
            qualified, table, purpose="capture", expected_schema=table.schema().to_pyarrow()
        )

        def collect() -> list[object]:
            with closing(case.session.batches(read, chunk_size=1)) as batches:
                return [value for batch in batches for value in batch.column("amount").to_pylist()]

        original = case.session._backend.con
        with capture(case.session):
            assert collect() == [2, None, 4]
            with sqlite3.connect(tmp_path / "source.sqlite") as writer:
                writer.execute(f"UPDATE {case.source.table} SET amount=99")
            assert collect() == [2, None, 4]
        assert case.session._backend.con is original
        assert collect() == [99, 99, 99]
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))


def test_sqlite_capture_failure_restores_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    with source_case("sqlite", "table", tmp_path, monkeypatch) as case:
        case.session.bind(case.source, source_identity="facts")
        original = case.session._backend.con
        with pytest.raises(KeyboardInterrupt), capture(case.session):
            assert case.session._backend.con is not original
            raise KeyboardInterrupt()
        assert case.session._backend.con is original
        assert original.execute("SELECT 1").fetchone() == (1,)
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))


def test_sqlite_backup_setup_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    with source_case("sqlite", "table", tmp_path, monkeypatch) as case:
        case.session.bind(case.source, source_identity="facts")
        original = case.session._backend.con

        def failed_connect(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("backup setup interrupted")

        monkeypatch.setattr("marivo.datasource.engines.sqlite.connect", failed_connect)
        with pytest.raises(RuntimeError, match="backup setup interrupted"), capture(case.session):
            pytest.fail("Incomplete backup entered its consumer")
        assert case.session._backend.con is original
        assert not case.session.submissions
        assert original.execute("SELECT 1").fetchone() == (1,)
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))
