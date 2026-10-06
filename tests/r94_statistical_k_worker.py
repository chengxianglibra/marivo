"""Execute every direct statistical result-card continuation from a native archive."""

import json
import os
import sys
from math import sqrt
from pathlib import Path
from statistics import NormalDist
from typing import Literal, Protocol, TypeAlias
from unittest.mock import patch

import duckdb
import ibis
import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution, runs_execution
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, receipt_digest
from marivo.analysis.materialization.graph_protocol import encode as descriptor_encode
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, digest, encode, obj, read
from tests.deviation_r82_oracle import expected
from tests.r86_journeys import Result, check
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_recovery_worker import forbidden
from tests.r94_statistical_recovery_worker import snapshot

Field: TypeAlias = (
    mv.MaterializedNumericRelation
    | mv.MaterializedTemporalRelation
    | mv.MaterializedBooleanRelation
    | mv.MaterializedCoefficientRelation
)
Saved: TypeAlias = _MaterializedRead | mv.MaterializedTable


class Continuation(Protocol):
    def execute(self) -> Saved: ...


def saved_result(result: Saved) -> dict[str, Json]:
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    result._dataset.verified()
    return {
        "artifact": result._dataset.artifact.artifact_ref,
        "descriptor_sha256": digest(descriptor_encode(descriptor, DESCRIPTOR).encode()),
        "execution_key_digest": descriptor.execution_key_digest,
        "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
        "rows": checked(json.loads(result.to_pandas().to_json(orient="table", index=False))),
        "primary_receipt_digest": receipt_digest(descriptor.primary_receipt),
        "part_receipt_digests": {part.role: receipt_digest(part) for part in descriptor.parts},
    }


