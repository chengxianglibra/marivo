"""Recover C08 retained reference, rank and full-opportunity continuations."""

import json
import os
import sys
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR
from marivo.analysis.materialization.graph_protocol import encode as descriptor_encode
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, checked, digest, encode, obj, read
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead | mv.MaterializedTable: ...


def saved(result: _MaterializedRead | mv.MaterializedTable) -> dict[str, Json]:
    if isinstance(result, _MaterializedRead):
        return snapshot(result)
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    verified = result._dataset.verified()
    return obj(
        checked(
            {
                "artifact": result._dataset.artifact.artifact_ref,
                "descriptor_sha256": digest(descriptor_encode(descriptor, DESCRIPTOR).encode()),
                "schema": descriptor.realized_schema,
                "rows": json.loads(result.to_pandas().to_json(orient="table", index=False)),
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
        for name, raw in obj(state["originals"]).items():
            original = obj(raw)
            artifact_reference = original["artifact"]
            assert isinstance(artifact_reference, str)
            result = session.artifact(artifact_reference)
            assert isinstance(result, _MaterializedRead) and snapshot(result) == original
            restored[name] = result
        current, members, bucket = restored["current"], restored["members"], restored["bucket"]
        reference, groups, weights, opportunities = (
            restored[name] for name in ("reference", "groups", "weights", "opportunities")
        )
        assert isinstance(current, mv.MaterializedNumericRelation)
        assert isinstance(members, mv.MaterializedAnalysisDomain)
        assert isinstance(bucket, mv.MaterializedCategoryRelation)
        assert isinstance(reference, mv.MaterializedRolledNumericRelation)
        assert isinstance(groups, mv.MaterializedGroupedNumericRelation)
        assert isinstance(weights, mv.MaterializedNumericRelation)
        assert isinstance(opportunities, mv.MaterializedNumericRelation)
        assert len(opportunities.to_pandas()) == 6
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        views: dict[str, Json] = {}

        def execute(name: str, operation: Continuation) -> _MaterializedRead | mv.MaterializedTable:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            outputs[name] = saved(result)
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert saved(operation.execute()) == outputs[name]
            assert run_ids(session) == seen
            return result

        def key_values(
            value: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
        ) -> dict[tuple[str, int, int], float]:
            return {
                (str(row.member), int(str(row.coord_0)), int(str(row.coord_1))): float(
                    str(row.value)
                )
                for row in value.to_pandas().itertuples(index=False)
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
        assert key_values(source_ranking.values) == complete
        assert key_values(source_ranking.ranks) == {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 3,
            ("b", 9007199254740993, 1): 1,
        }
        fixed_rollup = execute("reference", current.rollup())
        assert isinstance(fixed_rollup, mv.MaterializedRolledNumericRelation)
        share = execute("share", current.share_of(fixed_rollup))
        ranking = execute("ranking", current.rank(order="descending", ties="dense"))
        assert isinstance(share, mv.MaterializedNumericRelation)
        assert isinstance(ranking, mv.MaterializedRankingResult)
        assert key_values(share) == {key: amount / 6 for key, amount in complete.items()}
        assert key_values(ranking.values) == complete
        assert key_values(ranking.ranks) == {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 3,
            ("b", 9007199254740993, 1): 1,
        }
        prior = run_ids(session)
        views["values"], views["ranks"] = snapshot(ranking.values), snapshot(ranking.ranks)
        assert run_ids(session) == prior
        cohort = execute(
            "cohort", members.cohort(opportunities.value.gt(0), rule=mv.any_instance())
        )
        cohort_two = execute(
            "cohort_two", members.cohort(opportunities.value.gt(0), rule=mv.at_least(2))
        )
        assert isinstance(cohort, mv.MaterializedAnalysisDomain)
        assert isinstance(cohort_two, mv.MaterializedAnalysisDomain)
        assert (
            set(cohort.to_pandas().set_index(["member", "coord_0", "coord_1"]).index)
            == positive_keys
        )
        assert cohort_two.to_pandas().empty
        cohort_penetration = execute("cohort_penetration", cohort.penetration_in(members))
        assert cohort_penetration.to_pandas().value.tolist() == [2 / 3]
        selected = execute("selected", bucket.where(bucket.value.eq("a")).members())
        empty = execute("empty", bucket.where(bucket.value.eq("absent")).members())
        assert isinstance(selected, mv.MaterializedAnalysisDomain)
        assert isinstance(empty, mv.MaterializedAnalysisDomain)
        penetration = execute("penetration", selected.penetration_in(members))
        empty_penetration = execute("empty_penetration", empty.penetration_in(empty))
        assert penetration.to_pandas().value.tolist() == [2 / 3]
        frame = empty_penetration.to_pandas()
        assert frame.cell_tag.tolist() == ["undefined"] and frame.cell_reason.tolist() == [
            "empty_reference"
        ]
        fixed_reference = mv.reference_weights(
            weights, strata=(bucket,), unit=ms.ref.entity("sales.facts")
        )
        standardized = execute("standardized", groups.standardize(reference=fixed_reference))
        expected = 2 * (1 / 3) + 4 * (2 / 3)
        assert standardized.to_pandas().value.tolist() == [expected]
        assert isinstance(standardized, mv.MaterializedNumericRelation)
        selected_standard = execute(
            "standardized_selected", standardized.where(standardized.value.gt(0))
        )
        assert selected_standard.to_pandas().value.tolist() == [expected]
        assert isinstance(selected_standard, mv.MaterializedSelectedNumericRelation)
        assert reference_parts(selected_standard) == reference_parts(standardized)
        share_positive = execute("share_positive", share.where(share.value.gt(0)))
        assert isinstance(share_positive, mv.MaterializedSelectedNumericRelation)
        assert key_values(share_positive) == {
            key: amount / 6 for key, amount in complete.items() if amount
        }
        assert reference_parts(share_positive) == reference_parts(share)
        retained_reference_parts: dict[str, Json] = {
            "share": reference_parts(share),
            "standardized": reference_parts(standardized),
            "share_positive": reference_parts(share_positive),
            "standardized_selected": reference_parts(selected_standard),
        }
        for name, method, expected_stat in (
            ("share_sum", mv.sum(), 1),
            ("share_count", mv.count(), 3),
            ("share_mean", mv.mean(), 1 / 3),
        ):
            statistic = execute(name, share.summarize(method))
            assert statistic.to_pandas().value.tolist() == [expected_stat]
        limited = execute("limited", ranking.limit(2))
        filtered = execute("filtered", ranking.where(ranking.ranks.value.gt(1)))
        assert isinstance(limited, mv.MaterializedRankingResult) and isinstance(
            filtered, mv.MaterializedRankingResult
        )
        assert set(key_values(limited.ranks)) == positive_keys
        assert list(key_values(filtered.ranks).values()) == [2, 3]
        assert key_values(filtered.values) == {
            key: amount for key, amount in complete.items() if key[0] == "a"
        }
        table = execute("table", mv.table(amount=limited.values, rank=limited.ranks))
        assert isinstance(table, mv.MaterializedTable)
        frame = table.to_pandas()
        assert frame.amount.tolist() == [2, 4] and frame["rank"].tolist() == [2, 1]
        assert len(outputs) == 19
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
            "retained_reference_parts": retained_reference_parts,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
