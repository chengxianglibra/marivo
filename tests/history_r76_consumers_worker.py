"""Cold, source-poisoned History transport, F13 and Duration qualification."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.history_r76_consumers import expected, mean, transport
from tests.history_r76_worker import forbidden


def run(root):
    manifest = json.loads((root / "consumers.json").read_text())
    with (
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "batches", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ms, "load", forbidden),
        patch("marivo.analysis.materialization.history_execution.execute", forbidden),
    ):
        session = Session._from_runtime(
            DatasetRuntime(SessionStore._graph_store(root), manifest["session"])
        )
        history = session.artifact(manifest["history"])
        operations = {
            name: operation
            for name, operation in {**transport(history), "mean": mean(history)}.items()
            if name in manifest["fixed"]
        }
        for name, logical in operations.items():
            result = logical.execute()
            expected(name, result)
            assert result._dataset.state.artifact_ref.ref == manifest["fixed"][name]
        for name, reference in manifest["observations"].items():
            result = session.artifact(reference).rollup().execute()
            expected(name, result)
            assert result._dataset.state.artifact_ref.ref == manifest["fixed"][name]
        for name, reference in manifest["source"].items():
            expected(name, session.artifact(reference))
    print(json.dumps({"accepted": manifest["cells"]}))


if __name__ == "__main__":
    run(Path(sys.argv[1]))
