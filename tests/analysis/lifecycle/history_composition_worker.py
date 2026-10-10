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
from marivo.analysis.core.graph import FixedLeaf, MethodNode
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.lifecycle.history_consumers import expected, mean, observations, transport
from tests.analysis.lifecycle.history_fixtures import build_history_public, selected
from tests.analysis.lifecycle.history_operations import artifact, operations, replay
from tests.analysis.lifecycle.history_oracle import assert_view, views
from tests.analysis.lifecycle.history_worker import forbidden

HISTORY_VIEW_TYPES = {
    "in_state": mv.MaterializedBooleanRelation,
    "distribution": mv.MaterializedStateDistributionResult,
    "transitions": mv.MaterializedTransitionSummary,
    "violations": mv.MaterializedViolationResult,
    "intervals": mv.MaterializedStateIntervalResult,
    "dwell": mv.MaterializedDwellSummary,
}


def producer(root, form):
    session, members, window, claims, rows = build_history_public(
        root, form=form, subject="c", occurrence="c"
    )
    logical = replay(session, members, window, claims)
    history = logical.execute()
    source_views = {name: op().execute() for name, op in operations(logical).items()}
    for name, result in source_views.items():
        assert_view(result, name, views(rows, "c", "c"))
    observation_name = "state_revenue"
    observation = observations(logical)[observation_name].execute()
    expected(observation_name, observation)
    manifest = {
        "session": session.id,
        "history": artifact(history),
        "views": {name: artifact(result) for name, result in source_views.items()},
        "observations": {observation_name: artifact(observation)},
        "rows": rows,
        "form": form,
    }
    (root / "a10.json").write_text(json.dumps(manifest))
    (root / "source.duckdb").unlink()
    for path in root.glob("*.parquet"):
        path.unlink()


def scalar_operations(name, result):
    """Build representative fixed continuations for the numeric result family."""
    assert isinstance(result, mv.MaterializedNumericRelation)
    defined = result.where(result.value.is_defined())
    return {
        name + ":where": result.where(result.value.is_defined()),
        name + ":aggregate": defined.aggregate(mv.count()),
        name + ":rank": result.rank(order="descending", ties="ordinal"),
        name + ":zscore": result.deviation(method="zscore"),
    }


