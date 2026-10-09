"""Real History views and captured observations through public Session resume."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import (
    graph_local_execution,
    graph_publication,
    history_execution,
)
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden
from tests.shared_fixtures import lifecycle_project_files, run_ids
from tests.support.json import Json, checked, encode, obj, read

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
OLD = START - timedelta(days=3)
END = START + timedelta(seconds=100)
PREFIX = START + timedelta(seconds=12)
STATES = ("created", "paid", "closed")
CHECKPOINTS = (OLD, START, END)


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def snapshot(result: _MaterializedRead) -> dict[str, Json]:
    assert result._dataset is not None
    verified = result._dataset.verified()
    return obj(
        checked(
            json.loads(
                json.dumps(
                    {
                        "artifact": result._dataset.artifact.artifact_ref,
                        "primary": {
                            "schema": str(verified.primary.schema),
                            "rows": verified.primary.to_pylist(),
                        },
                        "parts": {
                            part.role: {
                                "schema": str(part.table.schema),
                                "rows": part.table.to_pylist(),
                            }
                            for part in verified.parts
                        },
                        "contract": asdict(result.contract()),
                        "evidence": asdict(result.evidence_digest()),
                        "findings": asdict(result.findings(limit=100)),
                    },
                    default=str,
                )
            )
        )
    )


def author(root: Path) -> None:
    (root / "models/datasources").mkdir(parents=True)
    semantic = root / "models/semantic"
    for name, code in lifecycle_project_files().items():
        destination = semantic / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(code)
    database = root / "source.duckdb"
    (root / "marivo.toml").write_text('[project]\nname="r94-history-public"\n')
    (root / "models/datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n"
    )
    rows = (
        ("f1", "o1", "created", OLD - timedelta(days=1), 1),
        ("f2", "o1", "paid", START + timedelta(seconds=5), 5),
        ("f3", "o1", "paid", START + timedelta(seconds=11), 11),
        ("f4", "o1", "closed", START + timedelta(seconds=12), 12),
        ("f5", "o1", "paid", START + timedelta(seconds=20), 20),
        ("f6", "o2", "created", START, 30),
        ("f7", "o2", "paid", START + timedelta(seconds=25), 25),
    )
    tables = {
        "orders": pa.table(
            {
                "order_id": ["o1", "o2", "o3"],
                "region": ["current"] * 3,
                "created_at": pa.array([OLD.date()] * 3, type=pa.date32()),
                "acquisition_channel": ["organic"] * 3,
                "plan_tier": ["free"] * 3,
            }
        ),
        "event_log": pa.table(
            {
                "event_id": [row[0] for row in rows],
                "order_id": [row[1] for row in rows],
                "event_type": [row[2] for row in rows],
                "event_time": pa.array([row[3] for row in rows], type=pa.timestamp("us", tz="UTC")),
                "amount": pa.array([row[4] for row in rows], type=pa.int64()),
            }
        ),
        "profiles": pa.table(
            {
                "order_id": ["o1", "o2", "o3"] * 2,
                "region": ["old", "Other", "silent", "new", None, "silent"],
                "day": pa.array([OLD.date()] * 3 + [START.date()] * 3, type=pa.date32()),
            }
        ),
    }
    connection = ibis.duckdb.connect(database, threads=1)
    try:
        for name, table in tables.items():
            connection.create_table(name, table)
    finally:
        connection.disconnect()
    path = semantic / "commerce/objects.py"
    with path.open("a") as stream:
        stream.write(
            """
