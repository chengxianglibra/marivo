"""Archive executed source F11 chains separately from persisted fit recovery proofs."""

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
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.rules import (
    CellDerive,
    DeviationFit,
    DeviationRead,
    MapCorrespond,
    ObserveMetric,
    PartsTransport,
    PreparedObservation,
    RowState,
)
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.methods.physical import QualificationKey, Qualified, SourceShape
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r81_static_freeze import array_json, object_json, read_json
from scripts.r82_deviation_evidence import forbidden


@dataclass(frozen=True)
class SourceChainProof:
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
    contribution_type: str
    followup_reduction: str
    summary_reduction: str
    followup_artifact: str
    summary_artifact: str
    producing_run_ref: str
    execution_key: str
    followup_receipt_digest: str
    summary_receipt_digest: str
    fit_scope: str
    input_digest: str
    consumed_fit_part_digests: tuple[tuple[str, str], ...]
    source_read_count: int
    local_count: int
    last_source_read: int
    first_local_consume: int
    manifest_digest: str


CHAINS = TypeAdapter(tuple[SourceChainProof, ...])


def _text(value: object) -> str:
    assert isinstance(value, str) and value
    return value


def _integer(value: object) -> int:
    assert type(value) is int and isinstance(value, int) and value >= 0
    return value


def _capture_fixture(path: Path) -> tuple[SourceChainProof, ...]:
    records: list[SourceChainProof] = []
    manifest = read_json(path)
    entries = array_json(manifest["entries"])
    assert len(entries) == 16, "a source chain fixture must finish all eight profiles and methods"
    profile = read_json(path.parent / "r82-profile.json")
    assert profile["followup"] is True
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
        session = mv.session.resume(_text(manifest["session"]), by="id")
        for value in entries:
            entry = object_json(value)
            followup = session.artifact(_text(entry["followup_artifact"]))
            summary = session.artifact(_text(entry["summary_artifact"]))
            assert isinstance(followup, mv.MaterializedNumericRelation)
            assert isinstance(summary, mv.MaterializedStatisticRelation)
            assert followup._dataset is not None and summary._dataset is not None
            checked = followup._dataset.verified()
            terminal = summary._dataset.verified()
            assert terminal.primary.num_rows == 1
            assert terminal.primary["value"][0].as_py() == checked.primary.num_rows
            assert all(tag == "defined" for tag in checked.primary["cell_tag"].to_pylist())
            assert any(p.role == "original_state" for p in checked.parts)
            descriptor = summary._dataset.artifact.descriptor
            plan = descriptor_plan(descriptor, summary._node.definition)
            selected = next(
                p
                for p in plan.physical_requirements
                if p.key.method.name == "deviation." + _text(entry["method"])
            )
            nodes = tuple(n for n in topology(plan.root) if isinstance(n, MethodNode))
            assert (
                sum(
                    isinstance(n.parameters, DeviationFit)
                    and n.parameters.method == entry["method"]
                    for n in nodes
                )
                == 1
            )
            fitted_node = next(n for n in nodes if isinstance(n.parameters, DeviationFit))
            assert any(
                isinstance(n, MethodNode)
                and isinstance(n.parameters, CellDerive)
                and n.parameters.method == "difference"
                for n in topology(fitted_node.inputs[0].node)
            )
            assert any(
                isinstance(n.parameters, PartsTransport) and n.parameters.predicates for n in nodes
            )
            assert any(
                isinstance(n.parameters, DeviationRead) and n.parameters.field == "observed"
                for n in nodes
            )
            assert any(
                (isinstance(n.parameters, MapCorrespond) and n.parameters.mode == "subjects")
                or (
                    isinstance(n.parameters, PartsTransport)
                    and n.parameters.mode == "projection"
                    and n.parameters.retained_roles == ("subject",)
                    and not n.parameters.keep_quantity
                )
                for n in nodes
            )
            assert any(
                isinstance(n.parameters, PreparedObservation)
                and isinstance(n.parameters.observation, ObserveMetric)
                and n.parameters.observation.method == "sum"
                for n in nodes
            )
            assert any(
                isinstance(n.parameters, RowState) and n.parameters.method == "count_defined"
                for n in nodes
            )
            assert isinstance(selected.key.shape, SourceShape)
            qualification = selected.implementation.qualification
            assert isinstance(qualification, Qualified)
            from scripts.r82_deviation_requirements import key_json

            assert key_json(selected.key) == object_json(entry["actual"])
            fit = object_json(entry["fit"])
            assert selected.key.input_types[0].name == fit["input_type"]
            reads, local, last, first = (
                _integer(entry["source_read_count"]),
                _integer(entry["local_count"]),
                _integer(entry["last_source_read"]),
                _integer(entry["first_local_consume"]),
            )
            assert reads > 0 and local == 1 and last < first
            part_digests = tuple(
                (role, _text(digest)) for role, digest in object_json(fit["parts"]).items()
            )
            assert all(len(digest) == 64 for _, digest in part_digests)
            grid = followup._node.root.signature.domain.time_grid
            records.append(
                SourceChainProof(
                    selected.key,
                    qualification.implementation_id,
                    selected.implementation.contract_version,
                    selected.implementation.precision,
                    "v1",
                    "r8_numeric_v1",
                    "r8.fit_inputs/v1",
                    "r8.fit_state/v1",
                    "select_output_retain_scope@v1",
                    _text(entry["domain"]),
                    _text(entry["key_profile"]),
                    "none"
                    if grid is None
                    else ("certified_unequal" if grid.snapshot_digest else "builtin_day")
                    + ":grid_us:"
                    + grid.report_timezone,
                    _text(entry["contribution_type"]),
                    "sum",
                    "count_defined",
                    followup._dataset.artifact.artifact_ref,
                    summary._dataset.artifact.artifact_ref,
                    summary._dataset.artifact.producing_run_ref,
                    descriptor.execution_key_digest,
                    receipt_digest(followup._dataset.artifact.descriptor.primary_receipt),
                    receipt_digest(descriptor.primary_receipt),
                    _text(fit["fit_scope"]),
                    _text(fit["input_digest"]),
                    part_digests,
                    reads,
                    local,
                    last,
                    first,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(records)


def capture(roots: tuple[Path, ...], output: Path) -> None:
    paths = sorted({p.resolve() for root in roots for p in root.rglob("r82-f11.json")})
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        records = tuple(record for batch in pool.map(_capture_fixture, paths) for record in batch)
    assert records, "no complete source F11 manifests"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(CHAINS.dump_json(tuple(records)))
    print(
        json.dumps(
            {
                "source_chains": len(records),
                "complete_manifests": len(paths),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture(tuple(args.roots), args.output)
