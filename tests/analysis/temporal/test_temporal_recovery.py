"""Native temporal means preserve int64 identities and terminal cold reads."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.release
@pytest.mark.slow
def test_native_temporal_mean_int64_identities_and_independent_cold_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = (
        (0, "a", 10, 1),
        (1, "a", 5, 1),
        (2, "a", 20, 2),
        (3, "a", 1, 2),
        (4, "b", 100, 1),
        (5, "b", 200, 2),
    )
    subject_keys = (9007199254740992, 9007199254740993)
    columns = "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP"
    data = SourceData(
        columns,
        ",".join(
            f"({subject_keys[0] + identity},{subject_keys[0] if tenant == 'a' else subject_keys[1]},'{tenant}',{amount},'2026-08-{day:02d} 00:00:00')"
            for identity, tenant, amount, day in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": subject_keys[0] + identity,
                "revision": subject_keys[0] if tenant == "a" else subject_keys[1],
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, day, tzinfo=timezone.utc),
            }
            for identity, tenant, amount, day in facts
        ],
    )
    subjects = SourceData(
        columns,
        f"(1,{subject_keys[0]},'a',0,'2026-08-01 00:00:00'),(2,{subject_keys[1]},'b',0,'2026-08-01 00:00:00')",
        data.clickhouse_columns,
        [
            {
                "id": identity,
                "revision": subject_keys[identity - 1],
                "tenant": tenant,
                "amount": 0,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, tenant in ((1, "a"), (2, "b"))
        ],
    )
    with ExitStack() as stack:
        case = stack.enter_context(source_case("duckdb", "table", tmp_path, monkeypatch, data))
        case.session.close()
        subject_case = stack.enter_context(
            source_case("duckdb", "table", tmp_path, monkeypatch, subjects)
        )
        assert isinstance(subject_case.source, TableSourceIR)
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        models = tmp_path / "models/semantic/sales/models.py"
        subject_models = (
            f"subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table({subject_case.source.table!r},database={subject_case.source.database!r}),primary_key=['revision'])\n"
            "subject_id=ms.dimension_column(name='id',entity=subjects,column='revision')\n"
            "fact_subject_key=ms.dimension_column(name='subject_key',entity=facts,column='revision')\n"
            "fact_subject=ms.relationship(name='fact_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(fact_subject_key,subject_id)])\n"
        )
        body = models.read_text()
        anchor = "amount_value = amount\n"
        assert anchor in body
        models.write_text(body.replace(anchor, subject_models + anchor))
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("temporal-int64-recovery", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.subjects"))
        total = session.catalog.metrics.get("sales.total")
        time = session.catalog.fields.get("sales.facts.happened")
        amount = session.catalog.fields.get("sales.facts.amount")
        scope = session.time_scope(start="2026-08-01", end="2026-08-03")
        calendar = scope.by(mv.grain("day"))
        temporal = session.observe({"value": total.per(time).mean()}, by=mv.product(scope, members))
        source = temporal.persist()
        spatial = session.observe({"value": total.per(time).mean()}, by=(scope,)).persist()
        represented = temporal.reduce({"value": temporal["value"].mean()}).persist()
        samples = session.observe(
            {"sum": total, "rows": amount.count(time=time)}, by=mv.product(calendar, members)
        ).persist()
        source_frame, spatial_frame, represented_frame, samples_frame = (
            saved.to_pandas() for saved in (source, spatial, represented, samples)
        )
        assert source_frame[source_frame.attrs["axes"][1]["name"]].tolist() == list(subject_keys)
        assert isinstance(source._backing, StoredFrameBacking)
        assert source._backing.data.schema.axes[1].columns[0].dtype == ScalarDataType("int64")
        assert source_frame["value"].tolist() == [18.0, 150.0]
        # Per subject: a has temporal totals [10+5,20+1], b has [100,200].
        # Original source observation completes space at each time: mean([115,221])=168.
        assert spatial_frame["value"].tolist() == [168.0]
        # A reduction over the two already completed values is mean([18,150])=84.
        assert represented_frame["value"].tolist() == [84.0]
        assert samples_frame["calendar:day"].tolist() == ["2026-08-01"] * 2 + ["2026-08-02"] * 2
        assert (
            samples_frame[samples_frame.attrs["axes"][1]["name"]].tolist() == list(subject_keys) * 2
        )
        assert samples_frame["sum"].tolist() == [15, 100, 21, 200]
        assert samples_frame["rows"].tolist() == [2, 1, 2, 1]
        calendar_metadata = samples_frame.attrs["axes"][0]["descriptor"]
        directory = samples_frame.attrs["frozen_coordinates"][0]["rows"]
        assert calendar_metadata["timezone"] == "UTC"
        assert [
            (cell.actual_start, cell.actual_end) for cell in directory.itertuples(index=False)
        ] == [
            (datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 2, tzinfo=timezone.utc)),
            (datetime(2026, 8, 2, tzinfo=timezone.utc), datetime(2026, 8, 3, tzinfo=timezone.utc)),
        ]
        outputs: dict[str, Json] = {
            name: {"artifact": saved.artifact.id, "snapshot": snapshot(saved, frame=frame)}
            for name, saved, frame in (
                ("subjects", source, source_frame),
                ("spatial", spatial, spatial_frame),
                ("represented", represented, represented_frame),
                ("samples", samples, samples_frame),
            )
        }
        state: dict[str, Json] = {
            "session": session.id,
            "subject_keys": list(subject_keys),
            "runs": checked(sorted(run_ids(session))),
            "outputs": outputs,
        }
        (tmp_path / "temporal-recovery.json").write_bytes(encode(state))
        source_trace.save(
            "temporal-int64-source-duckdb",
            {**case.environment, "profile": "table"},
            {"rows": 6},
            {
                "a": [["2026-08-01", 15, 2], ["2026-08-02", 21, 2]],
                "b": [["2026-08-01", 100, 1], ["2026-08-02", 200, 1]],
                "spatial_mean": 168,
                "represented_mean": 84,
            },
            source,
            (case.session, subject_case.session),
        )
    shutil.rmtree(tmp_path / "models")
    for path in tmp_path.glob("source.duckdb*"):
        path.unlink()
    worker = """
