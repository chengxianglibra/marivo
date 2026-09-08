"""Fresh-process producer and source-free recovery probes for Slice 2b."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.descriptors import (
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
from marivo.analysis.datasets.handles import MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    canonical_json,
    receipt_payload,
)
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import ObservationRuntimeOwner
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

CRASH_EXIT = 73
CRASH_POINTS = ("output_reserved", "after_rename", "insert_artifact", "after_commit")
_RELATIONS = (
    "sessions",
    "runtime_state",
    "analysis_action_runs",
    "analysis_action_run_inputs",
    "analysis_action_run_terminals",
    "dataset_artifacts",
    "dataset_evidence",
    "findings",
    "action_resource_journal",
)


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False), flush=True)


def _cell(value: object) -> None | str | int:
    if value is None or isinstance(value, (str, int)):
        return value
    raise AssertionError("Unexpected metadata cell type")


def snapshot(runtime: DatasetRuntime) -> dict[str, object]:
    """Use an independent SQLite reader, including inside a publishing transaction."""
    connection = sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        tables: dict[str, object] = {}
        for relation in _RELATIONS:
            cursor = connection.execute(f"SELECT * FROM {relation}")
            assert cursor.description is not None
            names = tuple(column[0] for column in cursor.description)
            records = [
                {name: _cell(row[index]) for index, name in enumerate(names)}
                for row in cursor.fetchall()
            ]
            records.sort(key=canonical_json)
            tables[relation] = records
        return {
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": tables,
            "counts": {name: len(rows) for name, rows in tables.items() if isinstance(rows, list)},
        }
    finally:
        connection.close()


def statistics(runtime: DatasetRuntime) -> dict[str, object]:
    stats = runtime.statistics
    return {
        "primary_queries": stats.primary_queries,
        "validation_queries": stats.validation_queries,
        "primary_stages": sum(kind == "primary" for kind, _shape in stats.statements),
        "source_fences": stats.source_fences,
        "transferred_rows": stats.transferred_rows,
        "transferred_bytes": stats.transferred_bytes,
        "events": dict(sorted(stats.events.items())),
        "statements": stats.statements,
        "statement_inventory_scope": "Runtime logical statements; adapter-internal metadata wire calls are not counted.",
    }


def versions() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        **{name: version(name) for name in ("duckdb", "ibis-framework", "pyarrow", "pandas")},
    }


def record_evidence(record: ArtifactRecord) -> dict[str, object]:
    descriptor = record.descriptor
    return {
        "artifact_ref": record.artifact_ref,
        "session_ref": record.session_ref,
        "producing_run_ref": record.producing_run_ref,
        "execution_key": record.execution_key_digest,
        "committed_at": record.committed_at,
        "definition_fingerprint": descriptor.definition_fingerprint,
        "family": descriptor.row_contract.shape_id.family_id,
        "shape": str(descriptor.row_contract.shape_id),
        "row_contract_fingerprint": descriptor.row_contract_fingerprint,
        "row_set_contract_fingerprint": descriptor.row_set_contract_fingerprint,
        "realized_schema_fingerprint": descriptor.realized_schema_fingerprint,
        "primary_receipt": receipt_payload(descriptor.storage_receipt),
        "retained_parts": [
            {
                "role": part.role,
                "contract_id": part.contract_id,
                "contract_version": part.contract_version,
                "receipt": receipt_payload(part.storage_receipt),
            }
            for part in descriptor.retained_parts
        ],
        "evidence": {
            "digest": record.evidence.evidence_digest,
            "finding_count": record.evidence.finding_count,
            "finding_set_digest": record.evidence.finding_set_digest,
            "extractor_versions": record.evidence.extractor_contract_versions,
            "quality_digest": record.evidence.quality_summary_digest,
        },
        "quality": descriptor.quality_summary.model_dump(mode="json"),
    }


def _source_files(project: Path) -> dict[str, str]:
    return {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(project.rglob("*"))
        if path.is_file() and path.suffix in (".parquet", ".json")
    }


def produce(project: Path, point: str) -> None:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    journey: dict[str, object] = {}
    observed: list[str] = []

    def crash(event: str) -> None:
        observed.append(event)
        if event != point:
            return
        run_ref = runtime.last_run_ref
        assert run_ref is not None
        run = runtime.store.run(run_ref)
        assert run is not None
        committed = runtime.store.lookup(runtime.session_ref, run.execution_key_digest)
        _emit(
            {
                "phase": "crash",
                "point": point,
                "pid": os.getpid(),
                "session_ref": runtime.session_ref,
                "run_ref": run_ref,
                "execution_key": run.execution_key_digest,
                "journey": journey,
                "statistics": statistics(runtime),
                "event_order": observed,
                "store": snapshot(runtime),
                "committed_artifact": None if committed is None else record_evidence(committed),
                "payload_files": _source_files(project),
            }
        )
        os._exit(CRASH_EXIT)

    runtime = DatasetRuntime.create(project, "slice-2b-crash", event=crash)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = sources.population(ref.entity("sales.orders"))
    logical = (
        sources.observe(
            ref.metric("sales.revenue"),
            population=population,
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        )
        .with_dimensions(ref.dimension("sales.customers.region"))
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )
    journey.update(
        {
            "family": logical.kind,
            "shape": str(logical.row_contract.shape_id),
            "definition_fingerprint": logical.definition_fingerprint,
            "row_contract_fingerprint": _row_contract_fingerprint(logical.row_contract),
            "row_set_contract_fingerprint": _row_set_contract_fingerprint(logical.row_set_contract),
            "columns": [field.name for field in logical.schema.columns],
            "population_fingerprint": population.definition_fingerprint,
        }
    )
    _emit(
        {
            "phase": "constructed",
            "point": point,
            "pid": os.getpid(),
            "session_ref": runtime.session_ref,
            "versions": versions(),
            "journey": journey,
            "statistics": statistics(runtime),
            "store": snapshot(runtime),
        }
    )
    logical.execute()
    raise AssertionError("The real producing action did not reach its assigned crash point")


def _row_value(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, bool, int, float)):
        return value
    raise AssertionError("Unexpected terminal fixture cell type")


def recover(project: Path, session_ref: str, run_ref: str) -> None:
    attempts = {"profile": 0, "credential": 0, "backend": 0, "compiler": 0, "source_factory": 0}

    def forbidden(kind: str) -> Callable[..., None]:
        def fail(*_args: object, **_kwargs: object) -> None:
            attempts[kind] += 1
            raise AssertionError("Cold recovery attempted " + kind)

        return fail

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(admission, "require_profile_for_backend_type", forbidden("profile"))
        )
        stack.enter_context(patch.object(admission, "_effective_kwargs", forbidden("credential")))
        stack.enter_context(
            patch.object(admission, "_build_backend_from_effective", forbidden("backend"))
        )
        stack.enter_context(patch.object(admission, "compile_dataset", forbidden("compiler")))
        stack.enter_context(patch.object(DatasetRuntime, "sources", forbidden("source_factory")))
        runtime = DatasetRuntime.open(project, session_ref)
        before = snapshot(runtime)
        payload_before = _source_files(project)
        prior = runtime.store.run(run_ref)
        assert prior is not None
        with session_writer_guard(runtime.store.layout.lock_path(session_ref)):
            reconcile_session(runtime.store, session_ref, event=runtime._event)
        reconciled = snapshot(runtime)
        run = runtime.store.run(run_ref)
        assert run is not None
        result: dict[str, object] = {
            "phase": "recovered",
            "pid": os.getpid(),
            "session_ref": session_ref,
            "run_ref": run_ref,
            "execution_key": run.execution_key_digest,
            "before": before,
            "after": reconciled,
            "lifecycle": run.lifecycle,
            "failure_kind": None if run.failure is None else run.failure.kind,
            "artifact_ref": run.output_artifact_ref,
            "payload_files_before": payload_before,
        }
        if run.lifecycle == "succeeded":
            assert run.output_artifact_ref is not None
            record = runtime.store.lookup(session_ref, run.execution_key_digest)
            assert record is not None
            handle = runtime.artifact(run.output_artifact_ref)
            assert isinstance(handle, MaterializedMetricDataset)
            assert isinstance(handle._root, MaterializedScanLeafHandle)
            assert type(handle._owner) is ObservationRuntimeOwner
            assert not hasattr(handle._owner, "semantic_registry")
            output = io.StringIO()
            with redirect_stdout(output):
                handle.show()
            frame = handle.to_pandas()
            assert frame["revenue"].sum() == 140.0
            assert len(output.getvalue().encode("utf-8")) <= 8192
            assert handle.findings().items == ()
            assert handle.evidence_digest.evidence_digest == record.evidence.evidence_digest
            result["committed_artifact"] = record_evidence(record)
            result["show"] = output.getvalue()
            result["rows"] = [
                {str(name): _row_value(value) for name, value in row.items()}
                for row in frame.to_dict(orient="records")
            ]
            assert snapshot(runtime) == reconciled
            assert _source_files(project) == payload_before
        else:
            assert run.lifecycle == "failed" and run.failure is not None
            assert run.failure.kind == "process_lost"
            assert runtime.store.lookup(session_ref, run.execution_key_digest) is None
            assert _source_files(project) == {}
        with session_writer_guard(runtime.store.layout.lock_path(session_ref)):
            reconcile_session(runtime.store, session_ref, event=runtime._event)
        assert snapshot(runtime) == reconciled
        assert runtime.store.resources(session_ref) == ()
        assert not any(attempts.values())
        result["payload_files_after"] = _source_files(project)
        result["statistics"] = statistics(runtime)
        result["forbidden_attempts"] = attempts
        result["repeated_reconciliation_unchanged"] = True
        _emit(result)


def main() -> None:
    os.environ["MARIVO_TELEMETRY"] = "off"
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--point", choices=CRASH_POINTS)
    parser.add_argument("--session")
    parser.add_argument("--run")
    args = parser.parse_args()
    if args.mode == "produce":
        assert isinstance(args.point, str)
        produce(args.project, args.point)
    else:
        assert isinstance(args.session, str) and isinstance(args.run, str)
        recover(args.project, args.session, args.run)


if __name__ == "__main__":
    main()
