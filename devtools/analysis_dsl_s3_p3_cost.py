"""Reproduce private J4 layout costs on fixed 1,000/100,000-fact sources.

Run with ``PYTHONPATH=. .venv/bin/python devtools/analysis_dsl_s3_p3_cost.py --all``.
Each layout and size runs in a separate process so peak RSS is comparable.
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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb
import ibis
import ibis.expr.types as ir
import pyarrow as pa
from tests.test_analysis_dsl_s3_p1 import _association

import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import publish_j1_artifact
from marivo.analysis.materialization.dsl_j4_source import capture_j4_endpoints
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.dsl_j1 import j1_row_contracts
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    seed_analysis_dsl_database,
)


@dataclass
class Counters:
    queries: int = 0
    batches: int = 0
    transferred_rows: int = 0
    transferred_bytes: int = 0
    largest_batch_rows: int = 0
    closed_readers: int = 0
    endpoint_arrow_bytes: int = 0
    peak_after_arrow_bytes: int = 0
    peak_before_numeric_bytes: int = 0
    peak_after_numeric_bytes: int = 0


class CountingReader:
    def __init__(self, native: pa.RecordBatchReader, counters: Counters) -> None:
        self.native = native
        self.schema = native.schema
        self.counters = counters
        self.closed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        for batch in self.native:
            self.counters.batches += 1
            self.counters.transferred_rows += batch.num_rows
            self.counters.transferred_bytes += batch.nbytes
            self.counters.largest_batch_rows = max(self.counters.largest_batch_rows, batch.num_rows)
            yield batch

    def close(self) -> None:
        if not self.closed:
            self.counters.closed_readers += 1
            self.closed = True
        self.native.close()


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


class CountingBackend:
    name = "duckdb"

    def __init__(self, native: ibis.BaseBackend, counters: Counters) -> None:
        self.native = native
        self.counters = counters

    def compile(self, expression: ir.Table) -> str:
        return self.native.compile(expression)

    def to_pyarrow_batches(self, expression: ir.Table, *, chunk_size: int) -> CountingReader:
        self.counters.queries += 1
        return CountingReader(
            self.native.to_pyarrow_batches(expression, chunk_size=chunk_size), self.counters
        )


def build_case(root: Path, facts: int) -> DslCase:
    database_path = root / "warehouse.duckdb"
    seed_analysis_dsl_database(database_path, DSL_NAMES, analysis_dsl_rows("j4"), float_amount=True)
    members = facts // 4
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute('DELETE FROM "order"')
        connection.execute('DELETE FROM "customer"')
        connection.execute(
            "INSERT INTO \"customer\" SELECT 'C' || i::VARCHAR, 'east' FROM range(?) AS members(i)",
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
    (root / "marivo.toml").write_text('[project]\nname = "analysis-dsl-cost"\n')
    for relative, content in analysis_dsl_project_files(DSL_NAMES, database_path).items():
        destination = (
            root / "models" / relative
            if relative.startswith("datasources/")
            else root / "models" / "semantic" / relative
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    catalog = ms.load(workspace_dir=root)
    session = session_attach.get_or_create("dsl-cost", report_timezone="UTC")
    return DslCase("j4", DSL_NAMES, root, database_path, catalog, session)


@contextmanager
def measured_source(
    case: DslCase, counters: Counters
) -> Iterator[tuple[CountingBackend, dict[str, ir.Table]]]:
    native = ibis.duckdb.connect(str(case.database_path))
    try:
        domain = case.names.domain
        yield (
            CountingBackend(native, counters),
            {
                f"{domain}.customer": native.table("customer"),
                f"{domain}.order": native.table("order"),
            },
        )
    finally:
        native.disconnect()


def capture_fixed(case: DslCase, association: object, counters: Counters) -> tuple[str, str]:
    from marivo.analysis.observation.dsl_j1 import J4Association

    assert isinstance(association, J4Association)
    with measured_source(case, counters) as (backend, tables):
        left, right = capture_j4_endpoints(association, backend, tables)
    counters.endpoint_arrow_bytes = left.primary.nbytes + right.primary.nbytes
    counters.peak_after_arrow_bytes = peak_rss_bytes()
    store = SessionStore.open_existing(case.root)
    refs: list[str] = []
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        for index, (node, result) in enumerate(
            ((association.left, left), (association.right, right))
        ):
            row, _ = j1_row_contracts(association.context, node.root)
            key = f"cost_endpoint_{index}"
            run = store.admit(
                "session",
                key,
                RunDatasetInput(
                    node.root.definition_fingerprint,
                    row.shape_id,
                    node.root.row_contract_fingerprint,
                    node.root.row_set_contract_fingerprint,
                    (node.root.operator_id,),
                    (f"{case.names.domain}.customer", f"{case.names.domain}.order"),
                ),
            )
            saved = publish_j1_artifact(
                store,
                run,
                node,
                result,
                input_binding=key,
                member_binding="cost_one_member_implementation",
            )
            refs.append(saved.artifact_ref)
    return refs[0], refs[1]


def run_one(facts: int, layout: str) -> dict[str, object]:
    import marivo.analysis.materialization.dsl_j4_source as j4_source

    with tempfile.TemporaryDirectory(prefix="marivo-s3-p3-") as directory:
        case = build_case(Path(directory), facts)
        association = _association(case)
        counters = Counters()
        runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
        original_capture = j4_source.capture_j4_endpoints
        original_reduce = j4_source.reduce_entity_spearman

        def observed_capture(*args: object, **kwargs: object):
            endpoints = original_capture(*args, **kwargs)
            counters.endpoint_arrow_bytes = sum(item.primary.nbytes for item in endpoints)
            counters.peak_after_arrow_bytes = peak_rss_bytes()
            return endpoints

        def observed_reduce(*args: object, **kwargs: object):
            counters.peak_before_numeric_bytes = peak_rss_bytes()
            outcome = original_reduce(*args, **kwargs)
            counters.peak_after_numeric_bytes = peak_rss_bytes()
            return outcome

        j4_source.capture_j4_endpoints = observed_capture
        j4_source.reduce_entity_spearman = observed_reduce
        start = time.perf_counter()
        artifact_read_bytes = 0
        input_materialized_bytes = 0
        if layout == "fixed_pandas":
            refs = capture_fixed(case, association, counters)
            store = SessionStore.open_existing(case.root)
            artifact_read_bytes = sum(
                store.artifact(ref).descriptor.storage_receipt.realized_byte_count for ref in refs
            )
            input_materialized_bytes = artifact_read_bytes
            result = runtime.execute_j1(
                association,
                input_nodes=(association.left, association.right),
                input_artifact_refs=refs,
            )
        else:
            route = "source_numeric" if layout == "source_numeric" else "python"
            result = runtime.execute_j1(
                association,
                source=lambda: measured_source(case, counters),
                source_route=route,
            )
        elapsed = time.perf_counter() - start
        coefficient = float(result.to_pandas()["coefficient"].iloc[0])
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        receipt = record.descriptor.storage_receipt
        return {
            "facts": facts,
            "members": facts // 4,
            "layout": layout,
            "coefficient": coefficient,
            "source_queries": counters.queries,
            "source_batches": counters.batches,
            "closed_readers": counters.closed_readers,
            "largest_batch_rows": counters.largest_batch_rows,
            "transferred_rows": counters.transferred_rows,
            "exchange_bytes": counters.transferred_bytes,
            "artifact_read_bytes": artifact_read_bytes,
            "endpoint_arrow_bytes": counters.endpoint_arrow_bytes,
            "peak_after_arrow_bytes": counters.peak_after_arrow_bytes,
            "peak_before_numeric_bytes": counters.peak_before_numeric_bytes,
            "peak_after_numeric_bytes": counters.peak_after_numeric_bytes,
            "conversion_copy_lower_bound_bytes": counters.endpoint_arrow_bytes
            if layout == "fixed_pandas"
            else 0,
            "input_materialized_bytes": input_materialized_bytes,
            "output_materialized_bytes": receipt.realized_byte_count,
            "materialized_bytes": input_materialized_bytes + receipt.realized_byte_count,
            "elapsed_seconds": round(elapsed, 3),
            "peak_rss_bytes": peak_rss_bytes(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--facts", type=int, choices=(1000, 100000))
    parser.add_argument("--layout", choices=("source_numeric", "fixed_pandas", "source_python"))
    args = parser.parse_args()
    if args.all:
        for facts in (1000, 100000):
            for layout in ("source_numeric", "fixed_pandas", "source_python"):
                completed = subprocess.run(
                    [sys.executable, __file__, "--facts", str(facts), "--layout", layout],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                print(completed.stdout.strip(), flush=True)
        return
    if args.facts is None or args.layout is None:
        parser.error("choose --all or both --facts and --layout")
    print(json.dumps(run_one(args.facts, args.layout), sort_keys=True))


if __name__ == "__main__":
    main()
