"""Native SQLite capture consistency, cancellation and connection ownership."""

import sqlite3
import time
from contextlib import closing, nullcontext
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import marivo.datasource.domain_snapshot as snapshots
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline, check, guard
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.domain_snapshot import capture
from marivo.datasource.ir import TableSourceIR
from tests.r9_source_cases import source_case


@pytest.mark.runtime
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
        with capture(case.session, checkpoint=lambda: None, guard=lambda cancel: nullcontext()):
            assert collect() == [2, None, 4]
            with sqlite3.connect(tmp_path / "source.sqlite") as writer:
                writer.execute(f"UPDATE {case.source.table} SET amount=99")
            assert collect() == [2, None, 4]
        assert case.session._backend.con is original
        assert collect() == [99, 99, 99]
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))


@pytest.mark.runtime
def test_sqlite_capture_interrupt_restores_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    with source_case("sqlite", "table", tmp_path, monkeypatch) as case:
        binding = case.session.bind(case.source, source_identity="facts")
        relation = binding.relation
        for _ in range(18):
            relation = relation.cross_join(binding.relation.view()).select(relation)
        relation = relation.aggregate(value=relation.count())
        qualified = case.session.qualify(
            binding,
            PhysicalRequirement("capture-deadline", 1, frozenset({"scan", "join", "group"})),
        )
        read = case.session.compile(
            qualified,
            relation,
            purpose="capture-deadline",
            expected_schema=relation.schema().to_pyarrow(),
        )
        original = case.session._backend.con
        native_statements: list[str] = []
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=0.1))
        try:
            with (
                pytest.raises(DomainPreparationError, match="execute_timeout"),
                capture(case.session, checkpoint=check, guard=guard),
            ):
                case.session._backend.con.set_trace_callback(native_statements.append)
                with closing(case.session.batches(read, chunk_size=1)) as batches:
                    list(batches)
        finally:
            CURRENT.reset(token)
        assert time.monotonic() - started < 3
        assert case.session._backend.con is original
        assert case.session.submissions[-1].state == "failed"
        assert read.sql in native_statements
        assert original.execute("SELECT 1").fetchone() == (1,)
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))


@pytest.mark.runtime
def test_sqlite_backup_checkpoint_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    with source_case("sqlite", "table", tmp_path, monkeypatch) as case:
        case.session.bind(case.source, source_identity="facts")
        original = case.session._backend.con
        calls = 0

        def checkpoint() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("backup interrupted")

        with (
            pytest.raises(RuntimeError, match="backup interrupted"),
            capture(case.session, checkpoint=checkpoint, guard=lambda cancel: nullcontext()),
        ):
            pytest.fail("Incomplete backup entered its consumer")
        assert case.session._backend.con is original
        assert not case.session.submissions
        assert original.execute("SELECT 1").fetchone() == (1,)
        assert not list(tmp_path.glob("marivo-sqlite-capture-*"))
