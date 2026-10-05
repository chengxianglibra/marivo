"""Public C15 source realization, explicit sharing and fixed exact-hit probes."""

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.r9_source_cases import source_case
from tests.r93_source_trace import SourceTrace
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_reference_consumers import _data


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_c15_public_source_reexecution_and_fixed_hit(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = _data(backend)
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-execution", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        values = members.observe(ms.ref.metric("sales.total"))
        independent = members.observe(ms.ref.metric("sales.total"))
        assert isinstance(values, mv.LogicalNumericRelation)
        assert isinstance(independent, mv.LogicalNumericRelation)
        shared = values.ratio(values)
        separate = values.ratio(independent)
        shared_root = shared._node.definition
        assert shared_root.inputs[0].node is shared_root.inputs[1].node
        assert (
            separate._node.definition.inputs[0].node is not separate._node.definition.inputs[1].node
        )
        assert len(topology(shared_root)) < len(topology(separate._node.definition))
        recovered_root = thaw_graph(freeze_graph(shared_root))
        assert isinstance(recovered_root, MethodNode)
        assert recovered_root.inputs[0].node is recovered_root.inputs[1].node

        captures: list[mv.MaterializedNumericRelation] = []
        counts: list[int] = []
        for _ in range(2):
            before = len(r93_source_trace.native_sql)
            captured = values.execute()
            assert isinstance(captured, mv.MaterializedNumericRelation)
            assert len(r93_source_trace.native_sql) > before
            counts.append(len(r93_source_trace.native_sql) - before)
            frame = captured.to_pandas().set_index(["member", "coord_0", "coord_1"])
            assert frame.value.to_dict() == {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): 0,
                ("b", 9007199254740993, 1): 4,
            }
            assert set(frame.cell_tag) == {"defined"}
            r93_source_trace.record(captured)
            captures.append(captured)
        first, second = captures
        assert first.state.artifact_ref != second.state.artifact_ref
        assert first._dataset is not None and second._dataset is not None
        first_record, second_record = first._dataset.artifact, second._dataset.artifact
        assert first_record.producing_run_ref != second_record.producing_run_ref
        shared_result = shared.execute()
        shared_frame = shared_result.to_pandas()
        assert shared_frame.cell_tag.tolist() == ["defined", "undefined", "defined"]
        assert shared_frame.loc[shared_frame.cell_tag == "defined", "value"].tolist() == [1, 1]
        r93_source_trace.record(shared_result)

        before_fixed = len(r93_source_trace.native_sql)

        def forbidden(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("C15 fixed continuation opened a business source")

        monkeypatch.setattr(SourceSession, "batches", forbidden)
        restored = session.artifact(first.state.artifact_ref)
        assert isinstance(restored, mv.MaterializedNumericRelation)
        assert restored.to_pandas().equals(first.to_pandas())
        continuation = restored.where(restored.value.is_defined())
        fixed = continuation.execute()
        assert fixed.to_pandas().equals(first.to_pandas())
        assert fixed._dataset is not None
        fixed_record = fixed._dataset.artifact
        hit = continuation.execute()
        assert hit._dataset is not None
        assert hit.state.artifact_ref == fixed.state.artifact_ref
        assert hit._dataset.artifact.producing_run_ref == fixed_record.producing_run_ref
        assert len(r93_source_trace.native_sql) == before_fixed
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c15-source-{backend}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 0, 4],
                "complete_identity_types": ["string", "int64", "int64"],
                "source_native_counts": counts,
                "source_artifacts_distinct": True,
                "source_runs_distinct": True,
                "shared_ratio_tags": ["defined", "undefined", "defined"],
                "shared_ratio_defined_values": [1, 1],
                "shared_frozen_node_preserved": True,
                "independent_equal_nodes_distinct": True,
                "independent_work_runtime_verified": False,
                "fixed_exact_artifact_hit": True,
                "fixed_exact_run_hit": True,
                "fixed_additional_native_submissions": 0,
                "resources": 0,
            },
            fixed,
            (case.session,),
        )


@pytest.mark.runtime
def test_c15_independent_equal_nodes_execute_distinct_source_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    """Execute the shared source scheduler risk once on a real DuckDB source."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = _data("duckdb")
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _author_c05_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-independent-work", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        first = members.observe(ms.ref.metric("sales.total"))
        second = members.observe(ms.ref.metric("sales.total"))
        assert isinstance(first, mv.LogicalNumericRelation)
        assert isinstance(second, mv.LogicalNumericRelation)
        assert first._node.definition.fingerprint == second._node.definition.fingerprint
        assert first._node.definition.identity != second._node.definition.identity
        shared, independent = first.ratio(first), first.ratio(second)
        stage_counts: list[int] = []
        native_counts: list[int] = []
        for relation in (shared, independent):
            before_owners = len(r93_source_trace.owners)
            before_native = len(r93_source_trace.native_sql)
            result = relation.execute()
            frame = result.to_pandas()
            assert frame.cell_tag.tolist() == ["defined", "undefined", "defined"]
            assert frame.loc[frame.cell_tag == "defined", "value"].tolist() == [1, 1]
            owners = r93_source_trace.owners[before_owners:]
            assert owners and all(owner._closed for owner in owners)
            submissions = [item for owner in owners for item in owner.submissions]
            stage_counts.append(sum(item.purpose == "analysis.graph.stage" for item in submissions))
            native_counts.append(len(r93_source_trace.native_sql) - before_native)
            assert all(item.state == "succeeded" for item in submissions)
            assert result._dataset is not None
            assert result._dataset.verified().parts
            r93_source_trace.record(result)
        assert stage_counts[0] > 0
        assert stage_counts[1] == stage_counts[0] + 1
        assert native_counts[1] > native_counts[0] > 0
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            "c15-independent-work-duckdb",
            {**case.environment, "profile": "table"},
            {"columns": data.columns, "values": data.values},
            {
                "same_definition_fingerprint": True,
                "distinct_node_identities": True,
                "stage_submissions": stage_counts,
                "native_submissions": native_counts,
                "independent_extra_observation_stages": 1,
                "ratio_tags": ["defined", "undefined", "defined"],
                "ratio_defined_values": [1, 1],
                "resources": 0,
                "boundary": "Common source stage scheduler risk on DuckDB; not six native backend executions",
            },
            None,
            (case.session,),
        )
