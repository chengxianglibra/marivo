"""Each execution prepares current physical types and preserves committed output."""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticRuntimeError
from tests.shared_fixtures import DslCaseFactory, catalog_value, terminal_runs


def _publications(session: mv.Session) -> tuple[int, ...]:
    counts: list[int] = []
    with session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


def test_public_schema_drift_rejects_before_business_read_and_retries(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    n = case.names
    logical = session.observe(
        {"value": catalog_value(session.catalog, f"{n.domain}.{n.revenue}", kind="metric")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"),),
    )
    previous = logical.persist()
    frame = previous.to_pandas()
    assert frame["value"].tolist() == [1000]
    manifest = previous._local._audit_manifest(previous._require_stored("artifact_id"))
    evidence = previous.evidence(limit=100)
    # A compatible physical carrier is requalified for this invocation.
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE DOUBLE')
    changed = logical.persist()
    assert changed.to_pandas()["value"].tolist() == [1000.0]
    before = terminal_runs(session)
    published_before = _publications(session)
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("business-read")
        raise AssertionError("Invalid physical type entered business compilation or read")

    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE VARCHAR')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(SourceSession, "compile", forbidden)
            patch.setattr(SourceSession, "execute", forbidden)
            with pytest.raises(SemanticRuntimeError) as caught:
                logical.persist()
        cause = caught.value
        assert isinstance(cause, SemanticRuntimeError)
        assert (
            cause.expected
            == f"a numeric or duration physical type for {n.domain}.{n.order}.{n.amount}"
        )
        assert cause.received == "duckdb physical_type='string'"
        assert cause.repair
        assert terminal_runs(session) == before
        assert _publications(session) == published_before
        assert calls == []
        assert previous._local._audit_manifest(previous._require_stored("artifact_id")) == manifest
        assert previous.evidence(limit=100) == evidence
        assert session.persisted_frame(previous.artifact_id).to_pandas().equals(frame)
        assert session._runtime.store.resources(session.id) == ()
    finally:
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute(f'ALTER TABLE "{n.order}" ALTER "{n.amount}" TYPE BIGINT')
    retried = logical.persist()
    assert retried.to_pandas()["value"].tolist() == [1000]
    assert len(terminal_runs(session)) == len(before) + 1
    assert session._runtime.store.resources(session.id) == ()
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        report = {
            "case": "public-order-amount-bigint-double-string-bigint",
            "baseline_value": 1000,
            "compatible_value": 1000.0,
            "retry_value": 1000,
            "invalid_type_runs": 0,
            "publication_delta": [0, 0, 0],
            "business_calls": calls,
            "prior_artifact_preserved": True,
        }
        Path(directory, "public-schema-drift.json").write_text(json.dumps(report, indent=2) + "\n")
