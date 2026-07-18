"""One-transaction typed evidence commit pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis.evidence.pipeline as pipeline_module
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.store import open_evidence_store
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage


def _frame(tmp_path: Path, *, ordinal: int = 0) -> MetricFrame:
    data = pd.DataFrame({"value": [100.0 + ordinal]})
    return MetricFrame(
        _df=data,
        meta=MetricFrameMeta(
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
            axes={},
            measure={"field": "value", "aggregation": "sum"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )


def _commit(tmp_path: Path, *, emit_evidence: bool = True, store=True, ordinal: int = 0):
    evidence_store = open_evidence_store(tmp_path / "judgment.db") if store else None
    frame = _frame(tmp_path, ordinal=ordinal)
    try:
        result = commit_result(
            store=evidence_store,
            frames_dir=tmp_path / "frames",
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue", "ordinal": ordinal}),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric": f"sales.revenue@v{ordinal + 1}"}
            ),
            subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
            extractor_family="metric_frame",
            emit_evidence=emit_evidence,
        )
        return result, evidence_store
    except BaseException:
        if evidence_store is not None:
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
            store=store,
            frames_dir=tmp_path / "frames",
            frame=_frame(tmp_path),
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values={"metric": "sales.revenue"}),
            semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
            subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
            extractor_family="metric_frame",
        )
    assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0

    monkeypatch.setattr("marivo.analysis.evidence.pipeline._atomic_write_meta", original)
    retried = commit_result(
        store=store,
        frames_dir=tmp_path / "frames",
        frame=_frame(tmp_path),
        step_type="observe",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values={"metric": "sales.revenue"}),
        semantic_anchors=CommitSemanticAnchors(values={"metric": "sales.revenue@v1"}),
        subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
        extractor_family="metric_frame",
    )
    assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert retried.evidence_status == "complete"
    store.close()


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
