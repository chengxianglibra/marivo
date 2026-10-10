"""Recover C08 retained reference, rank and full-opportunity continuations."""

import json
import os
import sys
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis
import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR
from marivo.analysis.materialization.graph_protocol import encode as descriptor_encode
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, arr, checked, digest, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead | mv.MaterializedTable: ...


def saved(
    result: _MaterializedRead | mv.MaterializedTable, *, frame: pd.DataFrame | None = None
) -> dict[str, Json]:
    if isinstance(result, _MaterializedRead):
        return snapshot(result, frame=frame)
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    verified = result._dataset.verified()
    return obj(
        checked(
            {
                "artifact": result._dataset.artifact.artifact_ref,
                "descriptor_sha256": digest(descriptor_encode(descriptor, DESCRIPTOR).encode()),
                "schema": descriptor.realized_schema,
                "rows": json.loads(
                    (result.to_pandas() if frame is None else frame).to_json(
                        orient="table", index=False
                    )
                ),
                "parts": [part.role for part in verified.parts],
            }
        )
    )


def reference_parts(
    value: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
) -> dict[str, Json]:
    assert value._dataset is not None
    parts = {
        part.role: digest(
            json.dumps(
                {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                sort_keys=True,
                default=str,
            ).encode()
        )
        for part in value._dataset.verified().parts
        if part.role in ("fixed_reference", "reference_proof", "stratum_values")
    }
    assert set(parts) == {"fixed_reference", "reference_proof", "stratum_values"}
    return dict(parts)


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-reference.json")
    identity = state["session"]
    assert isinstance(identity, str) and not (root / "models").exists()
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(identity, by="id")
        restored: dict[str, _MaterializedRead] = {}
        cold_originals = {"members", "bucket", "groups", "weights", "opportunities", "ranking"}
        for name, raw in obj(state["originals"]).items():
            if phase == "cold" and name not in cold_originals:
                continue
            original = obj(raw)
            artifact_reference = original["artifact"]
            assert isinstance(artifact_reference, str)
            result = session.artifact(artifact_reference)
            assert isinstance(result, _MaterializedRead) and snapshot(result) == original
            restored[name] = result
        current = restored.get("current")
        members, bucket = restored["members"], restored["bucket"]
        groups, weights, opportunities = (
            restored[name] for name in ("groups", "weights", "opportunities")
        )
        if phase == "fixed":
            assert isinstance(current, mv.MaterializedNumericRelation)
        assert isinstance(members, mv.MaterializedAnalysisDomain)
        assert isinstance(bucket, mv.MaterializedCategoryRelation)
        assert isinstance(groups, mv.MaterializedGroupedNumericRelation)
        assert isinstance(weights, mv.MaterializedNumericRelation)
        assert isinstance(opportunities, mv.MaterializedNumericRelation)
        opportunity_snapshot = obj(obj(state["originals"])["opportunities"])
        assert len(arr(obj(opportunity_snapshot["rows"])["data"])) == 6
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        frames: dict[str, pd.DataFrame] = {}
        views: dict[str, Json] = {}

        def execute(name: str, operation: Continuation) -> _MaterializedRead | mv.MaterializedTable:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            frame = result.to_pandas()
            frames[name] = frame
            outputs[name] = saved(result, frame=frame)
            return result

        def key_values(
            value: (
                pd.DataFrame
                | mv.MaterializedNumericRelation
                | mv.MaterializedSelectedNumericRelation
            ),
        ) -> dict[tuple[str, int, int], float]:
            frame = value if isinstance(value, pd.DataFrame) else value.to_pandas()
            return {
                (str(row.member), int(str(row.coord_0)), int(str(row.coord_1))): float(
                    str(row.value)
                )
                for row in frame.itertuples(index=False)
            }

        def snapshot_rows(value: Json) -> list[dict[str, Json]]:
            return [obj(row) for row in arr(obj(obj(value)["rows"])["data"])]

        def snapshot_key_values(value: Json) -> dict[tuple[str, int, int], float]:
            return {
                (str(row["member"]), int(str(row["coord_0"])), int(str(row["coord_1"]))): float(
                    str(row["value"])
                )
                for row in snapshot_rows(value)
            }

        complete = {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 0,
            ("b", 9007199254740993, 1): 4,
        }
        positive_keys = {key for key, amount in complete.items() if amount}
        source_ranking = restored["ranking"]
        assert isinstance(source_ranking, mv.MaterializedRankingResult)
        source_views: dict[str, Json] = {
            "values": snapshot(source_ranking.values),
            "ranks": snapshot(source_ranking.ranks),
        }
        assert snapshot_key_values(source_views["values"]) == complete
        assert snapshot_key_values(source_views["ranks"]) == {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 3,
            ("b", 9007199254740993, 1): 1,
        }
        if phase == "cold":
            expected_outputs = obj(state["outputs"])
            cohort = execute(
                "cohort", members.cohort(opportunities.value.gt(0), rule=mv.any_instance())
            )
            assert isinstance(cohort, mv.MaterializedAnalysisDomain)
            assert {
                (str(row["member"]), int(str(row["coord_0"])), int(str(row["coord_1"])))
                for row in snapshot_rows(outputs["cohort"])
            } == positive_keys
            cohort_penetration = execute("cohort_penetration", cohort.penetration_in(members))
            assert frames["cohort_penetration"].value.tolist() == [2 / 3]

            empty = execute("empty", bucket.where(bucket.value.eq("absent")).members())
            assert isinstance(empty, mv.MaterializedAnalysisDomain)
            empty_penetration = execute("empty_penetration", empty.penetration_in(empty))
            empty_rows = snapshot_rows(outputs["empty_penetration"])
            assert [row["cell_tag"] for row in empty_rows] == ["undefined"] and [
                row["cell_reason"] for row in empty_rows
            ] == ["empty_reference"]

            fixed_reference = mv.reference_weights(
                weights, strata=(bucket,), unit=ms.ref.entity("sales.facts")
            )
            standardized = execute("standardized", groups.standardize(reference=fixed_reference))
            assert isinstance(standardized, mv.MaterializedNumericRelation)
            expected = 2 * (1 / 3) + 4 * (2 / 3)
            assert frames["standardized"].value.tolist() == [expected]
            standardized_parts = reference_parts(standardized)

            cold_names = {
                "cohort",
                "cohort_penetration",
                "empty",
                "empty_penetration",
                "standardized",
            }
            assert set(outputs) == cold_names
            differences = {name for name in cold_names if outputs[name] != expected_outputs[name]}
            assert not differences, f"cold outputs differ from fixed outputs: {sorted(differences)}"
            assert session._runtime.store.resources(session.id) == ()
            for name, value in restored.items():
                assert snapshot(value) == obj(state["originals"])[name]
            assert run_ids(session) == before and kernels.call_count == 0
            cold_reference_parts = {
                "standardized": standardized_parts,
            }
            return {
                "phase": phase,
                "pid": os.getpid(),
                "outputs": outputs,
                "views": source_views,
                "source_views": source_views,
                "new_runs": 0,
                "kernels": 0,
                "resources": 0,
                "original_snapshot_preserved": True,
                "source_and_semantic_forbidden": True,
                "retained_reference_parts": checked(cold_reference_parts),
            }
        assert isinstance(current, mv.MaterializedNumericRelation)
        fixed_rollup = execute("reference", current.rollup())
        assert isinstance(fixed_rollup, mv.MaterializedRolledNumericRelation)
        share = execute("share", current.share_of(fixed_rollup))
        ranking = execute("ranking", current.rank(order="descending", ties="dense"))
        assert isinstance(share, mv.MaterializedNumericRelation)
        assert isinstance(ranking, mv.MaterializedRankingResult)
        assert key_values(frames["share"]) == {key: amount / 6 for key, amount in complete.items()}
        prior = run_ids(session)
        views["values"], views["ranks"] = snapshot(ranking.values), snapshot(ranking.ranks)
        assert snapshot_key_values(views["values"]) == complete
        assert snapshot_key_values(views["ranks"]) == {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 3,
            ("b", 9007199254740993, 1): 1,
        }
        assert run_ids(session) == prior
        cohort = execute(
            "cohort", members.cohort(opportunities.value.gt(0), rule=mv.any_instance())
        )
        assert isinstance(cohort, mv.MaterializedAnalysisDomain)
        assert {
            (str(row["member"]), int(str(row["coord_0"])), int(str(row["coord_1"])))
            for row in snapshot_rows(outputs["cohort"])
        } == positive_keys
        cohort_penetration = execute("cohort_penetration", cohort.penetration_in(members))
        assert frames["cohort_penetration"].value.tolist() == [2 / 3]
        empty = execute("empty", bucket.where(bucket.value.eq("absent")).members())
        assert isinstance(empty, mv.MaterializedAnalysisDomain)
        empty_penetration = execute("empty_penetration", empty.penetration_in(empty))
        empty_rows = snapshot_rows(outputs["empty_penetration"])
        assert [row["cell_tag"] for row in empty_rows] == ["undefined"] and [
            row["cell_reason"] for row in empty_rows
        ] == ["empty_reference"]
        fixed_reference = mv.reference_weights(
            weights, strata=(bucket,), unit=ms.ref.entity("sales.facts")
        )
        standardized = execute("standardized", groups.standardize(reference=fixed_reference))
        expected = 2 * (1 / 3) + 4 * (2 / 3)
        assert frames["standardized"].value.tolist() == [expected]
        assert isinstance(standardized, mv.MaterializedNumericRelation)
        selected_standard = execute(
            "standardized_selected", standardized.where(standardized.value.gt(0))
        )
        assert frames["standardized_selected"].value.tolist() == [expected]
        assert isinstance(selected_standard, mv.MaterializedSelectedNumericRelation)
        standardized_parts = reference_parts(standardized)
        selected_standard_parts = reference_parts(selected_standard)
        share_parts = reference_parts(share)
        assert selected_standard_parts == standardized_parts
        retained_reference_parts: dict[str, Json] = {
            "share": share_parts,
            "standardized": standardized_parts,
            "standardized_selected": selected_standard_parts,
        }
        execute("share_mean", share.aggregate(mv.mean()))
        assert frames["share_mean"].value.tolist() == [1 / 3]
        limited = execute("limited", ranking.limit(2))
        assert isinstance(limited, mv.MaterializedRankingResult)
        assert set(key_values(limited.ranks)) == positive_keys
        table = execute("table", mv.table(amount=limited.values, rank=limited.ranks))
        assert isinstance(table, mv.MaterializedTable)
        table_rows = snapshot_rows(outputs["table"])
        assert [row["amount"] for row in table_rows] == [2, 4] and [
            row["rank"] for row in table_rows
        ] == [2, 1]
        assert len(outputs) == 12
        assert session._runtime.store.resources(session.id) == ()
        for name, value in restored.items():
            assert snapshot(value) == obj(state["originals"])[name]
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == len(outputs)
            state["outputs"], state["views"] = outputs, views
            (root / "r94-reference.json").write_bytes(encode(state))
        else:
            assert (
                outputs == state["outputs"]
                and views == state["views"]
                and run_ids(session) == before
                and kernels.call_count == 0
            )
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "views": views,
            "source_views": source_views,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_preserved": True,
            "source_and_semantic_forbidden": True,
            "retained_reference_parts": checked(retained_reference_parts),
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
