"""Slice 1b: complete caller execution, original failures and no resource admission."""

from __future__ import annotations

import os
import subprocess
import traceback
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.materialization import local, storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.local_execution import (
    LocalInputStreams,
    LocalRequest,
    StreamInput,
    execute_local,
)
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.row import RowCall, execute_row
from tests.lazy_local_fixtures import REVENUE, pandas_methods, row_call, setup_local
from tests.lazy_observation_fixtures import make_sources


def test_complete_local_input_and_output_above_former_row_cap() -> None:
    source = make_sources().observe(REVENUE)
    count = 100_001
    table = pa.table(
        {
            "entity_identity": pa.StructArray.from_arrays([pa.array(range(count))], names=["id"]),
            "revenue": pa.array([1.0] * count),
        }
    )
    request = LocalRequest(
        StreamInput(source.row_contract, source.row_set_contract),
        (row_call(source.where(gt(REVENUE, 0))),),
    )
    result = execute_local(request, (LocalInputStreams(table.to_batches(max_chunksize=1024)),))
    assert result.input_rows == count and result.table.num_rows == count
    assert result.table["revenue"].to_pylist() == [1.0] * count


def test_wide_nested_dictionary_values_are_normalized_completely() -> None:
    payload = "x" * (8_388_608 + 1)
    values = pa.StructArray.from_arrays([pa.array([payload]).dictionary_encode()], names=["text"])
    normalized = storage._normalize_batch(pa.record_batch([values], names=["nested"]))
    assert normalized["nested"].to_pylist() == [{"text": payload}]
    assert normalized.schema.field("nested").type == pa.struct([("text", pa.string())])


@pytest.mark.runtime
def test_kernel_and_retained_collection_run_in_caller_without_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    retained = sources.observe(REVENUE).execute()
    caller = os.getpid()
    seen: list[tuple[str, int]] = []
    original_step, original_read = execute_row, storage._to_dataframe

    def step(frame: pd.DataFrame, call: RowCall) -> pd.DataFrame:
        seen.append(("kernel", os.getpid()))
        return original_step(frame, call)

    def read(table: pa.Table, row: DatasetRowContract) -> pd.DataFrame:
        seen.append(("read", os.getpid()))
        return original_read(table, row)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Production execution must not spawn a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(local, "execute_row", step)
    monkeypatch.setattr(storage, "_to_dataframe", read)
    with pandas_methods("metric.where"):
        result = retained.where(gt(REVENUE, 5)).execute()
    assert result.to_pandas()["revenue"].tolist() == [10.0, 30.0, 100.0, 7.0]
    assert {kind for kind, _ in seen} == {"kernel", "read"}
    assert all(pid == caller for _, pid in seen)
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("interrupted", [False, True])
def test_original_kernel_failure_survives_cleanup_and_has_no_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    retained = sources.observe(REVENUE).execute()
    original = (
        KeyboardInterrupt("caller interrupted") if interrupted else ValueError("kernel failed")
    )
    cause = RuntimeError("original cause")

    def failed_kernel(frame: pd.DataFrame, call: RowCall) -> pd.DataFrame:
        raise original from cause

    monkeypatch.setattr(local, "execute_row", failed_kernel)
    with pandas_methods("metric.where"), pytest.raises(type(original)) as caught:
        retained.where(gt(REVENUE, 5)).execute()
    assert caught.value is original and caught.value.__cause__ is cause
    assert "failed_kernel" in [
        entry.name for entry in traceback.extract_tb(caught.value.__traceback__)
    ]
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref)
    assert reopened.artifact(retained.state.artifact_ref).to_pandas().shape[0] == 6


@pytest.mark.runtime
@pytest.mark.parametrize("point", ["kernel", "after_rename", "before_commit", "after_commit"])
def test_killed_calling_process_recovers_only_atomic_publication(
    tmp_path: Path, point: str
) -> None:
    import signal
    import sys

    runtime, sources, _ = setup_local(tmp_path)
    retained = sources.observe(REVENUE).execute()
    script = """
import os, signal, sys
from pathlib import Path
from marivo.analysis.materialization import local
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.predicates import gt
from tests.lazy_local_fixtures import REVENUE, pandas_methods
root, session, artifact, point = sys.argv[1:]
runtime = DatasetRuntime.open(Path(root), session)
retained = runtime.artifact(artifact)
def kill(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)
def event(name):
    if name == point:
        kill()
if point == "kernel":
    local.execute_row = kill
else:
    runtime._hook = event
with pandas_methods("metric.where"):
    retained.where(gt(REVENUE, 5)).execute()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(tmp_path),
            runtime.session_ref,
            retained.state.artifact_ref.ref,
            point,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == -signal.SIGKILL, process.stdout + process.stderr
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref)
    with reopened.store._read() as conn:
        before = conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
        assert before == (2 if point == "after_commit" else 1)
    recovered = reopened.artifact(retained.state.artifact_ref)
    assert isinstance(recovered, MaterializedMetricDataset)
    with pandas_methods("metric.where"):
        result = recovered.where(gt(REVENUE, 5)).execute()
    assert result.to_pandas()["revenue"].tolist() == [10.0, 30.0, 100.0, 7.0]
    assert reopened.store.resources(reopened.session_ref) == ()
    with reopened.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 2
