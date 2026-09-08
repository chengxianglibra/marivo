"""Fresh-process retained folds and shared immutable membership evidence."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from unittest.mock import patch

import duckdb

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.local_worker import supervise
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_local_runtime_worker import _GUARDED_WORKER
from tests.lazy_materialization_crash_worker import record_evidence, statistics, versions
from tests.lazy_retained_fixtures import setup_retained

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
METRICS = (REVENUE, MEAN, ref.metric("sales.weighted_amount"), ref.metric("sales.conversion_rate"))


def run(mode: str, kind: str, project: Path, session: str, artifact: str) -> dict[str, object]:
    if mode == "produce":
        fixture = setup_retained(project, "engine" if kind == "engine" else "local")
        runtime, sources = fixture.runtime, fixture.sources
        population = sources.population(ref.entity("sales.customers"))
        result: MaterializedDataset
        if kind == "engine":
            result = population.sample(engine_sample(target_rows=2, seed=3)).execute()
            members = [
                list(identity) for identity in result.to_pandas()["entity_identity"].tolist()
            ]
            with duckdb.connect(str(fixture.database)) as backend:
                backend.execute("DROP TABLE customers")
        else:
            result = (
                sources.observe(
                    METRICS,
                    population=population,
                    time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
                )
                .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
                .execute()
            )
            members = []
            fixture.database.rename(project / "warehouse.offline")
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        return {
            "pid": os.getpid(),
            "session": runtime.session_ref,
            "artifact": result.state.artifact_ref.ref,
            "members": members,
            "after": snapshot(runtime),
            "evidence": record_evidence(record),
            "statistics": statistics(runtime),
            "versions": versions(),
        }
    runtime = DatasetRuntime.open(project, session)
    checkpoint = runtime.artifact(artifact)
    before = snapshot(runtime)
    if kind == "engine":
        assert isinstance(checkpoint, MaterializedPopulationDataset)
        registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
        runtime.target = EngineTarget(next(iter(registry.datasources)))
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        definitions = [
            sources.observe(metric, population=checkpoint).aggregate() for metric in (REVENUE, MEAN)
        ]
    else:
        assert isinstance(checkpoint, MaterializedMetricDataset)
        selected = checkpoint.where(gt(REVENUE, 0))
        definitions = [
            selected.aggregate().rollup(drop_time=True),
            checkpoint.where(gt(REVENUE, 30)).aggregate().rollup(drop_time=True).metric(MEAN),
        ]
    outputs: list[dict[str, object]] = []
    with ExitStack() as guards:
        if kind == "local":
            for name in (
                "_build_backend_from_effective",
                "_effective_kwargs",
                "require_profile_for_backend_type",
                "compile_dataset",
            ):
                guards.enter_context(patch.object(admission, name, forbidden))
            guards.enter_context(
                patch.object(
                    admission, "supervise", partial(supervise, worker_code=_GUARDED_WORKER)
                )
            )
        if mode == "cold":
            runtime.target = ObjectTarget("unconfigured")
            for name in ("place", "_build_backend_from_effective", "supervise"):
                guards.enter_context(patch.object(admission, name, forbidden))
        for logical in definitions:
            output = logical.execute()
            stats = {
                **statistics(runtime),
                "worker_pid": runtime.statistics.worker_pid,
                "handoffs": runtime.statistics.local_handoffs,
            }
            record = runtime.store.artifact(output.state.artifact_ref.ref)
            assert record is not None
            producing = runtime.store.run(record.producing_run_ref)
            assert producing is not None and producing.input_artifact_refs == (artifact,)
            frame = output.to_pandas()
            outputs.append(
                {
                    "artifact": output.state.artifact_ref.ref,
                    "rows": [
                        [None if value is None else float(value) for value in row]
                        for row in frame.itertuples(index=False, name=None)
                    ],
                    "columns": list(frame.columns),
                    "evidence": record_evidence(record),
                    "statistics": stats,
                }
            )
    return {
        "pid": os.getpid(),
        "session": session,
        "outputs": outputs,
        "before": before,
        "after": snapshot(runtime),
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "engine"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session", default="")
    parser.add_argument("--artifact", default="")
    arguments = parser.parse_args()
    print(
        json.dumps(
            run(
                arguments.mode,
                arguments.kind,
                arguments.project,
                arguments.session,
                arguments.artifact,
            ),
            sort_keys=True,
            allow_nan=False,
        )
    )
