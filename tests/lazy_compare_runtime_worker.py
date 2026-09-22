"""Fresh-process Delta publication, source-offline continuation and exact reuse."""

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
from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot, statistics, versions

REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
DAY = ref.time_dimension("sales.orders.order_time")


def _scalar(value: object) -> object:
    if value is pd.NA or value is pd.NaT or value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_scalar(item) for item in value]
    if type(value) in (str, int, float, bool):
        return value
    raise AssertionError(f"Unexpected public cell type: {type(value).__name__}")


def _rows(dataset: MaterializedDataset) -> list[list[object]]:
    return [
        [_scalar(cell) for cell in row]
        for row in dataset.to_pandas().itertuples(index=False, name=None)
    ]


def _metric(sources: LazySources, *, current: bool) -> LogicalMetricDataset:
    return (
        sources.observe(
            REVENUE,
            time_scope=time_scope(
                start="2026-02-02" if current else "2026-02-03",
                end="2026-02-03" if current else "2026-02-04",
            ),
        )
        .with_dimensions(CHANNEL)
        .with_time_axis(DAY, grain=grain("day"))
        .aggregate()
    )


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold execution-key recovery attempted source or execution work")


def run(mode: str, kind: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    access = (
        S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            "minioadmin",
            "minioadmin",
        )
        if kind == "object"
        else None
    )
    if mode == "produce":
        seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    if mode == "produce":
        runtime = DatasetRuntime.create(project, "delta-journey")
    else:
        runtime = DatasetRuntime.open(project, refs["session"])
    if kind == "engine":
        runtime.target = LocalTarget()
    elif kind == "object":
        assert access is not None
        runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    before = snapshot(runtime)
    if mode == "produce":
        current = _metric(sources, current=True).execute()
        baseline = _metric(sources, current=False).execute()
        result = current.compare(baseline).execute()
        assert result.findings().items
        result_refs = {
            "session": runtime.session_ref,
            "current": current.state.artifact_ref.ref,
            "baseline": baseline.state.artifact_ref.ref,
            "delta": result.state.artifact_ref.ref,
        }
        if kind == "engine":
            with duckdb.connect(str(database)) as connection:
                connection.execute("DROP TABLE orders")
        else:
            database.rename(project / "warehouse.offline")
        extra: dict[str, object] = {}
    else:
        recovered = runtime.artifact(refs["delta"])
        assert isinstance(recovered, MaterializedDeltaDataset)
        result = recovered
        selected = result.where(gt(result.fields.get("delta"), 0))
        continuation = selected.rank(selected.fields.get("delta")).limit(10)
        with ExitStack() as guards:
            if mode == "cold":
                for name in (
                    "place",
                    "compile_dataset",
                    "_build_backend_from_effective",
                    "execute_local",
                ):
                    guards.enter_context(patch.object(admission, name, _forbidden))
            continued = continuation.execute()
            if mode == "cold":
                assert continued.state.artifact_ref.ref == refs["continued"]
                retained_current = runtime.artifact(refs["current"])
                retained_baseline = runtime.artifact(refs["baseline"])
                assert isinstance(retained_current, MaterializedMetricDataset)
                assert isinstance(retained_baseline, MaterializedMetricDataset)
                assert (
                    retained_current.compare(retained_baseline).execute().state.artifact_ref.ref
                    == refs["delta"]
                )
                assert snapshot(runtime) == before
        result_refs = {**refs, "continued": continued.state.artifact_ref.ref}
        extra = {"continued_rows": _rows(continued), "continuation_statistics": statistics(runtime)}
    producing_run = runtime.store.run(result.state.producing_run_ref)
    assert producing_run is not None
    assert producing_run.input_artifact_refs == (result_refs["current"], result_refs["baseline"])
    return {
        "schema": "marivo.slice5a.delta-journey/v1",
        "mode": mode,
        "kind": kind,
        "pid": os.getpid(),
        "refs": result_refs,
        "before": before,
        "after": snapshot(runtime),
        "rows": _rows(result),
        "columns": [column.name for column in result.schema.columns],
        "row_contract": _row_contract_fingerprint(result.row_contract),
        "row_set_contract": _row_set_contract_fingerprint(result.row_set_contract),
        "findings": [encode_finding_body(item) for item in result.findings().items],
        "input_artifact_refs": producing_run.input_artifact_refs,
        "statistics": statistics(runtime),
        "versions": versions(),
        **extra,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "engine", "object"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    arguments = parser.parse_args()
    references: dict[str, str] = json.loads(arguments.refs)
    print(
        json.dumps(
            run(arguments.mode, arguments.kind, arguments.project, references),
            sort_keys=True,
            allow_nan=False,
        )
    )
