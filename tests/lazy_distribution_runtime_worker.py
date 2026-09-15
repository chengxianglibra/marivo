"""Fresh-process distribution preparation, source-closed continuation and exact reuse."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.descriptors import (
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.refs import ref
from marivo.semantic._quantile import QuantileMethod, quantile_metric
from tests.lazy_distinct_runtime_worker import frame_rows, rows
from tests.lazy_distribution_fixtures import (
    CHANNEL,
    METRIC,
    REGION,
    guard_distribution_transport,
    make_distribution_registry,
    seed_distribution_database,
)
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold exact reuse attempted source or worker execution")


def run(
    mode: str, kind: str, method: QuantileMethod, project: Path, refs: dict[str, str]
) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        seed_distribution_database(database)
        with duckdb.connect(str(database), config={"threads": 1}) as con:
            con.execute(
                "insert into orders select * replace(id+100 as id, amount*2 as amount, day+interval '1 day' as day) from orders"
            )
        registry, sidecar = make_distribution_registry(database, q=0.7)
        runtime = DatasetRuntime.create(project, "distribution-journey", target=LocalTarget())
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        current = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-02-02", end="2026-02-04"),
            )
            .with_dimensions(REGION, CHANNEL)
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        baseline = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-01-02", end="2026-01-04"),
            )
            .with_dimensions(REGION, CHANNEL)
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )
        delta = current.compare(baseline).execute()
        record = runtime.store.artifact(delta.state.artifact_ref.ref)
        assert record is not None
        database.rename(project / "origin.offline")
        return {
            "pid": os.getpid(),
            "refs": {"session": runtime.session_ref, "delta": delta.state.artifact_ref.ref},
            "after": snapshot(runtime),
            "artifact": record_evidence(record),
            "origin_removed": True,
        }
    runtime = DatasetRuntime.open(project, refs["session"], target=LocalTarget())
    if kind == "object":
        access = S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            "minioadmin",
            "minioadmin",
        )
        runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    before = snapshot(runtime)
    recovered_delta = runtime.artifact(refs["delta"])
    assert isinstance(recovered_delta, MaterializedDeltaDataset)
    delta = recovered_delta
    logical = delta.attribute(axes=(REGION, CHANNEL), mode="hierarchy", top_k=1)
    with ExitStack() as guards:
        guards.enter_context(guard_distribution_transport())
        if mode == "cold":
            for name in (
                "place",
                "compile_dataset",
                "_build_backend_from_effective",
                "execute_local",
            ):
                guards.enter_context(patch.object(admission, name, forbidden))
        result = logical.execute()
        selected = result.rank(result.fields.get("contribution")).limit(1).execute()
    assert isinstance(result, MaterializedAttributionDataset)
    assert result.findings().items and selected.findings().items == ()
    frame = result.to_pandas()
    for (ordinal, _), group in frame.groupby(["comparison_ordinal", "active_axis_mask"]):
        assert abs(float(group.contribution.sum()) - float(group.overall_delta.iloc[0])) < 1e-9
        assert ordinal in (0, 1)
    refs = {
        **refs,
        "attribution": result.state.artifact_ref.ref,
        "selected": selected.state.artifact_ref.ref,
    }
    record = runtime.store.artifact(refs["attribution"])
    assert record is not None
    if mode == "cold":
        assert before == snapshot(runtime)
        assert not runtime.statistics.statements
    return {
        "pid": os.getpid(),
        "refs": refs,
        "rows": frame_rows(frame),
        "selected_rows": rows(selected),
        "row_contract": _row_contract_fingerprint(result.row_contract),
        "row_set_contract": _row_set_contract_fingerprint(result.row_set_contract),
        "findings": [encode_finding_body(item) for item in result.findings().items],
        "artifact": record_evidence(record),
        "evidence_digest": result.evidence_digest.evidence_digest,
        "before": before,
        "after": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "object"))
    parser.add_argument("method", choices=("linear_interpolation@v1", "duckdb_tdigest@v1"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.kind, args.method, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
