"""One-transaction typed evidence commit pipeline."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pandas as pd
import pytest
from pydantic import ValidationError

import marivo.analysis.evidence.pipeline as pipeline_module
from marivo._compat import UTC
from marivo.analysis._semantic_persistence import MeasureBindingV1
from marivo.analysis.attribution_contract import AttributionAxisBindingV1
from marivo.analysis.errors import ArtifactQualityError, SessionLockedByAnotherProcessError
from marivo.analysis.evidence.audit import query_findings
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.store import open_evidence_store
from marivo.analysis.evidence.types import EvidenceAvailabilityIssue, Subject
from marivo.analysis.frames.attribution import (
    AttributionFrame,
    AttributionFrameMeta,
    AttributionReconciliation,
)
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._store import SessionStore
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from tests.shared_fixtures import make_test_metric_contract, make_test_multi_metric_contract


def _frame(tmp_path: Path, *, ordinal: int = 0) -> MetricFrame:
    data = pd.DataFrame({"value": [100.0 + ordinal]})
    contract = make_test_metric_contract(data, metric_id="sales.revenue", axes={})
    return MetricFrame(
        _df=data,
        meta=MetricFrameMeta(
            catalog_definition_fingerprint=contract["catalog_definition_fingerprint"],
            semantic_dependency_digest=contract["semantic_dependency_digest"],
            kind="metric_frame",
            ref="placeholder",
            session_id="sess_1",
            project_root=str(tmp_path),
            produced_by_job=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            metric_identity=contract["metric_identity"],
            metric_identities=contract["metric_identities"],
            key_schema=contract["key_schema"],
            comparable_value_semantics=contract["comparable_value_semantics"],
            axes={},
            measure={"field": "value", "aggregation": "sum"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )


def _attribution_frame(tmp_path: Path) -> AttributionFrame:
    df = pd.DataFrame(
        {
            "region": ["US", "CA"],
            "contribution": [2.0, 1.0],
            "share_of_total_delta": [2.0 / 3.0, 1.0 / 3.0],
            "share_of_positive_pool": [2.0 / 3.0, 1.0 / 3.0],
            "share_of_negative_pool": [None, None],
            "rank": [1, 2],
        }
    )
    meta = AttributionFrameMeta(
        kind="attribution_frame",
        ref="placeholder",
        session_id="sess_1",
        project_root=str(tmp_path),
        produced_by_job="job_attribute",
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
        row_count=2,
        byte_size=0,
        lineage=Lineage(),
        metric_ids=["sales.revenue"],
        source_refs=["frame_delta"],
        scope_delta_ref="frame_delta",
        attribution_kind="decomposition",
        driver_field="region",
        value_column="delta",
        contribution_column="contribution",
        method="sum",
        params={"axes": ["region"]},
        semantic_kind="segmented",
        semantic_model="sales",
        row_contract_version="generic-attribution-rows/v3",
        axis_bindings=(
            AttributionAxisBindingV1(
                ref=RefPayloadV1.from_ref(ref_factory.dimension("sales.orders.region")),
                output_column="region",
            ),
        ),
        reconciliation=AttributionReconciliation(
            partition_count=1,
            total_delta=3.0,
            contribution_sum=3.0,
            residual=0.0,
            max_abs_residual=0.0,
        ),
    )
    return AttributionFrame(_df=df, meta=meta)


def _multi_metric_frame(tmp_path: Path) -> tuple[MetricFrame, tuple[str, ...]]:
    metric_ids = (
        "sales.zeta",
        "sales.alpha",
        "sales.theta",
        "sales.beta",
        "sales.omega",
        "sales.gamma",
        "sales.delta",
        "sales.epsilon",
    )
    values = (1_000.0, 1.0, 900.0, 2.0, 800.0, 3.0, 4.0, 5.0)
    columns = tuple(metric_id.rsplit(".", 1)[-1] for metric_id in metric_ids)
    data = pd.DataFrame({column: [value] for column, value in zip(columns, values, strict=True)})
    contract = make_test_multi_metric_contract(*metric_ids, axes={})
    bindings = tuple(
        MeasureBindingV1(
            identity=identity,
            value_column=column,
            display_name=column,
            additivity="additive",
        )
        for identity, column in zip(contract["metric_identities"], columns, strict=True)
    )
    frame = MetricFrame(
        _df=data,
        meta=MetricFrameMeta(
            **contract,
            kind="metric_frame",
            ref="placeholder",
            session_id="sess_1",
            project_root=str(tmp_path),
            produced_by_job=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id=None,
            axes={},
            measure={},
            measures=None,
            measure_bindings=bindings,
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    return frame, metric_ids


def _commit(tmp_path: Path, *, emit_evidence: bool = True, store=True, ordinal: int = 0):
    evidence_store = open_evidence_store(tmp_path / "judgment.db") if store else None
    frame = _frame(tmp_path, ordinal=ordinal)
    try:
        result = commit_result(
            session=None,
            store=evidence_store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "ordinal": ordinal}),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="metric_frame",
            emit_evidence=emit_evidence,
        )
        return result, evidence_store
    except BaseException:
        if evidence_store is not None:
            evidence_store.close()
        raise


def _commit_attribution(tmp_path: Path):
    evidence_store = open_evidence_store(tmp_path / "judgment.db")
    frame = _attribution_frame(tmp_path)
    try:
        result = commit_result(
            session=None,
            store=evidence_store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="attribute",
            inputs=CommitInputs(input_refs=["frame_delta"]),
            params=CommitParams(values={"axes": ["region"], "metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(analysis_axis="decomposition"),
            extractor_family="attribution_frame",
        )
        return result, evidence_store
    except BaseException:
        evidence_store.close()
        raise


def test_complete_commit_persists_identical_digest_in_db_and_sidecar(tmp_path: Path) -> None:
    result, store = _commit(tmp_path)
    assert store is not None
    try:
        assert result.evidence_status == "complete"
        assert result.evidence_digest is not None
        row = (
            store.read()
            .execute(
                "SELECT digest_payload, fingerprint FROM artifact_digests WHERE artifact_id = ?",
                (result.ref,),
            )
            .fetchone()
        )
        sidecar = json.loads(
            (tmp_path / "frames" / result.ref / "meta.json").read_text(encoding="utf-8")
        )
        assert json.loads(row["digest_payload"]) == sidecar["evidence_digest"]
        assert row["fingerprint"] == result.evidence_digest.fingerprint
        assert (
            store.read()
            .execute("SELECT count(*) FROM findings WHERE artifact_id = ?", (result.ref,))
            .fetchone()[0]
            == 2
        )
    finally:
        store.close()


def test_multi_metric_commit_persists_and_renders_metric_input_order(tmp_path: Path) -> None:
    store = open_evidence_store(tmp_path / "judgment.db")
    frame, metric_ids = _multi_metric_frame(tmp_path)
    try:
        result = commit_result(
            session=None,
            store=store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metrics": list(metric_ids)}),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="metric_frame",
        )

        assert result.evidence_digest is not None
        assert tuple(item.subject.metric for item in result.evidence_digest.items) == metric_ids[:5]
        assert result.evidence_digest.omissions.omitted_items == 3
        rendered = result.render(max_output_bytes=None)
        evidence_line = (
            "evidence: items=5 omitted=3 selection=metric_input_order; "
            f"recover=session.evidence.findings(artifact_ref='{result.ref}')"
        )
        assert evidence_line in rendered
        assert rendered.index(evidence_line) < rendered.index("preview:")
        for metric_id in metric_ids[:5]:
            assert f"subject={metric_id} observation" in rendered

        findings = query_findings(
            store=store,
            session_id="sess_1",
            artifact_ref=result.ref,
            kind="observation",
            limit=50,
        )
        assert {finding.subject.metric for finding in findings.items} == set(metric_ids)

        row = (
            store.read()
            .execute(
                "SELECT digest_payload, fingerprint FROM artifact_digests WHERE artifact_id = ?",
                (result.ref,),
            )
            .fetchone()
        )
        sidecar = json.loads(
            (tmp_path / "frames" / result.ref / "meta.json").read_text(encoding="utf-8")
        )
        assert json.loads(row["digest_payload"]) == sidecar["evidence_digest"]
        assert row["fingerprint"] == result.evidence_digest.fingerprint
    finally:
        store.close()


def test_suppressed_evidence_is_unavailable_without_finding_digest_or_issue(tmp_path: Path) -> None:
    result, store = _commit(tmp_path, emit_evidence=False)
    assert store is not None
    try:
        assert result.evidence_status == "unavailable"
        assert result.evidence_digest is None
        assert result.meta.issues == ()
        assert store.read().execute("SELECT count(*) FROM findings").fetchone()[0] == 0
        assert store.read().execute("SELECT count(*) FROM artifact_digests").fetchone()[0] == 0
        assert store.read().execute("SELECT count(*) FROM artifact_issues").fetchone()[0] == 0
    finally:
        store.close()


def test_missing_store_keeps_artifact_usable_and_marks_evidence_unavailable(tmp_path: Path) -> None:
    result, _ = _commit(tmp_path, store=False)
    assert result.evidence_status == "unavailable"
    assert result.evidence_digest is None
    assert [issue.kind for issue in result.meta.issues] == ["evidence_store_unavailable"]
    assert result.to_pandas().iloc[0, 0] == 100.0


def test_projection_write_failure_keeps_artifact_usable_and_marks_store_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_projection(*_args, **_kwargs):
        raise OSError("projection write failed")

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_projection)
    result, store = _commit(tmp_path)
    assert store is not None
    try:
        assert result.evidence_status == "unavailable"
        assert result.evidence_digest is None
        assert [issue.kind for issue in result.meta.issues] == ["evidence_store_unavailable"]
        assert result.to_pandas().iloc[0, 0] == 100.0
        assert (tmp_path / "frames" / result.ref / "meta.json").is_file()
        assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    finally:
        store.close()


def test_integrity_failure_persists_unavailable_marker_and_retries_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = pipeline_module._insert_projection
    calls = 0

    def fail_complete_projection_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.IntegrityError("injected canonical key collision")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_complete_projection_once)
    failed, store = _commit(tmp_path)
    assert store is not None
    try:
        issue = next(
            item
            for item in failed.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue)
            and item.kind == "evidence_store_unavailable"
        )
        assert failed.evidence_status == "unavailable"
        assert issue.stable_error_category == "IntegrityError"
        assert issue.repair is not None
        assert issue.repair.kind == "retry"
        assert (
            store.read()
            .execute(
                "SELECT evidence_status FROM artifacts WHERE artifact_id = ?",
                (failed.ref,),
            )
            .fetchone()[0]
            == "unavailable"
        )
        assert store.read().execute("SELECT count(*) FROM findings").fetchone()[0] == 0
        assert store.read().execute("SELECT count(*) FROM artifact_digests").fetchone()[0] == 0
        issue_row = (
            store.read()
            .execute(
                "SELECT issue_payload FROM artifact_issues WHERE artifact_id = ?",
                (failed.ref,),
            )
            .fetchone()
        )
        sidecar = json.loads(
            (tmp_path / "frames" / failed.ref / "meta.json").read_text(encoding="utf-8")
        )
        assert json.loads(issue_row["issue_payload"]) == sidecar["issues"][0]
    finally:
        store.close()

    retried, retried_store = _commit(tmp_path)
    assert retried_store is not None
    try:
        assert retried.ref == failed.ref
        assert retried.evidence_status == "complete"
        assert retried.evidence_digest is not None
        assert retried_store.read().execute("SELECT count(*) FROM findings").fetchone()[0] == 2
        assert (
            retried_store.read().execute("SELECT count(*) FROM artifact_digests").fetchone()[0] == 1
        )
        assert (
            retried_store.read().execute("SELECT count(*) FROM artifact_issues").fetchone()[0] == 0
        )
    finally:
        retried_store.close()


@pytest.mark.parametrize("failure_kind", ["write", "lock"])
def test_unavailable_retry_failure_restores_sidecar_and_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    original = pipeline_module._insert_projection
    calls = 0

    def fail_complete_projection_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.IntegrityError("injected canonical key collision")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_complete_projection_once)
    failed, store = _commit(tmp_path)
    assert store is not None
    artifact_id = failed.ref
    meta_path = tmp_path / "frames" / artifact_id / "meta.json"
    previous_meta = meta_path.read_bytes()
    previous_issue = (
        store.read()
        .execute(
            "SELECT issue_payload FROM artifact_issues WHERE artifact_id = ?",
            (artifact_id,),
        )
        .fetchone()["issue_payload"]
    )
    store.close()

    session_store = SessionStore(project_root=tmp_path)
    with session_store._connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, name, question, cwd, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_1",
                "retry",
                None,
                str(tmp_path),
                "2026-07-18T00:00:00+00:00",
                "2026-07-18T00:00:00+00:00",
            ),
        )
    session_store.record_artifact(
        session_id="sess_1",
        artifact_id=artifact_id,
        kind=failed.meta.kind,
        path=f"frames/{artifact_id}/data.parquet",
        meta_path=f"frames/{artifact_id}/meta.json",
        content_hash=failed.meta.content_hash,
        produced_by_job=failed.meta.produced_by_job,
        evidence_status=failed.evidence_status,
        created_at=failed.meta.created_at.isoformat(),
    )
    previous_registration = dict(session_store.get_artifact("sess_1", artifact_id))

    def fail_retry_projection(*_args, **_kwargs):
        if failure_kind == "lock":
            raise SessionLockedByAnotherProcessError(message="injected evidence lock")
        raise OSError("injected evidence write failure")

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_retry_projection)
    evidence_store = open_evidence_store(tmp_path / "judgment.db")
    frame = _frame(tmp_path)
    session = SimpleNamespace(id="sess_1", _store=session_store)
    expected_error = SessionLockedByAnotherProcessError if failure_kind == "lock" else OSError
    try:
        with pytest.raises(expected_error):
            commit_result(
                session=cast("Any", session),
                store=evidence_store,
                frames_dir=tmp_path / "frames",
                frame=frame,
                step_type="observe",
                inputs=CommitInputs(input_refs=[]),
                params=CommitParams(values={"metric": "sales.revenue", "ordinal": 0}),
                semantic_anchors=CommitSemanticAnchors.from_frame(frame),
                subject=Subject(analysis_axis="scalar"),
                extractor_family="metric_frame",
            )

        assert meta_path.read_bytes() == previous_meta
        current_issue = (
            evidence_store.read()
            .execute(
                "SELECT issue_payload FROM artifact_issues WHERE artifact_id = ?",
                (artifact_id,),
            )
            .fetchone()["issue_payload"]
        )
        assert current_issue == previous_issue
        assert dict(session_store.get_artifact("sess_1", artifact_id)) == previous_registration
    finally:
        evidence_store.close()


def test_digest_failure_retains_typed_findings_and_marks_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_digest(**_kwargs):
        raise RuntimeError("digest failure")

    monkeypatch.setattr("marivo.analysis.evidence.pipeline.build_artifact_digest", fail_digest)
    result, store = _commit(tmp_path)
    assert store is not None
    try:
        assert result.evidence_status == "partial"
        assert result.evidence_digest is None
        assert [issue.kind for issue in result.meta.issues] == ["evidence_digest_unavailable"]
        assert store.read().execute("SELECT count(*) FROM findings").fetchone()[0] == 2
    finally:
        store.close()


def test_digest_failure_issue_carries_typed_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #73: a digest-stage blocking issue must carry a typed repair
    pointing at the frame's own operator, so an agent reads the next step off
    the issue instead of getting repair=None."""

    def fail_digest(**_kwargs):
        raise RuntimeError("digest failure")

    monkeypatch.setattr("marivo.analysis.evidence.pipeline.build_artifact_digest", fail_digest)
    result, store = _commit(tmp_path)
    assert store is not None
    try:
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue)
            and item.kind == "evidence_digest_unavailable"
        )
        assert issue.failed_stage == "digest"
        assert issue.stable_error_category == "RuntimeError"
        assert issue.findings_available is True
        assert issue.repair is not None
        assert issue.repair.kind == "inspect"
        assert issue.repair.action
        assert "re-run observe" in issue.repair.action
        assert issue.repair.help_target.surface == "analysis"
        assert issue.repair.help_target.canonical_id == "observe"
    finally:
        store.close()


