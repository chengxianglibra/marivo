"""Source-origin provider semantics, lookback and shared deadline enforcement."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.domains.completeness import (
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    EventCoverageRequestV1,
    SourceOriginCoverageStartV1,
)
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_lifecycle_fixtures import END, START, history, setup_lifecycle

pytestmark = pytest.mark.runtime


def receipt(
    request: EventCoverageRequestV1, *, bounded: bool = False, prefix: bool = False
) -> EventCoverageReceiptV1:
    return EventCoverageReceiptV1(
        event_ref=request.event_ref,
        event_fingerprint=request.event_fingerprint,
        source_entity_ref=request.source_entity_ref,
        source_origin_ref=request.source_origin_ref,
        occurred_at_ref=request.occurred_at_ref,
        coverage_start=BoundedCoverageStartV1(complete_from=START - timedelta(days=100))
        if bounded
        else SourceOriginCoverageStartV1(source_origin_ref=request.source_origin_ref),
        complete_through=START + timedelta(hours=4) if prefix else END,
        authority="fixture.origin@v1",
        observed_at=END,
        source_binding_fingerprint=request.source_binding_fingerprint,
        execution_domain_id=request.execution_domain_id,
    )


@pytest.mark.parametrize("mode", ["origin", "bounded", "prefix", "supplemented"])
def test_provider_origin_and_lookback_are_independent_of_window(tmp_path: Path, mode: str) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path)
    requests: list[EventCoverageRequestV1] = []

    def provider(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        requests.append(request)
        return receipt(
            request, bounded=mode in ("bounded", "supplemented"), prefix=mode == "prefix"
        )

    runtime.event_coverage_provider = provider
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute(
            "UPDATE started_rows SET occurred_at=TIMESTAMP '2026-01-01 00:00:00' WHERE customer_id=1"
        )
    result = history(sources, complete=mode == "supplemented").execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.lifecycle_evidence is not None
    summary = record.descriptor.lifecycle_evidence
    assert Counter(request.event_ref.path for request in requests) == {
        "sales.started": 1,
        "sales.finished": 1,
    }
    assert summary.coverage.complete == (mode in ("origin", "supplemented"))
    assert summary.left_clipped_count == 1
    if mode == "supplemented":
        assert summary.coverage.basis == "declared"
        assert all(f.supplemented_observation is not None for f in summary.coverage.events)
    ledger = next(
        part
        for part in record.descriptor.retained_parts
        if part.role == "lifecycle_subject_coverage"
    )
    rows = pa.Table.from_batches(
        list(payload_batches(tmp_path, ledger.storage_receipt, policy=ReadPolicy(), audit=True))
    ).to_pylist()
    expected = (
        END
        if mode in ("origin", "supplemented")
        else START + timedelta(hours=4)
        if mode == "prefix"
        else None
    )
    assert all(row["known_through"] == expected for row in rows)
    assert runtime.revalidate(result.state.artifact_ref).storage_authority == "readable"


def test_provider_deadline_interrupts_without_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_lifecycle(tmp_path)
    requests: list[EventCoverageRequestV1] = []

    def slow(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        requests.append(request)
        backend.raw_sql("SELECT sum(sqrt(i)) FROM range(1000000000) t(i)").fetchone()
        return receipt(request)

    runtime.event_coverage_provider = slow
    logical = history(sources, complete=False)
    with monkeypatch.context() as deadline:
        deadline.setattr(admission, "_SOURCE_EXECUTION_DEADLINE_SECONDS", 0.02)
        with pytest.raises(MaterializationError):
            logical.execute()
    assert len(requests) == 1
    assert snapshot(runtime)["dataset_artifacts"] == snapshot(runtime)["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    runtime.event_coverage_provider = lambda backend, request: receipt(request)
    assert logical.execute().to_pandas().shape[0] == 3
