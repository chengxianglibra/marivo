"""Bind each native producer to independent offline and cold fixed executions."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.file_fixtures import author_file_case, author_http_case
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
    phases: tuple[Literal["offline", "cold"], ...] = ("offline", "cold"),
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
        session = mv.session.create("r94-producer", timezone="UTC")
        values = session.observe(
            {"value": session.catalog.metrics.get("sales.total")},
            by=session.members(session.catalog.entities.get("sales.facts")),
        )
        captures = [values.persist(), values.persist()]
        assert captures[0].artifact.id != captures[1].artifact.id
        frames: list[pd.DataFrame] = []
        for value in captures:
            frame = value.to_pandas()
            indexed = frame.set_index(frame.columns[0])
            assert indexed[("value", "value")].to_dict() == {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): 0,
                ("b", 9007199254740993, 1): 4,
            }
            frames.append(frame)
        # Independent executions preserve the same complete output shape and values.
        source_trace.record(captures[0])
        state: dict[str, Json] = {
            "session": session.id,
            "inputs": [value.artifact.id for value in captures],
            "input_snapshots": [
                snapshot(value, frame=frame) for value, frame in zip(captures, frames, strict=True)
            ],
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
            assert sum(view_name in str(item.sql) for item in submissions) >= 2
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
    shutil.rmtree(tmp_path / "models")
    for path in tmp_path.glob("source.duckdb*"):
        path.unlink()
    # The producer has released its source; neither child inherits its Python state.
    reports: list[Json] = [{"phase": "produce", "pid": os.getpid()}]
    repository = PROJECT_ROOT
    environment = dict(os.environ, PYTHONPATH=str(repository))
    for phase in phases:
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
    assert len({row["pid"] for row in reports if isinstance(row, dict)}) == len(phases) + 1
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        phase_suffix = "" if phases == ("offline", "cold") else "-" + "-".join(phases)
        Path(directory, f"r94-recovery-{backend}{suffix}{phase_suffix}.json").write_text(
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


@pytest.mark.slow
def test_native_producer_cold_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    profile = "table"
    producer_recovery(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        source_trace,
        phases=("offline", "cold"),
    )


def test_http_source_remains_a_datasource_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    profile = "http-json-public"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", profile, tmp_path, monkeypatch, reference_data("duckdb")) as case:
        case.session.bind(
            case.source, source_identity="http_boundary.datasource", source_params=case.params
        )
        author_http_case(case, semantic_project_factory)
        session = mv.session.create("http-boundary", timezone="UTC")
        with pytest.raises(AnalysisError) as failure:
            session.observe(
                {"value": session.catalog.metrics.get("sales.total")},
                by=session.members(session.catalog.entities.get("sales.facts")),
            ).compute()
        error = failure.value
        assert error.expected and error.received
        assert "local" in error.expected.lower()
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
