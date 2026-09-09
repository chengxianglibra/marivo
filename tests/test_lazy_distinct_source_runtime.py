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
from marivo.analysis.materialization.contracts import sampling_payload
from marivo.analysis.materialization.targets import EngineTarget
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.refs import ref
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    DISTINCT_BUYERS,
    REGION,
    assert_no_raw_keys,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_distinct_runtime_worker import assert_daily_reconciliation, metric, rows
from tests.lazy_materialization_crash_worker import snapshot, statistics
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("states", ("LL", "LM", "ML", "MM"))
def test_every_operand_order_keeps_membership_inside_engine(tmp_path: Path, states: str) -> None:
    candidate = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_distinct_database(database, dense_time=True)
    registry, sidecar = make_distinct_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "distinct-operands", target=EngineTarget("warehouse"))
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    left, right = metric(sources, current=True), metric(sources, current=False)
    with guard_membership_transport():
        current = left.execute() if states[0] == "M" else left
        baseline = right.execute() if states[1] == "M" else right
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
    assert_daily_reconciliation(result)
    assert runtime.statistics.worker_pid is None
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
        "rows": rows(result),
        "before": before,
        "after": complete,
        "input_artifact_refs": run.input_artifact_refs,
        "statistics": statistics(runtime),
        "raw_membership_transfer": False,
        "worker_pid": runtime.statistics.worker_pid,
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


def test_distinct_keeps_one_shared_sample_realization(tmp_path: Path) -> None:
    candidate = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_distinct_database(database)
    registry, sidecar = make_distinct_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "distinct-sampling", target=EngineTarget("warehouse"))
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = sources.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=7, seed=11)
    )
    observed = (
        sources.observe(DISTINCT_BUYERS, population=population).with_dimensions(REGION).aggregate()
    )
    logical = observed.compare(observed).attribute(axes=(REGION,), top_k=1)
    before = snapshot(runtime)
    with guard_membership_transport():
        result = logical.execute()
    assert isinstance(result, MaterializedAttributionDataset)
    assert runtime.statistics.sampling_fences == 1
    assert runtime.statistics.worker_pid is None
    sampled_statistics = statistics(runtime)
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    (realization,) = record.descriptor.sampling_execution
    assert realization.target_rows == realization.realized_entity_count == 7
    assert realization.seed == 11 and len(realization.membership_digest) == 64
    assert (
        sum(part.role == "population_sampling_state" for part in record.descriptor.retained_parts)
        == 1
    )
    semantics = result.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics)
    assert semantics.approximation_class == "sampled_population"
    frame = result.to_pandas()
    assert not frame.empty
    assert frame.current_value.tolist() == frame.baseline_value.tolist()
    assert all(float(value) == 0.0 for value in frame.contribution)
    assert all(float(value) == 0.0 for value in frame.overall_delta)
    totals = [float(group.current_value.sum()) for _, group in frame.groupby("active_axis_mask")]
    assert len(totals) == 1
    assert 1 <= totals[0] <= realization.realized_entity_count
    proof = record.descriptor.attribution_evidence
    assert proof is not None and proof.complete and proof.max_reconciliation_error == 0
    complete = snapshot(runtime)
    assert logical.execute().state.artifact_ref.ref == result.state.artifact_ref.ref
    assert snapshot(runtime) == complete
    assert runtime.statistics.sampling_fences == runtime.statistics.primary_queries == 0
    assert (
        runtime.statistics.worker_pid is None and runtime.store.resources(runtime.session_ref) == ()
    )
    payload = {
        "artifact": result.state.artifact_ref.ref,
        "before": before,
        "after": complete,
        "rows": rows(result),
        "sampling": sampling_payload(record.descriptor.sampling_execution),
        "statistics": sampled_statistics,
        "resolution_totals": totals,
        "raw_membership_transfer": False,
    }
    assert_no_raw_keys(payload)
    directory = os.environ.get("MARIVO_SLICE5C_EVIDENCE_DIR")
    if directory:
        assert candidate == _manifest()
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / "distinct-shared-sampling.json").write_text(
            json.dumps(
                {
                    "schema": "marivo.slice5c.sampling-runtime/v1",
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
