"""Repeatable 10k/100k source-boundary measurements, separate from latency gates."""

from __future__ import annotations

import json
import os
import platform
import resource
import sys
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import ParamSpec, TypeVar

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredQuery, lower_query
from marivo.analysis.compiler.graph_plan import LocalCompute, plan_query
from marivo.analysis.compiler.original import ComponentLayout
from marivo.analysis.materialization import (
    graph_local_execution,
    graph_source_execution,
    query_components,
    query_key_fusion,
    source_original,
    source_original_dependent,
    terminal_publication,
)
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.materialization.terminal_storage import write_output
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session
from tests.analysis.materialization.test_source_original_recompute import (
    original_session as original_session,
)
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.slow
P = ParamSpec("P")
R = TypeVar("R")


def _unchanged(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    return lowered


def _measure(
    query: mv.Query, session: Session, monkeypatch: pytest.MonkeyPatch, *, optimized: bool
) -> dict[str, object]:
    timings: dict[str, float] = {}
    local_inputs: list[dict[str, object]] = []
    raw_rows: list[int] = []

    def timed(name: str, function: Callable[P, R]) -> Callable[P, R]:
        depth = 0

        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            nonlocal depth
            started = perf_counter()
            depth += 1
            try:
                return function(*args, **kwargs)
            finally:
                depth -= 1
                if depth == 0:
                    timings[name] = timings.get(name, 0.0) + perf_counter() - started

        return run

    with monkeypatch.context() as scoped:
        if not optimized:
            for module, name in (
                (source_original, "fuse_source_original"),
                (source_original_dependent, "fuse_dependent_original"),
                (query_key_fusion, "fuse_query_keys"),
                (query_key_fusion, "fuse_query_terminal"),
            ):
                scoped.setattr(module, name, _unchanged)
        trace = capture_source(scoped, measure_cost=True)
        scoped.setattr(
            graph_source_execution,
            "prepare_query_sources",
            timed("source_preparation", graph_source_execution.prepare_query_sources),
        )
        scoped.setattr(SourceSession, "compile", timed("ibis_sql_compile", SourceSession.compile))
        scoped.setattr(terminal_publication, "plan_query", timed("plan", plan_query))
        scoped.setattr(terminal_publication, "lower_query", timed("lower", lower_query))
        scoped.setattr(
            terminal_publication,
            "_compute",
            timed("execution_including_source", terminal_publication._compute),
        )
        scoped.setattr(
            terminal_publication,
            "write_output",
            timed("terminal_write", write_output),
        )
        run_local = graph_local_execution.run_local
        complete_components = query_components.complete_components

        def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
            local_inputs.append(
                {
                    "operation": type(stage.result.operation).__name__
                    if stage.result
                    else stage.kernel,
                    "rows": [len(item.table) for item in inputs],
                }
            )
            return run_local(stage, inputs)

        def components(
            layout: ComponentLayout, frame: pd.DataFrame
        ) -> tuple[pd.DataFrame, query_components.ComponentFrame]:
            result = complete_components(layout, frame)
            raw_rows.append(len(result[1].table))
            return result

        scoped.setattr(graph_local_execution, "run_local", local)
        scoped.setattr(query_components, "complete_components", components)
        started = perf_counter()
        artifact = query.execute()
        elapsed = perf_counter() - started
        frame = artifact.to_pandas()
        submitted = len(trace.native_sql)
        pd.testing.assert_frame_equal(session.artifact(artifact.id).to_pandas(), frame)
        assert len(trace.native_sql) == submitted
        assert all(owner._closed for owner in trace.owners)
        assert session._runtime.store.resources(session.id) == ()
        maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "optimized": optimized,
            "total_seconds": elapsed,
            "inclusive_phase_seconds": timings,
            "source_seconds": trace.source_seconds,
            "business_sql_count": len(trace.native_sql),
            "native_sql": trace.native_sql,
            "sql_utf8_bytes": [len(sql.encode()) for sql in trace.native_sql],
            "source_rows": trace.returned_rows,
            "source_column_counts": [len(columns) for columns in trace.returned_columns],
            "source_pandas_deep_bytes": trace.returned_pandas_bytes,
            "source_arrow_bytes": trace.returned_arrow_bytes,
            "uploaded_key_rows": trace.uploaded_key_rows,
            "uploaded_key_arrow_bytes": trace.uploaded_key_bytes,
            "raw_component_rows": raw_rows,
            "local_stage_input_rows": local_inputs,
            "process_lifetime_peak_rss_bytes": int(
                maximum_rss * (1 if sys.platform == "darwin" else 1024)
            ),
            "output_rows": len(frame),
            "values": frame[("value", "metric")].tolist(),
            "closed_sources_and_reopened_artifact": True,
        }


