"""Independent production, source-offline inspection and exact cold binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.domains.lifecycle import MaterializedLifecycleDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import descriptor_payload
from marivo.analysis.materialization.lifecycle_codec import evidence_payload
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import EngineTarget, LocalTarget
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_worker import assert_identity_private
from tests.lazy_lifecycle_fixtures import history, lifecycle_registry, setup_lifecycle


def run(mode: str, kind: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    result: MaterializedDataset
    if mode == "produce":
        runtime, sources, _ = setup_lifecycle(project, engine=kind == "engine")
        result = history(sources).execute()
        refs = {"session": runtime.session_ref, "history": result.state.artifact_ref.ref}
        database.unlink()
        before = None
    else:
        runtime = DatasetRuntime.open(
            project,
            refs["session"],
            target=EngineTarget("warehouse") if kind == "engine" else LocalTarget(),
        )
        before = snapshot(runtime)
        result = runtime.artifact(refs["history"])
        assert isinstance(result, MaterializedLifecycleDataset)
        if mode == "cold":
            registry, sidecar = lifecycle_registry(database)
            sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
            with (
                patch.object(admission, "place", forbidden),
                patch.object(admission, "compile_dataset", forbidden),
                patch.object(admission, "_build_backend_from_effective", forbidden),
            ):
                reused = history(sources).execute()
            assert reused.state.artifact_ref == result.state.artifact_ref
    assert isinstance(result, MaterializedLifecycleDataset)
    rows = result.to_pandas()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    parts: dict[str, str] = {}
    for part in record.descriptor.retained_parts:
        digest = hashlib.sha256()
        for batch in payload_batches(
            project, part.storage_receipt, policy=ReadPolicy(), audit=True
        ):
            digest.update(batch.serialize().to_pybytes())
        parts[part.role] = digest.hexdigest()
    report = runtime.revalidate(result.state.artifact_ref)
    assert report.storage_authority == "readable"
    assert report.artifact_integrity == report.evidence_integrity == "valid"
    assert_identity_private(runtime)
    assert not database.exists()
    return {
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "descriptor": descriptor_payload(record.descriptor),
        "evidence": evidence_payload(record.descriptor.lifecycle_evidence),
        "primary_sha256": hashlib.sha256(rows.to_json(date_format="iso").encode()).hexdigest(),
        "parts": parts,
        "origin_offline": True,
        "identity_private": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "recover", "cold"))
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
