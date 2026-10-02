"""A10 producer, source-free public continuation and cold exact-hit processes."""

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.history_rules import FIELDS
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import DurationType
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.history_r76_consumers import expected, mean, observations, transport
from tests.history_r76_fixtures import build_history_public, selected
from tests.history_r76_oracle import assert_view, views
from tests.history_r76_worker import forbidden
from tests.test_analysis_history_r76 import artifact, operations, replay


def producer(root, form):
    session, members, window, claims, rows = build_history_public(
        root, form=form, subject="c", occurrence="c"
    )
    logical = replay(session, members, window, claims)
    history = logical.execute()
    source_views = {name: op().execute() for name, op in operations(logical).items()}
    for name, result in source_views.items():
        assert_view(result, name, views(rows, "c", "c"))
    observed = {name: op.execute() for name, op in observations(logical).items()}
    for name, result in observed.items():
        expected(name, result)
    manifest = {
        "session": session.id,
        "history": artifact(history),
        "views": {name: artifact(result) for name, result in source_views.items()},
        "observations": {name: artifact(result) for name, result in observed.items()},
        "rows": rows,
        "form": form,
    }
    (root / "a10.json").write_text(json.dumps(manifest))
    (root / "source.duckdb").unlink()
    for path in root.glob("*.parquet"):
        path.unlink()


def scalar_operations(name, result, consumed):
    operations = {}
    for action in result.contract().actions:
        call = action.call
        consumed.add(name + ":" + call)
        if call == "relation.where(predicate)":
            operations[name + ":where"] = result.where(result.value.is_defined())
        elif call == "relation.summarize(method)":
            defined = result.where(result.value.is_defined())
            method = (
                mv.mean() if isinstance(result._node.root.value_type, DurationType) else mv.count()
            )
            operations[name + ":summarize"] = defined.summarize(method)
        elif call == "relation.members()":
            operations[name + ":members"] = result.members()
        elif call == "relation.group_by(*keys)":
            operations[name + ":group_by"] = result.group_by().summarize(mv.count())
        elif call == "relation.rank(order=order, ties=ties)":
            operations[name + ":rank"] = result.rank(order="descending", ties="ordinal")
        else:
            raise AssertionError("unexecuted fixed scalar K: " + name + ":" + call)
    return operations


def fixed_operations(session, manifest):
    history = session.artifact(manifest["history"])
    consumed = {"history:" + a.call for a in history.contract().actions}
    pending = {"history:" + name: op() for name, op in operations(history).items()}
    pending.update({"transport:" + name: op for name, op in transport(history).items()})
    pending["duration:mean"] = mean(history)
    for name, reference in manifest["observations"].items():
        result = session.artifact(reference)
        pending["observation:" + name] = result.rollup()
        try:
            _ = selected(history, name.split("_")[0]).observe
        except AnalysisError as error:
            assert "fixed selected members plus live Metric" in str(error)
        else:
            raise AssertionError("fixed membership admitted a live Metric")
    for name, reference in manifest["views"].items():
        result = session.artifact(reference)
        result.show()
        result.contract().show()
        assert "id=" in repr(result)
        assert len(repr(result).splitlines()) == 1
        if name == "in_state":
            pending.update(scalar_operations(name, result, consumed))
            continue
        fields = FIELDS[name]
        for field in fields:
            logical = getattr(result, field)
            consumed.add(name + ":relation." + field)
            pending[name + ":field:" + field] = logical
            fixed_field = logical.execute()
            pending.update(scalar_operations(name + "." + field, fixed_field, consumed))
        predicate = getattr(result, fields[0]).value.is_defined()
        pending[name + ":where"] = result.where(predicate)
        consumed.add(name + ":relation.where(predicate)")
        if name in ("intervals", "violations"):
            binding = result.subjects()
            pending[name + ":members"] = result.members(through=binding)
            consumed.add(name + ":relation.subjects()")
            consumed.add(name + ":relation.members(through=through)")
        assert {name + ":" + a.call for a in result.contract().actions} <= consumed
    return pending, sorted(consumed)


def offline(root, phase):
    manifest = json.loads((root / "a10.json").read_text())
    with ExitStack() as stack:
        for owner, method in (
            (SemanticProject, "load"),
            (SourceSession, "__enter__"),
            (SourceSession, "batches"),
            (duckdb, "connect"),
            (ibis.duckdb, "connect"),
            (ms, "load"),
        ):
            stack.enter_context(patch.object(owner, method, forbidden))
        stack.enter_context(
            patch("marivo.analysis.materialization.history_execution.execute", forbidden)
        )
        session = Session._from_runtime(
            DatasetRuntime(SessionStore._graph_store(root), manifest["session"])
        )
        expected_views = views(manifest["rows"], "c", "c")
        for name, reference in manifest["views"].items():
            assert_view(session.artifact(reference), name, expected_views)
        pending, consumed = fixed_operations(session, manifest)
        refs = {}
        for name, operation in pending.items():
            result = operation.execute()
            result.to_pandas()
            if name.startswith("history:"):
                assert_view(result, name.split(":")[1], expected_views)
            elif name.startswith("transport:") or name.startswith("observation:"):
                expected(name.split(":")[1], result)
            elif name == "duration:mean":
                expected("mean", result)
            refs[name] = artifact(result)
        if phase == "continue":
            manifest["fixed"] = refs
            manifest["executed_K"] = consumed
            (root / "a10.json").write_text(json.dumps(manifest))
        else:
            assert refs == manifest["fixed"]
            assert consumed == manifest["executed_K"]
            for reference in refs.values():
                session.artifact(reference).to_pandas()
    print(json.dumps({"accepted": phase, "outputs": len(refs), "K": len(consumed)}))


if __name__ == "__main__":
    phase, root = sys.argv[1], Path(sys.argv[2])
    if phase == "producer":
        producer(root, sys.argv[3])
        print(json.dumps({"accepted": "producer"}))
    else:
        offline(root, phase)
