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
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.release
@pytest.mark.slow
def test_native_original_mean_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [(0, "a", 1), (1, "a", 1), (2, "b", 100)]
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
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-mean", timezone="UTC")
        bucket = session.catalog.fields.get("sales.facts.bucket")
        amount = session.catalog.fields.get("sales.facts.amount")
        time = session.catalog.fields.get("sales.facts.happened")
        grouped = session.observe(
            {
                "value": session.catalog.metrics.get("sales.average"),
                "sum": session.catalog.metrics.get("sales.total"),
                "count": amount.count(time=time),
            },
            by=(bucket,),
        )
        source = grouped.persist()
        original = session.observe(
            {"value": session.catalog.metrics.get("sales.average")}
        ).persist()
        current = grouped.reduce({"value": grouped["value"].mean()}).persist()
        assert source.to_pandas()[("value", "value")].tolist() == [1.0, 100.0]
        assert original.to_pandas()[("value", "value")].tolist() == [34.0]
        assert current.to_pandas()[("value", "value")].tolist() == [50.5]
        outputs: dict[str, Json] = {
            name: {"artifact": result.artifact.id, "snapshot": snapshot(result)}
            for name, result in (("groups", source), ("original", original), ("current", current))
        }
        state: dict[str, Json] = {"session": session.id, "outputs": outputs}
        (tmp_path / "r94-mean.json").write_bytes(encode(state))
        source_trace.save(
            "mean-source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 3},
            {"a": [2, 2], "b": [100, 1]},
            source,
            (case.session,),
        )
        environment = checked({**case.environment, "profile": profile})
    shutil.rmtree(tmp_path / "models")
    for path in tmp_path.glob("source.duckdb*"):
        path.unlink()
    reports: list[Json] = []
    for phase in ("cold",):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.mean_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=PROJECT_ROOT,
            env=dict(
                os.environ,
                PYTHONPATH=str(PROJECT_ROOT),
                MARIVO_TELEMETRY="off",
            ),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 2
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
