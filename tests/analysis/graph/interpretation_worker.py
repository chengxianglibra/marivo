"""Independent source production and cold interpretation receipts."""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TypeAlias
from unittest.mock import patch

import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.lifecycle.history_public_recovery_worker import author
from tests.analysis.lifecycle.lifecycle_fixtures import END, START
from tests.analysis.statistics.journeys import create, inputs
from tests.support.json import Json, checked, encode, obj, read

Result: TypeAlias = _MaterializedRead | mv.MaterializedTable
PAIRING_KEY = "sales.order.order_id (identity), sales.order.member_seq (identity)"
OLD = START - timedelta(days=3)
PREFIX = START + timedelta(seconds=12)
STATES = ("created", "paid", "closed")
CHECKPOINTS = (OLD, START, END)


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


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("interpretation recovery accessed a source or current Semantic")


def snapshot(result: Result, *, check_display_budget: bool) -> dict[str, Json]:
    output = io.StringIO()
    with redirect_stdout(output):
        result.show(n=0, max_output_bytes=None)
    facts = dict(result.contract()._facts) if isinstance(result, _MaterializedRead) else {}
    if check_display_budget:
        bounded = io.StringIO()
        with redirect_stdout(bounded):
            result.show(n=2, max_output_bytes=8192)
        assert len(bounded.getvalue().encode()) <= 8192
    return obj(
        checked(
            json.loads(
                json.dumps(
                    {
                        "facts": facts,
                        "card": output.getvalue(),
                        "rows": result.to_pandas().to_dict("records"),
                    },
                    default=str,
                )
            )
        )
    )


def reference(result: Result) -> str:
    return (
        result.evidence_digest().artifact_ref.ref
        if isinstance(result, _MaterializedRead)
        else result.artifact_ref.ref
    )


