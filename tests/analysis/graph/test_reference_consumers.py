"""Bounded public reference consumers; these calls grant no complete C08 scenario."""

import json
import os
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case
from tests.support.json import key_json
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino"]
)
@pytest.mark.parametrize("operation", ["share", "rank", "cohort"])
def test_complete_identity_reference_consumers(
    backend: str,
    operation: Literal["share", "rank", "cohort"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data(backend)
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-reference", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        values = members.observe(ms.ref.metric("sales.total"), by=(ms.ref.entity("sales.facts"),))
        assert isinstance(values, mv.LogicalNumericRelation)
        fixed_values = values.execute()
        fixed_members = fixed_values.members()
        source: mv.MaterializedNumericRelation | mv.MaterializedAnalysisDomain
        fixed: mv.MaterializedNumericRelation | mv.MaterializedAnalysisDomain
        if operation == "share":
            source = values.share_of(values.rollup()).execute()
            source_definition = source._node.definition
            reference = fixed_values.rollup().execute()
        elif operation == "rank":
            ranking = values.rank(order="descending", ties="dense").execute()
            source = ranking.ranks
            source_definition = ranking._node.definition
        else:
            source = members.cohort(values.value.gt(0), rule=mv.any_instance()).execute()
            source_definition = source._node.definition

        source_trace.record(source, definition=source_definition)
        submissions_before = len(source_trace.native_sql)

        def forbid_source_read(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed reference continuation must not read the source")

        monkeypatch.setattr(SourceSession, "batches", forbid_source_read)
        if operation == "share":
            fixed = fixed_values.share_of(reference).execute()
            expected = {
                ("a", 9007199254740992, 1): 1 / 3,
                ("a", 9007199254740993, 2): 0,
                ("b", 9007199254740993, 1): 2 / 3,
            }
        elif operation == "rank":
            fixed_ranking = fixed_values.rank(order="descending", ties="dense").execute()
            fixed = fixed_ranking.ranks
            expected = {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): 3,
                ("b", 9007199254740993, 1): 1,
            }
        else:
            fixed = fixed_members.cohort(fixed_values.value.gt(0), rule=mv.any_instance()).execute()
            expected_members = {
                ("a", 9007199254740992, 1),
                ("b", 9007199254740993, 1),
            }
        for result in (source, fixed):
            frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
            if operation == "cohort":
                assert set(frame.index) == expected_members
            else:
                assert frame.value.to_dict() == expected
                assert set(frame.cell_tag) == {"defined"}
        assert len(source_trace.native_sql) == submissions_before
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.record(
            fixed,
            definition=fixed_ranking._node.definition if operation == "rank" else None,
        )
        source_trace.save(
            f"c08-source-{backend}-{operation}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "operation": operation,
                "complete_key_types": ["string", "int64", "int64"],
                "values": [1 / 3, 0, 2 / 3]
                if operation == "share"
                else [2, 3, 1]
                if operation == "rank"
                else None,
                "member_keys": [
                    ["a", 9007199254740992, 1],
                    ["b", 9007199254740993, 1],
                ]
                if operation == "cohort"
                else None,
                "fixed_source_reads_forbidden": True,
                "no_additional_native_submission": True,
                "resources": 0,
            },
            None,
            (case.session,),
        )
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert source._dataset is not None
            Path(evidence, f"supporting-c08-{operation}-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "operation": operation,
                        "environment": case.environment,
                        "source_and_fixed_oracle_passed": True,
                        "fixed_source_reads_forbidden": True,
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                source._dataset.artifact.descriptor, source_definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded reference support; no complete C08 qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_full_time_opportunities_and_static_target(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-opportunities", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
            grain=mv.grain("day"),
        )
        values = members.each(grid).observe(
            ms.ref.metric("sales.total"), during=grid.window, by=(ms.ref.entity("sales.facts"),)
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        fixed_targets = members.execute()
        fixed_values = values.execute()
        assert len(fixed_values.to_pandas()) == 6
        expected = {("a", 9007199254740992, 1), ("b", 9007199254740993, 1)}
        source_any = members.cohort(values.value.gt(0), rule=mv.any_instance()).execute()
        source_two = members.cohort(values.value.gt(0), rule=mv.at_least(2)).execute()

        def forbid_source_read(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed full-opportunity cohort must not read sources")

        monkeypatch.setattr(SourceSession, "batches", forbid_source_read)
        fixed_any = fixed_targets.cohort(fixed_values.value.gt(0), rule=mv.any_instance()).execute()
        fixed_two = fixed_targets.cohort(fixed_values.value.gt(0), rule=mv.at_least(2)).execute()
        for result in (source_any, fixed_any):
            frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
            assert set(frame.index) == expected
        for result in (source_two, fixed_two):
            assert result.to_pandas().empty
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert source_any._dataset is not None
            Path(evidence, f"supporting-c08-full-opportunities-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "environment": case.environment,
                        "opportunity_rows": 6,
                        "any_oracle": [list(key) for key in sorted(expected)],
                        "at_least_two_oracle": [],
                        "fixed_source_reads_forbidden": True,
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                source_any._dataset.artifact.descriptor, source_any._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded full-opportunity support; no complete C08 qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize("case_kind", ["complete", "missing", "negative"])
def test_group_reference_weights_preserve_inputs(
    backend: str,
    case_kind: Literal["complete", "missing", "negative"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis.errors import AnalysisError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    data = reference_data(backend, -1 if case_kind == "negative" else 4)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-weights", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        values = members.observe(ms.ref.metric("sales.total"), by=(ms.ref.entity("sales.facts"),))
        assert isinstance(values, mv.LogicalNumericRelation)
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        groups = values.group_by(bucket).rollup()
        weights: mv.LogicalNumericRelation | mv.LogicalSelectedNumericRelation
        weights = groups.share_of(groups.rollup())
        if case_kind == "missing":
            weights = weights.where(weights.value.gt(0.5))
        reference = mv.reference_weights(
            weights, strata=(bucket,), unit=ms.ref.entity("sales.facts")
        )
        fixed_groups = groups.execute()
        fixed_weights = weights.execute()
        fixed_bucket = bucket.execute()
        fixed_reference = mv.reference_weights(
            fixed_weights, strata=(fixed_bucket,), unit=ms.ref.entity("sales.facts")
        )
        before = set((tmp_path / ".marivo").rglob("*.parquet"))
        if case_kind == "complete":
            source = groups.standardize(reference=reference).execute()
            expected = float(Fraction(2) * Fraction(1 / 3) + Fraction(4) * Fraction(2 / 3))
            assert source.to_pandas().value.tolist() == [expected]
        else:
            match = "missing.*strata|nonnegative"
            with pytest.raises(AnalysisError, match=match):
                groups.standardize(reference=reference).execute()
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before

        def forbid_source_read(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed standardization must not read sources")

        monkeypatch.setattr(SourceSession, "batches", forbid_source_read)
        if case_kind == "complete":
            fixed = fixed_groups.standardize(reference=fixed_reference).execute()
            assert fixed.to_pandas().value.tolist() == [expected]
        else:
            with pytest.raises(AnalysisError, match=match):
                fixed_groups.standardize(reference=fixed_reference).execute()
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"supporting-c08-group-weights-{case_kind}-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "environment": case.environment,
                        "case_kind": case_kind,
                        "source_and_fixed_checks_passed": True,
                        "fixed_source_reads_forbidden": True,
                        "normalization": False,
                        "expected": expected if case_kind == "complete" else match,
                        "failure_publication_atomic": case_kind != "complete",
                        "boundary": "Bounded group-reference support; no complete C08 qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize("operation", ["penetration", "display"])
def test_penetration_and_terminal_display(
    backend: str,
    operation: Literal["penetration", "display"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-next", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        values = members.observe(ms.ref.metric("sales.total"), by=(ms.ref.entity("sales.facts"),))
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(values, mv.LogicalNumericRelation)
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        source: mv.MaterializedNumericRelation | mv.MaterializedTable
        fixed: mv.MaterializedNumericRelation | mv.MaterializedTable
        fixed_values = values.execute()
        fixed_bucket = bucket.execute()
        fixed_members = fixed_values.members()
        if operation == "penetration":
            selected = bucket.where(bucket.value.eq("a")).members()
            source = selected.penetration_in(members).execute()
            assert source.to_pandas().value.tolist() == [float(Fraction(2, 3))]
            empty_source = bucket.where(bucket.value.eq("absent")).members()
            empty_result = empty_source.penetration_in(empty_source).execute().to_pandas()
            assert empty_result.cell_tag.tolist() == ["undefined"]
            assert empty_result.cell_reason.tolist() == ["empty_reference"]
        else:
            ranking = values.rank(order="descending", ties="dense").execute()
            top = ranking.limit(2).execute()
            source = mv.table(amount=top.values, rank=top.ranks).execute()
            assert source.to_pandas()["amount"].tolist() == [2, 4]
            assert source.to_pandas()["rank"].tolist() == [2, 1]

        def forbid(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed continuation read source")

        monkeypatch.setattr(SourceSession, "batches", forbid)
        if operation == "penetration":
            selected = fixed_bucket.where(fixed_bucket.value.eq("a")).members()
            fixed = selected.penetration_in(fixed_members).execute()
            assert fixed.to_pandas().value.tolist() == [float(Fraction(2, 3))]
            empty = fixed_bucket.where(fixed_bucket.value.eq("absent")).members()
            result = empty.penetration_in(empty).execute().to_pandas()
            assert result.cell_tag.tolist() == ["undefined"]
            assert result.cell_reason.tolist() == ["empty_reference"]
        else:
            top = fixed_values.rank(order="descending", ties="dense").execute().limit(2).execute()
            fixed = mv.table(amount=top.values, rank=top.ranks).execute()
            assert fixed.to_pandas().equals(source.to_pandas())
