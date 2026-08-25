"""Artifact identity, semantic-authority, and evidence revalidation contracts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.analysis._artifact_integrity as artifact_integrity_module
import marivo.analysis.evidence.pipeline as pipeline_module
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.analysis._artifact_revalidation import _evidence_satisfies_contract
from marivo.analysis.errors import (
    CrossSessionFrameError,
    EvidenceIntegrityError,
    EvidenceStoreUnavailableError,
    FrameCacheCorruptedError,
    FrameRefNotFound,
    SchemaVersionMismatchError,
)
from marivo.analysis.evidence.identity import canonical_json, make_issue_id
from marivo.analysis.evidence.types import EvidenceAvailabilityIssue, RawFallback
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.intents._observe_persist import _commit_observe_metric_frame
from marivo.analysis.session._runtime import persist_frame
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import connect_sales_orders, sales_backends


def _bootstrap_project(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "revalidation"\n')
    datasource_dir = tmp_path / "models" / "datasources"
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive', name='order_count')\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
        "ms.ratio(\n"
        "    name='average_order_value',\n"
        "    numerator=revenue,\n"
        "    denominator=order_count,\n"
        ")\n"
    )


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    connection = connect_sales_orders()
    return mv.session.get_or_create(
        name="revalidation",
        backends=sales_backends(connection),
        use_datasources=False,
    )


def _observe(session, *, start: str = "2026-07-01", end: str = "2026-07-31"):
    return session.observe(
        metrics=make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start=start, end=end),
    )


def _semantic_file(tmp_path: Path) -> Path:
    return tmp_path / "models" / "semantic" / "sales" / "datasets.py"


def _rewrite_evidence_state(
    session,
    artifact_ref: str,
    *,
    status: Literal["complete", "partial", "unavailable"],
    issue: EvidenceAvailabilityIssue | None = None,
) -> None:
    frame = session.get_frame(artifact_ref)
    session_row = session._store.get_artifact(session.id, artifact_ref)
    assert session_row is not None
    meta_path = session.project_root / session_row["meta_path"]
    updated_meta = frame.meta.model_copy(
        update={
            "evidence_status": status,
            "evidence_digest": None,
            "issues": (issue,) if issue is not None else (),
        }
    )
    meta_path.write_text(updated_meta.model_dump_json(indent=2))

    with sqlite3.connect(session._store.db_path) as conn:
        conn.execute(
            "UPDATE artifacts SET evidence_status = ? WHERE session_id = ? AND artifact_id = ?",
            (status, session.id, artifact_ref),
        )
    store = session._evidence_store()
    assert store is not None
    conn = store.read()
    conn.execute(
        "UPDATE artifacts SET evidence_status = ? WHERE session_id = ? AND artifact_id = ?",
        (status, session.id, artifact_ref),
    )
    conn.execute("DELETE FROM findings WHERE artifact_id = ?", (artifact_ref,))
    conn.execute("DELETE FROM artifact_digests WHERE artifact_id = ?", (artifact_ref,))
    conn.execute("DELETE FROM artifact_issues WHERE artifact_id = ?", (artifact_ref,))
    if issue is not None:
        conn.execute(
            "INSERT INTO artifact_issues "
            "(issue_id, session_id, artifact_id, kind, severity, issue_payload, created_at_us) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                issue.issue_id,
                session.id,
                artifact_ref,
                issue.kind,
                issue.severity,
                canonical_json(issue),
                1,
            ),
        )


def _availability_issue(
    artifact_ref: str,
    *,
    severity: Literal["warning", "blocking"],
) -> EvidenceAvailabilityIssue:
    return EvidenceAvailabilityIssue(
        issue_id=make_issue_id(
            artifact_id=artifact_ref,
            kind=f"evidence_partial:{severity}",
            source_refs=(artifact_ref,),
        ),
        kind="evidence_partial",
        severity=severity,
        source_refs=(artifact_ref,),
        failed_stage="extract",
        findings_available=False,
        fallback=RawFallback(
            artifact_ref=artifact_ref,
            findings_available=False,
            rows_available=True,
            recommended_when=("partial_evidence",),
        ),
        stable_error_category="TestExtractionFailure",
    )


def test_current_and_recovered_artifact_revalidation_is_stable_and_ephemeral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    recovered = session.get_frame(frame.ref)
    store = session._evidence_store()
    assert store is not None
    before = tuple(
        store.read()
        .execute(
            "SELECT evidence_status, frame_sha FROM artifacts WHERE artifact_id = ?",
            (frame.ref,),
        )
        .fetchone()
    )

    first = session.revalidate(frame)
    second = session.revalidate(recovered)

    assert isinstance(first, mv.ArtifactRevalidation)
    assert first.status == "admissible"
    assert first.semantic_status == "current"
    assert first.evidence_status == "complete"
    assert first.content_hash == frame.meta.content_hash
    assert first.fingerprint == second.fingerprint
    assert first.authority_fingerprint == second.authority_fingerprint
    assert first.checked_at <= second.checked_at
    assert "does not mean datasource freshness" in first.render()
    assert repr(first).startswith("<ArtifactRevalidation status=admissible")
    with pytest.raises(ValidationError):
        first.status = "stale"  # type: ignore[misc]
    after = tuple(
        store.read()
        .execute(
            "SELECT evidence_status, frame_sha FROM artifacts WHERE artifact_id = ?",
            (frame.ref,),
        )
        .fetchone()
    )
    assert after == before
    assert session.get_frame(frame.ref).meta == recovered.meta

    session.close()
    resumed = mv.session.resume(session.id, use_datasources=False)
    cold = resumed.revalidate(resumed.get_frame(frame.ref))
    assert cold.fingerprint == first.fingerprint
    assert cold.status == "admissible"


def test_unrelated_catalog_change_does_not_make_scoped_artifact_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    semantic_file = _semantic_file(tmp_path)
    semantic_file.write_text(
        semantic_file.read_text().replace("orders.order_id.count()", "orders.amount.count()")
    )
    session._catalog = ms.load()

    result = session.revalidate(frame)

    assert result.status == "admissible"
    assert result.semantic_status == "current"
    assert result.recorded_catalog_fingerprint != result.current_catalog_fingerprint


def test_dependency_drift_is_stale_and_missing_authority_is_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    semantic_file = _semantic_file(tmp_path)
    original = semantic_file.read_text()
    semantic_file.write_text(original.replace("amount.sum()", "amount.mean()"))
    session._catalog = ms.load()

    stale = session.revalidate(frame)

    assert stale.status == "stale"
    assert stale.semantic_status == "stale"
    assert any(issue.kind == "definition_drift_detected" for issue in stale.issues)
    assert "metric:sales.revenue" in stale.render()
    assert "re-run the producing operator" in stale.render().lower()

    semantic_file.write_text(original[: original.index("@ms.metric")])
    session._catalog = ms.load()
    unknown = session.revalidate(frame)

    assert unknown.status == "indeterminate"
    assert unknown.semantic_status == "indeterminate"
    assert any(issue.kind == "semantic_authority_unknown" for issue in unknown.issues)


def test_multi_source_delta_any_dependency_drift_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    july = _observe(session, start="2026-07-01", end="2026-07-31")
    august = _observe(session, start="2026-08-01", end="2026-08-31")
    delta = session.compare(august, july)
    semantic_file = _semantic_file(tmp_path)
    semantic_file.write_text(semantic_file.read_text().replace("amount.sum()", "amount.mean()"))
    session._catalog = ms.load()

    result = session.revalidate(delta)

    assert result.status == "stale"
    assert result.semantic_status == "stale"


@pytest.mark.parametrize(
    ("status", "severity", "expected_status"),
    (
        ("unavailable", None, "indeterminate"),
        ("partial", "warning", "admissible"),
        ("partial", "blocking", "indeterminate"),
    ),
)
def test_healthy_negative_evidence_states_remain_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: Literal["complete", "partial", "unavailable"],
    severity: Literal["warning", "blocking"] | None,
    expected_status: Literal["admissible", "stale", "indeterminate"],
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    issue = _availability_issue(frame.ref, severity=severity) if severity is not None else None
    _rewrite_evidence_state(session, frame.ref, status=status, issue=issue)

    result = session.revalidate(session.get_frame(frame.ref))

    assert result.evidence_status == status
    assert result.status == expected_status


def test_store_unavailable_and_cross_session_fail_with_existing_typed_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    other = mv.session.get_or_create(name="other", use_datasources=False)

    with pytest.raises(CrossSessionFrameError):
        other.revalidate(frame)

    store = session._evidence_store()
    assert store is not None
    store.close()
    session._judgment_store = None
    session._judgment_store_unavailable = True
    with pytest.raises(EvidenceStoreUnavailableError):
        session.revalidate(frame)


def test_committed_ledger_recovers_missing_session_store_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    original_registration = session._store.get_artifact(session.id, frame.ref)
    assert original_registration is not None
    session._store.delete_artifact(session.id, frame.ref)
    assert session._store.get_artifact(session.id, frame.ref) is None
    original_validate = artifact_integrity_module.load_canonical_artifact_evidence
    validation_observations: list[object | None] = []

    def validate_before_publication(**kwargs):
        validation_observations.append(session._store.get_artifact(session.id, frame.ref))
        return original_validate(**kwargs)

    monkeypatch.setattr(
        artifact_integrity_module,
        "load_canonical_artifact_evidence",
        validate_before_publication,
    )

    recovered = session.get_frame(frame.ref)

    registration = session._store.get_artifact(session.id, frame.ref)
    assert registration is not None
    assert validation_observations == [None]
    assert registration["content_hash"] == frame.meta.content_hash
    assert registration["created_at"] == original_registration["created_at"]
    assert recovered.ref == frame.ref
    assert session.revalidate(recovered).status == "admissible"


def test_recovery_preserves_artifact_page_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    first = _observe(session, start="2026-07-01", end="2026-07-15")
    _observe(session, start="2026-07-16", end="2026-07-31")
    before = [
        (row["artifact_id"], row["created_at"])
        for row in session._store.page_artifacts(
            session.id,
            kind=None,
            evidence_status=None,
            limit=10,
            after=None,
        )
    ]

    session._store.delete_artifact(session.id, first.ref)
    session.get_frame(first.ref)

    after = [
        (row["artifact_id"], row["created_at"])
        for row in session._store.page_artifacts(
            session.id,
            kind=None,
            evidence_status=None,
            limit=10,
            after=None,
        )
    ]
    assert after == before


def test_unavailable_retry_hides_stale_registration_before_complete_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    template = _observe(session)
    original_insert = pipeline_module._insert_projection

    def fail_first_projection(*_args, **_kwargs):
        raise OSError("evidence projection unavailable")

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_first_projection)
    first = _commit_observe_metric_frame(
        session=session,
        frame=template,
        params={"retry_seam": True},
        metric_id="sales.revenue",
        model_name="sales",
        stored_where={},
        semantic_kind="scalar",
    )
    assert first.evidence_status == "unavailable"
    assert session._store.get_artifact(session.id, first.ref) is not None

    observations: list[tuple[str, int, object | None]] = []

    def inspect_before_projection(store, **kwargs):
        artifact_ref = str(kwargs["artifact_id"])
        meta_path = session._layout.frames_dir / artifact_ref / "meta.json"
        sidecar_status = json.loads(meta_path.read_text())["evidence_status"]
        ledger_rows = (
            store.read()
            .execute(
                "SELECT count(*) FROM artifacts WHERE artifact_id = ?",
                (artifact_ref,),
            )
            .fetchone()[0]
        )
        registration = session._store.get_artifact(session.id, artifact_ref)
        observations.append((sidecar_status, ledger_rows, registration))
        with pytest.raises(FrameRefNotFound):
            session.get_frame(artifact_ref)
        return original_insert(store, **kwargs)

    monkeypatch.setattr(pipeline_module, "_insert_projection", inspect_before_projection)
    retried = _commit_observe_metric_frame(
        session=session,
        frame=first,
        params={"retry_seam": True},
        metric_id="sales.revenue",
        model_name="sales",
        stored_where={},
        semantic_kind="scalar",
    )

    assert retried.ref == first.ref
    assert retried.evidence_status == "complete"
    assert observations == [("complete", 0, None)]
    registration = session._store.get_artifact(session.id, retried.ref)
    assert registration is not None
    assert registration["evidence_status"] == "complete"


def test_corrupt_recovery_marker_never_registers_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    session._store.delete_artifact(session.id, frame.ref)
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    meta_path.write_text("{interrupted")

    with pytest.raises(FrameCacheCorruptedError):
        session.get_frame(frame.ref)

    assert session._store.get_artifact(session.id, frame.ref) is None


def test_recovery_marker_rejects_non_current_evidence_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    session._store.delete_artifact(session.id, frame.ref)
    store = session._evidence_store()
    assert store is not None
    store.read().execute("PRAGMA user_version = 3")

    with pytest.raises(SchemaVersionMismatchError):
        session.get_frame(frame.ref)

    assert session._store.get_artifact(session.id, frame.ref) is None


def test_recovery_marker_rejects_complete_artifact_with_missing_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    store = session._evidence_store()
    assert store is not None
    store.read().execute("DELETE FROM findings WHERE artifact_id = ?", (frame.ref,))
    session._store.delete_artifact(session.id, frame.ref)

    with pytest.raises(EvidenceIntegrityError):
        session.get_frame(frame.ref)

    assert session._store.get_artifact(session.id, frame.ref) is None


def test_sidecar_ledger_and_content_corruption_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    store = session._evidence_store()
    assert store is not None
    store.read().execute(
        "UPDATE artifact_digests SET fingerprint = 'tampered' WHERE artifact_id = ?",
        (frame.ref,),
    )
    with pytest.raises(EvidenceIntegrityError):
        session.revalidate(frame)

    session = mv.session.get_or_create(
        name="content",
        backends=sales_backends(connect_sales_orders()),
        use_datasources=False,
    )
    content_frame = _observe(session)
    row = session._store.get_artifact(session.id, content_frame.ref)
    assert row is not None
    meta_path = session.project_root / row["meta_path"]
    payload = json.loads(meta_path.read_text())
    payload["content_hash"] = "sha256:tampered"
    meta_path.write_text(json.dumps(payload))
    with pytest.raises(FrameCacheCorruptedError):
        session.revalidate(content_frame)


def test_complete_digest_references_missing_findings_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    store = session._evidence_store()
    assert store is not None
    finding_count = (
        store.read()
        .execute(
            "SELECT count(*) FROM findings WHERE artifact_id = ?",
            (frame.ref,),
        )
        .fetchone()[0]
    )
    assert finding_count > 0
    store.read().execute("DELETE FROM findings WHERE artifact_id = ?", (frame.ref,))

    with pytest.raises(EvidenceIntegrityError):
        session.revalidate(frame)


def test_derived_revalidation_rehashes_every_typed_source_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _observe(session, start="2026-07-01", end="2026-07-15")
    baseline = _observe(session, start="2026-07-16", end="2026-07-31")
    delta = session.compare(current, baseline)
    source_row = session._store.get_artifact(session.id, baseline.ref)
    assert source_row is not None
    source_path = session.project_root / source_row["path"]
    source_data = pd.read_parquet(source_path)
    source_data.loc[source_data.index[0], "value"] += 1
    source_data.to_parquet(source_path, index=False)

    with pytest.raises(EvidenceIntegrityError):
        session.revalidate(delta)


def test_linked_component_artifact_revalidates_as_healthy_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = session.observe(make_ref("sales.average_order_value", SemanticKind.METRIC))
    component = frame.components()
    store = session._evidence_store()
    assert store is not None
    assert (
        store.read()
        .execute(
            "SELECT 1 FROM artifacts WHERE artifact_id = ?",
            (component.ref,),
        )
        .fetchone()
        is None
    )

    result = session.revalidate(component)

    assert result.status == "indeterminate"
    assert result.semantic_status == "current"
    assert result.evidence_status == "unavailable"


def test_linked_coverage_artifact_revalidates_as_healthy_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    parent = _observe(session)
    coverage = CoverageFrame(
        _df=pd.DataFrame({"coverage_ratio": [1.0]}),
        meta=CoverageFrameMeta(
            ref="coverage_revalidation",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=1,
            byte_size=0,
            parent_ref=parent.ref,
            axes={},
        ),
    )
    coverage.meta = persist_frame(session, coverage)

    result = session.revalidate(coverage)

    assert result.status == "indeterminate"
    assert result.semantic_status == "current"
    assert result.evidence_status == "unavailable"


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_artifact",
        "finding_ownership",
        "issue_payload",
        "artifact_schema",
    ),
)
def test_evidence_ledger_mismatches_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    store = session._evidence_store()
    assert store is not None
    conn = store.read()

    if corruption == "missing_artifact":
        conn.execute("DELETE FROM findings WHERE artifact_id = ?", (frame.ref,))
        conn.execute("DELETE FROM artifact_digests WHERE artifact_id = ?", (frame.ref,))
        conn.execute("DELETE FROM artifact_issues WHERE artifact_id = ?", (frame.ref,))
        conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (frame.ref,))
    elif corruption == "finding_ownership":
        conn.execute(
            "UPDATE findings SET session_id = 'tampered' WHERE artifact_id = ?",
            (frame.ref,),
        )
    elif corruption == "issue_payload":
        issue = _availability_issue(frame.ref, severity="warning")
        conn.execute(
            "INSERT INTO artifact_issues "
            "(issue_id, session_id, artifact_id, kind, severity, issue_payload, created_at_us) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                issue.issue_id,
                session.id,
                frame.ref,
                issue.kind,
                issue.severity,
                canonical_json(issue),
                1,
            ),
        )
    elif corruption == "artifact_schema":
        conn.execute(
            "UPDATE artifacts SET artifact_schema_version = 'v3' WHERE artifact_id = ?",
            (frame.ref,),
        )
    with pytest.raises(EvidenceIntegrityError):
        session.revalidate(frame)


def test_evidence_schema_mismatch_is_stable_across_connection_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    store = session._evidence_store()
    assert store is not None
    store.read().execute("PRAGMA user_version = 3")

    with pytest.raises(SchemaVersionMismatchError):
        session.revalidate(frame)

    session.close()
    with pytest.raises(SchemaVersionMismatchError):
        session.revalidate(frame)


def test_revalidation_does_not_access_datasource_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)

    class _NoDatasourceAccess:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"unexpected datasource access: {name}")

    session._connection_runtime = _NoDatasourceAccess()

    assert session.revalidate(frame).status == "admissible"


def test_partial_contract_requires_explicit_safe_warning_fallback() -> None:
    warning = _availability_issue("art_test", severity="warning")
    blocking = _availability_issue("art_test", severity="blocking")

    assert _evidence_satisfies_contract(
        evidence_status="partial",
        digest_present=False,
        issues=(warning,),
    )
    assert not _evidence_satisfies_contract(
        evidence_status="partial",
        digest_present=False,
        issues=(blocking,),
    )
    assert not _evidence_satisfies_contract(
        evidence_status="partial",
        digest_present=False,
        issues=(),
    )
