"""Read and continue retained native ranking and deviation owned fields offline."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import (
    graph_local_execution,
    history_execution,
    journey_execution,
)
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, obj, read
from tests.r94_domain_recovery_worker import checked_bytes, forbidden
from tests.r94_native_domain_k_worker import Continuation, run_ids
from tests.r94_statistic_k_worker import METHODS, compact


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    manifest = read(root / "r94-domain.json")
    session_id = manifest["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(history_execution, "execute", forbidden),
        patch.object(journey_execution, "execute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        views: dict[str, Json] = {}
        consumed: dict[str, Json] = {}

        def execute(label: str, logical: Continuation) -> _MaterializedRead:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            saved = compact(result)
            if phase == "cold":
                assert saved == obj(manifest["owned_field_K"])[label]
            prior = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                repeated = logical.execute()
                assert compact(repeated) == saved
            assert run_ids(session) == prior
            outputs[label] = saved
            return result

        for method in METHODS:
            expected = 4 if method == "sum" else 2
            for kind in ("rank", "zscore", "mad"):
                label = method + ":" + kind
                saved = obj(obj(manifest["statistic_K"])[label])
                reference = saved["artifact"]
                assert isinstance(reference, str)
                value = session.artifact(reference)
                assert isinstance(value, _MaterializedRead)
                assert compact(value) == saved
                actions = {action.call for action in value.contract().actions}
                consumed[label] = checked(sorted(actions))
                prior = run_ids(session)
                if kind == "rank":
                    assert isinstance(value, mv.MaterializedRankingResult)
                    assert actions == {
                        "relation.values",
                        "relation.ranks",
                        "relation.where(predicate)",
                        "relation.limit(count)",
                    }
                    for name, field, oracle in (
                        ("values", value.values, expected),
                        ("ranks", value.ranks, 1),
                    ):
                        assert field.to_pandas().value.tolist() == [oracle]
                        views[label + ":" + name] = compact(field)
                    assert run_ids(session) == prior
                    limited = execute(label + ":limit", value.limit(1))
                    assert isinstance(limited, mv.MaterializedRankingResult)
                    assert limited.ranks.to_pandas().value.tolist() == [1]
                    assert limited.values.to_pandas().value.tolist() == [expected]
                    empty = execute(label + ":where", value.where(value.ranks.value.gt(1)))
                    assert isinstance(empty, mv.MaterializedRankingResult)
                    assert empty.ranks.to_pandas().empty and empty.values.to_pandas().empty
                else:
                    assert isinstance(value, mv.MaterializedDeviationResult)
                    assert actions == {
                        "relation.observed",
                        "relation.reference",
                        "relation.deviation",
                        "relation.score",
                        "relation.where(predicate)",
                    }
                    original_facts = dict(value.contract()._facts)
                    for name, field, fit_oracle in (
                        ("observed", value.observed, expected),
                        ("reference", value.reference, expected),
                        ("deviation", value.deviation, 0),
                        ("score", value.score, None),
                    ):
                        frame = field.to_pandas()
                        if fit_oracle is None:
                            assert frame.cell_tag.tolist() == ["undefined"]
                            assert frame.cell_reason.tolist() == ["insufficient_samples"]
                        else:
                            assert frame.cell_tag.tolist() == ["defined"]
                            assert frame.value.tolist() == [fit_oracle]
                        views[label + ":" + name] = compact(field)
                        assert run_ids(session) == prior
                    for name, field in (
                        ("observed", value.observed),
                        ("reference", value.reference),
                        ("deviation", value.deviation),
                        ("score", value.score),
                    ):
                        selected = execute(
                            label + ":" + name + ":where",
                            field.where(field.value.is_defined()),
                        )
                        frame = selected.to_pandas()
                        assert len(frame) == (0 if name == "score" else 1)
                        if name != "score":
                            assert frame.value.tolist() == [0 if name == "deviation" else expected]
                    selected_fit = execute(
                        label + ":where", value.where(value.score.value.is_defined())
                    )
                    assert isinstance(selected_fit, mv.MaterializedDeviationResult)
                    assert selected_fit.to_pandas().empty
                    selected_facts = dict(selected_fit.contract()._facts)
                    for key in (
                        "fit_scope",
                        "fit_method",
                        "original_count",
                        "original_defined",
                        "fit_partition_0",
                        "scale_branches",
                    ):
                        assert selected_facts[key] == original_facts[key]
        assert len(outputs) == 60 and len(views) == 50
        if phase == "cold":
            assert views == obj(manifest["owned_field_views"])
            assert run_ids(session) == before and kernels.call_count == 0
        else:
            assert len(run_ids(session) - before) == kernels.call_count == 60
            manifest["owned_field_K"] = outputs
            manifest["owned_field_views"] = views
            (root / "r94-domain.json").write_bytes(checked_bytes(manifest))
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "source_offline": True,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "outputs": outputs,
            "views": views,
            "consumed_K": consumed,
            "resources": 0,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(checked_bytes(run(Path(sys.argv[1]), sys.argv[2])))
