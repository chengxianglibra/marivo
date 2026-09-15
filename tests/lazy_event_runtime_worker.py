"""Fresh-process retained membership, journey publication and offline binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import duckdb

from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.event_codec import evidence_payload
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import OCCURRENCE_CANARY, journey, setup_event
from tests.lazy_materialization_crash_worker import record_evidence, statistics, versions


def assert_identity_private(
    runtime: DatasetRuntime, canaries: tuple[str, ...] = (str(OCCURRENCE_CANARY),)
) -> None:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        for name in (*snapshot(runtime), "findings"):
            rows = db.execute(f"SELECT * FROM {name}").fetchall()
            assert all(canary not in repr(rows) for canary in canaries)
    assert all(canary not in repr(runtime.statistics.statements) for canary in canaries)
    assert all(
        canary not in str(path)
        for path in runtime.store.layout.session_dir(runtime.session_ref).rglob("*")
        for canary in canaries
    )


def run(mode: str, kind: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    target = LocalTarget()
    if mode == "produce":
        runtime, sources, database = setup_event(project, engine=True)
        metric = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        )
        selected = metric.where(gt(metric.fields.metric(ref.metric("sales.revenue")), 50)).execute()
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE orders")
        return {
            "pid": os.getpid(),
            "refs": {"session": runtime.session_ref, "membership": selected.state.artifact_ref.ref},
            "after": snapshot(runtime),
            "versions": versions(),
            "membership_origin_removed": True,
        }
    runtime = DatasetRuntime.open(project, refs["session"], target=target)
    before = snapshot(runtime)
    membership = runtime.artifact(refs["membership"])
    assert isinstance(membership, MaterializedMetricDataset)
    registry, sidecar = make_event_registry(database)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = journey(sources, population=membership)
    if mode == "cold":
        recovered = runtime.artifact(refs["journey"])
        assert isinstance(recovered, MaterializedEventDataset)
        with (
            patch.object(admission, "place", forbidden),
            patch.object(admission, "compile_dataset", forbidden),
            patch.object(admission, "_build_backend_from_effective", forbidden),
        ):
            result = logical.execute()
        assert result.state.artifact_ref == recovered.state.artifact_ref
        assert snapshot(runtime) == before
    else:
        with (
            patch.object(admission, "execute_local", forbidden),
            patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
        ):
            result = logical.execute()
        refs = {**refs, "journey": result.state.artifact_ref.ref}
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(2,), (2,)]
    assert frame.completion_status.tolist() == ["incomplete", "incomplete"]
    assert frame.event_identity.iloc[0] == (OCCURRENCE_CANARY + 2,)
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert record.evidence.finding_count == 0 and record.descriptor.retained_parts == ()
    assert runtime.statistics.local_handoffs == ()
    assert_identity_private(runtime)
    return {
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "terminal_sha256": hashlib.sha256(frame.to_json(date_format="iso").encode()).hexdigest(),
        "artifact": record_evidence(record),
        "event_evidence": evidence_payload(record.descriptor.event_evidence),
        "statistics": statistics(runtime),
        "versions": versions(),
        "occurrence_origins_removed": True,
        "identity_privacy_verified": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "engine"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.kind, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