def fields(result: Result, method: str) -> dict[str, Field]:
    if isinstance(result, mv.MaterializedDeviationResult):
        algorithm: Literal["zscore", "mad"] = "zscore" if method == "deviation.zscore@v1" else "mad"
        center, _, _, scores = expected((1, 2, 7), algorithm)
        values: dict[str, Field] = {
            "observed": result.observed,
            "reference": result.reference,
            "deviation": result.deviation,
            "score": result.score,
        }
        for name, oracle in (
            ("observed", [1, 2, 7]),
            ("reference", [float(center)] * 3),
            ("deviation", [float(value - center) for value in (1, 2, 7)]),
            ("score", list(scores)),
        ):
            frame = values[name].to_pandas()
            assert frame.value.tolist() == oracle
            assert list(frame.iloc[:, :3].itertuples(index=False, name=None)) == [
                ("a", 9007199254740992, 1),
                ("a", 9007199254740993, 2),
                ("b", 9007199254740993, 1),
            ]
        return values
    if isinstance(result, mv.MaterializedAssociationResult):
        assert result.coefficient.to_pandas().value.tolist() == [1.0]
        assert result.selected.to_pandas().value.tolist() == [True]
        return {"coefficient": result.coefficient, "selected": result.selected}
    if isinstance(result, mv.MaterializedTimeRunResult):
        assert result.start.to_pandas().value.tolist() == [pd.Timestamp("2026-08-02", tz="UTC")]
        assert result.end.to_pandas().value.tolist() == [pd.Timestamp("2026-08-04", tz="UTC")]
        assert result.count.to_pandas().value.tolist() == [2]
        assert result.duration.to_pandas().value.tolist() == [pd.Timedelta(days=2)]
        return {
            "start": result.start,
            "end": result.end,
            "count": result.count,
            "duration": result.duration,
        }
    points, variances = (
        ([7.0, 7.0], [13.0, 26.0])
        if method == "forecast.naive@v1"
        else ([10.0, 13.0], [12.0, 32.0])
        if method == "forecast.drift@v1"
        else ([2.0, 7.0], [36.0, 36.0])
    )
    z = NormalDist().inv_cdf(0.975)
    for field, sign in ((result.lower, -1), (result.upper, 1)):
        actual = field.to_pandas().value.tolist()
        oracle = [
            point + sign * z * sqrt(variance)
            for point, variance in zip(points, variances, strict=True)
        ]
        assert all(abs(a - b) <= 1e-12 for a, b in zip(actual, oracle, strict=True))
    assert result.prediction.to_pandas().value.tolist() == points
    return {"prediction": result.prediction, "lower": result.lower, "upper": result.upper}


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    assert not (root / "models").exists() and not (root / "source.duckdb").exists()
    path = root / "r94-statistical.json"
    manifest = read(path)
    identity = manifest["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(deviation_numeric, "fit", forbidden),
        patch.object(association_numeric, "score", forbidden),
        patch.object(forecast_numeric, "train", forbidden),
        patch.object(runs_execution, "compute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(identity, by="id")
        before = run_ids(session)

        def ids() -> set[str]:
            with session._runtime.store._read() as connection:
                rows = connection.execute(
                    "SELECT run_ref FROM analysis_action_runs WHERE session_ref=?", (session.id,)
                ).fetchall()
            values: set[str] = set()
            for row in rows:
                assert isinstance(row[0], str)
                values.add(row[0])
            return values

        assert ids() == before
        outputs: dict[str, Json] = {}
        views: dict[str, Json] = {}
        consumed: dict[str, Json] = {}

        def execute(label: str, logical: Continuation) -> Saved:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            saved = saved_result(result)
            if phase == "cold":
                assert saved == obj(manifest["statistical_K"])[label]
            prior = ids()
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert saved_result(logical.execute()) == saved
            assert ids() == prior
            outputs[label] = saved
            return result

        for origin in ("source", "fixed"):
            for method, raw in obj(manifest[origin]).items():
                saved = obj(raw)
                reference = saved["artifact"]
                assert isinstance(reference, str)
                result = session.artifact(reference)
                assert isinstance(result, Result) and snapshot(result) == saved
                check(result, method)
                label = origin + ":" + method
                owned = fields(result, method)
                actions = {action.call for action in result.contract().actions}
                expected_actions = {"relation." + name for name in owned} | {
                    "relation.where(predicate)"
                }
                if isinstance(result, mv.MaterializedTimeRunResult):
                    expected_actions.add(
                        "mv.table(start=relation.start, end=relation.end, count=relation.count, duration=relation.duration)"
                    )
                assert actions == expected_actions
                consumed[label] = checked(sorted(actions))
                prior = ids()
                for name, field in owned.items():
                    views[label + ":" + name] = saved_result(field)
                assert ids() == prior
                for name, field in owned.items():
                    original_frame = field.to_pandas()
                    for selection in ("defined", "empty"):
                        predicate = (
                            field.value.is_defined()
                            if selection == "defined"
                            else mv.not_(field.value.is_defined())
                        )
                        selected = execute(
                            label + ":" + name + ":" + selection, field.where(predicate)
                        )
                        if selection == "empty":
                            assert selected.to_pandas().empty
                        else:
                            selected_frame = selected.to_pandas()
                            assert set(selected_frame.columns) <= set(original_frame.columns)
                            assert selected_frame.equals(original_frame[selected_frame.columns]), (
                                label,
                                name,
                                selected_frame.to_dict("records"),
                                original_frame[selected_frame.columns].to_dict("records"),
                            )
                first = next(iter(owned.values()))
                facts = dict(result.contract()._facts)
                for selection in ("defined", "empty"):
                    predicate = (
                        first.value.is_defined()
                        if selection == "defined"
                        else mv.not_(first.value.is_defined())
                    )
                    selected = execute(label + ":where:" + selection, result.where(predicate))
                    assert isinstance(selected, Result)
                    if selection == "empty":
                        assert selected.to_pandas().empty
                    else:
                        assert len(selected.to_pandas()) == len(result.to_pandas())
                        selected_fields = fields(selected, method)
                        for name, field in selected_fields.items():
                            frame = field.to_pandas()
                            original = owned[name].to_pandas()
                            assert set(frame.columns) <= set(original.columns)
                            assert frame.equals(original[frame.columns])
                    assert selected._dataset is not None and result._dataset is not None
                    original_parts = {
                        part.role: part.table for part in result._dataset.verified().parts
                    }
                    selected_parts = {
                        part.role: part.table for part in selected._dataset.verified().parts
                    }
                    scope_roles = {
                        "fit_inputs",
                        "fit_state",
                        "pair_inputs",
                        "association_state",
                        "training_inputs",
                        "forecast_state",
                        "future_cells",
                        "condition_cells",
                    }
                    for role in scope_roles & original_parts.keys():
                        assert selected_parts[role].equals(original_parts[role])
                    selected_facts = dict(selected.contract()._facts)
                    for key in (
                        "fit_scope",
                        "fit_method",
                        "original_count",
                        "original_defined",
                        "fit_partition_0",
                        "scale_branches",
                        "run_scope",
                        "run_transform",
                        "original_true",
                        "original_false",
                        "original_unavailable",
                        "association_method",
                        "observation_unit",
                        "quantity_count",
                        "original_lags",
                        "original_candidates",
                        "valid_candidates",
                        "selected_candidates",
                        "forecast_model",
                        "horizon",
                        "interval_level",
                        "assumptions",
                        "interval",
                        "scope",
                        "series_count",
                        "history_lengths",
                        "degrees_of_freedom",
                        "exact_zero_series",
                    ):
                        if key in facts:
                            assert selected_facts[key] == facts[key]
                table = execute(label + ":table", mv.table(**owned))
                assert isinstance(table, mv.MaterializedTable)
                table_frame = table.to_pandas()
                assert len(table_frame) == len(result.to_pandas())
                for name, field in owned.items():
                    assert table_frame[name].tolist() == field.to_pandas().value.tolist()
                assert snapshot(result) == saved
        assert len(views) == 54 and len(outputs) == 162
        if phase == "cold":
            assert views == obj(manifest["statistical_views"])
            assert ids() == before and kernels.call_count == 0
        else:
            assert len(ids() - before) == kernels.call_count == len(outputs)
            manifest["statistical_K"] = outputs
            manifest["statistical_views"] = views
            path.write_bytes(encode(manifest))
        assert run_ids(session) == ids()
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "views": views,
            "consumed_K": consumed,
            "new_runs": len(ids() - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "source_semantic_duckdb_statistical_kernels_forbidden": True,
            "original_snapshots_preserved": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
