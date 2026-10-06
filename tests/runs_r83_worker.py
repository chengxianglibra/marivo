"""Independent producer, source-offline continuation and cold Store 7 recovery."""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import duckdb
import ibis
from typing_extensions import NotRequired

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.materialization.runs_execution import CAPTURE, RUNS, _decode
from marivo.analysis.methods.physical import Qualified
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, key_json
from tests.json_support import arr as array_json
from tests.json_support import obj as object_json
from tests.json_support import read as read_json


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline runs touched current Semantic/calendar or source")


class Manifest(TypedDict):
    session: str
    original: str
    result: str
    score: str
    proofs: list[dict[str, Json]]
    continued: NotRequired[str]
    selected: NotRequired[str]


def read_manifest(path: Path) -> Manifest:
    data = read_json(path)

    def text(key: str) -> str:
        value = data[key]
        assert isinstance(value, str)
        return value

    result: Manifest = {
        "session": text("session"),
        "original": text("original"),
        "result": text("result"),
        "score": text("score"),
        "proofs": [object_json(row) for row in array_json(data["proofs"])],
    }
    if "continued" in data:
        result["continued"] = text("continued")
    if "selected" in data:
        result["selected"] = text("selected")
    return result


def proof(result: mv.MaterializedTimeRunResult, proof_class: str) -> dict[str, Json]:
    assert result._dataset is not None
    dataset = result._dataset
    retained = dataset.verified()
    capture, state = _decode(retained.parts)
    physical = next(
        p
        for p in descriptor_plan(
            dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if p.key.method.name == "time.runs"
    )
    assert retained.contract.key_fields == ("key_0",)
    assert isinstance(physical.implementation.qualification, Qualified)
    return {
        "proof_class": proof_class,
        "qualification_key": key_json(physical.key),
        "implementation_id": physical.implementation.qualification.implementation_id,
        "implementation_contract_version": "v" + str(physical.implementation.contract_version),
        "precision_contract": physical.implementation.precision,
        "state_version": "v1",
        "numeric_policy": "r8_numeric_v1",
        "domain": "time",
        "key_profile": "time_cell:string",
        "origin_profile": "PARQUET-US-UTC",
        "time_profile": "builtin_day:grid_us:UTC"
        if capture.signature.domain.time_grid is not None
        and capture.signature.domain.time_grid.snapshot_digest is None
        else "certified_calendar:grid_us:UTC",
        "retained_parts": [p.role for p in retained.parts],
        "input_codec": capture.version,
        "state_codec": state.version,
        "artifact": dataset.artifact.artifact_ref,
        "primary_receipt_digest": receipt_digest(dataset.artifact.descriptor.primary_receipt),
        "part_receipt_digests": {
            p.role: receipt_digest(p) for p in dataset.artifact.descriptor.parts
        },
        "input_digest": hashlib.sha256(CAPTURE.dump_json(capture)).hexdigest(),
        "state_digest": hashlib.sha256(RUNS.dump_json(state)).hexdigest(),
        "source_offline": proof_class != "source_kernel",
        "independent_oracle": "fixture values 1,2,7; threshold 1 joins cells two and three; threshold 2 selects cell three; day widths 1/1/1 or certified period widths 1/2/3",
    }


def run(root: Path, phase: str, calendar: bool = False) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    manifest_path = root / "r83.json"
    if phase == "produce":
        catalog = ms.load(workspace_dir=root)
        if calendar:
            from tests.deviation_r82_time_worker import publish_calendar

            publish_calendar(catalog, "UTC")
        session = mv.session.get_or_create("r83", report_timezone="UTC")
        grid = mv.time_grid(
            during=mv.time_scope(
                start="2026-08-01", end="2026-08-07" if calendar else "2026-08-04"
            ),
            grain=ms.calendar_grain(
                calendar=ms.ref.period_calendar("sales.unequal"), level="period"
            )
            if calendar
            else mv.grain("day"),
        )
        members = session.members(ms.ref.entity("sales.order"))
        raw = members.each(grid).observe(ms.ref.metric("sales.maximum_0"), during=grid.window)
        daily = raw.group_by(grid).rollup()
        captured_original = daily.execute()
        assert captured_original.to_pandas().value.tolist() == [1, 2, 7]
        entity_runs = raw.runs(where=raw.value.gt(1))
        next_members = entity_runs.count.members()
        assert isinstance(next_members, mv.LogicalAnalysisDomain)
        next_result = next_members.observe(
            ms.ref.metric("sales.total_0"),
            during=mv.time_scope(
                start="2026-08-01", end="2026-08-07" if calendar else "2026-08-04"
            ),
        ).execute()
        assert sorted(next_result.to_pandas().value.tolist()) == [2, 7]
        fixed_entity_runs = entity_runs.execute()
        assert len(fixed_entity_runs.count.members().execute().to_pandas()) == 2
        result = daily.runs(where=daily.value.gt(1)).execute()
        assert result.count.to_pandas().value.tolist() == [2]
        assert result.duration.to_pandas().value.iloc[0].days == (5 if calendar else 2)
        for method in ("mad", "zscore"):
            scored = daily.deviation(method=method)
            scored_runs = scored.score.runs(where=scored.score.value.gt(1)).execute()
            assert scored_runs.count.to_pandas().value.tolist() == [1]
        constant_reference = daily.deviation(method="mad").reference
        unavailable_scores = constant_reference.deviation(method="zscore").score
        unavailable_runs = unavailable_scores.runs(where=unavailable_scores.value.gt(1)).execute()
        assert unavailable_runs.count.to_pandas().empty
        assert dict(unavailable_runs.contract()._facts)["original_unavailable"] == "3"
        assert result.count.rank(
            order="descending", ties="dense"
        ).execute().values.to_pandas().value.tolist() == [2]
        mv.table(
            start=result.start, end=result.end, count=result.count, duration=result.duration
        ).execute()
        manifest: Manifest = {
            "session": session.id,
            "original": captured_original.evidence_digest().artifact_ref.ref,
            "result": result.evidence_digest().artifact_ref.ref,
            "proofs": [proof(result, "source_kernel")],
            "score": scored_runs.evidence_digest().artifact_ref.ref,
        }
        manifest_path.write_text(json.dumps(manifest))
    else:
        manifest = read_manifest(manifest_path)
        with (
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "batches", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest["session"], by="id")
            original = session.artifact(manifest["original"])
            recovered = session.artifact(manifest["result"])
            assert isinstance(original, mv.MaterializedNumericRelation)
            assert isinstance(recovered, mv.MaterializedTimeRunResult)
            assert recovered.count.to_pandas().value.tolist() == [2]
            recovered_score = session.artifact(manifest["score"])
            assert isinstance(recovered_score, mv.MaterializedTimeRunResult)
            assert recovered_score.count.to_pandas().value.tolist() == [1]
            if phase == "fixed":
                continued = original.runs(where=original.value.gt(1)).execute()
                assert continued.count.to_pandas().value.tolist() == [2]
                selected = recovered.where(recovered.count.value.gt(1)).execute()
                assert selected.count.to_pandas().value.tolist() == [2]
                manifest["continued"] = continued.evidence_digest().artifact_ref.ref
                manifest["proofs"].append(proof(continued, "fixed_kernel"))
                manifest["selected"] = selected.evidence_digest().artifact_ref.ref
                manifest_path.write_text(json.dumps(manifest))
            else:
                recovered_selected = session.artifact(manifest["selected"])
                assert isinstance(recovered_selected, mv.MaterializedTimeRunResult)
                assert recovered_selected.count.to_pandas().value.tolist() == [2]
                from marivo.analysis.materialization import runs_execution

                with patch.object(
                    runs_execution, "execute", wraps=runs_execution.execute
                ) as consumer:
                    cold = original.runs(where=original.value.gt(2)).execute()
                assert consumer.call_count > 0
                assert cold.count.to_pandas().value.tolist() == [1]
                manifest["proofs"].append(proof(cold, "fresh_process_source_offline_kernel"))
                manifest_path.write_text(json.dumps(manifest))
    print(json.dumps({"accepted": phase}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == "calendar")
