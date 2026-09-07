"""Fresh-process evidence for independent membership and observation windows."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow.parquet as pq

from marivo.analysis import time_scope
from marivo.analysis.datasets.handles import MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import encode_descriptor
from marivo.analysis.observation.contracts import metric_definition, scope_payload
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.refs import ref
from marivo.semantic.ir import SemiAdditive, TimeFoldIR
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics


def _rows(dataset: MaterializedMetricDataset) -> list[dict[str, object]]:
    frame = dataset.to_pandas()
    assert tuple(frame.columns) == ("snapshot_value",) and len(frame) == 1
    value = frame.iloc[0, 0]
    assert isinstance(value, (int, float))
    return [{"snapshot_value": float(value)}]


def produce(project: Path) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads = 1")
        connection.execute("DELETE FROM snapshots")
        connection.execute("""
            INSERT INTO snapshots (id, amount, day) VALUES
              (1,10,DATE '2026-01-31'), (2,20,DATE '2026-01-31'),
              (1,100,DATE '2026-02-28'), (2,200,DATE '2026-02-28'),
              (3,900,DATE '2026-02-28'),
              (1,1000,DATE '2026-03-31'), (2,2000,DATE '2026-03-31'),
              (3,9000,DATE '2026-03-31')
        """)
        expected: dict[str, float] = {}
        for name, predicate in (
            ("january", "s.day >= DATE '2026-01-01' AND s.day < DATE '2026-02-01'"),
            ("february", "s.day >= DATE '2026-02-01' AND s.day < DATE '2026-03-01'"),
            ("unscoped", "TRUE"),
        ):
            row = connection.execute(f"""
                WITH members AS (SELECT id FROM snapshots WHERE day = DATE '2026-01-31'),
                selected AS (
                    SELECT s.* FROM snapshots s JOIN members m ON m.id = s.id
                    WHERE {predicate}
                )
                SELECT sum(amount) FROM selected WHERE day = (SELECT max(day) FROM selected)
            """).fetchone()
            assert row is not None
            expected[name] = float(row[0])
    registry, sidecar = make_execution_registry(database)
    registry = replace(registry, measures=dict(registry.measures), metrics=dict(registry.metrics))
    axis = ref.time_dimension("sales.snapshots.snapshot_at")
    registry.measures["sales.snapshots.amount"] = replace(
        registry.measures["sales.snapshots.amount"],
        additivity=SemiAdditive(axis.path, TimeFoldIR("last")),
    )
    registry.metrics["sales.snapshot_value"] = replace(
        registry.metrics["sales.revenue"],
        semantic_id="sales.snapshot_value",
        name="snapshot_value",
        entities=("sales.snapshots",),
        measure="sales.snapshots.amount",
        aggregation_target="sales.snapshots.amount",
    )
    registry.freeze()
    runtime = DatasetRuntime.create(project, "scope-recovery")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    january = time_scope(start="2026-01-01", end="2026-02-01")
    february = time_scope(start="2026-02-01", end="2026-03-01")
    population = sources.population(ref.entity("sales.snapshots"), time_scope=january)
    explicit = sources.observe(
        ref.metric("sales.snapshot_value"), population=population, time_scope=february
    ).aggregate()
    unscoped = sources.observe(
        ref.metric("sales.snapshot_value"), population=population
    ).aggregate()
    assert metric_definition(explicit).time_scope == february
    assert metric_definition(unscoped).time_scope is None
    assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0
    artifacts: dict[str, object] = {}
    for name, logical in (("february", explicit), ("unscoped", unscoped)):
        materialized = logical.execute()
        assert isinstance(materialized, MaterializedMetricDataset)
        rows = _rows(materialized)
        assert rows == [{"snapshot_value": expected[name]}]
        record = runtime.store.artifact(materialized.state.artifact_ref.ref)
        assert record is not None
        authority = record.descriptor.population_authority
        assert authority.definition_fingerprint == population.definition_fingerprint
        assert authority.membership_scope == scope_payload(january)
        assert authority.version_selection is not None
        assert record.descriptor.definition_fingerprint == logical.definition_fingerprint
        artifacts[name] = {
            "artifact_ref": record.artifact_ref,
            "definition_fingerprint": logical.definition_fingerprint,
            "rows": rows,
            "artifact": record_evidence(record),
            "descriptor": json.loads(encode_descriptor(record.descriptor)),
            "authored_observation_scope": scope_payload(metric_definition(logical).time_scope),
            "execution_statistics": statistics(runtime),
        }
    return {
        "pid": os.getpid(),
        "session_ref": runtime.session_ref,
        "reference_values": expected,
        "artifacts": artifacts,
        "after": snapshot(runtime),
    }


def recover(project: Path, session_ref: str, references: tuple[str, str]) -> dict[str, object]:
    attempts = dict.fromkeys(
        (
            "profile",
            "credentials",
            "backend",
            "compiler",
            "source_factory",
            "source_statement",
            "metadata_parquet",
        ),
        0,
    )

    def forbidden(kind: str) -> Callable[..., None]:
        def fail(*args: object, **kwargs: object) -> None:
            attempts[kind] += 1
            raise AssertionError("Cold scope recovery attempted " + kind)

        return fail

    with ExitStack() as stack:
        for name, category in (
            ("require_profile_for_backend_type", "profile"),
            ("_effective_kwargs", "credentials"),
            ("_build_backend_from_effective", "backend"),
            ("compile_dataset", "compiler"),
            ("read_json_source", "source_statement"),
        ):
            stack.enter_context(patch.object(admission, name, forbidden(category)))
        stack.enter_context(patch.object(DatasetRuntime, "sources", forbidden("source_factory")))
        runtime = DatasetRuntime.open(project, session_ref)
        before = snapshot(runtime)
        artifacts: dict[str, object] = {}
        for name, reference in zip(("february", "unscoped"), references, strict=True):
            with patch.object(pq, "ParquetFile", forbidden("metadata_parquet")):
                handle = runtime.artifact(reference)
                record = runtime.store.artifact(reference)
            assert isinstance(handle, MaterializedMetricDataset)
            assert isinstance(handle._root, MaterializedScanLeafHandle)
            assert record is not None
            artifacts[name] = {
                "artifact_ref": reference,
                "definition_fingerprint": handle.definition_fingerprint,
                "rows": _rows(handle),
                "artifact": record_evidence(record),
                "descriptor": json.loads(encode_descriptor(record.descriptor)),
            }
        assert snapshot(runtime) == before
        assert not any(attempts.values())
        return {
            "pid": os.getpid(),
            "before": before,
            "after": snapshot(runtime),
            "artifacts": artifacts,
            "forbidden_attempts": attempts,
            "statistics": statistics(runtime),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--artifacts", nargs=2)
    args = parser.parse_args()
    if args.mode == "produce":
        result = produce(args.project)
    else:
        assert isinstance(args.session, str)
        assert isinstance(args.artifacts, list) and len(args.artifacts) == 2
        result = recover(args.project, args.session, (args.artifacts[0], args.artifacts[1]))
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
