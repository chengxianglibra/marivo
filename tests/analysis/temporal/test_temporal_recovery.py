"""Native temporal mean retains ordered spatial samples across independent recovery."""

import shutil
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, checked, encode
from tests.support.source_trace import SourceTrace


def _native_temporal_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
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
    subject_keys = (9007199254740992, 9007199254740993)
    revisions = subject_keys
    evidence_prefix = "temporal-int64-"
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
    profile = "table"
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
        if backend == "duckdb":
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
        key_column = "revision"
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
            by=(mv.member(),),
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        source = values.execute()
        assert isinstance(source, mv.MaterializedNumericRelation)
        state: dict[str, Json] = {
            "session": session.id,
            "subject_keys": list(subject_keys),
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
    reports: list[Json] = []
