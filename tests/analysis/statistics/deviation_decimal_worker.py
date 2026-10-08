"""Public Decimal law qualification in independent producer/fixed/cold processes."""

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
from marivo.analysis.methods.physical import DecimalType, NoTime
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.deviation_oracle import decimal_finish, expected, forbidden


def verify(
    result: mv.MaterializedDeviationResult,
    precision: int,
    scale: int,
    method: DeviationMethod,
    route: str,
) -> None:
    assert result._dataset is not None
    checked = result._dataset.verified()
    inputs, state = _decode(checked.parts)
    original = load(inputs.primary).sort_by([(inputs.keys[0], "ascending")])
    views = load(state.views).sort_by([(inputs.keys[0], "ascending")])
    values = tuple(Decimal((0, (digit,), -scale)) for digit in (1, 2, 7))
    assert original[inputs.keys[0]].to_pylist() == ["law_a", "law_b", "law_c", "law_null"]
    assert len(inputs.keys) == 2
    assert original[inputs.keys[1]].to_pylist() == [1, 2, 3, 4]
    assert original.schema.field(inputs.keys[0]).type == pa.string()
    assert original.schema.field(inputs.keys[1]).type == pa.int64()
    assert original["cell_tag"].to_pylist() == ["defined"] * 3 + ["null"]
    assert original["value"].to_pylist() == [*values, None]
    assert original.schema.field("value").type == pa.decimal128(precision, scale)
    center, _, _, scores = expected(values, method)
    assert result.score.to_pandas().sort_values("member").value.iloc[:3].tolist() == list(scores)
    assert views["reference__value"].to_pylist() == [decimal_finish(center, max(scale, 6))] * 4
    assert views["deviation__value"].to_pylist() == [
        *(decimal_finish(Fraction(x) - center, max(scale, 6)) for x in values),
        None,
    ]
    assert views.schema.field("reference__value").type == pa.decimal128(38, max(scale, 6))
    assert sum(len(partition.indices) for partition in state.partitions) == 4
    assert result.evidence_digest().finding_count == 0
    plan = descriptor_plan(result._dataset.artifact.descriptor, result._node.definition)
    actual = next(
        p.key for p in plan.physical_requirements if p.key.method.name == f"deviation.{method}"
    )
    assert actual.input_types == (DecimalType(precision, scale),)
    assert actual.input_domains == ("entity",) and isinstance(actual.shape.time, NoTime)
    assert actual.route == route


def run(root: Path, precision: int, scale: int, phase: str) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    manifest_path = root / "deviation-decimal.json"
    if phase == "produce":
        ms.load(workspace_dir=root)
        session = mv.session.get_or_create("r82-decimal-laws", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.order"))
        entries = []
        source = members.read(ms.ref.measure(f"sales.order.law_{scale}"))
        original = source.execute()
        for method in ("zscore", "mad"):
            result = source.deviation(method=method).execute()
            verify(result, precision, scale, method, "ibis_python")
            entries.append(
                {
                    "scale": scale,
                    "method": method,
                    "input": original.evidence_digest().artifact_ref.ref,
                    "result": result.evidence_digest().artifact_ref.ref,
                }
            )
        manifest_path.write_text(json.dumps({"session": session.id, "entries": entries}))
    else:
        manifest = json.loads(manifest_path.read_text())
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
                captured_input = session.artifact(entry["input"])
                source_result = session.artifact(entry["result"])
                assert isinstance(captured_input, mv.MaterializedNumericRelation)
                original = captured_input
                assert isinstance(source_result, mv.MaterializedDeviationResult)
                verify(source_result, precision, entry["scale"], entry["method"], "ibis_python")
                fixed = original.deviation(method=entry["method"]).execute()
                verify(fixed, precision, entry["scale"], entry["method"], "artifact_python")
                artifact_ref = fixed.evidence_digest().artifact_ref.ref
                if phase == "fixed":
                    entry["fixed"] = artifact_ref
                else:
                    assert artifact_ref == entry["fixed"]
                    cold_input = original.rank(order="ascending", ties="dense").values.execute()
                    cold_logical = cold_input.deviation(method=entry["method"])
                    cold = cold_logical.execute()
                    verify(cold, precision, entry["scale"], entry["method"], "artifact_python")
                    entry["cold"] = cold.evidence_digest().artifact_ref.ref
                    assert entry["cold"] != entry["fixed"]
                    runs = session.runs().items
                    assert (
                        cold_logical.execute().evidence_digest().artifact_ref.ref == entry["cold"]
                    )
                    assert session.runs().items == runs
            manifest_path.write_text(json.dumps(manifest))
    print(
        json.dumps(
            {
                "accepted": phase,
                "precision": precision,
                "scale": scale,
                "methods": ["zscore", "mad"],
            }
        )
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