def test_missing_store_issue_carries_typed_repair(tmp_path: Path) -> None:
    """Issue #73: a store-unavailable blocking issue from a missing store must
    carry an environment repair naming what to restore before retry."""
    result, _ = _commit(tmp_path, store=False)
    issue = next(
        item
        for item in result.meta.issues
        if isinstance(item, EvidenceAvailabilityIssue) and item.kind == "evidence_store_unavailable"
    )
    assert issue.failed_stage == "store"
    assert issue.stable_error_category == "store_unavailable"
    assert issue.findings_available is False
    assert issue.repair is not None
    assert issue.repair.kind == "environment"
    assert issue.repair.action
    assert "evidence store is unavailable" in issue.repair.action
    assert "re-run observe" in issue.repair.action
    assert issue.repair.help_target.surface == "analysis"
    assert issue.repair.help_target.canonical_id == "observe"


def test_projection_failure_issue_carries_typed_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #73: a store-unavailable blocking issue from a projection write
    failure must carry an environment repair preserving the real error
    category."""

    def fail_projection(*_args, **_kwargs):
        raise OSError("projection write failed")

    monkeypatch.setattr(pipeline_module, "_insert_projection", fail_projection)
    result, store = _commit(tmp_path)
    assert store is not None
    try:
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue)
            and item.kind == "evidence_store_unavailable"
        )
        assert issue.failed_stage == "store"
        assert issue.stable_error_category == "OSError"
        assert issue.findings_available is False
        assert issue.repair is not None
        assert issue.repair.kind == "environment"
        assert issue.repair.action
        assert "re-run observe" in issue.repair.action
        assert issue.repair.help_target.surface == "analysis"
        assert issue.repair.help_target.canonical_id == "observe"
    finally:
        store.close()


def test_meta_write_failure_removes_db_registration_and_retry_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_evidence_store(tmp_path / "judgment.db")
    original = pipeline_module._atomic_write_meta

    def fail_meta(_path, _payload):
        raise OSError("meta write failed")

    monkeypatch.setattr("marivo.analysis.evidence.pipeline._atomic_write_meta", fail_meta)
    with pytest.raises(OSError, match="meta write failed"):
        commit_result(
            session=None,
            store=store,
            frames_dir=tmp_path / "frames",
            frame=_frame(tmp_path),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
    assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0

    monkeypatch.setattr("marivo.analysis.evidence.pipeline._atomic_write_meta", original)
    retried = commit_result(
        session=None,
        store=store,
        frames_dir=tmp_path / "frames",
        frame=_frame(tmp_path),
        step_type="observe",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values={"metric": "sales.revenue"}),
        semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
        subject=Subject(analysis_axis="scalar"),
        extractor_family="metric_frame",
    )
    assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert retried.evidence_status == "complete"
    store.close()


def test_meta_is_published_before_complete_evidence_becomes_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger commit is the publication marker only after meta is durable."""
    store = open_evidence_store(tmp_path / "judgment.db")
    original = pipeline_module._atomic_write_meta
    observed_artifact_counts: list[int] = []

    def inspect_before_meta(path, payload):
        observed_artifact_counts.append(
            store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0]
        )
        original(path, payload)

    monkeypatch.setattr(pipeline_module, "_atomic_write_meta", inspect_before_meta)
    try:
        result = commit_result(
            session=None,
            store=store,
            frames_dir=tmp_path / "frames",
            frame=_frame(tmp_path),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
        assert observed_artifact_counts == [0]
        assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
        assert (tmp_path / "frames" / result.ref / "meta.json").is_file()
    finally:
        store.close()


def test_meta_interrupt_never_publishes_complete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_evidence_store(tmp_path / "judgment.db")

    def interrupt_meta(_path, _payload):
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline_module, "_atomic_write_meta", interrupt_meta)
    try:
        with pytest.raises(KeyboardInterrupt):
            commit_result(
                session=None,
                store=store,
                frames_dir=tmp_path / "frames",
                frame=_frame(tmp_path),
                step_type="observe",
                inputs=CommitInputs(input_refs=[]),
                params=CommitParams(values={"metric": "sales.revenue"}),
                semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
                subject=Subject(analysis_axis="scalar"),
                extractor_family="metric_frame",
            )
        assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    finally:
        store.close()


