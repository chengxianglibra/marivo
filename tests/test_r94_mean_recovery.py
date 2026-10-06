"""Each native mean producer binds original components to independent recovery."""

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
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, encode, obj, read
from tests.r9_source_cases import SourceData, source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_domain_recovery_worker import snapshot
from tests.test_r93_capability_consumers import _author_c05_project


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_original_mean_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [(i, "a", 1) for i in range(100)] + [(100, "b", 100)]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({9007199254740992 + i},1,'{tenant}',{amount},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for i, tenant, amount in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": 9007199254740992 + i,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for i, tenant, amount in facts
        ],
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-mean", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        values = members.observe(ms.ref.metric("sales.average"))
        assert isinstance(values, mv.LogicalNumericRelation)
        source = values.group_by(bucket).rollup().execute()
        assert isinstance(source, mv.MaterializedGroupedNumericRelation)
        state: dict[str, Json] = {
            "session": session.id,
            "source": source.state.artifact_ref.ref,
            "original": snapshot(source),
        }
        (tmp_path / "r94-mean.json").write_bytes(encode(state))
        r93_source_trace.save(
            "mean-source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 101},
            {"a": [100, 100], "b": [100, 1]},
            source,
            (case.session,),
        )
        environment = checked({**case.environment, "profile": profile})
    shutil.rmtree(tmp_path / "models")
    for path in tmp_path.glob("source.duckdb*"):
        path.unlink()
    for path in tmp_path.glob("source.sqlite*"):
        path.unlink()
    reports: list[Json] = []
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_mean_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(
                os.environ,
                PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                MARIVO_TELEMETRY="off",
            ),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "mean-recovery-" + backend + ".json").write_bytes(
            encode(
                {
                    "environment": environment,
                    "source_pid": os.getpid(),
                    "source": state,
                    "reports": reports,
                }
            )
        )
