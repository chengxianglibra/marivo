"""Bind each native producer to independent offline and cold fixed executions."""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.file_fixtures import author_file_case, author_http_case
from tests.analysis.materialization.recovery_worker import snapshot
from tests.datasource.source_cases import source_case
from tests.support.json import Json
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


def producer_recovery(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data(backend)
    if backend == "trino" and profile == "non-iceberg":
        # The connector's default TIMESTAMP(3) is a distinct, unqualified key.
        # Bind this producer to the currently required UTC microsecond profile.
        data = replace(data, columns=data.columns.replace("TIMESTAMP", "TIMESTAMP(6)"))
    suffix = (
        "-" + profile
        if profile
        in (
            "view",
            "parquet",
            "csv",
            "local-json",
            "non-iceberg",
            "distributed",
        )
        else ""
    )
    source_file: Path | None = None
    file_digest: str | None = None
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        if profile == "view":
            assert isinstance(case.source, TableSourceIR) and case.source.table.endswith("_view")
        if profile in ("parquet", "csv", "local-json"):
            source_file = author_file_case(case, data, profile, semantic_project_factory)
            file_digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        else:
            author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-producer", report_timezone="UTC")
        try:
            values = session.members(ms.ref.entity("sales.facts")).observe(
                ms.ref.metric("sales.total")
            )
        except AnalysisError as error:
            if profile in ("parquet", "csv", "local-json"):
                assert error.expected and error.received and error.repair is not None
                assert session.runs().items == ()
                assert session._runtime.store.resources(session.id) == ()
                if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                    Path(directory, f"r94-file-blocker-{profile}.json").write_text(
                        json.dumps(
                            {
                                "backend": backend,
                                "profile": profile,
                                "source_descriptor": case.source.to_dict(),
                                "file_sha256": file_digest,
                                "error": {
                                    "type": type(error).__name__,
                                    "expected": error.expected,
                                    "received": error.received,
                                    "repair": error.repair.action,
                                },
                                "runs": 0,
                                "resources": 0,
                                "native_business_submissions": len(source_trace.native_sql),
                                "producer_published": False,
                                "boundary": "Actual public source admission blocker; no producer/fixed/cold qualification is granted.",
                            },
                            sort_keys=True,
                        )
                    )
            raise
        assert isinstance(values, mv.LogicalNumericRelation)
        captures = [values.execute(), values.execute()]
        assert all(isinstance(value, mv.MaterializedNumericRelation) for value in captures)
        assert captures[0].state.artifact_ref != captures[1].state.artifact_ref
        for value in captures:
            frame = value.to_pandas().set_index(["member", "coord_0", "coord_1"])
            assert frame.value.to_dict() == {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): 0,
                ("b", 9007199254740993, 1): 4,
            }
            source_trace.record(value)
        state: dict[str, Json] = {
            "session": session.id,
            "inputs": [value.state.artifact_ref.ref for value in captures],
            "input_snapshots": [snapshot(value) for value in captures],
        }
        (tmp_path / "r94-state.json").write_text(json.dumps(state, sort_keys=True))
        if profile == "view":
            assert isinstance(case.source, TableSourceIR)
            view_name = case.source.table
            submissions = [
                item
                for owner in source_trace.owners
                if owner is not case.session
                for item in owner.submissions
                if item.sql is not None
            ]
            assert (
                sum(
                    item.purpose == "analysis.graph.stage" and view_name in str(item.sql)
                    for item in submissions
                )
                >= 2
            )
        source_trace.save(
            f"r94-producer-{backend}{suffix}",
            {**case.environment, "profile": profile},
            {
                "columns": data.columns,
                "values": data.values,
                "source_descriptor": case.source.to_dict(),
                "file_sha256": file_digest,
            },
            {"values": [2, 0, 4], "independent_source_realizations": 2},
            None,
            (case.session,),
        )
    if source_file is not None:
        source_file.unlink()
        (tmp_path / "source.duckdb").unlink()
        assert not source_file.exists() and not (tmp_path / "source.duckdb").exists()
    # The producer has released its source; neither child inherits its Python state.
    reports: list[Json] = [{"phase": "produce", "pid": os.getpid()}]
    repository = PROJECT_ROOT
    environment = dict(os.environ, PYTHONPATH=str(repository))
    for phase in ("fixed", "cold"):
        report = tmp_path / f"r94-{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.recovery_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(json.loads(report.read_text()))
    assert len({row["pid"] for row in reports if isinstance(row, dict)}) == 3
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"r94-recovery-{backend}{suffix}.json").write_text(
            json.dumps(
                {
                    "backend": backend,
                    "profile": profile,
                    "source_file_removed": source_file is not None,
                    "file_sha256": file_digest,
                    "reports": reports,
                },
                sort_keys=True,
            )
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_producer_offline_and_cold_continuation(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    producer_recovery(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, source_trace
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("duckdb", "sqlite", "postgres", "mysql"))
def test_view_producer_offline_and_cold_continuation(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    producer_recovery(
        backend, "view", tmp_path, monkeypatch, semantic_project_factory, source_trace
    )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ("parquet", "csv", "local-json"))
def test_file_producer_offline_and_cold_continuation(
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    producer_recovery(
        "duckdb", profile, tmp_path, monkeypatch, semantic_project_factory, source_trace
    )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ("http-json-public", "http-json-auth"))
def test_http_source_remains_a_datasource_boundary(
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", profile, tmp_path, monkeypatch, reference_data("duckdb")) as case:
        case.session.bind(
            case.source, source_identity="http_boundary.datasource", source_params=case.params
        )
        author_http_case(case, semantic_project_factory)
        session = mv.session.get_or_create("http-boundary", report_timezone="UTC")
        with pytest.raises(DatasetConstructionError) as failure:
            session.members(ms.ref.entity("sales.facts")).observe(ms.ref.metric("sales.total"))
        error = failure.value
        assert error.expected and error.received
        assert "existing local CSV or unparameterized GET JSON file" in error.expected
        assert "existing_local_file=False" in error.received
        assert "query_parameters=0" in error.received
        assert error.repair is not None
        assert not session.runs().items
        assert session._runtime.store.resources(session.id) == ()
        assert source_trace.native_sql == []
        report = {
            "environment": {**case.environment, "profile": profile},
            "raw_rows": reference_data("duckdb").rows,
            "declarations": {
                str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (tmp_path / "models").rglob("*.py")
            },
            "source": "expected_refusal",
            "fixed": "not_applicable_no_analysis_producer",
            "cold": "not_applicable_no_analysis_producer",
            "error": {"expected": error.expected, "received": error.received},
            "runs": 0,
            "resources": 0,
            "native_business_submissions": 0,
        }
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "http-analysis-boundary-" + profile + ".json").write_text(
                json.dumps(report, default=str, sort_keys=True)
            )


@pytest.mark.runtime
def test_non_iceberg_producer_offline_and_cold_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    producer_recovery(
        "trino", "non-iceberg", tmp_path, monkeypatch, semantic_project_factory, source_trace
    )


@pytest.mark.runtime
def test_clickhouse_distributed_producer_offline_and_cold_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    producer_recovery(
        "clickhouse", "distributed", tmp_path, monkeypatch, semantic_project_factory, source_trace
    )