def test_parquet_interrupt_never_publishes_sidecar_or_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_evidence_store(tmp_path / "judgment.db")

    def interrupt_replace(_source, target):
        if Path(target).name == "data.parquet":
            raise OSError("injected parquet replace failure")
        raise AssertionError(f"unexpected replace target: {target}")

    monkeypatch.setattr(pipeline_module.os, "replace", interrupt_replace)
    try:
        with pytest.raises(OSError, match="injected parquet replace failure"):
            commit_result(
                session=None,
                store=store,
                frames_dir=tmp_path / "frames",
                frame=_frame(tmp_path),
                step_type="observe",
                inputs=CommitInputs(input_refs=[]),
                params=CommitParams(values={"metric": "sales.revenue"}),
                semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
                subject=Subject(analysis_axis="scalar"),
                extractor_family="metric_frame",
            )
        assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        assert list((tmp_path / "frames").rglob("meta.json")) == []
        assert list((tmp_path / "frames").rglob("*.tmp")) == []
    finally:
        store.close()


def test_evidence_lock_is_typed_and_not_downgraded_to_unavailable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "judgment.db"
    first = open_evidence_store(db_path)
    second = open_evidence_store(db_path, busy_timeout_ms=50)
    try:
        with (
            first.transaction(immediate=True),
            pytest.raises(SessionLockedByAnotherProcessError),
        ):
            commit_result(
                session=None,
                store=second,
                frames_dir=tmp_path / "frames",
                frame=_frame(tmp_path),
                step_type="observe",
                inputs=CommitInputs(input_refs=[]),
                params=CommitParams(values={"metric": "sales.revenue"}),
                semantic_anchors=CommitSemanticAnchors.from_frame(_frame(tmp_path)),
                subject=Subject(analysis_axis="scalar"),
                extractor_family="metric_frame",
            )
        assert second.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    finally:
        second.close()
        first.close()


