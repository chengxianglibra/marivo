"""Recover mean continuations without source, host resolution or reader probes."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.rules import ObserveMetric
from marivo.datasource import timezone as source_timezone
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode, read


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "timezone-recovery.json")
    identity, reference = state["session"], state["source"]
    assert isinstance(identity, str) and isinstance(reference, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(source_timezone, "probe_engine_timezone", forbidden),
        patch.object(source_timezone, "_resolve_system_timezone", forbidden),
    ):
        session = mv.session.resume(identity, by="id")
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert snapshot(value) == state["original"]
        parameters = next(
            node.parameters
            for node in topology(value._node.definition)
            if isinstance(node, MethodNode) and isinstance(node.parameters, ObserveMetric)
        )
        assert checked(parameters.temporal.model_dump(mode="json")) == state["temporal"]
        assert session.report_tz_name == "Asia/Shanghai"
        before = run_ids(session)
        result = value.rollup().execute()
        assert result.to_pandas().value.tolist() == [100 / 3]
        output = snapshot(result)
        if phase == "fixed":
            assert len(run_ids(session) - before) == 1
            state["rolled"] = output
            (root / "timezone-recovery.json").write_bytes(encode(state))
        else:
            assert phase == "cold" and run_ids(session) == before
            assert output == state["rolled"]
        assert snapshot(value) == state["original"]
        return {
            "pid": os.getpid(),
            "phase": phase,
            "source_and_timezone_forbidden": True,
            "output": output,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
