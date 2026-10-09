"""Independent producer, offline kernel continuation and cold statistical recovery."""

import json
import os
import sys
from pathlib import Path
from typing import Literal, TypeAlias
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.materialization.statistical_execution import decode_forecast, decode_pairs
from marivo.analysis.methods.physical import Qualified
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.support.json import Json, key_json
from tests.support.json import arr as array_json
from tests.support.json import obj as object_json
from tests.support.json import read as read_json

Statistic: TypeAlias = mv.MaterializedAssociationResult | mv.MaterializedForecastResult
AssociationMethod: TypeAlias = Literal["pearson", "spearman", "kendall"]


def text_value(value: Json) -> str:
    assert isinstance(value, str)
    return value


def association_method(value: Json) -> AssociationMethod:
    if value == "pearson":
        return "pearson"
    if value == "spearman":
        return "spearman"
    assert value == "kendall"
    return "kendall"


def forecast_model(value: Json) -> mv.ForecastModel:
    if value == "naive@v1":
        return mv.naive()
    if value == "drift@v1":
        return mv.drift()
    assert value == "seasonal_naive@v1"
    return mv.seasonal_naive(periods=2)


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline statistics touched current Semantic/calendar or source")


def proof(result: Statistic, proof_class: str) -> dict[str, Json]:
    dataset = result._dataset
    assert dataset is not None
    retained = dataset.verified()
    codecs: tuple[str, str]
    if isinstance(result, mv.MaterializedAssociationResult):
        captured, state = decode_pairs(retained.parts)
        method = "association." + captured.declaration.method
        codecs = captured.version, state.version
    else:
        captured_f, state_f = decode_forecast(retained.parts)
        method = "forecast." + captured_f.declaration.model
        codecs = captured_f.version, state_f.version
    physical = next(
        p
        for p in descriptor_plan(
            dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if p.key.method.name == method
    )
    assert isinstance(physical.implementation.qualification, Qualified)
    return {
        "proof_class": proof_class,
        "qualification_key": key_json(physical.key),
        "implementation_id": physical.implementation.qualification.implementation_id,
        "implementation_contract_version": "v1",
        "precision_contract": physical.implementation.precision,
        "state_version": "v1",
        "numeric_policy": "r8_numeric_v1",
        "domain": "time",
        "key_profile": "time_cell:string",
        "origin_profile": "PARQUET-US-UTC",
        "time_profile": "builtin_day:grid_us:UTC",
        "retained_parts": [p.role for p in retained.parts],
        "input_codec": codecs[0],
        "state_codec": codecs[1],
        "artifact": dataset.artifact.artifact_ref,
        "primary_receipt_digest": receipt_digest(dataset.artifact.descriptor.primary_receipt),
        "part_receipt_digests": {
            p.role: receipt_digest(p) for p in dataset.artifact.descriptor.parts
        },
        "source_offline": proof_class != "source_kernel",
        "oracle": "fixture vectors 1,2,7 and exact positive scale multiples; complete pairs correlation one; normative per-series forecast point equations",
    }


def check(result: Statistic) -> None:
    if isinstance(result, mv.MaterializedAssociationResult):
        rows = result.coefficient.to_pandas()
        assert rows.value.tolist() == [1.0] * len(rows)
        assert result.selected.to_pandas().value.tolist() == [True] * len(rows)
    else:
        values = result.prediction.to_pandas().value.tolist()
        assert result._dataset is not None
        _, state = decode_forecast(result._dataset.verified().parts)
        model = state.series[0].training.model
        assert values == (
            [7.0, 7.0] if model == "naive" else [10.0, 13.0] if model == "drift" else [2.0, 7.0]
        )
    page = result.findings(limit=100)
    assert page.items and result.evidence_digest().finding_count == len(page.items)
    assert result.finding(page.items[0].finding_id) == page.items[0]
    result.show(max_output_bytes=4096)


def run(root: Path, phase: str) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    path = root / "statistics-recovery.json"
    result: Statistic
    logical: mv.LogicalAssociationResult | mv.LogicalForecastResult
    fresh: mv.LogicalAssociationResult | mv.LogicalForecastResult
    originals: list[mv.MaterializedGroupedNumericRelation] = []
    if phase == "produce":
        ms.load(workspace_dir=root)
        session = mv.session.get_or_create("r84", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.order"))
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
        )
        sources = [
            members.observe(
                ms.ref.metric(f"sales.total_{i}"),
                during=grid,
                by=(mv.member(),),
            )
            .group_by(grid)
            .rollup()
            for i in (0, 1, 5)
        ]
        for source in sources:
            original = source.execute()
            assert isinstance(original, mv.MaterializedGroupedNumericRelation)
            originals.append(original)
        entries: list[Json] = []
        proofs: list[Json] = []
        for method in ("pearson", "spearman", "kendall"):
            result = sources[0].correlate(sources[1], method=method).execute()
            check(result)
            entries.append(
                {
                    "method": method,
                    "kind": "association",
                    "result": result.evidence_digest().artifact_ref.ref,
                }
            )
            proofs.append(proof(result, "source_kernel"))
        for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=2)):
            result = sources[0].forecast(horizon=mv.periods(2), model=model).execute()
            check(result)
            entries.append(
                {
                    "method": model.model_id,
                    "kind": "forecast",
                    "result": result.evidence_digest().artifact_ref.ref,
                }
            )
            proofs.append(proof(result, "source_kernel"))
        path.write_text(
            json.dumps(
                {
                    "session": session.id,
                    "originals": [r.evidence_digest().artifact_ref.ref for r in originals],
                    "entries": entries,
                    "proofs": proofs,
                }
            )
        )
    else:
        data = read_json(path)
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(text_value(data["session"]), by="id")
            for ref in array_json(data["originals"]):
                restored_original = session.artifact(text_value(ref))
                assert isinstance(restored_original, mv.MaterializedGroupedNumericRelation)
                originals.append(restored_original)
            for item in array_json(data["entries"]):
                entry = object_json(item)
                restored = session.artifact(text_value(entry["result"]))
                assert isinstance(
                    restored, (mv.MaterializedAssociationResult, mv.MaterializedForecastResult)
                )
                result = restored
                check(result)
                if phase == "fixed":
                    if entry["kind"] == "association":
                        logical = originals[0].correlate(
                            originals[1], method=association_method(entry["method"])
                        )
                    else:
                        model = forecast_model(entry["method"])
                        logical = originals[0].forecast(horizon=mv.periods(2), model=model)
                    fixed = logical.execute()
                    check(fixed)
                    assert (
                        logical.execute().evidence_digest().artifact_ref
                        == fixed.evidence_digest().artifact_ref
                    )
                    entry["continued"] = fixed.evidence_digest().artifact_ref.ref
                    array_json(data["proofs"]).append(proof(fixed, "fixed_kernel"))
                else:
                    continued = session.artifact(text_value(entry["continued"]))
                    assert isinstance(
                        continued, (mv.MaterializedAssociationResult, mv.MaterializedForecastResult)
                    )
                    check(continued)
                    if entry["kind"] == "association":
                        fresh = originals[0].correlate(
                            originals[1],
                            method=association_method(entry["method"]),
                            lag_range=range(0, 1),
                        )
                    else:
                        model = forecast_model(entry["method"])
                        fresh = originals[0].forecast(
                            horizon=mv.periods(2), model=model, interval_level=0.9
                        )
                    from marivo.analysis.core.rules import AssociationFit, ForecastFit
                    from marivo.analysis.materialization import statistical_execution

                    with patch.object(
                        statistical_execution, "execute", wraps=statistical_execution.execute
                    ) as consumer:
                        cold = fresh.execute()
                    assert any(
                        isinstance(call.args[0].parameters, (AssociationFit, ForecastFit))
                        for call in consumer.call_args_list
                    )
                    check(cold)
                    array_json(data["proofs"]).append(
                        proof(cold, "fresh_process_source_offline_kernel")
                    )
                    selected = result.where(
                        result.selected.value.eq(True)
                        if isinstance(result, mv.MaterializedAssociationResult)
                        else result.prediction.value.gt(0)
                    ).execute()
                    assert (
                        selected.evidence_digest().finding_count
                        == result.evidence_digest().finding_count
                    )
        path.write_text(json.dumps(data))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
