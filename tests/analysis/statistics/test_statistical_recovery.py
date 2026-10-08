"""Bind real native statistical producers to independent fixed and cold kernels."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods.physical import ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.runs_physical import implementations
from marivo.analysis.methods.semantics import MethodKey
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.recovery_worker import snapshot as input_snapshot
from tests.analysis.statistics.journeys import check, graphs
from tests.analysis.statistics.statistical_recovery_worker import method_proof, snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, encode, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


def test_remote_group_runs_declarations_are_exact() -> None:
    grouped = [
        item
        for item in implementations(MethodKey("time.runs"))
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend != "duckdb"
        and item.key.input_domains == ("group",)
    ]
    assert len(grouped) == 5
    assert {
        item.key.shape.backend for item in grouped if isinstance(item.key.shape, SourceShape)
    } == {"sqlite", "postgres", "mysql", "trino", "clickhouse"}
    for item in grouped:
        assert isinstance(item.key.shape, SourceShape)
        assert item.key.shape.form == "table" and item.key.shape.table_kind == "native"
        assert item.key.shape.time == TimeShape("instant", "us", "UTC")
        assert item.key.input_types == (ScalarType("int64"),)
        assert item.key.route == "ibis_python"


def source_data(backend: str) -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": identity,
            "revision": revision,
            "tenant": tenant,
            "amount": amount,
            "happened": datetime(2026, 8, day, tzinfo=timezone.utc),
        }
        for identity, revision, tenant, amount, day in (
            (9007199254740992, 1, "a", 1, 1),
            (9007199254740993, 2, "a", 2, 2),
            (9007199254740993, 1, "b", 7, 3),
        )
    ]

    def literal(value: object) -> str:
        if isinstance(value, datetime):
            text = value.strftime("%Y-%m-%d %H:%M:%S")
            return f"TIMESTAMP '{text}'" if backend == "trino" else repr(text)
        return repr(value) if isinstance(value, str) else str(value)

    return SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join("(" + ",".join(literal(value) for value in row.values()) + ")" for row in rows),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        rows,
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_nine_methods_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, source_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        model = tmp_path / "models/semantic/sales/models.py"
        with model.open("a") as stream:
            stream.write(
                "copy = ms.measure_column(name='copy', entity=facts, column='amount', "
                "additivity=ms.additive_all())\n"
                "copy_total = ms.aggregate(name='copy_total', measure=copy, "
                "agg='sum', empty=ms.empty.zero())\n"
            )
        session = mv.session.get_or_create("r94-statistical", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        window = mv.time_scope(start="2026-08-01", end="2026-08-04")
        entity = members.observe(
            ms.ref.metric("sales.total"), during=window, by=(ms.ref.entity("sales.facts"),)
        )
        other = members.observe(
            ms.ref.metric("sales.copy_total"), during=window, by=(ms.ref.entity("sales.facts"),)
        )
        grid = mv.time_grid(during=window, grain=mv.grain("day"))
        timed = (
            members.each(grid)
            .observe(
                ms.ref.metric("sales.total"), during=grid.window, by=(ms.ref.entity("sales.facts"),)
            )
            .group_by(grid)
            .rollup()
        )
        assert isinstance(entity, mv.LogicalNumericRelation)
        assert isinstance(other, mv.LogicalNumericRelation)
        assert isinstance(timed, mv.LogicalRolledNumericRelation)
        originals = entity, other, timed
        inputs: list[Json] = []
        for _ in range(2):
            saved: list[Json] = []
            for value in originals:
                input_result = value.execute()
                assert isinstance(input_result, mv.MaterializedNumericRelation)
                assert input_result.to_pandas().value.tolist() == [1, 2, 7]
                if isinstance(value, mv.LogicalNumericRelation):
                    assert input_result.to_pandas().set_index(
                        ["member", "coord_0", "coord_1"]
                    ).value.to_dict() == {
                        ("a", 9007199254740992, 1): 1,
                        ("a", 9007199254740993, 2): 2,
                        ("b", 9007199254740993, 1): 7,
                    }
                source_trace.record(input_result)
                saved.append(input_snapshot(input_result))
            inputs.append(saved)
        sources: dict[str, Json] = {}
        proofs: list[Json] = []
        for method, logical in graphs(originals).items():
            result = logical.execute()
            check(result, method)
            sources[method] = snapshot(result)
            proofs.append(method_proof(result, method, "produce"))
            repeated = logical.execute()
            check(repeated, method)
            assert repeated.state.artifact_ref != result.state.artifact_ref
        state: dict[str, Json] = {"session": session.id, "inputs": inputs, "source": sources}
        (tmp_path / "r94-statistical.json").write_bytes(encode(state))
        source_trace.save(
            "r94-statistical-producer-" + backend,
            case.environment,
            {
                "rows": [
                    {
                        name: str(value) if isinstance(value, datetime) else value
                        for name, value in row.items()
                    }
                    for row in source_data(backend).rows
                ]
            },
            {"values": [1, 2, 7], "composite_keys_above_2pow53": True},
            None,
            (case.session,),
        )
        assert session._runtime.store.resources(session.id) == ()
    shutil.rmtree(tmp_path / "models")
    (tmp_path / "source.duckdb").unlink(missing_ok=True)
    reports: list[Json] = [{"phase": "produce", "pid": os.getpid(), "proofs": proofs}]
    repository = PROJECT_ROOT
    for phase in ("fixed", "cold"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.statistical_recovery_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "r94-statistical-recovery-" + backend + ".json").write_bytes(
            encode(
                {
                    "backend": backend,
                    "profile": profile,
                    "reports": reports,
                    "manifest": read(tmp_path / "r94-statistical.json"),
                }
            )
        )
