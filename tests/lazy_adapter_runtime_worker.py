"""Fresh-interpreter adapter journeys; stdout is bounded credential-free evidence."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

from marivo._compat import Never
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_local_fixtures import REVENUE
from tests.lazy_runtime_patch_targets import runtime_patch_owner


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("forbidden origin or placement work")


def snapshot(runtime: DatasetRuntime) -> dict[str, int]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        return {
            name: int(db.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in (
                "analysis_action_runs",
                "analysis_action_run_terminals",
                "dataset_artifacts",
                "dataset_evidence",
                "analysis_action_run_inputs",
                "action_resource_journal",
            )
        }


def run(mode: str, kind: str, project: Path, session: str, artifact: str) -> dict[str, object]:
    assert kind == "engine"
    result: MaterializedDataset
    if mode == "produce":
        fixture = setup_adapter(project, "engine")
        runtime, sources = fixture.runtime, fixture.sources
        result = sources.population(ref.entity("sales.customers")).execute()
        with duckdb.connect(str(fixture.database)) as db:
            db.execute("DROP TABLE customers")
        before = {}
    else:
        runtime = DatasetRuntime.open(project, session)
        retained = runtime.artifact(artifact)
        before = snapshot(runtime)
        registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        assert isinstance(retained, MaterializedPopulationDataset)
        logical = sources.observe(REVENUE, population=retained)
        with contextlib.ExitStack() as guards:
            if mode == "cold":
                guards.enter_context(patch.object(runtime_patch_owner("place"), "place", forbidden))
            result = logical.execute()
    after = snapshot(runtime)
    rows: list[list[object]] = []
    shown = ""
    if mode != "cold":
        frame = result.to_pandas()
        for index in range(len(frame)):
            rows.append(
                [
                    list(frame["entity_identity"].iloc[index]),
                    None
                    if "revenue" not in frame or frame["revenue"].isna().iloc[index]
                    else float(frame["revenue"].iloc[index]),
                ]
            )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result.show()
        shown = output.getvalue()
    stats = runtime.statistics
    return {
        "pid": os.getpid(),
        "session": runtime.session_ref,
        "artifact": result.state.artifact_ref.ref,
        "rows": rows,
        "show": shown,
        "before": before,
        "after": after,
        "statistics": {
            "primary_queries": stats.primary_queries,
            "transferred_rows": stats.transferred_rows,
            "local_executions": stats.events.get("local_execution_started", 0),
            "handoffs": stats.local_handoffs,
            "events": stats.events,
            "statements": stats.statements,
        },
        "versions": {"duckdb": duckdb.__version__, "ibis": ibis.__version__},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("engine",))
    parser.add_argument("project", type=Path)
    parser.add_argument("--session", default="")
    parser.add_argument("--artifact", default="")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.kind, args.project, args.session, args.artifact), allow_nan=False
        )
    )
