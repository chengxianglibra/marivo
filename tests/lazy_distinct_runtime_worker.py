"""Fresh-process source-private membership recovery and exact binding acceptance."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.analysis.session._lazy_sources import LazySources
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
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions

DAY = ref.time_dimension("sales.orders.order_time")


def metric(sources: LazySources, *, current: bool, all_axes: bool = True) -> LogicalMetricDataset:
    """Use equal three-day ordinal scopes with unequal key multiplicities per day."""
    result = sources.observe(
        DISTINCT_BUYERS,
        time_scope=time_scope(
            start="2026-02-02" if current else "2026-01-02",
            end="2026-02-05" if current else "2026-01-05",
        ),
    )
    result = result.with_dimensions(REGION, CHANNEL) if all_axes else result.with_dimensions(REGION)
    return result.with_time_axis(DAY, grain=grain("day")).aggregate()


def _cell(value: object) -> object:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_cell(item) for item in value]
    if type(value) in (bool, int, float, str):
        return value
    raise AssertionError(f"Unexpected retained cell: {type(value).__name__}")


def rows(dataset: MaterializedDataset) -> list[list[object]]:
    return frame_rows(dataset.to_pandas())


def frame_rows(frame: pd.DataFrame) -> list[list[object]]:
    return [[_cell(cell) for cell in row] for row in frame.itertuples(index=False, name=None)]


def assert_daily_reconciliation(result: MaterializedAttributionDataset) -> pd.DataFrame:
    semantics = result.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics)
    assert semantics.method == "distinct_membership@v1"
    assert semantics.resolution_semantics == "independent" and semantics.rollup_safe is False
    frame = result.to_pandas()
    for ordinal, expected in enumerate((0.0, -1.0, 1.0)):
        scoped = frame[frame.comparison_ordinal == ordinal]
        assert not scoped.empty
        for _, resolution in scoped.groupby("active_axis_mask"):
            assert abs(float(resolution.contribution.sum()) - expected) < 1e-9
            assert all(abs(float(value) - expected) < 1e-9 for value in resolution.overall_delta)
    assert_no_raw_keys(frame_rows(frame))
    return frame


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Exact retained binding attempted new compilation or execution")


def run(mode: str, kind: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        seed_distinct_database(database, dense_time=True)
    registry, sidecar = make_distinct_registry(database)
    runtime = (
        DatasetRuntime.create(project, "distinct-journey", target=LocalTarget())
        if mode == "produce"
        else DatasetRuntime.open(project, refs["session"])
    )
    runtime.target = LocalTarget()
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    before = snapshot(runtime)
    if mode == "produce":
        delta = metric(sources, current=True).compare(metric(sources, current=False)).execute()
        barrier = (
            metric(sources, current=True, all_axes=False)
            .compare(metric(sources, current=False, all_axes=False))
            .execute()
        )
        refs = {
            "session": runtime.session_ref,
            "delta": delta.state.artifact_ref.ref,
            "barrier_delta": barrier.state.artifact_ref.ref,
        }
        descriptor = runtime.store.artifact(refs["delta"])
        assert descriptor is not None
        memberships = tuple(
            part
            for part in descriptor.descriptor.retained_parts
            if part.contract_id == "delta.distinct_membership"
        )
        assert len(memberships) == 2
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE orders")
            connection.execute("DROP TABLE customers")
        evidence = {
            "pid": os.getpid(),
            "refs": refs,
            "before": before,
            "after": snapshot(runtime),
            "rows": rows(delta),
            "artifact": record_evidence(descriptor),
            "origin_removed": True,
            "private_membership_parquet": True,
            "versions": versions(),
        }
        assert_no_raw_keys(evidence)
        return evidence

    runtime.target = LocalTarget()
    if kind == "object":
        access = S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            os.environ["MARIVO_TEST_S3_ACCESS_KEY"],
            os.environ["MARIVO_TEST_S3_SECRET_KEY"],
        )
        runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    recovered = runtime.artifact(refs["delta"])
    recovered_barrier = runtime.artifact(refs["barrier_delta"])
    assert isinstance(recovered, MaterializedDeltaDataset) and isinstance(
        recovered_barrier, MaterializedDeltaDataset
    )
    delta, barrier = recovered, recovered_barrier
    barrier_before = snapshot(runtime)
    with ExitStack() as guards:
        guards.enter_context(patch.object(runtime.store, "artifact", _forbidden))
        for name in ("place", "compile_dataset", "_build_backend_from_effective", "supervise"):
            guards.enter_context(patch.object(admission, name, _forbidden))
        try:
            barrier.attribute(axes=(REGION, CHANNEL))
        except DatasetConstructionError as error:
            assert "materialized missing-axis barrier" in str(error)
        else:
            raise AssertionError("A retained Delta recovered its missing axis")
    barrier_after = snapshot(runtime)
    assert barrier_after == barrier_before
    logical = delta.attribute(axes=(REGION, CHANNEL), mode="hierarchy", top_k=1)
    with ExitStack() as guards:
        guards.enter_context(guard_membership_transport())
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective", "supervise"):
                guards.enter_context(patch.object(admission, name, _forbidden))
        result = logical.execute()
        assert isinstance(result, MaterializedAttributionDataset)
        selected = result.where(eq(result.fields.get("active_axis_mask"), (True, True)))
        continued = selected.rank(selected.fields.get("contribution")).limit(100).execute()
        assert continued.findings().items == ()
        if mode == "cold":
            assert result.state.artifact_ref.ref == refs["attribution"]
            assert continued.state.artifact_ref.ref == refs["continued"]
            assert snapshot(runtime) == before
    frame = assert_daily_reconciliation(result)
    refs = {
        **refs,
        "attribution": result.state.artifact_ref.ref,
        "continued": continued.state.artifact_ref.ref,
    }
    run_record = runtime.store.run(result.state.producing_run_ref)
    assert run_record is not None and run_record.input_artifact_refs == (refs["delta"],)
    descriptor = runtime.store.artifact(refs["attribution"])
    assert descriptor is not None
    digest = result.evidence_digest
    assert digest.evidence_digest == descriptor.evidence.evidence_digest
    assert result.findings().items
    assert all(
        part.contract_id != "delta.distinct_membership"
        for part in descriptor.descriptor.retained_parts
    )
    evidence = {
        "schema": "marivo.slice5c.retained-journey/v1",
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "rows": frame_rows(frame),
        "continued_rows": rows(continued),
        "row_contract": _row_contract_fingerprint(result.row_contract),
        "row_set_contract": _row_set_contract_fingerprint(result.row_set_contract),
        "findings": [encode_finding_body(item) for item in result.findings().items],
        "evidence_digest": digest.evidence_digest,
        "input_artifact_refs": run_record.input_artifact_refs,
        "artifact": record_evidence(descriptor),
        "missing_axis_barrier_before": barrier_before,
        "missing_axis_barrier_after": barrier_after,
        "statistics": statistics(runtime),
        "versions": versions(),
        "private_membership_parquet": True,
    }
    assert_no_raw_keys(evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "object"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    references: dict[str, str] = json.loads(args.refs)
    print(
        json.dumps(
            run(args.mode, args.kind, args.project, references), sort_keys=True, allow_nan=False
        )
    )
