"""Independent source-offline three-axis continuations and exact cold hits."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.materialization import (
    funnel_execution,
    graph_local_execution,
    journey_execution,
)
from marivo.analysis.materialization.graph_relation import FrozenBinding, Relation
from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def recover(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    state = obj(read(root / "direct-axes.json"))
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(journey_execution, "execute", forbidden),
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        inputs: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            result = session.artifact(reference)
            assert isinstance(result, _MaterializedRead) and snapshot(result) == saved
            inputs[name] = result
        current, baseline, change = inputs["current"], inputs["baseline"], inputs["change"]
        assert isinstance(current, mv.MaterializedFunnelResult)
        assert isinstance(baseline, mv.MaterializedFunnelResult)
        assert isinstance(change, mv.MaterializedFunnelComparisonResult)
        assert run_ids(session) == before
        axes = tuple(
            ms.ref.dimension("sales.subjects." + name) for name in ("region", "code", "channel")
        )
        end = mv.step(
            participant=ms.participant_role(event=ms.ref.event("sales.end"), name="subject"),
            key="end",
        )
        operations: dict[
            str,
            mv.LogicalNumericRelation
            | mv.LogicalFunnelComparisonResult
            | mv.LogicalAttributionResult,
        ] = {
            "counts": current.read(current.reached_count),
            "loss": current.read(mv.funnel_loss_rate(step=end)),
            "comparison": current.compare(baseline),
            "joint": change.attribute(
                target=mv.funnel_loss_rate(step=end), axes=axes, mode="joint", top_k=2
            ),
            "hierarchy": change.attribute(
                target=mv.funnel_loss_rate(step=end), axes=axes, mode="hierarchy", top_k=2
            ),
        }
        outputs: dict[str, Json] = {}
        graphs: dict[str, Json] = {}
        for name, logical in operations.items():
            if phase == "cold":
                graph = obj(state["graphs"])[name]
                assert isinstance(graph, str)
                node = thaw_graph(graph)
                assert isinstance(node, MethodNode)
                with (
                    patch.object(graph_local_execution, "execute_verified_fixed", forbidden),
                    patch.object(funnel_execution, "execute", forbidden),
                ):
                    dataset = Relation(session._runtime, node, FrozenBinding(node)).execute()
                    value = session.artifact(dataset.artifact.artifact_ref)
                    assert isinstance(value, _MaterializedRead)
                    result = value
            else:
                result = logical.execute()
                graphs[name] = freeze_graph(logical._node.root)
                assert logical.execute().state.artifact_ref == result.state.artifact_ref
            outputs[name] = snapshot(result)
            frame = result.to_pandas()
            if name == "counts":
                assert frame.value.sum() == 6
            elif name == "loss":
                assert sorted(frame.value) == [0.0, 0.0, 1.0, 1.0]
            elif name in ("joint", "hierarchy"):
                assert isinstance(result, mv.MaterializedAttributionResult)
                expected_resolutions = [3] if name == "joint" else [1, 2, 3]
                assert sorted(frame.resolution.unique()) == expected_resolutions
                for resolution in expected_resolutions:
                    level = frame.loc[frame.resolution == resolution]
                    assert abs(level.contribution.sum() + 0.25) < 1e-15
                    assert abs(level.current.sum() - 0.5) < 1e-15
                    assert abs(level.baseline.sum() - 0.75) < 1e-15
                # Inactive hierarchy prefixes must not turn exact BIGINT coordinates into floats.
                full = frame.loc[frame.resolution == 3]
                codes = full["sales.subjects.code"].dropna().tolist()
                assert all(type(value) is int for value in codes)
                assert 2**53 + 1 in codes
                assert 2**53 + 2 in codes
                assert set(codes).issubset({2**53 + 1, 2**53 + 2, 2**53 + 4})
                evidence = result.evidence_digest()
                assert evidence.finding_count > 0
                page = result.findings(limit=1)
                assert result.finding(page.items[0].finding_id) == page.items[0]
                assert any(any(json.loads(mask)) for mask in frame.other_mask)
        after_hits = run_ids(session)
        new_allocation_runs = 0
        if phase == "cold":
            assert outputs == obj(state["outputs"])
            assert after_hits == before
            # A genuinely new cold continuation still uses retained three-axis parts.
            continuation = change.attribute(
                target=mv.funnel_loss_rate(step=end), axes=axes, mode="hierarchy", top_k=3
            )
            new = continuation.execute()
            assert continuation.execute().state.artifact_ref == new.state.artifact_ref
            rows = new.to_pandas()
            for resolution in (1, 2, 3):
                assert (
                    abs(rows.loc[rows.resolution == resolution, "contribution"].sum() + 0.25)
                    < 1e-15
                )
            new_allocation_runs = len(run_ids(session) - after_hits)
            assert new_allocation_runs == 1
        else:
            state["outputs"], state["graphs"] = outputs, graphs
            (root / "direct-axes.json").write_bytes(encode(state))
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        return {
            "pid": os.getpid(),
            "outputs": outputs,
            "new_hit_runs": len(after_hits - before),
            "new_allocation_runs": new_allocation_runs,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(recover(Path(sys.argv[1]), sys.argv[2])))
