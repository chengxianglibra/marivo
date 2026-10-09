"""Bounded additive and component attribution source and retained consumers."""

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.materialization.graph_snapshot import document_json, graph_document
from marivo.datasource.adapters import SourceSession
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case
from tests.support.json import key_json
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend,axis_kind",
    [
        (backend, "single")
        for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
    ]
    + [("duckdb", "joint"), ("sqlite", "null")],
)
@pytest.mark.parametrize("metric_kind", ["sum", "mean"])
def test_source_and_retained(
    backend: str,
    metric_kind: Literal["sum", "mean"],
    axis_kind: Literal["single", "joint", "null"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    data = reference_data(backend)
    data = replace(
        data,
        columns=data.columns + ", region VARCHAR(10)",
        values=data.values.replace("00:00:00')", "00:00:00','east')"),
        clickhouse_columns=data.clickhouse_columns + ", region String",
        rows=[{**row, "region": "east"} for row in data.rows],
    )
    prefix = "TIMESTAMP " if backend == "trino" else ""
    data = replace(
        data,
        values=data.values
        + f",(9007199254740994,1,'b',3,{prefix}'2026-07-31 00:00:00','east'),(9007199254740995,1,'c',5,{prefix}'2026-07-31 00:00:00','east')",
        rows=data.rows
        + [
            {
                "id": identity,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 7, 31, tzinfo=timezone.utc),
                "region": "east",
            }
            for identity, tenant, amount in ((9007199254740994, "b", 3), (9007199254740995, "c", 5))
        ],
    )
    if axis_kind == "null":
        data = replace(
            data,
            values=data.values.replace("'east'", "NULL"),
            rows=[{**row, "region": None} for row in data.rows],
        )

    def forbid_snapshot(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Ordinary C09 reads must not claim a snapshot capture")

    monkeypatch.setattr("marivo.datasource.domain_snapshot.capture", forbid_snapshot)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        models = tmp_path / "models/semantic/sales/models.py"
        models.write_text(
            models.read_text()
            + "\nregion = ms.dimension_column(name='region', entity=facts, column='region')\n"
        )
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("r93-attribution", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        axes: tuple[Ref[DimensionKind], ...] = (ms.ref.dimension("sales.facts.bucket"),)
        if axis_kind in ("joint", "null"):
            axes += (ms.ref.dimension("sales.facts.region"),)
        current = members.observe(
            ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            by=(
                ms.ref.entity("sales.facts"),
                *axes,
            ),
        ).rollup()
        baseline = members.observe(
            ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
            during=mv.time_scope(start="2026-07-31", end="2026-08-01"),
            by=(
                ms.ref.entity("sales.facts"),
                *axes,
            ),
        ).rollup()
        change = current.compare(baseline)
        if axis_kind == "null":
            before = set((tmp_path / ".marivo").rglob("*.parquet"))
            with pytest.raises(DomainPreparationError, match="coordinate is not a Defined string"):
                change.attribute(axes=axes).execute()
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
            return
        fixed_change = change.execute()
        source = change.attribute(axes=axes).execute()
        source_trace.record(source)
        source_frame = source.contribution.to_pandas()
        assert fixed_change.to_pandas().value.tolist() == ([-2] if metric_kind == "sum" else [-1])
        for view, expected in (
            (
                source.current,
                {"a": 2, "b": 4, "c": 0} if metric_kind == "sum" else {"a": 1, "b": 2, "c": 0},
            ),
            (
                source.baseline,
                {"a": 0, "b": 3, "c": 5} if metric_kind == "sum" else {"a": 0, "b": 1.5, "c": 2.5},
            ),
        ):
            frame = view.to_pandas()
            assert dict(zip(frame.coord_0, frame.value, strict=True)) == expected
        expected_contributions = (
            {"a": 2, "b": 1, "c": -5} if metric_kind == "sum" else {"a": 1, "b": 0.5, "c": -2.5}
        )
        assert source_frame.value.dtype.kind == ("i" if metric_kind == "sum" else "f")
        if axis_kind == "single":
            assert (
                dict(zip(source_frame.coord_0, source_frame.value, strict=True))
                == expected_contributions
            )
        else:
            assert {(row.coord_0, row.coord_1): row.value for row in source_frame.itertuples()} == {
                (key, "east"): value for key, value in expected_contributions.items()
            }

        def forbid(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed attribution read source")

        submissions_before = len(source_trace.native_sql)
        monkeypatch.setattr(SourceSession, "batches", forbid)
        fixed = fixed_change.attribute(axes=axes).execute()
        assert fixed.contribution.to_pandas().equals(source_frame)

        top = fixed_change.attribute(axes=axes, top_k=1).execute()
        top_frame = top.contribution.to_pandas()
        assert sorted(top_frame.value.tolist()) == (
            [-3, 1] if metric_kind == "sum" else [-1.5, 0.5]
        )
        retained = top_frame[top_frame.coord_0 == "b"]
        assert retained.value.tolist() == ([1] if metric_kind == "sum" else [0.5])
        assert top_frame[top_frame.coord_0.isna()].value.tolist() == (
            [-3] if metric_kind == "sum" else [-1.5]
        )
        selection = fixed.where(fixed.contribution.value.gt(0))
        if backend == "duckdb" and axis_kind == "joint":
            assert (
                len(document_json(graph_document(selection._node.definition)).encode())
                < 1024 * 1024
            )
        selected = selection.execute()
        assert sorted(selected.contribution.to_pandas().value.tolist()) == (
            [1, 2] if metric_kind == "sum" else [0.5, 1]
        )
        assert dict(selected.contract()._facts)["complete_partition"] == "False"
        if axis_kind == "joint":
            hierarchy = fixed_change.attribute(axes=axes, mode="hierarchy").execute()
            hierarchy_frame = hierarchy.contribution.to_pandas()
            assert sorted(hierarchy_frame.value.tolist()) == sorted(
                list(expected_contributions.values()) * 2
            )

        assert len(source_trace.native_sql) == submissions_before
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        source_trace.save(
            f"c09-source-{backend}-{metric_kind}-{axis_kind}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "metric_kind": metric_kind,
                "axis_kind": axis_kind,
                "input_key_types": ["string", "int64", "int64"],
                "current": [2, 4, 0] if metric_kind == "sum" else [1, 2, 0],
                "baseline": [0, 3, 5] if metric_kind == "sum" else [0, 1.5, 2.5],
                "contributions": [2, 1, -5] if metric_kind == "sum" else [1, 0.5, -2.5],
                "top_k_other": [-3, 1] if metric_kind == "sum" else [-1.5, 0.5],
                "selected": [1, 2] if metric_kind == "sum" else [0.5, 1],
                "hierarchy": sorted(list(expected_contributions.values()) * 2)
                if axis_kind == "joint"
                else None,
                "ordinary_source_snapshot_forbidden": True,
                "fixed_source_reads_forbidden": True,
                "no_additional_native_submission": True,
                "resources": 0,
            },
            fixed,
            (case.session,),
        )

        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert source._dataset is not None
            Path(evidence, f"supporting-c09-{metric_kind}-{axis_kind}-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "axis_kind": axis_kind,
                        "metric_kind": metric_kind,
                        "environment": case.environment,
                        "source_and_fixed_oracle_passed": True,
                        "fixed_source_reads_forbidden": True,
                        "ordinary_source_snapshot_forbidden": True,
                        "top_k_other_and_selection_passed": True,
                        "hierarchy_passed": axis_kind == "joint",
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                source._dataset.artifact.descriptor, source._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded int64 original sum/mean attribution support; no complete C09 qualification",
                    },
                    sort_keys=True,
                )
            )
