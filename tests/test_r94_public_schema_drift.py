"""Public physical schema drift refusal and exact retry on the selected graph."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.session._lazy_read_model import FailedRun
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory


def _publications(session: mv.Session) -> tuple[int, ...]:
    counts: list[int] = []
    with session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


@pytest.mark.runtime
def test_public_schema_drift_rejects_before_business_read_and_retries(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    logical = (
        case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
        .observe(
            ms.ref.metric(f"{n.domain}.{n.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        )
        .rollup()
    )
    previous = logical.execute()
    frame = previous.to_pandas()
    assert frame["value"].tolist() == [1000]
    assert previous._dataset is not None
    retained = previous._dataset.verified()
    descriptor = previous._dataset.artifact.descriptor
    state = previous.state
    contract = asdict(previous.contract())
    runs_before = case.session.runs().items
    published_before = _publications(case.session)
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("business-read")
        raise AssertionError("Schema drift entered business compilation or read")

    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE DOUBLE')
    try:
        with monkeypatch.context() as patch:
            for method in ("compile", "batches", "stage_derived"):
                patch.setattr(SourceSession, method, forbidden)
            with pytest.raises(DatasetConstructionError) as caught:
                logical.execute()
            error = caught.value
            assert error.expected == "the exact source schema selected before Run allocation"
            assert error.received == f"changed physical binding for {n.domain}.{n.order}"
            assert error.repair is not None
            assert error.repair.action == (
                "Rebuild and execute the logical graph against the current source schema."
            )
            assert error.location == "analysis.graph_preflight"
        after = case.session.runs().items
        new = [run for run in after if run.run_id not in {run.run_id for run in runs_before}]
        assert len(after) == len(runs_before) + 1 and len(new) == 1
        failed = new[0]
        assert isinstance(failed, FailedRun)
        assert failed.failure.kind == "execution_failed"
        assert failed.failure.phase == "stage_execution"
        assert failed.failure.safe_location == "analysis.graph.stage_execution"
        assert failed.failure.expected == "a fully validated atomic graph publication"
        assert failed.failure.received == "execution, cancellation or publication failure"
        assert _publications(case.session) == published_before
        assert case.session._runtime.store.resources(case.session.id) == ()
        assert calls == []
        assert previous.state == state
        assert previous._dataset.artifact.descriptor == descriptor
        assert asdict(previous.contract()) == contract
        assert previous.to_pandas().equals(frame)
        current = previous._dataset.verified()
        assert current.primary.equals(retained.primary, check_metadata=True)
        assert tuple(part.role for part in current.parts) == tuple(
            part.role for part in retained.parts
        )
        assert all(
            current_part.table.equals(old_part.table, check_metadata=True)
            for current_part, old_part in zip(current.parts, retained.parts, strict=True)
        )
    finally:
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE BIGINT')
    retried = logical.execute()
    assert retried.to_pandas()["value"].tolist() == [1000]
    assert len(case.session.runs().items) == len(runs_before) + 2
    assert case.session.get_run(retried.state.producing_run_ref).lifecycle == "succeeded"
    assert case.session._runtime.store.resources(case.session.id) == ()
    report = {
        "case": "public-order-amount-bigint-double-bigint-drift",
        "baseline_value": 1000,
        "retry_value": 1000,
        "schema_drift_failed_runs": 1,
        "terminal_kind": failed.failure.kind,
        "terminal_phase": failed.failure.phase,
        "terminal_location": failed.failure.safe_location,
        "schema_drift_publication_delta": [0, 0, 0],
        "business_calls": calls,
        "prior_artifact_and_full_parts_preserved": True,
        "error": {
            "kind": type(error).__name__,
            "expected": error.expected,
            "received": error.received,
            "repair": error.repair.action,
            "location": error.location,
        },
    }
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "public-schema-drift.json").write_text(json.dumps(report, indent=2) + "\n")
