"""Public History artifact recovery in independent producer and offline processes."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import lifecycle_project_files, run_ids
from tests.support.json import Json, checked, encode, obj, read

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
OLD = START - timedelta(days=3)
END = START + timedelta(seconds=100)


def snapshot(result: mv.MaterializedHistoryResult) -> dict[str, Json]:
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


def replay(session: Session) -> mv.LogicalHistoryResult:
    completeness = mv.SourceOriginCompletenessDeclarationV1(
        inputs=tuple(
            ms.ref.event("commerce." + name)
            for name in ("order_created", "payment_captured", "order_closed")
        ),
        source_origin_ref=ms.ref.datasource("warehouse"),
        complete_through=END,
        rationale="All independently authored occurrences before the declared endpoint are retained.",
    )
    return session.lifecycle.replay(
        ms.ref.state_model("commerce.order_lifecycle"),
        population=session.members(ms.ref.entity("commerce.orders")),
        window=mv.time_scope(start=OLD.isoformat(), end=END.isoformat()),
        seed=mv.from_inception(),
        completeness=(completeness,),
    )


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("public cold History restoration accessed project, source, or execution")


def produce(root: Path) -> dict[str, Json]:
    author(root)
    ms.load(workspace_dir=root)
    session = mv.session.get_or_create("r94-history-public", report_timezone="UTC")
    saved = snapshot(replay(session).execute())
    state: dict[str, Json] = {"session": session.id, "history": saved}
    (root / "state.json").write_bytes(encode(state))
    assert session._runtime.store.resources(session.id) == ()
    return {"phase": "produce", "pid": os.getpid(), "history": saved, "resources": 0}


def restore(root: Path) -> dict[str, Json]:
    state = read(root / "state.json")
    identity = state["session"]
    assert isinstance(identity, str)
    saved = obj(state["history"])
    reference = saved["artifact"]
    assert isinstance(reference, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(SourceSession, "batches", forbidden),
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
        history = session.artifact(reference)
        assert isinstance(history, mv.MaterializedHistoryResult)
        assert snapshot(history) == saved
        assert run_ids(session) == before
        assert kernels.call_count == 0
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": "restore",
            "pid": os.getpid(),
            "history": snapshot(history),
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
        }


if __name__ == "__main__":
    root, phase, report = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    observed = produce(root) if phase == "produce" else restore(root)
    report.write_bytes(encode(observed))
