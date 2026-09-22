"""Fresh-process source-algebra production and source-free retained acceptance."""

from __future__ import annotations

import argparse
import io
import json
import os
from collections.abc import Callable
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from marivo.analysis.datasets.handles import MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import encode_descriptor
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import is_in
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def _rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        converted: dict[str, object] = {}
        for key, value in row.items():
            if value is pd.NA or value is None:
                converted[str(key)] = None
            elif isinstance(value, (str, bool, int, float)):
                converted[str(key)] = value
            else:
                raise AssertionError("Unexpected fixture result cell")
        result.append(converted)
    return result


def _statistics(runtime: DatasetRuntime) -> dict[str, object]:
    return statistics(runtime)


def produce(project: Path) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads=1")
        connection.execute(
            "UPDATE customers SET region = CASE WHEN id IN (1,3) THEN 'west' ELSE 'east' END"
        )
        connection.execute("INSERT INTO customers (id,region) VALUES (5,'excluded')")
        expected = connection.execute("""
            WITH eligible AS (SELECT * FROM customers WHERE region IN ('west','east')),
            grouped AS (
                SELECT c.region, sum(o.amount) AS revenue, count(o.amount) AS order_count
                FROM eligible c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.region
            )
            SELECT *, row_number() OVER (ORDER BY revenue DESC NULLS LAST, region ASC) AS rank
            FROM grouped ORDER BY rank, region LIMIT 1
        """).fetchall()
    registry, sidecar = make_execution_registry(database)
    events: list[str] = []
    runtime = DatasetRuntime.create(project, "slice-3a-source", event=events.append)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = sources.population(ref.entity("sales.customers")).where(
        is_in(ref.dimension("sales.customers.region"), ("west", "east"))
    )
    observed = (
        sources.observe(
            [ref.metric("sales.revenue"), ref.metric("sales.order_count")], population=population
        )
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    logical = observed.rank(observed.fields.metric(ref.metric("sales.revenue"))).limit(1)
    before = snapshot(runtime)
    construction = _statistics(runtime)
    assert construction["events"] == {} and construction["primary_queries"] == 0
    materialized = logical.execute()
    assert runtime.statistics.primary_queries == 1
    frame = materialized.to_pandas()
    actual = tuple(frame.itertuples(index=False, name=None))
    assert actual == tuple(expected), (actual, expected)
    output = io.StringIO()
    with redirect_stdout(output):
        materialized.show()
    assert len(output.getvalue().encode("utf-8")) <= 8192
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    assert (
        "dataset.final_row_key_unique",
        0,
    ) in record.descriptor.population_authority.validation_results
    assert runtime.store.resources(runtime.session_ref) == ()
    result = {
        "phase": "produced",
        "pid": os.getpid(),
        "versions": versions(),
        "session_ref": runtime.session_ref,
        "run_ref": runtime.last_run_ref,
        "artifact_ref": record.artifact_ref,
        "before": before,
        "after": snapshot(runtime),
        "construction_statistics": construction,
        "statistics": _statistics(runtime),
        "events": events,
        "artifact": record_evidence(record),
        "descriptor": json.loads(encode_descriptor(record.descriptor)),
        "rows": _rows(frame),
        "reference_rows": [list(row) for row in expected],
        "show": output.getvalue(),
        "row_order": "rank ascending, complete region row key ascending",
    }
    return result


def recover(project: Path, session_ref: str, artifact_ref: str) -> dict[str, object]:
    attempts = dict.fromkeys(
        (
            "profile",
            "credential",
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
            raise AssertionError("Retained recovery attempted " + kind)

        return fail

    with ExitStack() as stack:
        for name, category in (
            ("require_profile_for_backend_type", "profile"),
            ("_effective_kwargs", "credential"),
            ("_build_backend_from_effective", "backend"),
            ("compile_dataset", "compiler"),
            ("read_json_source", "source_statement"),
        ):
            stack.enter_context(patch.object(admission, name, forbidden(category)))
        stack.enter_context(patch.object(DatasetRuntime, "sources", forbidden("source_factory")))
        runtime = DatasetRuntime.open(project, session_ref)
        before = snapshot(runtime)
        with patch.object(pq, "ParquetFile", forbidden("metadata_parquet")):
            handle = runtime.artifact(artifact_ref)
            record = runtime.store.artifact(artifact_ref)
        assert isinstance(handle, MaterializedMetricDataset)
        assert isinstance(handle._root, MaterializedScanLeafHandle)
        assert record is not None
        output = io.StringIO()
        with redirect_stdout(output):
            handle.show()
        frame = handle.to_pandas()
        assert not any(attempts.values())
        assert snapshot(runtime) == before
        return {
            "phase": "recovered",
            "pid": os.getpid(),
            "versions": versions(),
            "session_ref": session_ref,
            "artifact_ref": artifact_ref,
            "before": before,
            "after": snapshot(runtime),
            "statistics": _statistics(runtime),
            "forbidden_attempts": attempts,
            "artifact": record_evidence(record),
            "descriptor": json.loads(encode_descriptor(record.descriptor)),
            "rows": _rows(frame),
            "show": output.getvalue(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--artifact")
    args = parser.parse_args()
    if args.mode == "produce":
        result = produce(args.project)
    else:
        assert isinstance(args.session, str) and isinstance(args.artifact, str)
        result = recover(args.project, args.session, args.artifact)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
