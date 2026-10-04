"""Public non-time domain/type/key qualifications with offline fixed kernels."""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.deviation_execution import _decode, load
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.analysis.methods.physical import DecimalType, NoTime, ScalarType
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.deviation_r82_oracle import PROFILES, decimal_finish, expected, forbidden


def verify(
    result: mv.MaterializedDeviationResult,
    index: int,
    method: DeviationMethod,
    domain: str,
    key_profile: str,
    route: str,
) -> None:
    assert result._dataset is not None
    checked = result._dataset.verified()
    inputs, state = _decode(checked.parts)
    original, views = load(inputs.primary), load(state.views)
    ordering = [(key, "ascending") for key in inputs.keys]
    if ordering:
        original, views = original.sort_by(ordering), views.sort_by(ordering)
    scale = int(PROFILES[index].split(",")[1][:-1]) if index > 1 else 0
    raw = (
        tuple(Decimal((0, (digit,), -scale)) for digit in (1, 2, 7))
        if index > 1
        else (0.1, 0.2, 0.7)
        if index == 1
        else (1, 2, 7)
    )
    wanted = (raw[-1],) if domain == "scalar" else raw
    assert original["value"].to_pylist() == list(wanted)
    assert original["cell_tag"].to_pylist() == ["defined"] * len(wanted)
    assert len(state.partitions) == 1 and state.partitions[0].fit.n == len(wanted)
    if domain == "entity":
        assert original[inputs.keys[0]].to_pylist() == (
            [1, 2, 3] if key_profile == "KI" else ["a", "b", "c"]
        )
        if key_profile == "KC":
            assert len(inputs.keys) == 2
            assert original[inputs.keys[1]].to_pylist() == [11, 12, 13]
    elif domain == "category":
        assert original[inputs.keys[0]].to_pylist() == ["a", "b", "c"]
    center, _, _, scores = expected(wanted, method)
    assert views["score__value"].to_pylist() == (list(scores) if len(wanted) > 1 else [None])
    if len(wanted) == 1:
        assert views["score__cell_reason"].to_pylist() == ["insufficient_samples"]
    ref_type = views.schema.field("reference__value").type
    reference = (
        decimal_finish(center, ref_type.scale)
        if isinstance(ref_type, pa.Decimal128Type)
        else float(center)
    )
    assert views["reference__value"].to_pylist() == [reference] * len(wanted)
    assert views["deviation__value"].to_pylist() == [
        decimal_finish(Fraction(x) - center, ref_type.scale)
        if isinstance(ref_type, pa.Decimal128Type)
        else float(Fraction(x) - center)
        for x in wanted
    ]
    typ = (
        DecimalType(int(PROFILES[index].split("(")[1].split(",")[0]), scale)
        if index > 1
        else ScalarType("int64" if index == 0 else "float64")
    )
    actual = next(
        p.key
        for p in descriptor_plan(
            result._dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if p.key.method.name == f"deviation.{method}"
    )
    assert (
        actual.input_types == (typ,)
        and actual.route == route
        and isinstance(actual.shape.time, NoTime)
    )
    assert actual.input_domains == (
        ("singleton",)
        if domain == "scalar"
        else ("group",)
        if domain == "category"
        else ("entity",)
    )


def run(root: Path, phase: str, domain: str, key_profile: str) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    path = root / "r82-matrix.json"
    if phase == "produce":
        ms.load(workspace_dir=root)
        session = mv.session.get_or_create("r82-domains", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.order"))
        entries = []
        for index in range(len(PROFILES)):
            raw = members.read(ms.ref.measure(f"sales.order.profile_{index}"))
            category = members.read(ms.ref.dimension("sales.order.channel"))
            assert isinstance(category, mv.LogicalCategoryRelation)
            source = (
                raw.summarize(mv.max())
                if domain == "scalar"
                else raw.group_by(category).summarize(mv.max())
                if domain == "category"
                else raw
            )
            original = source.execute()
            for method in ("zscore", "mad"):
                result = source.deviation(method=method).execute()
                verify(result, index, method, domain, key_profile, "ibis_python")
                entries.append(
                    {
                        "index": index,
                        "method": method,
                        "input": original.evidence_digest().artifact_ref.ref,
                        "result": result.evidence_digest().artifact_ref.ref,
                    }
                )
        path.write_text(json.dumps({"session": session.id, "entries": entries}))
    else:
        manifest = json.loads(path.read_text())
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "batches", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest["session"], by="id")
            for entry in manifest["entries"]:
                captured_input, recovered = (
                    session.artifact(entry["input"]),
                    session.artifact(entry["result"]),
                )
                assert isinstance(
                    captured_input,
                    (mv.MaterializedNumericRelation, mv.MaterializedStatisticRelation),
                )
                original = captured_input
                assert isinstance(recovered, mv.MaterializedDeviationResult)
                verify(
                    recovered, entry["index"], entry["method"], domain, key_profile, "ibis_python"
                )
                fixed = original.deviation(method=entry["method"]).execute()
                verify(
                    fixed, entry["index"], entry["method"], domain, key_profile, "artifact_python"
                )
                if phase == "fixed":
                    entry["fixed"] = fixed.evidence_digest().artifact_ref.ref
                else:
                    assert fixed.evidence_digest().artifact_ref.ref == entry["fixed"]
                    cold_input = original.rank(order="ascending", ties="dense").values.execute()
                    cold_logical = cold_input.deviation(method=entry["method"])
                    cold = cold_logical.execute()
                    verify(
                        cold,
                        entry["index"],
                        entry["method"],
                        domain,
                        key_profile,
                        "artifact_python",
                    )
                    entry["cold"] = cold.evidence_digest().artifact_ref.ref
                    assert entry["cold"] != entry["fixed"]
                    runs = session.runs().items
                    assert (
                        cold_logical.execute().evidence_digest().artifact_ref.ref == entry["cold"]
                    )
                    assert session.runs().items == runs
            path.write_text(json.dumps(manifest))
    print(
        json.dumps(
            {
                "accepted": phase,
                "domain": domain,
                "key_profile": key_profile,
                "profiles": len(PROFILES),
            }
        )
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
