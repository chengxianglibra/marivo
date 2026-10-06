"""Native C07 composite-key consumers and source-free fixed continuations."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
@pytest.mark.parametrize("operation", ("nested", "union", "ratio"))
def test_complete_keys_source_and_fixed(
    backend: str,
    operation: Literal["nested", "union", "ratio"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data(backend)
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-comparison", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        metric = ms.ref.metric("sales.total")

        def observe(month: int) -> mv.LogicalNumericRelation:
            result = members.observe(
                metric,
                during=mv.time_scope(
                    start=f"2026-{month:02d}-01",
                    end=f"2026-{month + 1:02d}-01",
                ),
            )
            assert isinstance(result, mv.LogicalNumericRelation)
            return result

        current, baseline, earlier = observe(8), observe(7), observe(6)
        source: mv.MaterializedNumericRelation | mv.MaterializedDifferenceRelation
        fixed_logical: mv.LogicalNumericRelation | mv.LogicalDifferenceRelation
        if operation == "ratio":
            logical = current.ratio(current)
            source = logical.execute()
            endpoint = current.execute()
            fixed_logical = endpoint.ratio(endpoint)
        elif operation == "nested":
            first, second = current.compare(baseline), baseline.compare(earlier)
            source = first.compare(second).execute()
            first_fixed, second_fixed = first.execute(), second.execute()
            fixed_logical = first_fixed.compare(second_fixed)
        else:
            design = mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))
            left = current.where(current.value.gt(0))
            right = baseline.where(baseline.value.gt(0))
            source = left.compare(right, design=design).execute()
            left_fixed, right_fixed = left.execute(), right.execute()
            fixed_logical = left_fixed.compare(right_fixed, design=design)
        source_trace.record(source)
        submissions_before = len(source_trace.native_sql)

        def forbid_source(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed C07 continuation must not read a source")

        monkeypatch.setattr(SourceSession, "batches", forbid_source)
        fixed = fixed_logical.execute()
        assert len(source_trace.native_sql) == submissions_before
        complete = {
            ("a", 9007199254740992, 1),
            ("a", 9007199254740993, 2),
            ("b", 9007199254740993, 1),
        }
        defined = {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 0,
            ("b", 9007199254740993, 1): 4,
        }
        for result in (source, fixed):
            frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
            if operation == "nested":
                assert set(frame.index) == complete
                assert frame.value.to_dict() == defined
                assert set(frame.cell_tag) == {"defined"}
                selected = result.where(result.value.gt(0)).members().execute()
                assert set(
                    selected.to_pandas().set_index(["member", "coord_0", "coord_1"]).index
                ) == {key for key, value in defined.items() if value > 0}
            elif operation == "union":
                assert set(frame.index) == {key for key, value in defined.items() if value > 0}
                assert set(frame.cell_tag) == {"undefined"}
                assert set(frame.cell_reason) == {"missing_side"}
                assert frame.value.isna().all()
            else:
                assert isinstance(result, mv.MaterializedNumericRelation)
                assert set(frame.index) == complete
                for key, value in defined.items():
                    assert frame.loc[key, "cell_tag"] == ("defined" if value else "undefined")
                    if value:
                        assert frame.loc[key, "value"] == 1.0
                    else:
                        assert frame.loc[key, "cell_reason"] == "zero_denominator"
                with pytest.raises(AnalysisError):
                    result.rollup()
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c07-source-{backend}-{operation}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "operation": operation,
                "complete_key_types": ["string", "int64", "int64"],
                "values": [2, 0, 4]
                if operation == "nested"
                else [1, None, 1]
                if operation == "ratio"
                else [None, None],
                "cell_reason": "missing_side"
                if operation == "union"
                else "zero_denominator"
                if operation == "ratio"
                else None,
                "fixed_source_reads_forbidden": True,
                "no_additional_native_submission": True,
                "resources": 0,
            },
            fixed,
            (case.session,),
        )
