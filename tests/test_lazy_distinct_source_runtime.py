"""Source-private membership across all logical and engine Metric operand orders."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    REGION,
    assert_no_raw_keys,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_distinct_runtime_worker import assert_daily_reconciliation, frame_rows, metric
from tests.lazy_materialization_crash_worker import snapshot, statistics
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("states", ("LL", "LM", "ML", "MM"))
def test_every_operand_order_keeps_membership_inside_engine(tmp_path: Path, states: str) -> None:
    candidate = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_distinct_database(database, dense_time=True)
    registry, sidecar = make_distinct_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "distinct-operands", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    left, right = metric(sources, current=True), metric(sources, current=False)
    current = left.execute() if states[0] == "M" else left
    baseline = right.execute() if states[1] == "M" else right
    with guard_membership_transport():
        if states == "MM":
            with duckdb.connect(str(database), config={"threads": 1}) as connection:
                connection.execute("DROP TABLE orders")
                connection.execute("DROP TABLE customers")
        before = snapshot(runtime)
        logical = current.compare(baseline).attribute(
            axes=(REGION, CHANNEL), mode="hierarchy", top_k=1
        )
        assert snapshot(runtime) == before
        result = logical.execute()
    assert isinstance(result, MaterializedAttributionDataset)
    frame = assert_daily_reconciliation(result)
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    run = runtime.store.run(result.state.producing_run_ref)
    assert run is not None
    assert run.input_artifact_refs == tuple(
        operand.state.artifact_ref.ref
        for operand in (current, baseline)
        if isinstance(operand, MaterializedDataset)
    )
    complete = snapshot(runtime)
    assert logical.execute().state.artifact_ref.ref == result.state.artifact_ref.ref
    assert snapshot(runtime) == complete
    assert runtime.store.resources(runtime.session_ref) == ()
    payload = {
        "states": states,
        "artifact": result.state.artifact_ref.ref,
        "rows": frame_rows(frame),
        "before": before,
        "after": complete,
        "input_artifact_refs": run.input_artifact_refs,
        "statistics": statistics(runtime),
        "raw_membership_transfer": False,
        "local_executions": runtime.statistics.events.get("local_execution_started", 0),
    }
    assert_no_raw_keys(payload)
    directory = os.environ.get("MARIVO_SLICE5C_EVIDENCE_DIR")
    if directory:
        assert candidate == _manifest()
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"distinct-operands-{states}.json").write_text(
            json.dumps(
                {
                    "schema": "marivo.slice5c.operand-runtime/v1",
                    "emitted_at": datetime.now(timezone.utc).isoformat(),
                    "candidate_before": candidate,
                    "candidate_after": _manifest(),
                    **payload,
                },
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