def test_repeated_commit_reuses_existing_projection_without_rewriting_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, store = _commit(tmp_path)
    assert store is not None
    before = {
        table: store.read().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("artifacts", "findings", "artifact_digests")
    }
    store.close()

    def fail_if_rewritten(_path, _payload):
        raise AssertionError("an immutable committed artifact must not rewrite meta.json")

    monkeypatch.setattr(pipeline_module, "_atomic_write_meta", fail_if_rewritten)
    repeated, repeated_store = _commit(tmp_path)
    assert repeated_store is not None
    try:
        after = {
            table: repeated_store.read().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("artifacts", "findings", "artifact_digests")
        }
        assert repeated.ref == first.ref
        assert repeated.evidence_digest == first.evidence_digest
        assert after == before
    finally:
        repeated_store.close()


def test_attribution_extract_failure_non_blocking_with_typed_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #68: readable/reconciled attribution rows must not surface a blocking
    evidence_partial with repair=None.

    When finding extraction raises but the attribution rows are already
    materialized and reconciliation verified, the evidence_partial issue must be
    downgraded to warning (not blocking) and carry a typed repair preserving the
    real stable error category.
    """

    def fail_extract(**kwargs):
        raise ValidationError.from_exception_data(
            "ContributionFindingValue",
            [
                {
                    "type": "model_attributes_type",
                    "loc": ("dimension_keys", "channel"),
                    "input": {"type": "dict"},
                }
            ],
        )

    monkeypatch.setattr(pipeline_module, "_extract_findings", fail_extract)
    result, store = _commit_attribution(tmp_path)
    assert store is not None
    try:
        assert result.evidence_status == "partial"
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue) and item.kind == "evidence_partial"
        )
        assert issue.severity == "warning"
        assert issue.failed_stage == "extract"
        assert issue.stable_error_category == "ValidationError"
        assert issue.findings_available is False
        assert issue.fallback.rows_available is True
        assert issue.repair is not None
        assert issue.repair.kind == "inspect"
        assert issue.repair.action
        assert issue.repair.help_target.surface == "analysis"
        assert issue.repair.help_target.canonical_id == "attribute"
        # Rows remain readable even though findings extraction failed.
        assert len(result.to_pandas()) == 2
    finally:
        store.close()


def test_attribution_unreconciled_is_rejected_before_evidence_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocking construction quality prevents any Artifact publication."""

    def fail_extract(**kwargs):
        raise ValidationError.from_exception_data(
            "ContributionFindingValue",
            [
                {
                    "type": "model_attributes_type",
                    "loc": ("dimension_keys", "channel"),
                    "input": {"type": "dict"},
                }
            ],
        )

    monkeypatch.setattr(pipeline_module, "_extract_findings", fail_extract)
    frame = _attribution_frame(tmp_path)
    frame.meta = frame.meta.model_copy(update={"reconciliation": None})
    evidence_store = open_evidence_store(tmp_path / "judgment.db")
    try:
        with pytest.raises(ArtifactQualityError) as excinfo:
            commit_result(
                session=None,
                store=evidence_store,
                frames_dir=tmp_path / "frames",
                frame=frame,
                step_type="attribute",
                inputs=CommitInputs(input_refs=["frame_delta"]),
                params=CommitParams(values={"axes": ["region"], "metric": "sales.revenue"}),
                semantic_anchors=CommitSemanticAnchors.from_frame(frame),
                subject=Subject(analysis_axis="decomposition"),
                extractor_family="attribution_frame",
            )
        assert excinfo.value._context["failed_checks"]
        assert evidence_store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        assert not (tmp_path / "frames").exists()
    finally:
        evidence_store.close()


