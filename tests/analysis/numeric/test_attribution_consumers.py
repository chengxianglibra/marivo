"""Bounded additive and component attribution source and retained consumers."""

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.datasource.adapters import SourceSession
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case
from tests.support.json import key_json
from tests.support.source_trace import SourceTrace


@pytest.mark.slow
def test_source_and_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = "table"
    data = reference_data(backend)
    data = replace(
        data,
        columns=data.columns + ", region VARCHAR(10), null_region VARCHAR(10)",
        values=data.values.replace("00:00:00')", "00:00:00','east',NULL)"),
        clickhouse_columns=data.clickhouse_columns
        + ", region String, null_region Nullable(String)",
        rows=[{**row, "region": "east", "null_region": None} for row in data.rows],
    )
    data = replace(
        data,
        values=data.values
        + ",(9007199254740994,1,'b',3,'2026-07-31 00:00:00','east',NULL),(9007199254740995,1,'c',5,'2026-07-31 00:00:00','east',NULL)",
        rows=data.rows
        + [
            {
                "id": identity,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 7, 31, tzinfo=timezone.utc),
                "region": "east",
                "null_region": None,
            }
            for identity, tenant, amount in ((9007199254740994, "b", 3), (9007199254740995, "c", 5))
        ],
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
            + "null_region = ms.dimension_column(name='null_region', entity=facts, column='null_region')\n"
        )
        session = mv.session.get_or_create("r93-attribution", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        for metric_kind, axis_kind in (("sum", "single"), ("mean", "joint"), ("sum", "null")):
            axes: tuple[Ref[DimensionKind], ...] = (ms.ref.dimension("sales.facts.bucket"),)
            if axis_kind == "joint":
                axes += (ms.ref.dimension("sales.facts.region"),)
            elif axis_kind == "null":
                axes += (ms.ref.dimension("sales.facts.null_region"),)
            current = members.observe(
                ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                by=(
                    mv.member(),
                    *axes,
                ),
            ).rollup()
            baseline = members.observe(
                ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
                during=mv.time_scope(start="2026-07-31", end="2026-08-01"),
                by=(
                    mv.member(),
                    *axes,
                ),
            ).rollup()
            change = current.compare(baseline).absolute
            if axis_kind == "null":
                before = set((tmp_path / ".marivo").rglob("*.parquet"))
                with pytest.raises(MaterializationError) as invalid_axis:
                    change.attribute(axes=axes).execute()
                assert invalid_axis.value.expected == "cell_policy"
                assert invalid_axis.value.received == "5 violating rows"
                assert invalid_axis.value.repair is not None
                assert invalid_axis.value.run_ref is not None
                assert session.get_run(invalid_axis.value.run_ref).lifecycle == "failed"
                assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
                continue
            fixed: mv.MaterializedAttributionResult | None = None
            source = change.attribute(axes=axes).execute()
            source_trace.record(source)
            business_keys = [f"coord_{i}" for i in range(len(axes))]
            source_table = (
                mv.table(
                    current=source.current,
                    baseline=source.baseline,
                    contribution=source.contribution,
                )
                .execute()
                .to_pandas()
                .sort_values(business_keys)
                .reset_index(drop=True)
            )
            source_frame = source_table.loc[:, [*business_keys, "contribution"]].rename(
                columns={"contribution": "value"}
            )
            for column, expected in (
                (
                    "current",
                    {"a": 2, "b": 4, "c": 0} if metric_kind == "sum" else {"a": 1, "b": 2, "c": 0},
                ),
                (
                    "baseline",
                    {"a": 0, "b": 3, "c": 5}
                    if metric_kind == "sum"
                    else {"a": 0, "b": 1.5, "c": 2.5},
                ),
            ):
                assert (
                    dict(zip(source_table.coord_0, source_table[column], strict=True)) == expected
                )
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
                assert {
                    (row.coord_0, row.coord_1): row.value for row in source_frame.itertuples()
                } == {(key, "east"): value for key, value in expected_contributions.items()}

            if axis_kind == "single":

                def forbid(*args: object, **kwargs: object) -> NoReturn:
                    pytest.fail("Fixed attribution read source")

                fixed_change = change.execute()
                submissions_before = len(source_trace.native_sql)
                original_batches = SourceSession.batches
                monkeypatch.setattr(SourceSession, "batches", forbid)
                fixed = fixed_change.attribute(axes=axes).execute()
                assert (
                    fixed.contribution.to_pandas()
                    .loc[:, [*business_keys, "value"]]
                    .sort_values(business_keys)
                    .reset_index(drop=True)
                    .equals(source_frame)
                )
                assert len(source_trace.native_sql) == submissions_before
                assert session._runtime.store.resources(session._runtime.session_ref) == ()
                monkeypatch.setattr(SourceSession, "batches", original_batches)
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
                    "fixed_attribution_passed": axis_kind == "single",
                    "ordinary_source_snapshot_forbidden": True,
                    "fixed_source_reads_forbidden": axis_kind == "single",
                    "no_additional_native_submission_checked": axis_kind == "single",
                    "resources_zero_after_fixed_checked": axis_kind == "single",
                },
                fixed,
                (case.session,),
            )

            evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
            if evidence:
                assert source._dataset is not None
                Path(
                    evidence, f"supporting-c09-{metric_kind}-{axis_kind}-{backend}.json"
                ).write_text(
                    json.dumps(
                        {
                            "backend": backend,
                            "axis_kind": axis_kind,
                            "metric_kind": metric_kind,
                            "environment": case.environment,
                            "source_oracle_passed": True,
                            "fixed_attribution_passed": axis_kind == "single",
                            "fixed_source_reads_forbidden": axis_kind == "single",
                            "ordinary_source_snapshot_forbidden": True,
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
