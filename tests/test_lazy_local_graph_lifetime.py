"""Graph ownership releases retained allocations when their final consumer ends."""

from __future__ import annotations

import gc
import time
import weakref
from multiprocessing import Pipe
from multiprocessing.connection import Connection

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.materialization import local_worker
from marivo.analysis.materialization.local import LocalBudget, LocalPolicy, frame_bytes
from marivo.analysis.materialization.local_worker import (
    ArtifactInput,
    LocalBoundary,
    LocalGraphRequest,
    LocalPartInput,
    LocalStage,
    StreamInput,
)
from marivo.analysis.operators.row import PartFrame, RowCall
from tests.lazy_compare_fixtures import comparison_spec


def test_comparison_releases_row_method_parts_before_next_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = comparison_spec()
    released: list[weakref.ReferenceType[pd.DataFrame]] = []
    tail_checked = False

    def collect(
        connection: Connection,
        selected: StreamInput | ArtifactInput,
        parts: tuple[LocalPartInput, ...],
        budget: LocalBudget,
    ) -> tuple[local_worker._Frames, int]:
        frame = pd.DataFrame(
            {
                "region": pd.Series(["a"], dtype=pd.ArrowDtype(pa.string())),
                "revenue": pd.Series([4.0], dtype="double[pyarrow]"),
            }
        )
        budget.live_bytes += frame_bytes(frame)
        return local_worker._Frames(frame, (), pa.Schema.from_pandas(frame)), len(frame)

    def row_step(
        frame: pd.DataFrame,
        parts: tuple[PartFrame, ...],
        calls: tuple[RowCall, ...],
        budget: LocalBudget,
    ) -> tuple[pd.DataFrame, tuple[PartFrame, ...], tuple[tuple[int, int], ...]]:
        nonlocal tail_checked
        result = frame.copy(deep=True)
        output_parts: tuple[PartFrame, ...] = ()
        if calls[0].method == "metric.where":
            retained = pd.DataFrame(
                {"state": pd.Series(["x" * 2048], dtype=pd.ArrowDtype(pa.string()))}
            )
            released.append(weakref.ref(retained))
            output_parts = (
                PartFrame(
                    "test_state", "test_state", 1, pa.Schema.from_pandas(retained), (), retained
                ),
            )
        else:
            gc.collect()
            assert len(released) == 1 and released[0]() is None
            assert parts == ()
            assert budget.live_bytes == frame_bytes(frame)
            tail_checked = True
        old_size = frame_bytes(frame) + sum(frame_bytes(part.frame) for part in parts)
        new_size = frame_bytes(result) + sum(frame_bytes(part.frame) for part in output_parts)
        budget.live_bytes += new_size - old_size
        return result, output_parts, ((id(frame), id(result)),)

    monkeypatch.setattr(local_worker, "_collect_input", collect)
    monkeypatch.setattr(local_worker, "execute_retained_suffix", row_step)
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.current_row, spec.current_rows)),),
        (
            LocalStage(
                1,
                (0,),
                RowCall(
                    "metric.where",
                    spec.current_row,
                    spec.current_rows,
                    spec.current_row,
                    spec.current_rows,
                ),
            ),
            LocalStage(2, (1, 1), spec),
            LocalStage(
                3,
                (2,),
                RowCall(
                    "delta.where",
                    spec.output_row,
                    spec.output_rows,
                    spec.output_row,
                    spec.output_rows,
                ),
            ),
        ),
        3,
        LocalPolicy(),
        time.monotonic() + 60,
    )
    parent, child = Pipe()
    try:
        output, _, _, _ = local_worker._execute_graph(
            parent, request, LocalBudget(request.policy, request.deadline)
        )
    finally:
        parent.close()
        child.close()
    assert tail_checked
    assert output.frame["delta"].tolist() == [0.0]
