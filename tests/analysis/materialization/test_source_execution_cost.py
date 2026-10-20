"""Repeatable source, current-row and explicit-key boundary measurements."""

from __future__ import annotations

import json
import os
import platform
import resource
import sys
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session
from tests.analysis.materialization.test_source_original_recompute import (
    original_session as original_session,
)
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.slow


def _measure(
    frame: mv.AnalysisFrame, session: Session, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    with monkeypatch.context() as scoped:
        trace = capture_source(scoped, measure_cost=True)
        runs = len(session.runs().items)
        started = perf_counter()
        completed = frame.compute()
        evaluated = perf_counter()
        rows = completed.to_pandas()
        assert len(session.runs().items) == runs
        saved = completed.persist()
        published = perf_counter()
        submitted = len(trace.native_sql)
        reopened = session.persisted_frame(saved.artifact.id).to_pandas()
        pd.testing.assert_frame_equal(reopened, rows, check_exact=True)
        assert len(trace.native_sql) == submitted
        assert len(session.runs().items) == runs + 1
        assert all(owner._closed for owner in trace.owners)
        assert session._runtime.store.resources(session.id) == ()
        return {
            "compute_seconds": evaluated - started,
            "export_and_publish_seconds": published - evaluated,
            "reopen_seconds": perf_counter() - published,
            "source_seconds": trace.source_seconds,
            "native_sql": trace.native_sql,
            "business_sql_count": len(trace.native_sql),
            "sql_utf8_bytes": [len(sql.encode()) for sql in trace.native_sql],
            "qualifications": trace.qualifications,
            "source_rows": trace.returned_rows,
            "source_column_counts": [len(columns) for columns in trace.returned_columns],
            "source_pandas_deep_bytes": trace.returned_pandas_bytes,
            "source_arrow_bytes": trace.returned_arrow_bytes,
            "uploaded_key_rows": trace.uploaded_key_rows,
            "uploaded_key_arrow_bytes": trace.uploaded_key_bytes,
            "process_lifetime_peak_rss_bytes": int(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * (1 if sys.platform == "darwin" else 1024)
            ),
            "output_rows": len(rows),
            "values": rows["value", "metric"].tolist(),
            "closed_sources_and_reopened_frame": True,
        }


def _save(
    name: str, session: Session, database: Path, facts: int, measurements: list[dict[str, object]]
) -> None:
    sql = measurements[0]["native_sql"]
    assert isinstance(sql, list) and all(isinstance(item, str) for item in sql)
    with duckdb.connect(str(database)) as db:
        plans = [
            db.execute("EXPLAIN ANALYZE " + item).fetchall()[0][1]
            for item in sql
            if "_marivo_keys_" not in item
        ]
    destination = os.environ.get("MARIVO_SOURCE_COST_DIR")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}-{facts}.json").write_text(
            json.dumps(
                {
                    "scenario": name,
                    "facts": facts,
                    "repetitions_per_route": 3,
                    "environment": {
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "duckdb": duckdb.__version__,
                        "ibis": ibis.__version__,
                        "pandas": pd.__version__,
                    },
                    "explain_analyze": plans,
                    "explain_scope": "Only source SQL independent of invocation-local key relations can be replayed after cleanup.",
                    "measurements": measurements,
                    "boundary": "Actual explicit local source and Frame evaluations followed by publication without replay. EXPLAIN is a separate diagnostic read. Byte counts are in-memory, RSS is process lifetime peak; no latency or provider-wide qualification claim.",
                },
                indent=2,
            )
            + "\n"
        )


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
    database = tmp_path / "original.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("DELETE FROM orders")
        db.execute(f"INSERT INTO orders SELECT i,i%{groups},i%2,i%7,i%3+1 FROM range({facts}) t(i)")
    amount = session.catalog.fields.get("sales.orders.amount")
    bucket = session.catalog.fields.get("sales.orders.bucket")
    channel = session.catalog.fields.get("sales.orders.channel")
    if scenario == "numeric_top":
        parent = session.read(
            session.members(session.catalog.require(ms.ref.entity("sales.orders"))),
            {"metric": amount},
        )
        eligible = parent.where(parent["metric"] >= 5)
        query = eligible.top(1, by=eligible["metric"])
        expected: int | float = 6
    elif scenario == "mean_rollup":
        query = session.observe({"metric": amount.mean()})
        expected = sum(index % 7 for index in range(facts)) / facts
    else:
        producer = session.observe({"metric": amount.mean()}, by=(bucket,))
        fixed = producer.top(1, by=producer["metric"]).compute()
        selected = session.population_from(fixed, on=((fixed.axes[0], bucket),))
        query = session.observe(
            {"metric": amount.mean()}, by=(bucket, channel), where=mv.in_population(selected)
        )
        expected = max(
            sum(index % 7 for index in range(group, facts, groups))
            / len(range(group, facts, groups))
            for group in range(groups)
        )
    measurements = [_measure(query, session, monkeypatch) for _ in range(3)]
    for measurement in measurements:
        assert measurement["values"] == [expected]
        assert measurement["output_rows"] == 1
        assert measurement["uploaded_key_rows"] == ([1] if scenario == "selected_drill" else [])
        assert measurement["source_rows"] == ([facts] if scenario == "numeric_top" else [1])
        if scenario == "mean_rollup":
            assert measurement["source_rows"] == [1]
    _save(scenario, session, database, facts, measurements)


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
    database = tmp_path / "population.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("DELETE FROM orders")
        db.execute("DELETE FROM links")
        db.execute(f"INSERT INTO orders SELECT i,i%10,1 FROM range({facts}) t(i)")
        db.execute(f"INSERT INTO links SELECT i,i,i%2+1 FROM range({facts}) t(i)")
        db.execute(f"INSERT INTO links SELECT i+{facts},i,i%2+1 FROM range({facts}) t(i)")
    orders = session.members(session.catalog.require(ms.ref.entity("sales.orders")))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    if scenario == "image_count":
        query = session.observe(
            {
                "metric": session.catalog.fields.get("sales.tags.id")
                .count()
                .where(mv.in_population(orders.image(via=path)))
            }
        )
        expected = 2
    else:
        fields = session.read(
            session.members(session.catalog.require(ms.ref.entity("sales.tags"))),
            {"label": session.catalog.fields.get("sales.tags.label")},
        )
        fixed = fields.where(fields["label"] == "A").compute()
        targets = session.population_from(
            fixed, on=((fixed.axes[0], session.catalog.entities.get("sales.tags")),)
        )
        query = session.observe(
            {"metric": session.catalog.metrics.get("sales.revenue")},
            where=mv.in_population(orders.preimage(targets, via=path)),
        )
        expected = facts // 2
    measurements = [_measure(query, session, monkeypatch) for _ in range(3)]
    for measurement in measurements:
        assert measurement["values"] == [expected]
        assert measurement["uploaded_key_rows"] == ([1] if scenario == "query_preimage" else [])
        assert measurement["source_rows"] == (
            [1, facts, facts // 2] if scenario == "query_preimage" else [1]
        )
    _save(scenario, session, database, facts, measurements)
