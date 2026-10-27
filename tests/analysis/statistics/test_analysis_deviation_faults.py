"""Terminal reader and publication failures preserve statistical input artifacts."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.storage import _open_payload
from tests.analysis.statistics.pandas_workflows import assert_clean, deviation, orders, plain
from tests.shared_fixtures import DslCaseFactory, run_ids, terminal_runs


def test_terminal_reader_failure_stops_pandas_scoring_without_run(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_reading as reading

    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    before = run_ids(case.session)
    opened = _open_payload
    for fault in ("iteration", "close"):
        closed: list[bool] = []

        class Reader:
            def __init__(self, inner: pq.ParquetFile) -> None:
                self.inner = inner
                self.schema_arrow = inner.schema_arrow
                self.metadata = inner.metadata
                self.num_row_groups = inner.num_row_groups

            def __enter__(self) -> "Reader":
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

            def iter_batches(
                self,
                *,
                batch_size: int = 65536,
                row_groups: list[int] | None = None,
                _fault: str = fault,
            ) -> Iterator[pa.RecordBatch]:
                if _fault == "iteration":
                    raise pa.ArrowInvalid("injected statistics input reader failure")
                yield from self.inner.iter_batches(batch_size=batch_size, row_groups=row_groups)

            def close(self, _fault: str = fault, _closed: list[bool] = closed) -> None:
                self.inner.close()
                _closed.append(True)
                if _fault == "close":
                    raise pa.ArrowInvalid("injected statistics input close failure")

        def open_payload(root: Path, receipt: LocalReceipt) -> tuple[Reader, Path]:
            inner, path = opened(root, receipt)
            return Reader(inner), path

        with monkeypatch.context() as scoped:
            scoped.setattr(reading, "_open_payload", open_payload)
            with pytest.raises(AnalysisError):
                deviation(plain(artifact), "zscore")
        assert closed == [True]
        assert run_ids(case.session) == before
        assert_clean(case.session, artifacts=1)


def test_decimal_sum_uses_arrow_native_accumulator_and_storage_boundary(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    maximum = Decimal("99999999999999999999999999999999999999")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'ALTER TABLE "order" ALTER amount TYPE DECIMAL(38,0) USING CAST(amount AS DECIMAL(38,0))'
        )
        connection.execute('UPDATE "order" SET amount=?', [maximum])
    query = orders(case)
    previous = query.persist()
    before = run_ids(case.session)
    assert plain(previous).value.tolist() == [maximum] * 11
    native = pa.compute.sum(pa.array([maximum] * 11, type=pa.decimal128(38, 0))).as_py()
    total = query.reduce({"total": query["value"].sum()}).persist()
    assert total.to_pandas()["total"].tolist() == [native]
    assert_clean(case.session, artifacts=2)
    assert len(run_ids(case.session)) == len(before) + 1


def test_exported_subject_selection_does_not_mutate_saved_frame(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    before = run_ids(case.session)
    expected = plain(artifact)
    scores = deviation(expected.copy(deep=True), "zscore")
    selected = scores.loc[scores.score > 0]
    assert not selected.empty
    for name in ("population", "observe", "deviation"):
        assert not hasattr(artifact, name)
    pd.testing.assert_frame_equal(plain(artifact), expected)
    assert run_ids(case.session) == before


@pytest.mark.parametrize("cancel", (False, True))
def test_local_failure_releases_prepared_sources_and_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    from marivo.analysis.materialization import frame_evaluation

    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    previous = query.persist()
    saved = previous.to_pandas()
    before = run_ids(case.session)

    def fail(*args: object, **kwargs: object) -> None:
        if cancel:
            raise KeyboardInterrupt("cancelled after source preparation")
        raise RuntimeError("local consumer failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(frame_evaluation, "run_frame_plan", fail)
        with pytest.raises((AnalysisError, KeyboardInterrupt)):
            query.persist()
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(previous.artifact_id).to_pandas(), saved
    )
    assert_clean(case.session, artifacts=1)
    assert run_ids(case.session) == before


@pytest.mark.parametrize("point", ("insert_findings", "before_commit"))
def test_transaction_failure_has_no_partial_success(
    analysis_dsl_case_factory: DslCaseFactory,
    point: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    runtime = case.session._runtime
    injected: list[str] = []

    def inject(actual: str) -> None:
        if actual == point:
            injected.append(actual)
            raise RuntimeError("injected terminal publication failure")

    from marivo.analysis.materialization import terminal_publication

    monkeypatch.setattr(terminal_publication, "_event", inject)
    with pytest.raises(AnalysisError):
        orders(case).persist()
    assert injected == [point]
    assert len(terminal_runs(case.session, status="failed")) == 1
    assert_clean(case.session, artifacts=0)
    with runtime.store._read() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM analysis_action_run_terminals WHERE outcome='succeeded'"
            ).fetchone()[0]
            == 0
        )


def test_lost_commit_ack_returns_committed_output_without_source_replay(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    case = analysis_dsl_case_factory("j2")
    execute = SourceSession.execute
    reads: list[bool] = []
    committed: list[int] = []

    def read(source: SourceSession, compiled: CompiledRead) -> pd.DataFrame:
        frame = execute(source, compiled)
        reads.append(True)
        return frame

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            committed.append(len(reads))
            raise OSError("lost terminal commit acknowledgement")

    monkeypatch.setattr(SourceSession, "execute", read)
    from marivo.analysis.materialization import terminal_publication

    monkeypatch.setattr(terminal_publication, "_event", lose_ack)
    artifact = orders(case).persist()
    assert reads and committed == [len(reads)]
    assert len(plain(artifact)) == 11
    runs = terminal_runs(case.session, status="succeeded")
    assert len(runs) == 1
    assert runs[0].lifecycle == "succeeded"
    assert runs[0].output_artifact_ref == artifact.artifact_id
    assert (
        artifact._local._audit_manifest(artifact._require_stored("artifact_id")).run_id
        == runs[0].run_ref
    )
    assert artifact.evidence_digest().finding_count == 0
    assert_clean(case.session, artifacts=1)
    before = run_ids(case.session)
    deviation(plain(artifact), "zscore")
    assert run_ids(case.session) == before
