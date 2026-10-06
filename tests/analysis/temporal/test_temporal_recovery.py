"""Native temporal mean retains ordered spatial samples across independent recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


def _native_temporal_recovery(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
    subject_kind: Literal["string", "int64"],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (0, "a", 10, 1),
        (1, "a", 5, 1),
        (2, "a", 20, 2),
        (3, "a", 1, 2),
        (4, "b", 100, 1),
        (5, "b", 200, 2),
    ]
    subject_keys: tuple[str, str] | tuple[int, int] = (
        ("a", "b") if subject_kind == "string" else (9007199254740992, 9007199254740993)
    )
    revisions = (1, 2) if subject_kind == "string" else subject_keys
    evidence_prefix = "temporal-" if subject_kind == "string" else "temporal-int64-"
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({9007199254740992 + i},{revisions[0] if tenant == 'a' else revisions[1]},'{tenant}',{amount},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + f"'2026-08-{day:02d} 00:00:00')"
            for i, tenant, amount, day in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": 9007199254740992 + i,
                "revision": revisions[0] if tenant == "a" else revisions[1],
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, day, tzinfo=timezone.utc),
            }
            for i, tenant, amount, day in facts
        ],
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    subjects = SourceData(
        data.columns,
        f"(1,{revisions[0]},'a',0,"
        + ("TIMESTAMP " if backend == "trino" else "")
        + f"'2026-08-01 00:00:00'),(2,{revisions[1]},'b',0,"
        + ("TIMESTAMP " if backend == "trino" else "")
        + "'2026-08-01 00:00:00')",
        data.clickhouse_columns,
        [
            {
                "id": i,
                "revision": revisions[i - 1],
                "tenant": tenant,
                "amount": 0,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for i, tenant in ((1, "a"), (2, "b"))
        ],
    )
    with ExitStack() as stack:
        case = stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
        if backend in ("duckdb", "sqlite"):
            case.session.close()
        subject_case = stack.enter_context(
            source_case(backend, profile, tmp_path, monkeypatch, subjects)
        )
        assert isinstance(subject_case.source, TableSourceIR)
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        models = tmp_path / "models" / "semantic" / "sales" / "models.py"
        models.write_text(
            models.read_text()
            + "\nstatus = ms.measure_column(name='status', entity=facts, column='amount', additivity=ms.additive_all(except_=(happened,)), status_time_dimension=happened, status_time_fold='mean')\nfolded = ms.aggregate(name='folded', measure=status, agg='sum')\n"
        )
        key_column = "tenant" if subject_kind == "string" else "revision"
        models.write_text(
            models.read_text()
            + f"subjects=ms.entity(name='subjects', datasource=ms.ref.datasource('warehouse'), source=md.table({subject_case.source.table!r}, database={subject_case.source.database!r}), primary_key=[{key_column!r}])\nsubject_id=ms.dimension_column(name='id',entity=subjects,column={key_column!r})\nfact_subject_key=ms.dimension_column(name='subject_key',entity=facts,column={key_column!r})\nfact_subject=ms.relationship(name='fact_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(fact_subject_key,subject_id)])\n"
        )
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("r94-temporal", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.subjects"))
        values = members.observe(
            ms.ref.metric("sales.folded"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
            via=ms.ref.relationship("sales.fact_subject"),
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        source = values.execute()
        assert isinstance(source, mv.MaterializedNumericRelation)
        state: dict[str, Json] = {
            "session": session.id,
            "subject_keys": list(subject_keys),
            "subject_kind": subject_kind,
            "source": source.state.artifact_ref.ref,
            "original": snapshot(source),
        }
        (tmp_path / "r94-temporal.json").write_bytes(encode(state))
        source_trace.save(
            evidence_prefix + "source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 6},
            {
                "a": [["2026-08-01", 15, 2], ["2026-08-02", 21, 2]],
                "b": [["2026-08-01", 100, 1], ["2026-08-02", 200, 1]],
            },
            source,
            (case.session, subject_case.session),
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
                "tests.analysis.temporal.temporal_recovery_worker",
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
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, evidence_prefix + "recovery-" + backend + ".json").write_bytes(
            encode(
                {
                    "environment": environment,
                    "source_pid": os.getpid(),
                    "source": state,
                    "reports": reports,
                }
            )
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_temporal_mean_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    _native_temporal_recovery(
        backend, tmp_path, monkeypatch, semantic_project_factory, source_trace, "string"
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_temporal_int64_subject_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    _native_temporal_recovery(
        backend, tmp_path, monkeypatch, semantic_project_factory, source_trace, "int64"
    )
