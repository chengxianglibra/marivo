"""Opt-in, serial source/fixed benchmark using the shared native DSL fixture."""

from __future__ import annotations

import json
import os
import re
import resource
import tracemalloc
from collections.abc import Callable, Iterator
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import ParamSpec, TypeVar

import duckdb
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_source_execution
from marivo.datasource.adapters import SourceBatchStream, SourceSession
from tests.shared_fixtures import DslCaseFactory
from tests.support.execution_logs import execution_records

P = ParamSpec("P")
R = TypeVar("R")


@pytest.mark.runtime
@pytest.mark.parametrize("cardinality", [2, 1000])
def test_coordinate_state_cost(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    cardinality: int,
) -> None:
    destination = os.environ.get("MARIVO_COORD_BENCH_OUTPUT")
    if destination is None:
        pytest.skip("Set MARIVO_COORD_BENCH_OUTPUT for this opt-in cost measurement")
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("SET threads=1")
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" SELECT CAST(i AS VARCHAR), '
            "['A','B','C'][1+i%3], 'category-' || CAST((i//3)%? AS VARCHAR), "
            "'paid', TIMESTAMPTZ '2026-08-01 00:00:00+00', 1+i%17 FROM range(20000) r(i)",
            [cardinality],
        )
    costs: dict[str, float] = {}

    def measured(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
        def decorate(function: Callable[P, R]) -> Callable[P, R]:
            @wraps(function)
            def call(*args: P.args, **kwargs: P.kwargs) -> R:
                start = perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    costs[name] = costs.get(name, 0.0) + perf_counter() - start

            return call

        return decorate

    original_iterate = SourceBatchStream._iterate

    def iterate(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        active = original_iterate(stream)
        while True:
            start = perf_counter()
            try:
                batch = next(active)
            except StopIteration:
                costs["driver_fetch_arrow_seconds"] = (
                    costs.get("driver_fetch_arrow_seconds", 0.0) + perf_counter() - start
                )
                return
            costs["driver_fetch_arrow_seconds"] = (
                costs.get("driver_fetch_arrow_seconds", 0.0) + perf_counter() - start
            )
            yield batch

    monkeypatch.setattr(
        SourceSession, "compile", measured("compile_seconds")(SourceSession.compile)
    )
    monkeypatch.setattr(SourceSession, "batches", measured("submit_seconds")(SourceSession.batches))
    monkeypatch.setattr(SourceBatchStream, "_iterate", iterate)
    monkeypatch.setattr(
        graph_source_execution,
        "_result",
        measured("exchange_validate_seconds")(graph_source_execution._result),
    )
    if hasattr(graph_source_execution, "_split_transport"):
        monkeypatch.setattr(
            graph_source_execution,
            "_split_transport",
            measured("exchange_split_seconds")(graph_source_execution._split_transport),
        )
    n = case.names
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(
            ms.ref.entity(f"{n.domain}.{n.customer}"),
            ms.ref.dimension(f"{n.domain}.{n.order}.{n.channel}"),
        ),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    phases: list[dict[str, object]] = []

    def capture(
        name: str,
        operation: Callable[
            [],
            mv.MaterializedNumericRelation
            | mv.MaterializedRolledNumericRelation
            | mv.MaterializedGroupedNumericRelation,
        ],
    ) -> (
        mv.MaterializedNumericRelation
        | mv.MaterializedRolledNumericRelation
        | mv.MaterializedGroupedNumericRelation
    ):
        offset = len(execution_records(case.root))
        costs.clear()
        tracemalloc.start()
        start = perf_counter()
        result = operation()
        seconds = perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        records = execution_records(case.root)[offset:]
        queries = [record for record in records if record.get("event") == "query.submitted"]
        completed = [record for record in records if record.get("event") == "query.completed"]
        plans: list[str] = []
        analyzed: list[str] = []
        database_seconds: list[float] = []
        with duckdb.connect(str(case.database_path), read_only=True) as connection:
            connection.execute("SET threads=1")
            for query in queries:
                if query.get("purpose") == "analysis.graph.stage":
                    sql = query["sql"]
                    assert isinstance(sql, str)
                    plan = connection.execute("EXPLAIN (FORMAT JSON) " + sql).fetchone()
                    assert plan is not None
                    plans.append(str(plan[1]))
                    profile = connection.execute("EXPLAIN ANALYZE " + sql).fetchone()
                    assert profile is not None
                    analyzed.append(str(profile[1]))
                    timing = re.search(r"Total Time: ([0-9.]+)s", str(profile[1]))
                    assert timing is not None
                    database_seconds.append(float(timing.group(1)))
        assert len(plans) == (0 if name == "fixed_rollup" else 1)
        phases.append(
            {
                "phase": name,
                "wall_seconds": seconds,
                **costs,
                "python_peak_bytes": peak,
                "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "query_count": len(queries),
                "result_query_count": len(plans),
                "queries": completed,
                "plans": plans,
                "analyzed_plans": analyzed,
                "diagnostic_database_seconds": database_seconds,
                "diagnostic_query_count": len(plans) * 2,
                "rows": result.to_pandas().to_dict(orient="records"),
            }
        )
        return result

    saved = capture("source_observe", observed.execute)
    assert isinstance(saved, mv.MaterializedNumericRelation)
    capture("source_rollup", observed.rollup().execute)
    capture("fixed_rollup", saved.rollup().execute)
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    label = os.environ.get("MARIVO_COORD_BENCH_LABEL", "candidate")
    repeat = os.environ.get("MARIVO_COORD_BENCH_REPEAT", "1")
    (output / f"{label}-{cardinality}-{repeat}.json").write_text(
        json.dumps(
            {
                "label": label,
                "cardinality_per_subject": cardinality,
                "input_rows": 20000,
                "workers": 1,
                "database_threads": 1,
                "tracemalloc": True,
                "phase_cost_boundary": "submit includes database execution; fetch includes driver and Arrow conversion; exchange excludes publication; query duration also includes consumer time",
                "phases": phases,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