import os, sys
from pathlib import Path
from unittest.mock import patch
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource import timezone as source_timezone
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import encode, obj, read
root=Path(sys.argv[1])
os.chdir(root)
os.environ['MARIVO_PROJECT_ROOT']=str(root)
state=read(root/'temporal-recovery.json')
with patch.object(ms,'load',forbidden), patch.object(SemanticProject,'load',forbidden), patch.object(SourceSession,'__init__',forbidden), patch.object(duckdb,'connect',forbidden), patch.object(ibis.duckdb,'connect',forbidden), patch.object(source_timezone,'probe_engine_timezone',forbidden), patch.object(source_timezone,'_resolve_system_timezone',forbidden):
    session=mv.session.resume(state['session'])
    before=run_ids(session)
    assert sorted(before)==state['runs']
    assert session.timezone=='UTC'
    outputs=obj(state['outputs'])
    frames={}
    for name,output in outputs.items():
        selected=obj(output)
        saved=session.persisted_frame(selected['artifact'])
        frame=saved.to_pandas()
        assert snapshot(saved,frame=frame)==selected['snapshot']
        frames[name]=frame
    assert frames['subjects'].iloc[:,1].tolist()==state['subject_keys']
    assert frames['subjects']['value'].tolist()==[18.0,150.0]
    assert frames['spatial']['value'].tolist()==[168.0]
    assert frames['represented']['value'].tolist()==[84.0]
    assert frames['samples']['sum'].tolist()==[15,100,21,200]
    # Pandas can consume the committed time samples without a new source evaluation.
    assert frames['samples']['sum'].groupby(frames['samples'].iloc[:,0]).sum().mean()==168
    assert run_ids(session)==before
    Path(sys.argv[2]).write_bytes(encode({'pid':os.getpid(),'phase':sys.argv[3],'source_forbidden':True,'runs':sorted(before)}))
"""
    output = tmp_path / "cold.json"
    completed = subprocess.run(
        [sys.executable, "-c", worker, str(tmp_path), str(output), "cold"],
        cwd=PROJECT_ROOT,
        env=dict(
            os.environ,
            PYTHONPATH=str(PROJECT_ROOT),
            TZ="America/Los_Angeles",
            MARIVO_TELEMETRY="off",
        ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = read(output)
    assert obj(report)["pid"] != os.getpid()
    assert obj(report)["source_forbidden"] is True
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "temporal-int64-recovery-duckdb.json").write_bytes(
            encode(checked({"source_pid": os.getpid(), "source": state, "reports": [report]}))
        )