def inspect_results(results: dict[str, Result]) -> dict[str, Json]:
    saved: dict[str, Json] = {
        name: snapshot(result, check_display_budget=name in {"distribution", "wide_table"})
        for name, result in results.items()
    }
    association = results["association"]
    assert isinstance(association, mv.MaterializedAssociationResult)
    association_snapshot = obj(saved["association"])
    assert obj(association_snapshot["facts"])["pairing_key"] == PAIRING_KEY
    assert PAIRING_KEY in str(association_snapshot["card"])
    association_rows = association_snapshot["rows"]
    assert isinstance(association_rows, list) and len(association_rows) == 1
    assert obj(association_rows[0])["value"] == 1.0
    for view in (association.coefficient, association.selected):
        assert dict(view.contract()._facts)["pairing_key"] == PAIRING_KEY
        output = io.StringIO()
        with redirect_stdout(output):
            view.show(n=0, max_output_bytes=None)
        assert PAIRING_KEY in output.getvalue()
    distribution = obj(obj(saved["distribution"])["facts"])
    assert distribution["row_grain"] == "checkpoint, model_state, commerce.profiles.region"
    assert "do not sum across states" in str(distribution["count_scope"])
    assert "alternate states of seeded Subjects" in str(distribution["zero_state_cells"])
    wide_card = str(obj(saved["wide_table"])["card"])
    assert wide_card.count("do not sum across states") == 1
    count_columns = ("seeded", "censored", *(f"count_{i}" for i in range(16)))
    assert "[" + ", ".join(count_columns) + "].count_scope" in wide_card
    mean = obj(obj(saved["mean"])["facts"])
    assert mean["duration_unit"] == "us"
    assert mean["duration_seconds"] == "seconds = ticks / 1000000"
    assert obj(obj(saved["dwell"])["facts"])["mean_duration.duration_unit"] == "us"
    assert obj(obj(saved["dwell"])["facts"])["row_grain"] == "model_state"
    dwell = results["dwell"]
    assert isinstance(dwell, mv.MaterializedDwellSummary)
    logical = dwell.where(dwell.completed_count.value.eq(1))
    expected_units = {
        name: value for name, value in dwell.contract()._facts if ".duration_" in name
    }
    assert expected_units == {
        name: value for name, value in logical.contract()._facts if ".duration_" in name
    }
    output = io.StringIO()
    with redirect_stdout(output):
        logical.contract().show()
    assert "seconds = ticks / 1000000" in output.getvalue()
    return saved


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_TELEMETRY"] = "off"
    state_path = root / "interpretation.json"
    if phase == "produce":
        root.mkdir(parents=True, exist_ok=True)
        history_root = root / "history"
        author(history_root)
        os.environ["MARIVO_PROJECT_ROOT"] = str(history_root)
        ms.load(workspace_dir=history_root)
        history_session = mv.session.get_or_create("interpretation", report_timezone="UTC")
        logical = replay(history_session, "prefix")
        distribution = logical.distribution(
            at=CHECKPOINTS, axes=(ms.ref.dimension("commerce.profiles.region"),)
        ).execute()
        assert_distribution(distribution, "prefix")
        dwell = replay(history_session, "complete").dwell().execute()
        results: dict[str, Result] = {
            "distribution": distribution,
            "dwell": dwell,
            "mean": dwell.mean_duration.execute(),
            "wide_table": mv.table(
                seeded=distribution.seeded_subject_count,
                censored=distribution.coverage_censored_count,
                **{f"count_{i}": distribution.seeded_subject_count for i in range(16)},
            ).execute(),
        }
        history_ids: dict[str, Json] = {name: reference(result) for name, result in results.items()}
        statistics_root = root / "statistics"
        os.environ["MARIVO_PROJECT_ROOT"] = str(statistics_root)
        statistics_session = create(statistics_root, "parquet")
        a, b, _ = inputs(statistics_session)
        association = a.correlate(b, method="pearson").execute()
        results.update(
            association=association,
        )
        statistics_ids: dict[str, Json] = {
            name: reference(result) for name, result in results.items() if name not in history_ids
        }
        saved = inspect_results(results)
        run_counts = (len(history_session.runs().items), len(statistics_session.runs().items))
        state_path.write_bytes(
            encode(
                {
                    "history_session": history_session.id,
                    "statistics_session": statistics_session.id,
                    "history_ids": history_ids,
                    "statistics_ids": statistics_ids,
                    "snapshots": saved,
                    "run_counts": list(run_counts),
                }
            )
        )
    else:
        state = read(state_path)
        results = {}
        sessions: dict[str, mv.Session] = {}
        with (
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
        ):
            for family in ("history", "statistics"):
                os.environ["MARIVO_PROJECT_ROOT"] = str(root / family)
                session_id = state[family + "_session"]
                assert isinstance(session_id, str)
                session = mv.session.resume(session_id, by="id")
                sessions[family] = session
                for name, artifact_id in obj(state[family + "_ids"]).items():
                    assert isinstance(artifact_id, str)
                    value = session.artifact(artifact_id)
                    assert isinstance(value, (_MaterializedRead, mv.MaterializedTable))
                    results[name] = value
            restored_association = results["association"]
            assert isinstance(restored_association, mv.MaterializedAssociationResult)
            recovered_runs = tuple(session.runs().items for session in sessions.values())
            assert inspect_results(results) == obj(state["snapshots"])
            assert recovered_runs == tuple(session.runs().items for session in sessions.values())
        assert phase == "cold"
        expected_run_counts = state["run_counts"]
        assert isinstance(expected_run_counts, list) and len(expected_run_counts) == 2
        assert [len(items) for items in recovered_runs] == expected_run_counts
    return {"phase": phase, "pid": os.getpid()}


if __name__ == "__main__":
    with patch.object(ibis.duckdb, "connect", partial(ibis.duckdb.connect, threads=1)):
        print(json.dumps(run(Path(sys.argv[1]), sys.argv[2])))