def fixed_operations(session, manifest, *, check_refusal):
    history = session.artifact(manifest["history"])
    assert isinstance(history, mv.MaterializedHistoryResult)
    assert history.contract().actions
    assert len(repr(history).splitlines()) == 1
    pending = {"history:intervals": operations(history)["intervals"]()}
    pending["transport:completed_members"] = transport(history)["completed_members"]
    pending["duration:mean"] = mean(history)
    observation_name = "state_revenue"
    observation = session.artifact(manifest["observations"][observation_name])
    pending["observation:" + observation_name] = observation.rollup()
    if check_refusal:
        try:
            _ = selected(history, "state").observe
        except AnalysisError as error:
            assert "fixed selected members plus live Metric" in str(error)
        else:
            raise AssertionError("fixed membership admitted a live Metric")

    field_ops = {}
    distribution = session.artifact(manifest["views"]["distribution"])
    violations = session.artifact(manifest["views"]["violations"])
    intervals = session.artifact(manifest["views"]["intervals"])
    field_ops["distribution:field:known_state_count"] = distribution.known_state_count
    field_ops["distribution:field:seeded_subject_count"] = distribution.seeded_subject_count
    field_ops["violations:field:kind"] = violations.kind
    field_ops["intervals:field:start"] = intervals.start
    field_ops["intervals:field:left_clipped"] = intervals.left_clipped
    return pending, field_ops


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
        session = Session._from_runtime(DatasetRuntime(SessionStore(root), manifest["session"]))
        expected_views = views(manifest["rows"], "c", "c")
        if phase == "cold":
            for name, reference in manifest["views"].items():
                result = session.artifact(reference)
                assert isinstance(result, HISTORY_VIEW_TYPES[name])
                assert_view(result, name, expected_views)
                assert result.contract().actions
                assert len(repr(result).splitlines()) == 1
            observation = session.artifact(manifest["observations"]["state_revenue"])
            assert isinstance(observation, mv.MaterializedNumericRelation)
            expected("state_revenue", observation)
            assert observation.contract().actions
        pending, field_ops = fixed_operations(session, manifest, check_refusal=phase == "continue")
        refs = {}
        rejected = {}
        refusal_proofs = {}

        def execute(name, operation):
            counts = None
            if name.endswith(":correlate"):
                with session._runtime.store._read() as connection:
                    counts = tuple(
                        int(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
                        for table in ("dataset_artifacts", "dataset_evidence", "findings")
                    )
            try:
                result = operation.execute()
            except StatisticalRelationError as error:
                if not name.endswith(":correlate") or error.code != "r8.no_valid_candidate":
                    raise
                assert isinstance(operation, mv.LogicalAssociationResult)
                node = operation._node.root
                assert isinstance(node, MethodNode)
                original = node.inputs[0].node
                assert isinstance(original, FixedLeaf)
                value = session.artifact(original.artifact)
                assert isinstance(value, mv.MaterializedNumericRelation)
                frame = value.to_pandas()
                assert set(frame.cell_tag) == {"defined"}
                assert frame.value.nunique() <= 1
                assert error.received is not None
                assert "constant_" in error.received
                assert error.expected == "at least one valid lag per pair/series"
                assert error.repair is not None
                assert error.repair.action
                assert session._runtime.store.resources(session.id) == ()
                with session._runtime.store._read() as connection:
                    assert counts == tuple(
                        int(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
                        for table in ("dataset_artifacts", "dataset_evidence", "findings")
                    )
                assert counts is not None
                rejected[name] = error.code
                refusal_proofs[name] = {
                    "code": error.code,
                    "expected": error.expected,
                    "received": error.received,
                    "repair": error.repair.action,
                    "unchanged_artifact_evidence_findings_counts": list(counts),
                    "resources": 0,
                }
                return None
            result.to_pandas()
            if name.startswith("history:"):
                view_name = name.split(":")[1]
                if phase in ("continue", "cold"):
                    assert_view(result, view_name, expected_views)
                else:
                    assert isinstance(result, HISTORY_VIEW_TYPES[view_name])
            elif name.startswith("transport:") or name.startswith("observation:"):
                expected(name.split(":")[1], result)
            elif name == "duration:mean":
                expected("mean", result)
            refs[name] = artifact(result)
            result_types[name] = type(result).__name__
            return result

        result_types = {}
        if phase == "cold":
            pending = {"history:intervals": pending["history:intervals"]}
            field_ops = {}

        results = {}
        for name, operation in pending.items():
            results[name] = execute(name, operation)
        for name, operation in field_ops.items():
            results[name] = execute(name, operation)

        numeric = results.get("distribution:field:known_state_count")
        if numeric is not None:
            scalar = scalar_operations("distribution.known_state_count", numeric)
            for name, operation in scalar.items():
                execute(name, operation)
            if phase == "continue":
                seeded = results["distribution:field:seeded_subject_count"]
                correlation_input = execute(
                    "distribution.seeded_subject_count:correlate_input",
                    seeded.deviation(method="zscore").deviation,
                )
                assert isinstance(correlation_input, mv.MaterializedNumericRelation)
                execute(
                    "distribution.seeded_subject_count:correlate",
                    seeded.correlate(correlation_input),
                )
        if phase == "continue":
            manifest["fixed"] = refs
            manifest["executed_K"] = sorted(refs.keys() | rejected.keys())
            manifest["rejected_K"] = rejected
            (root / "a10.json").write_text(json.dumps(manifest))
        else:
            assert refs == {name: manifest["fixed"][name] for name in refs}
            assert not rejected
            for reference in refs.values():
                session.artifact(reference).to_pandas()
    print(
        json.dumps(
            {
                "accepted": phase,
                "outputs": len(refs),
                "K": len(refs) + len(rejected),
                "rejected_K": rejected,
                "refusal_proofs": refusal_proofs,
                "result_types": result_types,
            }
        )
    )


if __name__ == "__main__":
    phase, root = sys.argv[1], Path(sys.argv[2])
    if phase == "producer":
        producer(root, sys.argv[3])
        print(json.dumps({"accepted": "producer"}))
    else:
        offline(root, phase)