def test_non_attribution_extract_failure_repair_points_at_own_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-attribution frame whose findings extraction fails must get a repair
    pointing at its own operator, not at attribute.

    commit_result is the shared commit path for every frame family; the
    attribution-specific reconciliation wording and the analysis.attribute
    help_target only apply to attribution frames (issue #68 re-review P3-1).
    """
    from pydantic import ValidationError

    def fail_extract(**kwargs):
        raise ValidationError.from_exception_data(
            "ContributionFindingValue",
            [
                {
                    "type": "model_attributes_type",
                    "loc": ("dimension_keys", "channel"),
                    "input": {"type": "dict"},
                }
            ],
        )

    monkeypatch.setattr(pipeline_module, "_extract_findings", fail_extract)
    result, store = _commit(tmp_path)  # metric_frame / step_type="observe"
    assert store is not None
    try:
        assert result.evidence_status == "partial"
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue) and item.kind == "evidence_partial"
        )
        assert issue.severity == "blocking"
        assert issue.stable_error_category == "ValidationError"
        assert issue.repair is not None
        assert issue.repair.kind == "inspect"
        # The repair must point at observe (this frame's operator), never at
        # attribute (P3-1: else branch hardcoded the attribution wording).
        assert issue.repair.help_target.canonical_id == "observe"
        assert "attribute" not in issue.repair.action
        assert "re-run observe" in issue.repair.action
    finally:
        store.close()


def test_funnel_step_extract_failure_repair_help_target_is_resolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A funnel compare step's extraction-failure repair must carry a
    resolvable help_target.

    _operator_for pass-throughs compare.funnel verbatim into the repair's
    operator; that dotted name is not registered on the analysis help surface
    (marivo.help("analysis.compare.funnel") raises MarivoHelpTargetError), so
    the help_target must be normalized to the parent id "compare" (re-review
    P3-2). The action text keeps the exact dotted operator.
    """
    from pydantic import ValidationError

    def fail_extract(**kwargs):
        raise ValidationError.from_exception_data(
            "ContributionFindingValue",
            [
                {
                    "type": "model_attributes_type",
                    "loc": ("dimension_keys", "channel"),
                    "input": {"type": "dict"},
                }
            ],
        )

    monkeypatch.setattr(pipeline_module, "_extract_findings", fail_extract)
    evidence_store = open_evidence_store(tmp_path / "judgment.db")
    frame = _frame(tmp_path)
    try:
        result = commit_result(
            session=None,
            store=evidence_store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="compare.funnel",
            inputs=CommitInputs(input_refs=["a", "b"]),
            params=CommitParams(values={"metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(analysis_axis="decomposition"),
            extractor_family="delta_frame",
        )
        assert result.evidence_status == "partial"
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue) and item.kind == "evidence_partial"
        )
        assert issue.repair is not None
        assert issue.repair.kind == "inspect"
        # Action text keeps the exact dotted operator...
        assert "re-run compare.funnel" in issue.repair.action
        # ...but the help_target normalizes to the resolvable parent id.
        assert issue.repair.help_target.canonical_id == "compare"
        import marivo

        marivo.help(issue.repair.help_target.display)
    finally:
        evidence_store.close()


def test_select_metric_digest_failure_repair_help_target_is_resolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A select_metric step's digest-failure repair must carry a resolvable
    help_target.

    _operator_for pass-throughs select_metric verbatim into the repair's
    operator; that dotted name is not registered on the analysis help surface
    (marivo.help("analysis.select_metric") raises MarivoHelpTargetError), so
    the help_target must be normalized to the parent id "MetricFrame.metric"
    (re-review P3, MR !71). The action text keeps the exact dotted operator.
    """

    def fail_digest(**_kwargs):
        raise RuntimeError("digest failure")

    monkeypatch.setattr("marivo.analysis.evidence.pipeline.build_artifact_digest", fail_digest)
    evidence_store = open_evidence_store(tmp_path / "judgment.db")
    frame = _frame(tmp_path)
    try:
        result = commit_result(
            session=None,
            store=evidence_store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="select_metric",
            inputs=CommitInputs(input_refs=["a", "b"]),
            params=CommitParams(values={"metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(analysis_axis="scalar"),
            extractor_family="projection",
        )
        assert result.evidence_status == "partial"
        issue = next(
            item
            for item in result.meta.issues
            if isinstance(item, EvidenceAvailabilityIssue)
            and item.kind == "evidence_digest_unavailable"
        )
        assert issue.repair is not None
        assert issue.repair.kind == "inspect"
        # Action text keeps the exact dotted operator...
        assert "re-run select_metric" in issue.repair.action
        # ...but the help_target normalizes to the resolvable parent id.
        assert issue.repair.help_target.canonical_id == "MetricFrame.metric"
        import marivo

        marivo.help(issue.repair.help_target.display)
    finally:
        evidence_store.close()


def test_pipeline_atomic_write_parquet_disables_dictionary_encoding(tmp_path: Path) -> None:
    """Issue #77 P2: the evidence pipeline writer must not dictionary-encode.

    pyarrow 25.0.0 races when multithreaded readers load dictionary-encoded
    pages, so every committed frame (including evidence pipeline outputs) must
    be written with ``use_dictionary=False``.
    """
    import pyarrow.parquet as pq

    target = tmp_path / "data.parquet"
    # Low-cardinality string columns are exactly what pyarrow dictionary-encodes
    # by default.
    df = pd.DataFrame({"component": ["a", "b", "c"] * 50, "value": list(range(150))})
    pipeline_module._atomic_write_parquet(df, target)

    parquet_file = pq.ParquetFile(target)
    encodings = {
        str(encoding)
        for col in range(parquet_file.metadata.num_columns)
        for encoding in parquet_file.metadata.row_group(0).column(col).encodings
    }
    assert not any("DICTIONARY" in encoding for encoding in encodings)


def test_reuse_committed_result_reads_through_retrying_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #77 P2: the reuse path must read frames via ``_read_parquet_frame``.

    A bare ``pd.read_parquet`` here would silently swallow the transient
    dictionary-bounds error (via ``except Exception: return None``) and force a
    recompute. Guard the wiring so a revert back to ``pd.read_parquet`` fails
    this test.
    """
    first, store = _commit(tmp_path)
    assert store is not None
    store.close()

    calls: dict[str, int] = {"n": 0}
    real = pipeline_module._read_parquet_frame

    def counting_reader(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(pipeline_module, "_read_parquet_frame", counting_reader)
    repeated, repeated_store = _commit(tmp_path)
    assert repeated_store is not None
    try:
        assert repeated.ref == first.ref
        assert calls["n"] >= 1
    finally:
        repeated_store.close()
