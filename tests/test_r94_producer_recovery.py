"""Bind each native producer to independent offline and cold fixed executions."""

import csv
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json
from tests.r9_source_cases import Case, SourceData, source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_recovery_worker import snapshot
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_reference_consumers import _data


def author_file_case(
    case: Case,
    data: SourceData,
    profile: str,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Path:
    assert isinstance(case.source, (CsvSourceIR, JsonSourceIR, ParquetSourceIR))
    path = Path(case.source.path)
    if profile == "parquet":
        pq.write_table(pa.Table.from_pylist(data.rows), path)
        source = f"md.parquet({str(path)!r})"
        parse = "ms.timestamp(timezone='UTC')"
    else:
        rows = [
            {
                key: value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value
                for key, value in row.items()
            }
            for row in data.rows
        ]
        if profile == "csv":
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            source = f"md.csv({str(path)!r})"
            parse = "ms.timestamp(timezone='UTC')"
        else:
            assert profile == "local-json"
            path.write_text(json.dumps(rows))
            source = f"md.json({str(path)!r})"
            parse = "ms.timestamp(timezone='UTC')"
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={case.session.datasource.fields['path']!r}, read_only=True)\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source={source}, primary_key=['tenant','id','revision'])\n"
            + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
            + f"happened=ms.time_dimension_column(name='happened', entity=facts, column='happened', granularity='second', parse={parse}, is_default=True)\n"
            + "total=ms.aggregate(name='total', measure=amount, agg='sum', empty=ms.empty.zero())\n",
        }
    )
    return path


def producer_recovery(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = _data(backend)
    suffix = "-" + profile if profile in ("view", "parquet", "csv", "local-json") else ""
    source_file: Path | None = None
    file_digest: str | None = None
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        if profile == "view":
            assert isinstance(case.source, TableSourceIR) and case.source.table.endswith("_view")
        if profile in ("parquet", "csv", "local-json"):
            source_file = author_file_case(case, data, profile, semantic_project_factory)
            file_digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        else:
            _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
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
                                "native_business_submissions": len(r93_source_trace.native_sql),
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
            r93_source_trace.record(value)
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
                for owner in r93_source_trace.owners
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
        r93_source_trace.save(
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
    repository = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(repository))
    for phase in ("fixed", "cold"):
        report = tmp_path / f"r94-{phase}.json"
        completed = subprocess.run(
            [sys.executable, "-m", "tests.r94_recovery_worker", str(tmp_path), phase, str(report)],
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
    r93_source_trace: SourceTrace,
) -> None:
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    producer_recovery(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, r93_source_trace
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("duckdb", "sqlite", "postgres", "mysql"))
def test_view_producer_offline_and_cold_continuation(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    producer_recovery(
        backend, "view", tmp_path, monkeypatch, semantic_project_factory, r93_source_trace
    )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ("parquet", "csv", "local-json"))
def test_file_producer_offline_and_cold_continuation(
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    producer_recovery(
        "duckdb", profile, tmp_path, monkeypatch, semantic_project_factory, r93_source_trace
    )
