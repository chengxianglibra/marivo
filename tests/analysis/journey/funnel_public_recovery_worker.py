"""Canonical public Funnel production and source-free Session recovery."""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import (
    funnel_execution,
    graph_local_execution,
    journey_execution,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.lifecycle.lifecycle_fixtures import END, START
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def author(root: Path, form: str) -> None:
    assert form in ("table", "parquet")
    models = root / "models"
    (models / "datasources").mkdir(parents=True)
    (models / "semantic/commerce").mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname="r94-funnel"\n')
    baseline = START - timedelta(days=3)
    identities = [9007199254740993, 9007199254740994]
    rows = [
        (identities[0], "started", START),
        (identities[1], "started", START + timedelta(seconds=2)),
        (identities[0], "finished", START + timedelta(seconds=5)),
        (identities[0], "started", baseline),
        (identities[1], "started", baseline + timedelta(seconds=2)),
        (identities[1], "finished", baseline + timedelta(seconds=5)),
    ]
    tables = {
        "subjects": pa.table({"sid": pa.array(identities, type=pa.int64())}),
        "facts": pa.table(
            {
                "oid": pa.array([9007199254741000 + i for i in range(6)], type=pa.int64()),
                "sid": pa.array([row[0] for row in rows], type=pa.int64()),
                "kind": pa.array([row[1] for row in rows], type=pa.string()),
                "instant": pa.array([row[2] for row in rows], type=pa.timestamp("us", tz="UTC")),
            }
        ),
    }
    database = root / "source.duckdb"
    connection = ibis.duckdb.connect(database)
    try:
        for name, table in tables.items():
            connection.create_table(name, table)
            if form == "parquet":
                pq.write_table(table, root / (name + ".parquet"))
    finally:
        connection.disconnect()
    (models / "datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n"
    )
    (models / "semantic/commerce/_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='commerce',owner='Analytics',default=True)\n"
    )
    sources = {
        name: f"md.table({name!r})"
        if form == "table"
        else f"md.parquet({str(root / (name + '.parquet'))!r})"
        for name in tables
    }
    code = f"""import marivo.datasource as md
import marivo.semantic as ms
subjects = ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source={sources["subjects"]},primary_key=['sid'])
facts = ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source={sources["facts"]},primary_key=['oid'])
sid = ms.dimension_column(name='sid',entity=subjects,column='sid')
fact_sid = ms.dimension_column(name='sid',entity=facts,column='sid')
oid = ms.dimension_column(name='oid',entity=facts,column='oid')
kind = ms.dimension_column(name='kind',entity=facts,column='kind')
instant = ms.time_dimension_column(name='instant',entity=facts,column='instant',granularity='second',parse=ms.timestamp(timezone='UTC'))
participant = ms.relationship(name='participant',from_entity=facts,to_entity=subjects,keys=[ms.join_on(fact_sid,sid)])
fact_count = ms.count(name='fact_count',entity=facts,time=instant)
"""
    for event in ("started", "finished"):
        code += f"""@ms.event(name={event!r},identity=(oid,),occurred_at=instant,
    participants=(ms.participant(name='subject',path=(participant,),cardinality='one'),),
    ai_context=ms.ai_context(business_definition='A governed {event} occurrence.'))
def {event}(rows):
    return ms.bind(kind,rows) == {event!r}
"""
    (models / "semantic/commerce/objects.py").write_text(code)


def materialize(root: Path, *, source_followup: bool = True) -> dict[str, Json]:
    baseline = START - timedelta(days=3)
    ms.load(workspace_dir=root)
    session = mv.session.get_or_create("r94-funnel", report_timezone="UTC")
    population = session.members(ms.ref.entity("commerce.subjects"))
    pattern = steps()
    claim = mv.BoundedCompletenessDeclarationV1(
        inputs=(ms.ref.event("commerce.started"), ms.ref.event("commerce.finished")),
        complete_from=baseline,
        complete_through=END,
        rationale="All six independently authored fixture events are retained.",
    )
    funnels: list[mv.MaterializedFunnelResult] = []
    extra_inputs: dict[str, Json] = {}
    for start in (START, baseline):
        journey = session.events.match(
            mv.EventPattern(steps=pattern),
            population=population,
            cohort_window=mv.time_scope(
                start=start.isoformat(), end=(start + timedelta(seconds=30)).isoformat()
            ),
            completion_through=start + timedelta(seconds=100),
            matching=mv.first_per_subject(),
            completeness=(claim,),
        )
        funnels.append(journey.funnel(axes=(ms.ref.dimension("commerce.subjects.sid"),)).execute())
        if start == START:
            matched = journey.execute()
            extra_inputs["journey"] = snapshot(matched)
            if source_followup:
                dropped = journey.read(mv.dropped_before(step=pattern[1]))
                selected = dropped.where(dropped.value.eq(True)).members(
                    through=journey.subjects(pattern[0].participant)
                )
                assert isinstance(selected, mv.LogicalAnalysisDomain)
                following = selected.observe(
                    ms.ref.metric("commerce.fact_count"),
                    during=mv.time_scope(start=START, end=END),
                    via=ms.ref.relationship("commerce.participant"),
                    by=(mv.member(),),
                ).execute()
                following_frame = following.to_pandas()
                assert following_frame.member.tolist() == [9007199254740994]
                assert following_frame.value.tolist() == [1]
    current, previous = funnels
    state: dict[str, Json] = {
        "session": session.id,
        "inputs": {
            "current": snapshot(current),
            "baseline": snapshot(previous),
            **extra_inputs,
        },
    }
    (root / "r94-funnel.json").write_bytes(encode(state))
    return {"phase": "produce", "pid": os.getpid(), "inputs": state["inputs"]}


def produce(root: Path, form: str) -> dict[str, Json]:
    author(root, form)
    return materialize(root)


def steps() -> tuple[mv.PatternStep, mv.PatternStep]:
    return (
        mv.step(
            participant=ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
            key="start",
        ),
        mv.step(
            participant=ms.participant_role(
                event=ms.ref.event("commerce.finished"), name="subject"
            ),
            key="finish",
        ),
    )


def recover(root: Path, phase: str) -> dict[str, Json]:
    state = read(root / "r94-funnel.json")
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(journey_execution, "execute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        inputs: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            restored = session.artifact(reference)
            assert isinstance(restored, _MaterializedRead) and snapshot(restored) == saved
            inputs[name] = restored
        current, baseline = inputs["current"], inputs["baseline"]
        matched = inputs["journey"]
        assert isinstance(matched, mv.MaterializedJourneyResult)
        assert isinstance(current, mv.MaterializedFunnelResult)
        assert isinstance(baseline, mv.MaterializedFunnelResult)
        if phase == "fixed":
            change = current.compare(baseline).execute()
            assert isinstance(change, mv.MaterializedFunnelComparisonResult)
        else:
            saved_comparison = obj(obj(state["outputs"])["comparison"])
            comparison_reference = saved_comparison["artifact"]
            assert isinstance(comparison_reference, str)
            change = session.artifact(comparison_reference)
            assert isinstance(change, mv.MaterializedFunnelComparisonResult)
            assert snapshot(change) == saved_comparison
        assert change.evidence_digest().finding_count == 2
        first = change.findings(limit=1)
        second = change.findings(limit=1, cursor=first.next_cursor)
        assert first.has_more and not second.has_more
        assert change.finding(second.items[0].finding_id) == second.items[0]
        after_comparison = run_ids(session)
        if phase == "fixed":
            assert len(after_comparison - before) == kernels.call_count == 1
        else:
            assert after_comparison == before and kernels.call_count == 0
        assert change._dataset is not None
        receipt = change._dataset.artifact.descriptor.primary_receipt.local
        saved_findings = change.findings(limit=100)
        saved_evidence = change.evidence_digest()
        payload = root / receipt.project_relative_path / "data.parquet"
        offline = payload.with_suffix(".offline")
        payload.rename(offline)
        try:
            assert change.findings(limit=100) == saved_findings
            assert change.evidence_digest() == saved_evidence
            with pytest.raises(MaterializationError, match="selected storage is missing"):
                change.to_pandas()
        finally:
            offline.rename(payload)
        operations: dict[
            str,
            mv.LogicalNumericRelation
            | mv.LogicalFunnelComparisonResult
            | mv.LogicalAttributionResult
            | mv.LogicalBooleanRelation,
        ] = {
            "cohort": current.read(current.cohort_count),
            "delta": change.read(mv.funnel_loss_rate(step=steps()[1])),
            "allocation": change.attribute(
                target=mv.funnel_loss_rate(step=steps()[1]),
                axes=(ms.ref.dimension("commerce.subjects.sid"),),
            ),
            "dropout": matched.read(mv.dropped_before(step=steps()[1])),
        }
        outputs: dict[str, Json] = {"comparison": snapshot(change)} if phase == "fixed" else {}
        for name, logical in operations.items():
            if phase == "cold" and name == "allocation":
                continue
            if phase == "cold":
                with (
                    patch.object(graph_local_execution, "execute_verified_fixed", forbidden),
                    patch.object(funnel_execution, "execute", forbidden),
                ):
                    result: _MaterializedRead = logical.execute()
            else:
                result = logical.execute()
                before_repeat = run_ids(session)
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    assert snapshot(logical.execute()) == snapshot(result)
                assert run_ids(session) == before_repeat
            outputs[name] = snapshot(result)
            frame = result.to_pandas()
            if name == "cohort":
                assert frame.value.tolist() == [1, 1, 1, 1]
            elif name == "delta":
                assert sorted(frame.value) == [-1.0, 1.0]
            elif name == "allocation":
                assert result.evidence_digest().finding_count > 0
            elif name == "dropout":
                assert frame.value.tolist() == [False, True]
        if phase == "fixed":
            assert kernels.call_count == len(run_ids(session) - before) == len(operations) + 1
            state["outputs"] = outputs
            (root / "r94-funnel.json").write_bytes(encode(state))
        else:
            expected = obj(state["outputs"])
            assert outputs == {name: expected[name] for name in outputs}, {
                name: {
                    key: (value, obj(expected[name]).get(key))
                    for key, value in obj(raw).items()
                    if value != obj(expected[name]).get(key)
                }
                for name, raw in outputs.items()
                if raw != expected[name]
            }
            assert run_ids(session) == before and kernels.call_count == 0
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "kernels": kernels.call_count,
            "new_runs": len(run_ids(session) - before),
            "resources": 0,
        }


if __name__ == "__main__":
    root, phase, report = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    observed = produce(root, sys.argv[4]) if phase == "produce" else recover(root, phase)
    report.write_bytes(encode(observed))
