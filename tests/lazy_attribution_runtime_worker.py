"""Fresh-process retained attribution with the original datasource unavailable."""

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
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget, S3Access
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot, statistics, versions

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")
DAY = ref.time_dimension("sales.orders.order_time")


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


def _rows(dataset: MaterializedDataset) -> list[list[object]]:
    return [
        [_cell(cell) for cell in row]
        for row in dataset.to_pandas().itertuples(index=False, name=None)
    ]


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Exact retained binding attempted new compilation or execution")


def _seed(database: Path) -> None:
    seed_execution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("UPDATE customers SET region = 'Other' WHERE id = 3")
        rows = []
        identity = 1
        for day, values in (
            ("2026-02-02", (10.0, 30.0, 50.0, 0.0)),
            ("2026-02-03", (20.0, 10.0, 15.0, 5.0)),
            ("2026-02-04", (5.0, 10.0, 20.0, 5.0)),
            ("2026-02-05", (10.0, 5.0, 5.0, 0.0)),
        ):
            for customer, channel, amount in zip(
                (1, 1, 2, 3), ("web", "app", None, "web"), values, strict=True
            ):
                rows.append((identity, customer, amount, 1.0, day, channel))
                identity += 1
        connection.executemany(
            "INSERT INTO orders (id,customer_id,amount,weight,day,channel) VALUES (?,?,?,?,?,?)",
            rows,
        )


def run(
    mode: str, kind: str, method: str, project: Path, refs: dict[str, str]
) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        _seed(database)
    registry, sidecar = make_execution_registry(database)
    runtime = (
        DatasetRuntime.create(project, "attribution-journey")
        if mode == "produce"
        else DatasetRuntime.open(project, refs["session"])
    )
    if kind == "engine":
        runtime.target = EngineTarget(next(iter(registry.datasources)))
    if kind == "object":
        access = S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            "minioadmin",
            "minioadmin",
        )
        runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    before = snapshot(runtime)
    if mode == "produce":
        metric = ref.metric("sales.revenue" if method == "additive" else "sales.mean_amount")
        current = (
            sources.observe(metric, time_scope=time_scope(start="2026-02-02", end="2026-02-04"))
            .with_dimensions(REGION, CHANNEL)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        baseline = (
            sources.observe(metric, time_scope=time_scope(start="2026-02-04", end="2026-02-06"))
            .with_dimensions(REGION, CHANNEL)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        delta = current.compare(baseline).execute()
        refs = {
            "session": runtime.session_ref,
            "delta": delta.state.artifact_ref.ref,
        }
        if kind == "engine":
            with duckdb.connect(str(database), config={"threads": 1}) as connection:
                connection.execute("DROP TABLE orders")
                connection.execute("DROP TABLE customers")
        else:
            database.rename(project / "warehouse.offline")
        return {
            "pid": os.getpid(),
            "refs": refs,
            "after": snapshot(runtime),
            "versions": versions(),
        }

    recovered = runtime.artifact(refs["delta"])
    assert isinstance(recovered, MaterializedDeltaDataset)
    delta = recovered
    logical = delta.attribute(axes=[REGION, CHANNEL], mode="hierarchy", top_k=1)
    with ExitStack() as guards:
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective", "supervise"):
                guards.enter_context(patch.object(admission, name, _forbidden))
        result = logical.execute()
        assert isinstance(result, MaterializedAttributionDataset)
        selected = result.where(eq(result.fields.get("active_axis_mask"), (True, True)))
        continuation = selected.rank(selected.fields.get("contribution")).limit(100)
        continued = continuation.execute()
        assert continued.findings().items == ()
        if mode == "cold":
            assert result.state.artifact_ref.ref == refs["attribution"]
            assert continued.state.artifact_ref.ref == refs["continued"]
            assert snapshot(runtime) == before
    refs = {
        **refs,
        "attribution": result.state.artifact_ref.ref,
        "continued": continued.state.artifact_ref.ref,
    }
    frame = result.to_pandas()
    expected = (50.0, 30.0) if method == "additive" else (12.5, 7.5)
    for ordinal, expected_delta in enumerate(expected):
        group = frame[frame["comparison_ordinal"] == ordinal]
        assert not group.empty
        for _, resolution in group.groupby("active_axis_mask"):
            assert abs(float(resolution["contribution"].sum()) - expected_delta) < 1e-9
            assert all(
                abs(float(value) - expected_delta) < 1e-9 for value in resolution["overall_delta"]
            )
    assert result.findings().items
    run_record = runtime.store.run(result.state.producing_run_ref)
    assert run_record is not None and run_record.input_artifact_refs == (refs["delta"],)
    return {
        "schema": "marivo.slice5b.retained-journey/v1",
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "rows": [[_cell(cell) for cell in row] for row in frame.itertuples(index=False, name=None)],
        "continued_rows": _rows(continued),
        "row_contract": _row_contract_fingerprint(result.row_contract),
        "row_set_contract": _row_set_contract_fingerprint(result.row_set_contract),
        "findings": [encode_finding_body(item) for item in result.findings().items],
        "input_artifact_refs": run_record.input_artifact_refs,
        "statistics": statistics(runtime),
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "engine", "object"))
    parser.add_argument("method", choices=("additive", "component"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    references: dict[str, str] = json.loads(args.refs)
    print(
        json.dumps(
            run(args.mode, args.kind, args.method, args.project, references),
            sort_keys=True,
            allow_nan=False,
        )
    )
