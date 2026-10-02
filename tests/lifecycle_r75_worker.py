"""Recover published canonical History in a fresh process with source access poisoned."""

import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import duckdb

import marivo.analysis as mv
from marivo.analysis.materialization import history_execution
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods import history
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject


def forbidden(*args, **kwargs):
    raise AssertionError("cold History recovery opened source/current Semantic or replayed origin")


def render(call):
    output = StringIO()
    with redirect_stdout(output):
        call()
    return output.getvalue()


root = Path(sys.argv[1])
payload = json.loads((root / "r75.json").read_text())
duckdb.connect = forbidden
SourceSession.__enter__ = forbidden
SemanticProject.load = forbidden
history.replay = forbidden
history_execution.replay = forbidden
store = SessionStore._graph_store(root)
runtime = DatasetRuntime(store, payload["session"])
session = Session._from_runtime(runtime)
before = session.runs().items
if payload.get("reject"):
    from marivo.analysis.errors import AnalysisError

    try:
        session.artifact(payload["artifact"])
    except AnalysisError:
        assert session.runs().items == before
        assert not runtime.statistics.statements
        print(json.dumps({"accepted": True, "rejected": True}))
        raise SystemExit(0) from None
    raise AssertionError("damaged canonical History was accepted")
result = session.artifact(payload["artifact"])
assert isinstance(result, mv.MaterializedHistoryResult)
verified = result._dataset.verified()
assert verified.parts[0].table.to_pylist() == payload["part"]
assert result.to_pandas().to_json() == payload["frame"]
assert render(result.contract().show) == payload["contract"]
assert render(result.show) == payload["show"]
assert result.evidence_digest().finding_count == 0
assert result.findings().items == ()
assert not runtime.statistics.statements
assert session.runs().items == before
print(json.dumps({"accepted": True, "cell": payload["cell"]}))
