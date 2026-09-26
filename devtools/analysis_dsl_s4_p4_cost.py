"""Measure the public Analysis DSL path on 1,000/100,000 DuckDB facts.

Run with ``.venv/bin/python devtools/analysis_dsl_s4_p4_cost.py --all``.
Each size uses a fresh process, governed project and public Session. The source
adapter is wrapped only to count Ibis reads; execution remains through the
public ``Session -> observe -> correlate -> execute`` path.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import duckdb
import ibis.expr.types as ir
import pyarrow as pa
from analysis_dsl_s4_p3.fixture import prepare

import marivo.analysis as mv
import marivo.analysis.materialization.dsl_public_source as public_source
import marivo.semantic as ms
from marivo.analysis.materialization.dsl_j1_artifact import J1Node
from marivo.analysis.materialization.source_stage import J1IbisBackend


class Counters:
    def __init__(self) -> None:
        self.queries = 0
        self.batches = 0
        self.rows = 0
        self.bytes = 0
        self.largest_batch_rows = 0
        self.closed_readers = 0


class CountingReader:
    def __init__(self, native: pa.RecordBatchReader, counters: Counters) -> None:
        self.native = native
        self.schema = native.schema
        self.counters = counters
        self.closed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        for batch in self.native:
            self.counters.batches += 1
            self.counters.rows += batch.num_rows
            self.counters.bytes += batch.nbytes
            self.counters.largest_batch_rows = max(self.counters.largest_batch_rows, batch.num_rows)
            yield batch

    def close(self) -> None:
        if not self.closed:
            self.counters.closed_readers += 1
            self.closed = True
        self.native.close()


class CountingBackend:
    name = "duckdb"

    def __init__(self, native: J1IbisBackend, counters: Counters) -> None:
        self.native = native
        self.counters = counters

    def compile(self, expression: ir.Table) -> str:
        return self.native.compile(expression)

    def to_pyarrow_batches(self, expression: ir.Table, *, chunk_size: int) -> CountingReader:
        self.counters.queries += 1
        return CountingReader(
            self.native.to_pyarrow_batches(expression, chunk_size=chunk_size), self.counters
        )


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def build_project(root: Path, facts: int) -> tuple[mv.Session, Path]:
    prepare(root, "j4")
    database = root / "warehouse.duckdb"
    members = facts // 4
    connection = duckdb.connect(str(database))
    try:
        connection.execute("SET threads = 1")
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
        connection.execute(
            "INSERT INTO customer SELECT 'C' || i::VARCHAR, 'east' FROM range(?) AS customers(i)",
            [members],
        )
        connection.execute(
            'INSERT INTO "order" '
            "SELECT 'O' || i::VARCHAR, "
            "'C' || (CASE WHEN i < ? THEN i % ? ELSE i % ? END)::VARCHAR, "
            "'web', 'paid', TIMESTAMPTZ '2026-08-15 00:00:00+00', "
            "(i % 13 + 1)::DOUBLE FROM range(?) AS orders(i)",
            [facts // 2, members // 2, members, facts],
        )
    finally:
        connection.close()

    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    ms.load(workspace_dir=root)
    return mv.session.get_or_create("s4-p4-cost", report_timezone="UTC"), database


def counter_snapshot(counters: Counters) -> dict[str, int]:
    return {
        "source_queries": counters.queries,
        "source_batches": counters.batches,
        "source_rows": counters.rows,
        "source_bytes": counters.bytes,
        "largest_batch_rows": counters.largest_batch_rows,
        "closed_readers": counters.closed_readers,
    }


def run_one(facts: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="marivo-s4-p4-cost-") as directory:
        root = Path(directory) / "project"
        session, _ = build_project(root, facts)
        counters = Counters()
        original_source = public_source.public_j1_source

        @contextmanager
        def counted_source(
            node: J1Node, project_root: str
        ) -> Iterator[tuple[CountingBackend, Mapping[str, ir.Table]]]:
            with original_source(node, project_root) as (backend, tables):
                yield CountingBackend(backend, counters), tables

        public_source.public_j1_source = counted_source
        customers = session.members(ms.ref.entity("sales.customer"))
        buyer = ms.ref.relationship("sales.order_buyer")
        august = mv.time_scope(start="2026-08-01", end="2026-09-01")
        revenue = customers.observe(ms.ref.metric("sales.revenue"), during=august, via=buyer)
        count = customers.observe(ms.ref.metric("sales.order_count"), during=august, via=buyer)
        association = revenue.correlate(count, method="spearman")

        source_start = time.perf_counter()
        result = association.execute()
        source_seconds = time.perf_counter() - source_start
        source_counters = counter_snapshot(counters)

        fixed_start = time.perf_counter()
        selected = result.coefficient.where(result.coefficient.value.lt(0)).execute()
        fixed_seconds = time.perf_counter() - fixed_start
        all_counters = counter_snapshot(counters)

        output = result.to_pandas()
        selection = selected.to_pandas()
        return {
            "facts": facts,
            "members": facts // 4,
            "entry": "public Session.members/observe/correlate/execute",
            "association_status": str(output.iloc[0]["status"]),
            "coefficient": float(output.iloc[0]["coefficient"]),
            "selected_rows": len(selection),
            "public_source_seconds": round(source_seconds, 3),
            "public_fixed_continuation_seconds": round(fixed_seconds, 3),
            "source_counters": source_counters,
            "all_counters": all_counters,
            "peak_rss_bytes": peak_rss_bytes(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--facts", type=int, choices=(1000, 100000))
    args = parser.parse_args()
    if args.all:
        for facts in (1000, 100000):
            completed = subprocess.run(
                [sys.executable, __file__, "--facts", str(facts)],
                check=True,
                capture_output=True,
                text=True,
            )
            print(completed.stdout.strip(), flush=True)
        return
    if args.facts is None:
        parser.error("choose --all or --facts")
    print(json.dumps(run_one(args.facts), sort_keys=True))


if __name__ == "__main__":
    main()
