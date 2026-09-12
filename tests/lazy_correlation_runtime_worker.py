"""Three independent processes proving Association recovery without source replay."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.refs import ref
from tests.lazy_distinct_runtime_worker import rows
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold binding reuse attempted executable origin or worker access")


def run(
    mode: str, method: CorrelationMethod, kind: str, project: Path, refs: dict[str, str]
) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        seed_execution_database(database)
        registry, sidecar = make_execution_registry(database)
        runtime = DatasetRuntime.create(project, "correlation-journey", target=LocalTarget())
        source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        metric = source.observe(
            [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
        ).execute()
        database.rename(project / "origin.offline")
        return {
            "pid": os.getpid(),
            "refs": {"session": runtime.session_ref, "metric": metric.state.artifact_ref.ref},
            "after": snapshot(runtime),
            "origin_removed": True,
        }
    runtime = DatasetRuntime.open(
        project,
        refs["session"],
        target=LocalTarget(),
    )
    if kind == "object":
        access = S3Access(
            "fixture",
            os.environ["MARIVO_TEST_S3_ENDPOINT"],
            os.environ["MARIVO_TEST_S3_BUCKET"],
            "minioadmin",
            "minioadmin",
        )
        runtime.target = ObjectTarget("fixture")
        runtime.object_bindings = (access,)
    before = snapshot(runtime)
    recovered = runtime.artifact(refs["metric"])
    assert isinstance(recovered, MaterializedMetricDataset)
    metric = recovered
    logical = metric.correlate(method=method)
    with ExitStack() as guards:
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective", "supervise"):
                guards.enter_context(patch.object(admission, name, forbidden))
        result = logical.execute()
        selected = result.rank(result.fields.get("coefficient")).limit(1).execute()
    assert result.evidence_digest.finding_count == 1
    refs = {
        **refs,
        "association": result.state.artifact_ref.ref,
        "selected": selected.state.artifact_ref.ref,
    }
    record = runtime.store.artifact(refs["association"])
    assert record is not None
    if mode == "cold":
        assert snapshot(runtime) == before
        assert not runtime.statistics.statements
    return {
        "pid": os.getpid(),
        "refs": refs,
        "rows": rows(result),
        "selected_rows": rows(selected),
        "artifact": record_evidence(record),
        "findings": [encode_finding_body(f) for f in result.findings().items],
        "before": before,
        "after": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("method", choices=("pearson", "spearman", "kendall"))
    parser.add_argument("kind", choices=("local", "engine", "object"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.method, args.kind, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
