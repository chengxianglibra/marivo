"""Complete discovery input guards and direct private DataFrame continuations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization import local_execution
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import to_local_frame
from marivo.analysis.materialization.local_execution import (
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalStage,
    StreamInput,
)
from marivo.analysis.observation.contracts import RetainedRowsPayload
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators import candidate_values
from marivo.analysis.operators.candidate_contracts import (
    CandidateEvaluationSummary,
    CandidateObjective,
    CandidatePayload,
    CandidateSpecV1,
)
from marivo.analysis.operators.candidate_dataset import LogicalCandidateDataset
from marivo.analysis.operators.compare import execute_compare
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.row import RowCall
from tests.lazy_candidate_fixtures import VALUES, candidate_input, discover, setup_candidate
from tests.lazy_forecast_fixtures import history_frame
from tests.lazy_observation_fixtures import make_sources


@contextmanager
def _stream(table: pa.Table) -> Iterator[tuple[LocalInputStreams, ...]]:
    yield (LocalInputStreams(table.to_batches(max_chunksize=len(VALUES))),)


def _case(objective: CandidateObjective) -> tuple[LogicalCandidateDataset, pa.Table]:
    source = candidate_input(make_sources(), objective, panel=True)
    candidate = discover(source, objective)
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    spec = candidate._root.payload.spec
    frame = pd.concat(
        [
            history_frame(VALUES, channel="a"),
            history_frame(tuple(value * 2 for value in VALUES), channel="b"),
        ],
        ignore_index=True,
    )
    if objective == "period_shifts":
        assert isinstance(source._root, LogicalRootHandle)
        assert isinstance(source._root.payload, ComparePayload)
        baseline = frame.copy(deep=True)
        baseline["revenue"] = 1.0
        baseline["order_time"] = [value.replace(month=1) for value in baseline.order_time]
        frame = execute_compare(frame, baseline, source._root.payload.spec)
    frame = frame.loc[:, [field.name for field in spec.input_row.schema.columns]]
    return candidate, pa.Table.from_pandas(frame, preserve_index=False)


def _row_call(value: LogicalCandidateDataset) -> RowCall:
    assert isinstance(value._root, LogicalRootHandle)
    payload = value._root.payload
    assert isinstance(payload, RetainedRowsPayload)
    source = value._inputs[0]
    return RowCall(
        value._root.operator_id,
        source.row_contract,
        source.row_set_contract,
        value.row_contract,
        value.row_set_contract,
        payload.predicate,
        payload.rank,
        payload.limit_count,
    )


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
@pytest.mark.parametrize(
    "guard",
    [None, "duplicate_key"],
)
def test_complete_panel_and_budgets_precede_one_discovery_invocation(
    monkeypatch: pytest.MonkeyPatch, objective: CandidateObjective, guard: str | None
) -> None:
    candidate, table = _case(objective)
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    spec = candidate._root.payload.spec
    calls = 0
    original = candidate_values.execute_candidate

    def execute(
        frame: pd.DataFrame,
        received: CandidateSpecV1,
        check: Callable[[], None] | None = None,
    ) -> tuple[pd.DataFrame, CandidateEvaluationSummary]:
        nonlocal calls
        calls += 1
        assert len(frame) == len(VALUES) * 2
        assert set(frame.channel) == {"a", "b"}
        assert received is spec and check is None
        return original(frame, received, check)

    monkeypatch.setattr(candidate_values, "execute_candidate", execute)
    if guard == "duplicate_key":
        table = pa.concat_tables([table, table.slice(len(table) - 1)])
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.input_row, spec.input_rows)),),
        (LocalStage(1, (0,), spec),),
        1,
    )
    with _stream(table) as parent:
        if guard is not None:
            with pytest.raises(MaterializationError):
                local_execution._execute_graph(parent, request)
            assert calls == 0
        else:
            output = local_execution._execute_graph(parent, request)
            assert calls == 1 and output.input_rows == len(VALUES) * 2
            assert output.summaries.candidate is not None
            assert output.summaries.candidate.definition is spec.definition
            assert isinstance(output.summaries.candidate.evaluation, CandidateEvaluationSummary)
            assert output.summaries.candidate.evaluation.evaluated_series_count == 2


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_candidate_successors_reuse_private_frames_without_reconversion(
    monkeypatch: pytest.MonkeyPatch, objective: CandidateObjective
) -> None:
    candidate, table = _case(objective)
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    spec = candidate._root.payload.spec
    filtered = candidate.where(gt(candidate.fields.get("score"), 0.5))
    ranked = filtered.rank(filtered.fields.get("score"))
    limited = ranked.limit(1)
    conversions = 0
    convert = to_local_frame

    def to_frame(table: pa.Table, row: DatasetRowContract) -> pd.DataFrame:
        nonlocal conversions
        conversions += 1
        return convert(table, row)

    monkeypatch.setattr(local_execution, "to_local_frame", to_frame)
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.input_row, spec.input_rows)),),
        (
            LocalStage(1, (0,), spec),
            LocalStage(2, (1,), _row_call(filtered)),
            LocalStage(3, (2,), _row_call(ranked)),
            LocalStage(4, (3,), _row_call(limited)),
        ),
        4,
    )
    with _stream(table) as parent:
        result = local_execution._execute_graph(parent, request)
        assert conversions == 1 and len(result.frames.frame) == 1
        assert len(result.handoffs) == 4
        assert all(left[1] == right[0] for left, right in pairwise(result.handoffs))
        summary = result.summaries.candidate
        assert summary is not None and summary.definition is spec.definition
        assert summary.evaluation.emitted_candidate_count > len(result.frames.frame)


@pytest.mark.runtime
def test_custom_period_checkpoint_discovers_using_retained_calendar_when_source_offline(
    tmp_path: Path,
) -> None:
    from marivo._temporal import certify_period_calendar, semantic_grain, time_scope
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.refs import ref
    from marivo.semantic.ir import PeriodCalendarIR
    from tests.lazy_execution_fixtures import make_execution_registry

    runtime, _, database = setup_candidate(tmp_path, (-2.0, -2.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0))
    registry, sidecar = make_execution_registry(database)
    calendar, axis = (
        ref.period_calendar("sales.fiscal"),
        ref.time_dimension("sales.orders.order_time"),
    )
    snapshot = certify_period_calendar(
        calendar_ref=calendar,
        boundary_timezone="UTC",
        coverage=(date(2026, 2, 1), date(2026, 2, 11)),
        rows=tuple({"date": date(2026, 2, day), "period": (day - 1) // 2} for day in range(1, 11)),
        levels={"reporting_period": "period"},
    )
    template = registry.metrics["sales.revenue"]
    registry = replace(
        registry,
        period_calendars={
            calendar.path: PeriodCalendarIR(
                calendar.path,
                "sales",
                "fiscal",
                axis.path,
                "UTC",
                ("2026-02-01", "2026-02-11"),
                (("reporting_period", "sales.orders.channel"),),
                template.ai_context,
                "fiscal",
                template.location,
            )
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=runtime,
        session_id=runtime.session_ref,
        store_id=runtime.store.store_id,
        period_calendar_snapshots=(snapshot,),
    )
    retained = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-09")
        )
        .with_time_axis(axis, grain=semantic_grain(calendar=calendar, level="reporting_period"))
        .aggregate()
        .execute()
    )
    database.rename(tmp_path / "origin.offline")
    result = retained.discover.interesting_windows(threshold=1.0).execute()
    frame = result.to_pandas()
    assert frame.window_start.tolist() == [date(2026, 2, 1)]
    assert frame.window_end.tolist() == [date(2026, 2, 3)]
    assert frame.point_count.tolist() == [2]
    assert frame.direction.tolist() == ["low"]
    assert frame.reason_codes.tolist() == [("global_zscore_run",)]
    assert result.evidence_digest.finding_count == 0
