"""Store receipts and atomic Runtime failures for the connected scoring methods."""

from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.methods import deviation_numeric as numeric
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("fault", ("iteration", "close"))
def test_fixed_reader_failure_cannot_admit_or_publish_scoring(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.analysis.materialization.graph_storage as storage
    from marivo.analysis.materialization.storage import _open_payload

    case = analysis_dsl_case_factory("j2")
    original = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .execute()
    )
    runs = case.session.runs().items
    opened = _open_payload
    closed: list[bool] = []

    class Reader:
        def __init__(self, inner: pq.ParquetFile) -> None:
            self.inner = inner
            self.schema_arrow = inner.schema_arrow

        def iter_batches(
            self,
            *,
            batch_size: int = 65536,
            row_groups: list[int] | None = None,
            columns: list[str] | None = None,
            use_threads: bool = True,
        ) -> Iterator[pa.RecordBatch]:
            if fault == "iteration":
                raise pa.ArrowInvalid("injected scoring input reader failure")
            yield from self.inner.iter_batches(
                batch_size=batch_size,
                row_groups=row_groups,
                columns=columns,
                use_threads=use_threads,
            )

        def close(self) -> None:
            self.inner.close()
            closed.append(True)
            if fault == "close":
                raise pa.ArrowInvalid("injected scoring input close failure")

    def open_payload(root: Path, receipt: LocalReceipt) -> tuple[Reader, Path]:
        inner, path = opened(root, receipt)
        return Reader(inner), path

    monkeypatch.setattr(storage, "_open_payload", open_payload)
    with pytest.raises(AnalysisError):
        original.deviation(method=method).execute()
    assert closed == [True]
    assert case.session.runs().items == runs
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_source_close_failure_is_atomic_after_scoring_admission(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceBatchStream

    case = analysis_dsl_case_factory("j2")
    source = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    original = source.execute()
    close = SourceBatchStream.close
    closed: list[bool] = []

    def fail(self: SourceBatchStream) -> None:
        close(self)
        closed.append(True)
        raise pa.ArrowInvalid("injected scoring source close failure")

    monkeypatch.setattr(SourceBatchStream, "close", fail)
    with pytest.raises(AnalysisError):
        source.deviation(method=method).execute()
    assert closed
    runtime = case.session._runtime
    assert runtime.store.resources(runtime.session_ref) == ()
    assert len(original.to_pandas()) == 11
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_decimal_output_overflow_is_rejection_not_positive_qualification(
    analysis_dsl_case_factory: DslCaseFactory, method: numeric.DeviationMethod
) -> None:
    case = analysis_dsl_case_factory("j2")
    maximum = Decimal("99999999999999999999999999999999999999")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'ALTER TABLE "order" ALTER amount TYPE DECIMAL(38,0) USING CAST(amount AS DECIMAL(38,0))'
        )
        connection.execute('UPDATE "order" SET amount=?', [maximum])
    source = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    previous = source.execute()
    with pytest.raises(StatisticalRelationError) as error:
        source.deviation(method=method).execute()
    assert error.value.code == "r8.numeric_overflow"
    runtime = case.session._runtime
    assert runtime.store.resources(runtime.session_ref) == ()
    assert previous.to_pandas().value.tolist() == [maximum] * 11
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_fixed_selected_subject_requires_retained_observation_contract(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.deviation_r82_worker import forbidden

    case = analysis_dsl_case_factory("j2")
    result = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method=method)
        .execute()
    )
    fixed_members = result.where(result.score.value.gt(0)).execute().observed.members()
    runs = case.session.runs().items
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    with pytest.raises(
        StatisticalRelationError, match="no retained observation contract"
    ) as failure:
        operation = "observe"
        _ = getattr(fixed_members, operation)
    assert failure.value.code == "r8.retained_part"
    assert case.session.runs().items == runs


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize(
    "role",
    (
        "primary",
        "fit_inputs",
        "fit_state",
        "finding_policy",
        "grid_cells",
        "subject",
        "subject_map",
    ),
)
@pytest.mark.parametrize("fault", ("missing", "corrupt"))
def test_each_receipt_rejects_recovery_continuation_and_exact_hit(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    role: str,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-03"), grain=mv.grain("day")
    )
    original = (
        case.session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid.window,
            via=ms.ref.relationship("sales." + case.names.buyer),
        )
        .execute()
    )
    logical = original.deviation(method=method)
    fixed = logical.execute()
    assert len(fixed.score.to_pandas()) == 8
    assert fixed._dataset is not None
    descriptor = fixed._dataset.artifact.descriptor
    receipt = (
        descriptor.primary_receipt.local
        if role == "primary"
        else next(part.local for part in descriptor.parts if part.role == role)
    )
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"damaged deviation receipt")
    runs = case.session.runs().items
    for action in (
        lambda: case.session.artifact(fixed.evidence_digest().artifact_ref),
        lambda: fixed.score.to_pandas(),
        lambda: fixed.where(fixed.score.value.is_defined()).execute(),
        logical.execute,
    ):
        with pytest.raises(AnalysisError):
            action()
        assert case.session.runs().items == runs


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("fault", ("cancel", "consumer_error"))
def test_local_failure_releases_prepared_sources_and_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: numeric.DeviationMethod,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    source = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    previous = source.execute()
    previous_ref = previous.evidence_digest().artifact_ref

    def fail(xs: tuple[Fraction, ...], algorithm: numeric.DeviationMethod) -> numeric.Fit:
        if fault == "cancel":
            raise KeyboardInterrupt("cancelled after source preparation")
        raise RuntimeError("scoring consumer failed")

    monkeypatch.setattr(numeric, "fit", fail)
    with pytest.raises((AnalysisError, KeyboardInterrupt)):
        source.deviation(method=method).execute()
    assert len(case.session.artifact(previous_ref).to_pandas()) == 11
    runtime = case.session._runtime
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM findings").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize(
    "point",
    ("insert_artifact", "insert_evidence", "insert_findings", "insert_terminal", "before_commit"),
)
def test_transaction_failure_has_no_partial_success(
    analysis_dsl_case_factory: DslCaseFactory, method: numeric.DeviationMethod, point: str
) -> None:
    case = analysis_dsl_case_factory("j2")

    def inject(actual: str) -> None:
        if actual == point:
            raise RuntimeError("injected deviation publication failure")

    runtime = case.session._runtime
    runtime._hook = inject
    with pytest.raises(AnalysisError):
        case.session.members(ms.ref.entity("sales.order")).read(
            ms.ref.measure("sales.order.amount")
        ).deviation(method=method).execute()
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM analysis_action_run_terminals WHERE outcome='succeeded'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.runtime
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_lost_commit_ack_returns_verified_fit_without_source_replay(
    analysis_dsl_case_factory: DslCaseFactory,
    method: numeric.DeviationMethod,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceBatchStream

    case = analysis_dsl_case_factory("j2")
    iterate = SourceBatchStream._iterate
    reads: list[bool] = []
    committed_read_counts: list[int] = []

    def read(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        reads.append(True)
        yield from iterate(stream)

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            committed_read_counts.append(len(reads))
            raise OSError("lost scoring commit acknowledgement")

    monkeypatch.setattr(SourceBatchStream, "_iterate", read)
    runtime = case.session._runtime
    runtime._hook = lose_ack
    result = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method=method)
        .execute()
    )
    assert result._dataset is not None
    checked = result._dataset.verified()
    assert checked.primary.num_rows == 11
    assert {part.role for part in checked.parts} >= {
        "fit_inputs",
        "fit_state",
        "finding_policy",
        "subject_map",
    }
    assert reads and committed_read_counts == [len(reads)]
    assert result._dataset.artifact.producing_run_ref == runtime.last_run_ref
    assert result.evidence_digest().finding_count == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM analysis_action_run_terminals WHERE outcome='succeeded'"
            ).fetchone()[0]
            == 1
        )
