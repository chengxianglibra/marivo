"""Archive verified R8.2 kernel receipts without consulting current Semantic or sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.deviation_execution import _decode
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    descriptor_plan,
    receipt_digest,
)
from marivo.analysis.methods.physical import QualificationKey, Qualified
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r81_static_freeze import array_json, object_json, read_json


@dataclass(frozen=True)
class KernelProof:
    origin: QualificationKey
    actual: QualificationKey
    implementation_id: str
    contract_version: int
    precision_contract: str
    state_version: str
    numeric_policy: str
    input_codec: str
    state_codec: str
    selection_transform: str
    domain: str
    key_profile: str
    time_profile: str
    proof_class: str
    artifact_ref: str
    producing_run_ref: str
    execution_key: str
    descriptor_digest: str
    primary_receipt_digest: str
    part_receipt_digests: tuple[tuple[str, str], ...]
    input_digest: str
    fit_scope_rows: int
    fit_scope_digest: str
    fit_state_digest: str
    current_key_fields: tuple[tuple[str, str], ...]


PROOFS = TypeAdapter(tuple[KernelProof, ...])


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("qualification receipt capture accessed current Semantic or source")


def proof(
    result: mv.MaterializedDeviationResult, origin: QualificationKey, phase: str
) -> KernelProof:
    dataset = result._dataset
    assert dataset is not None
    checked = dataset.verified()
    descriptor = dataset.artifact.descriptor
    selected = next(
        p
        for p in descriptor_plan(descriptor, result._node.definition).physical_requirements
        if p.key.method.name in ("deviation.zscore", "deviation.mad")
    )
    inputs, state = _decode(checked.parts)
    domain = descriptor.signature.domain
    temporal = domain.time_grid is not None
    profile = (
        (
            "KC"
            if len(inputs.keys) - int(temporal) == 2
            else "KI"
            if checked.primary.schema.field(inputs.keys[0]).type == pa.int64()
            else "KS"
        )
        if domain.kind == "entity"
        else "KT"
    )
    digest = result.evidence_digest()
    assert digest.finding_count == 0
    qualification = selected.implementation.qualification
    assert isinstance(qualification, Qualified)
    versions = {partition.fit.version for partition in state.partitions}
    policies = {partition.fit.numeric_policy for partition in state.partitions}
    assert len(versions) == len(policies) == 1
    return KernelProof(
        origin,
        selected.key,
        qualification.implementation_id,
        selected.implementation.contract_version,
        selected.implementation.precision,
        next(iter(versions)),
        next(iter(policies)),
        inputs.version,
        state.version,
        state.transform,
        "entity_time"
        if temporal and domain.kind == "entity"
        else "category_time"
        if temporal and len(domain.instance_key) > 1
        else "time"
        if temporal
        else "scalar"
        if domain.kind == "singleton"
        else "category"
        if domain.kind == "group"
        else "entity",
        profile,
        "none"
        if domain.time_grid is None
        else (
            "certified_unequal"
            if domain.time_grid is not None and domain.time_grid.snapshot_digest is not None
            else "builtin_day"
        )
        + ":grid_us:"
        + domain.time_grid.report_timezone,
        phase,
        dataset.artifact.artifact_ref,
        dataset.artifact.producing_run_ref,
        descriptor.execution_key_digest,
        hashlib.sha256(DESCRIPTOR.dump_json(descriptor)).hexdigest(),
        receipt_digest(descriptor.primary_receipt),
        tuple((part.role, receipt_digest(part)) for part in descriptor.parts),
        state.input_digest,
        inputs.primary.rows,
        inputs.primary.digest,
        state.views.digest,
        descriptor.primary_receipt.key_fields,
    )


def _capture_fixture(path: Path) -> tuple[KernelProof, ...]:
    collected: list[KernelProof] = []
    manifest = read_json(path)
    session_id, entries = manifest["session"], array_json(manifest["entries"])
    assert isinstance(session_id, str)
    assert entries and all(
        "fixed" in object_json(entry) and "cold" in object_json(entry) for entry in entries
    )
    os.environ["MARIVO_PROJECT_ROOT"] = str(path.parent)
    os.environ["MARIVO_TELEMETRY"] = "off"
    with (
        patch.object(SemanticProject, "load", forbidden),
        patch.object(ms, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "batches", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
    ):
        session = mv.session.resume(session_id, by="id")
        for row in entries:
            entry = object_json(row)
            source_ref, fixed_ref, cold_ref = entry["result"], entry["fixed"], entry["cold"]
            assert (
                isinstance(source_ref, str)
                and isinstance(fixed_ref, str)
                and isinstance(cold_ref, str)
            )
            source, fixed, cold = (
                session.artifact(source_ref),
                session.artifact(fixed_ref),
                session.artifact(cold_ref),
            )
            assert isinstance(source, mv.MaterializedDeviationResult)
            assert isinstance(fixed, mv.MaterializedDeviationResult)
            assert isinstance(cold, mv.MaterializedDeviationResult)
            assert source._dataset is not None
            key = next(
                p.key
                for p in descriptor_plan(
                    source._dataset.artifact.descriptor, source._node.definition
                ).physical_requirements
                if p.key.method.name in ("deviation.zscore", "deviation.mad")
            )
            collected.extend(
                (
                    proof(source, key, "source_kernel"),
                    proof(fixed, key, "fixed_kernel"),
                    proof(cold, key, "fresh_process_source_offline_kernel"),
                )
            )
    return tuple(collected)


def capture(roots: tuple[Path, ...], manifest_name: str, output: Path) -> None:
    paths = sorted({path.resolve() for root in roots for path in root.rglob(manifest_name)})
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        collected = tuple(proof for batch in pool.map(_capture_fixture, paths) for proof in batch)
    assert collected, "no complete qualification manifests found"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(PROOFS.dump_json(collected))
    print(
        json.dumps(
            {
                "proofs": len(collected),
                "complete_manifests": len(paths),
                "output": str(output),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", required=True, action="append", type=Path)
    parser.add_argument(
        "--manifest", required=True, choices=("r82-laws.json", "r82-matrix.json", "r82-time.json")
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture(tuple(args.roots), args.manifest, args.output)