@pytest.mark.parametrize("facts", (10_000, 100_000))
@pytest.mark.parametrize("scenario", ("numeric_top", "mean_rollup", "selected_drill"))
def test_source_execution_cost(
    original_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    facts: int,
    scenario: str,
) -> None:
    session = original_session
    groups = facts if scenario == "numeric_top" else 100
    with duckdb.connect(str(tmp_path / "original.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            f"INSERT INTO orders SELECT i, i % {groups}, i % 2, i % 7, i % 3 + 1 FROM range({facts}) t(i)"
        )
    amount = session.catalog.fields.get("sales.orders.amount")
    bucket = session.catalog.fields.get("sales.orders.bucket")
    channel = session.catalog.fields.get("sales.orders.channel")
    if scenario == "numeric_top":
        parent = session.read(session.members(ms.ref.entity("sales.orders")), {"metric": amount})
        eligible = parent.where(parent["metric"] >= 5)
        query = eligible.top(1, by=eligible["metric"])
    else:
        parent = session.observe({"metric": amount.mean()}, by=(bucket,))
        query = (
            parent.rollup()
            if scenario == "mean_rollup"
            else parent.top(1, by=parent["metric"]).drill(add=(channel,))
        )
    measurements = [
        _measure(query, session, monkeypatch, optimized=optimized)
        for _ in range(3)
        for optimized in (False, True)
    ]
    totals = [sum(index % 7 for index in range(group, facts, groups)) for group in range(groups)]
    counts = [len(range(group, facts, groups)) for group in range(groups)]
    expected = (
        6
        if scenario == "numeric_top"
        else sum(totals) / facts
        if scenario == "mean_rollup"
        else max(total / count for total, count in zip(totals, counts, strict=True))
    )
    baseline_values = [expected]
    for measurement in measurements:
        assert measurement["values"] == baseline_values
        if measurement["optimized"]:
            assert measurement["source_rows"] == [1]
            assert measurement["uploaded_key_rows"] == []
            assert measurement["raw_component_rows"] == []
        else:
            source_rows = measurement["source_rows"]
            assert isinstance(source_rows, list)
            assert sum(source_rows) >= facts
    optimized_sql = measurements[1]["native_sql"]
    assert isinstance(optimized_sql, list)
    with duckdb.connect(str(tmp_path / "original.duckdb")) as connection:
        plans = [
            connection.execute("EXPLAIN ANALYZE " + sql).fetchall()[0][1] for sql in optimized_sql
        ]
    for measurement in measurements:
        del measurement["native_sql"]
    destination = os.environ.get("MARIVO_SOURCE_COST_DIR")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{scenario}-{facts}.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "environment": {
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "duckdb": duckdb.__version__,
                        "ibis": ibis.__version__,
                        "pandas": pd.__version__,
                    },
                    "optimized_explain_analyze": plans,
                    "facts": facts,
                    "groups": groups,
                    "repetitions_per_route": 3,
                    "independent_expected_values": baseline_values,
                    "boundary": "Instrumented local DuckDB executions. Byte counts are in-memory representations, not wire bytes. Phase times overlap. RSS is the process lifetime maximum, including fixture setup and earlier repeats. EXPLAIN ANALYZE is a separate diagnostic read after timed executions; inspect its operators for actual scans. No latency speedup is inferred.",
                    "measurements": measurements,
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("facts", (10_000, 100_000))
@pytest.mark.parametrize("scenario", ("image_count", "query_preimage"))
def test_population_source_execution_cost(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    facts: int,
    scenario: str,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("DELETE FROM links")
        connection.execute(f"INSERT INTO orders SELECT i,i%10,1 FROM range({facts}) t(i)")
        connection.execute(f"INSERT INTO links SELECT i,i,i%2+1 FROM range({facts}) t(i)")
        connection.execute(f"INSERT INTO links SELECT i+{facts},i,i%2+1 FROM range({facts}) t(i)")
    orders = session.members(ms.ref.entity("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    if scenario == "image_count":
        image = orders.image(via=path)
        query = session.observe(
            {
                "metric": session.catalog.fields.get("sales.tags.id")
                .count()
                .where(mv.in_population(image))
            }
        )
        expected = 2
    else:
        tags = session.members(ms.ref.entity("sales.tags"))
        fields = session.read(tags, {"label": session.catalog.fields.get("sales.tags.label")})
        targets = fields.where(fields["label"] == "A").population()
        selected = orders.preimage(targets, via=path)
        query = session.observe(
            {"metric": session.catalog.metrics.get("sales.revenue")}, members=selected
        )
        expected = facts // 2
    measurements = [
        _measure(query, session, monkeypatch, optimized=optimized)
        for _ in range(3)
        for optimized in (False, True)
    ]
    for measurement in measurements:
        assert measurement["values"] == [expected]
        if measurement["optimized"]:
            assert measurement["source_rows"] == [1]
            assert measurement["uploaded_key_rows"] == []
            assert measurement["raw_component_rows"] == []
        else:
            rows = measurement["source_rows"]
            assert isinstance(rows, list) and sum(rows) >= facts
    optimized_sql = measurements[1]["native_sql"]
    assert isinstance(optimized_sql, list)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        plans = [
            connection.execute("EXPLAIN ANALYZE " + sql).fetchall()[0][1] for sql in optimized_sql
        ]
    for measurement in measurements:
        del measurement["native_sql"]
    destination = os.environ.get("MARIVO_SOURCE_COST_DIR")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{scenario}-{facts}.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "environment": {
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "duckdb": duckdb.__version__,
                        "ibis": ibis.__version__,
                        "pandas": pd.__version__,
                    },
                    "facts": facts,
                    "relationship_rows": 2 * facts,
                    "target_identities": 3,
                    "repetitions_per_route": 3,
                    "independent_expected_values": [expected],
                    "optimized_explain_analyze": plans,
                    "boundary": "Instrumented local DuckDB. Phase times overlap; RSS is the lifetime maximum. Sizes are memory representations, not wire bytes. EXPLAIN runs separately after measurement. No latency or physical scan count claim.",
                    "measurements": measurements,
                },
                indent=2,
            )
            + "\n"
        )