amount = ms.measure_column(name='amount',entity=event_log,column='amount',additivity=ms.additive_all(),unit='USD')
revenue = ms.aggregate(name='revenue',measure=amount,agg='sum',time=event_time,nulls=ms.nulls.ignore(),empty=ms.empty.zero())
fact_count = ms.count(name='fact_count',entity=event_log,time=event_time)
profiles = ms.entity(name='profiles',datasource=warehouse,source=md.table('profiles'),primary_key=['order_id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('commerce.profiles.day'),grain='day',timezone='UTC'))
profile_id = ms.dimension_column(name='order_id',entity=profiles,column='order_id')
profile_region = ms.dimension_column(name='region',entity=profiles,column='region')
profile_day = ms.time_dimension_column(name='day',entity=profiles,column='day',granularity='day')
order_history = ms.relationship(name='order_history',from_entity=orders,to_entity=profiles,keys=[ms.join_on(order_id,profile_id)])
"""
        )


def replay(session: Session, coverage: str) -> mv.LogicalHistoryResult:
    claims = (
        ()
        if coverage == "unknown"
        else (
            mv.SourceOriginCompletenessDeclarationV1(
                inputs=tuple(
                    ms.ref.event("commerce." + name)
                    for name in ("order_created", "payment_captured", "order_closed")
                ),
                source_origin_ref=ms.ref.datasource("warehouse"),
                complete_through=PREFIX if coverage == "prefix" else END,
                rationale="All independently authored occurrences before the declared endpoint are retained.",
            ),
        )
    )
    return session.lifecycle.replay(
        ms.ref.state_model("commerce.order_lifecycle"),
        population=session.members(ms.ref.entity("commerce.orders")),
        window=mv.time_scope(start=OLD.isoformat(), end=END.isoformat()),
        seed=mv.from_inception(),
        completeness=claims,
    )


def assert_truth(result: _MaterializedRead, coverage: str) -> None:
    frame = result.to_pandas()
    assert frame.member.tolist() == ["o1", "o2", "o3"]
    if coverage == "complete":
        assert frame.value.tolist() == [True, False, False]
        assert frame.cell_tag.tolist() == ["defined"] * 3
    else:
        assert frame.value.isna().tolist() == [True] * 3
        assert frame.cell_tag.tolist() == ["unknown"] * 3


def assert_violations(result: _MaterializedRead, coverage: str) -> None:
    frame = result.to_pandas()
    if coverage == "unknown":
        assert frame.empty
        return
    assert frame["kind"].tolist() == (
        ["illegal_transition", "transition_from_terminal"]
        if coverage == "complete"
        else ["illegal_transition"]
    )
    assert frame.state_at_event.tolist() == (
        ["paid", "closed"] if coverage == "complete" else ["paid"]
    )
    assert frame.trigger.tolist() == ["commerce.payment_captured"] * len(frame)
    assert frame.occurred_at.tolist() == [
        START + timedelta(seconds=value)
        for value in ((11, 20) if coverage == "complete" else (11,))
    ]


def assert_distribution(result: mv.MaterializedStateDistributionResult, coverage: str) -> None:
    assert result._dataset is not None
    primary = result._dataset.verified().primary
    actual = {
        (row["checkpoint"], row["model_state"], row["axis_0"]): (
            row["known_state_count"],
            row["seeded_subject_count"],
            row["coverage_censored_count"],
            row["share_among_seeded"],
        )
        for row in primary.to_pylist()
    }
    expected: dict[tuple[datetime, str, str | None], tuple[int, int, int, float | None]] = {}
    for at in CHECKPOINTS:
        axes = ("old", "Other", "silent") if at == OLD else ("new", None, "silent")
        status = (
            (None, None, None)
            if coverage == "unknown" or (coverage == "prefix" and at == END)
            else ("created", "not_started", "not_started")
            if at == OLD
            else ("created", "created", "not_started")
            if at == START
            else ("closed", "paid", "not_started")
        )
        for axis, current in zip(axes, status, strict=True):
            seeded = int(current in STATES)
            for state in STATES:
                count = int(current == state)
                expected[at, state, axis] = (
                    count,
                    seeded,
                    int(current is None),
                    float(count) if seeded else None,
                )
    assert actual == expected
    assert len(actual) == 27


def selected(history: mv.LogicalHistoryResult, kind: str) -> mv.LogicalAnalysisDomain:
    if kind == "state":
        truth = history.read(
            mv.in_state(
                ms.model_state(model=ms.ref.state_model("commerce.order_lifecycle"), name="closed"),
                at=END,
            )
        )
        population = truth.where(truth.value.eq(True)).members()
        assert isinstance(population, mv.LogicalAnalysisDomain)
        return population
    relation = history.intervals() if kind == "interval" else history.violations()
    predicate = (
        relation.state.value.eq("closed")
        if isinstance(relation, mv.LogicalStateIntervalResult)
        else relation.kind.value.eq("transition_from_terminal")
    )
    population = relation.where(predicate).members(through=relation.subjects())
    assert isinstance(population, mv.LogicalAnalysisDomain)
    return population


def assert_complete_view(result: _MaterializedRead, kind: str) -> None:
    assert result._dataset is not None
    rows = result._dataset.verified().primary.to_pylist()
    if kind == "intervals":
        assert len(rows) == 5
        assert sorted(row["state"] for row in rows) == [
            "closed",
            "created",
            "created",
            "paid",
            "paid",
        ]
        completed_paid = [
            row for row in rows if row["state"] == "paid" and row["status"] == "completed"
        ]
        assert [row["observed_duration"] for row in completed_paid] == [timedelta(seconds=7)]
    elif kind == "dwell":
        assert len(rows) == 3
        state_key = result._dataset.verified().contract.key_fields[0]
        paid = next(row for row in rows if row[state_key] == "paid")
        assert paid["interval_count"] == 2 and paid["completed_count"] == 1
        assert (
            paid["mean_duration"]
            == paid["median_duration"]
            == paid["p90_duration"]
            == timedelta(seconds=7)
        )
    else:
        assert kind == "transitions"
        assert sum(row["count"] for row in rows) == 3


def produce(root: Path, risk: str) -> dict[str, Json]:
    author(root)
    ms.load(workspace_dir=root)
    session = mv.session.get_or_create("r94-history-public", report_timezone="UTC")
    inputs: dict[str, Json] = {}
    if risk == "views":
        for coverage in ("complete", "prefix", "unknown"):
            logical = replay(session, coverage)
            history = logical.execute()
            inputs[coverage + ":history"] = snapshot(history)
            distribution = logical.distribution(
                at=CHECKPOINTS, axes=(ms.ref.dimension("commerce.profiles.region"),)
            ).execute()
            assert_distribution(distribution, coverage)
            inputs[coverage + ":distribution"] = snapshot(distribution)
            violations = logical.violations().execute()
            assert_violations(violations, coverage)
            inputs[coverage + ":source_violations"] = snapshot(violations)
            truth = logical.read(
                mv.in_state(
                    ms.model_state(
                        model=ms.ref.state_model("commerce.order_lifecycle"), name="closed"
                    ),
                    at=END,
                )
            ).execute()
            assert_truth(truth, coverage)
            inputs[coverage + ":source_truth"] = snapshot(truth)
            if coverage == "complete":
                for kind, view in (
                    ("intervals", logical.intervals()),
                    ("dwell", logical.dwell()),
                    ("transitions", logical.transitions()),
                ):
                    result = view.execute()
                    assert_complete_view(result, kind)
                    inputs["complete:source_" + kind] = snapshot(result)
    else:
        assert risk == "captured_observations"
        captured_history = replay(session, "complete")
        for kind in ("state", "interval", "violation"):
            population = selected(captured_history, kind)
            for metric in ("revenue", "fact_count"):
                observed = population.observe(
                    ms.ref.metric("commerce." + metric),
                    during=mv.time_scope(start=START.isoformat(), end=END.isoformat()),
                    via=ms.ref.relationship("commerce.event_to_order"),
                    by=(mv.member(),),
                ).execute()
                frame = observed.to_pandas()
                assert frame.member.tolist() == ["o1"]
                assert frame.value.tolist() == [48 if metric == "revenue" else 4]
                inputs[kind + ":" + metric] = snapshot(observed)
    state: dict[str, Json] = {"session": session.id, "inputs": inputs}
    (root / "state.json").write_bytes(encode(state))
    assert session._runtime.store.resources(session.id) == ()
    return {"phase": "produce", "pid": os.getpid(), "inputs": inputs, "resources": 0}


def recover(root: Path, risk: str, phase: str) -> dict[str, Json]:
    state = read(root / "state.json")
    identity = state["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(history_execution, "execute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
        patch.object(graph_publication, "execute_verified_fixed", kernels),
    ):
        session = mv.session.resume(identity, by="id")
        before = run_ids(session)
        restored: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            restored_value = session.artifact(reference)
            assert isinstance(restored_value, _MaterializedRead)
            assert snapshot(restored_value) == saved
            restored[name] = restored_value
        assert run_ids(session) == before
        operations: dict[str, Continuation] = {}
        if risk == "views":
            for coverage in ("complete", "prefix", "unknown"):
                history = restored[coverage + ":history"]
                distribution = restored[coverage + ":distribution"]
                assert isinstance(history, mv.MaterializedHistoryResult)
                assert isinstance(distribution, mv.MaterializedStateDistributionResult)
                assert_distribution(distribution, coverage)
                operations[coverage + ":violations"] = history.violations()
                operations[coverage + ":truth"] = history.read(
                    mv.in_state(
                        ms.model_state(
                            model=ms.ref.state_model("commerce.order_lifecycle"), name="closed"
                        ),
                        at=END,
                    )
                )
                operations[coverage + ":known"] = distribution.known_state_count
                operations[coverage + ":censored"] = distribution.coverage_censored_count
                if coverage == "complete":
                    operations.update(
                        {
                            "complete:intervals": history.intervals(),
                            "complete:dwell": history.dwell(),
                            "complete:transitions": history.transitions(),
                        }
                    )
        else:
            for name, observed in restored.items():
                assert isinstance(observed, mv.MaterializedNumericRelation)
                operations[name] = observed.rollup()
        outputs: dict[str, Json] = {}
        for name, logical in operations.items():
            prior_runs, prior_kernels = run_ids(session), kernels.call_count
            if phase == "cold":
                with (
                    patch.object(graph_local_execution, "execute_verified_fixed", forbidden),
                    patch.object(graph_publication, "execute_verified_fixed", forbidden),
                ):
                    result = logical.execute()
            else:
                result = logical.execute()
            counts = (kernels.call_count - prior_kernels, len(run_ids(session) - prior_runs))
            if phase == "cold":
                assert counts == (0, 0), (name, counts)
            elif risk == "captured_observations":
                assert counts == (1, 1), (name, counts)
            else:
                assert counts in ((0, 0), (1, 1)), (name, counts)
            if name.endswith(":truth"):
                assert_truth(result, name.split(":")[0])
            elif name.endswith(":violations"):
                assert_violations(result, name.split(":")[0])
            elif risk == "captured_observations":
                assert result.to_pandas().value.tolist() == [48 if name.endswith("revenue") else 4]
            elif name in ("complete:intervals", "complete:dwell", "complete:transitions"):
                assert_complete_view(result, name.split(":")[1])
            saved = snapshot(result)
            prior = run_ids(session)
            with (
                patch.object(graph_local_execution, "execute_verified_fixed", forbidden),
                patch.object(graph_publication, "execute_verified_fixed", forbidden),
            ):
                assert snapshot(logical.execute()) == saved
            assert run_ids(session) == prior
            outputs[name] = saved
        if phase == "fixed":
            new_runs = len(run_ids(session) - before)
            assert kernels.call_count == new_runs
            if risk == "captured_observations":
                assert new_runs == len(operations) == 6
            state["outputs"] = outputs
            (root / "state.json").write_bytes(encode(state))
        else:
            assert outputs == obj(state["outputs"])
            assert run_ids(session) == before and kernels.call_count == 0
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
        }


if __name__ == "__main__":
    root, risk, phase, report = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    observed = produce(root, risk) if phase == "produce" else recover(root, risk, phase)
    report.write_bytes(encode(observed))
