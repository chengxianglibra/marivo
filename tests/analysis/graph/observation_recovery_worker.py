"""Source-free typed observation states and cold continuation hits."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.materialization.graph_relation import FrozenBinding, Relation
from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.attribution_carrier_worker import parts
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def recover(root: Path, phase: str) -> None:
    os.chdir(root)
    state = obj(read(root / "observations.json"))
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        graphs: dict[str, Json] = {}
        outputs: dict[str, Json] = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            artifact = saved["artifact"]
            assert isinstance(artifact, str)
            fixed = session.artifact(artifact)
            assert isinstance(fixed, mv.MaterializedNumericRelation)
            assert snapshot(fixed) == saved
            if state.get("distribution") is True:
                assert parts(fixed) == obj(state["parts"])[name]
            if phase == "cold":
                graph = obj(state["graphs"])[name]
                assert isinstance(graph, str)
                node = thaw_graph(graph)
                assert isinstance(node, MethodNode)
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    dataset = Relation(session._runtime, node, FrozenBinding(node)).execute()
                    result = session.artifact(dataset.artifact.artifact_ref)
            else:
                logical = (
                    fixed.where(fixed.value.is_defined()).aggregate(mv.count())
                    if state.get("distribution") is True
                    else fixed.rollup()
                )
                result = logical.execute()
                assert logical.execute().state.artifact_ref == result.state.artifact_ref
                expected = (
                    obj(state["expected"])[name]
                    if state.get("distribution") is True
                    else 3
                    if name == "number"
                    else 2**53 + 10
                )
                assert result.to_pandas().value.tolist() == [expected]
                graphs[name] = freeze_graph(logical._node.root)
            assert isinstance(result, _MaterializedRead)
            outputs[name] = snapshot(result)
        if phase == "cold":
            assert outputs == obj(state["outputs"])
            assert run_ids(session) == before
        else:
            state["graphs"], state["outputs"] = graphs, outputs
            (root / "observations.json").write_bytes(encode(state))
        assert session._runtime.store.resources(session.id) == ()


if __name__ == "__main__":
    recover(Path(sys.argv[1]), sys.argv[2])
