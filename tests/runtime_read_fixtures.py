"""Deterministic private Session-runtime read fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from marivo._compat import UTC
from marivo.analysis.frames.coverage import CoverageFrameMeta
from marivo.analysis.frames.metric import MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import Session
from tests.shared_fixtures import make_test_metric_meta_contract


@dataclass
class RuntimeReadHarness:
    project_root: Path
    store: SessionStore
    session_id: str
    session: object
    _clock: int = 0

    @classmethod
    def create(cls, project_root: Path) -> RuntimeReadHarness:
        store = SessionStore(project_root=project_root)
        row = store.get_or_insert_session(name="runtime-reads", question=None, cwd=project_root)
        session_id = str(row["id"])
        session = SimpleNamespace(
            id=session_id,
            project_root=project_root,
            _store=store,
        )
        for method_name in ("runs", "get_run", "artifact", "revalidate", "graph"):
            setattr(session, method_name, MethodType(getattr(Session, method_name), session))
        return cls(
            project_root=project_root,
            store=store,
            session_id=session_id,
            session=session,
        )

    def timestamp(self) -> str:
        value = datetime(2026, 8, 30, tzinfo=UTC) + timedelta(seconds=self._clock)
        self._clock += 1
        return value.isoformat()

    def begin_run(
        self,
        run_id: str,
        *,
        capability_id: str = "observe",
        inputs: tuple[str, ...] = (),
        arguments: list[dict[str, object]] | None = None,
        started_at: str | None = None,
    ) -> None:
        self.store.begin_run(
            session_id=self.session_id,
            run_id=run_id,
            capability_id=capability_id,
            analysis_purpose=f"purpose:{run_id}",
            arguments=arguments or [],
            omitted_argument_names=(),
            input_artifact_refs=inputs,
            started_at=started_at or self.timestamp(),
        )

    def add_artifact(
        self,
        ref: str,
        *,
        producer: str | None,
        inputs: tuple[str, ...] = (),
        kind: str = "metric_frame",
        evidence_status: str = "complete",
        finding_count: int = 0,
    ) -> None:
        artifact_dir = (
            self.project_root
            / ".marivo"
            / "analysis"
            / "sessions"
            / self.session_id
            / "frames"
            / ref
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        created_at = self.timestamp()
        content_hash = f"sha256:{ref}"
        common: dict[str, Any] = {
            "ref": ref,
            "session_id": self.session_id,
            "project_root": str(self.project_root),
            "produced_by_job": producer,
            "analysis_purpose": f"purpose:{producer}" if producer else None,
            "created_at": datetime.fromisoformat(created_at),
            "row_count": 1,
            "byte_size": 1,
            "evidence_status": evidence_status,
            "finding_count": finding_count,
            "content_hash": content_hash,
            "lineage": Lineage(
                steps=(
                    [
                        LineageStep(
                            intent="observe",
                            job_ref=producer,
                            inputs=list(inputs),
                            params_digest="sha256:test",
                            analysis_purpose=f"purpose:{producer}",
                        )
                    ]
                    if producer is not None
                    else []
                )
            ),
        }
        if kind == "metric_frame":
            meta = MetricFrameMeta(
                kind="metric_frame",
                metric_id="sales.revenue",
                **make_test_metric_meta_contract("sales.revenue"),
                measure={"name": "value", "column": "value"},
                window=None,
                semantic_kind="scalar",
                **common,
            )
        elif kind == "coverage_frame":
            meta = CoverageFrameMeta(
                kind="coverage_frame",
                parent_ref=inputs[0] if inputs else ref,
                axes={},
                **common,
            )
        else:
            raise ValueError(f"unsupported runtime-read fixture Artifact kind: {kind}")
        payload = meta.model_dump(mode="json")
        meta_path = artifact_dir / "meta.json"
        meta_path.write_text(json.dumps(payload), encoding="utf-8")
        data_path = artifact_dir / "data.parquet"
        self.store.record_artifact(
            session_id=self.session_id,
            artifact_id=ref,
            kind=kind,
            path=str(data_path.relative_to(self.project_root)),
            meta_path=str(meta_path.relative_to(self.project_root)),
            content_hash=content_hash,
            produced_by_job=producer,
            evidence_status=evidence_status,
            finding_count=finding_count,
            created_at=created_at,
        )

    def succeed(
        self,
        run_id: str,
        output_ref: str,
        *,
        output_mode: str = "produced",
        queries: list[dict[str, object]] | None = None,
    ) -> None:
        self.store.complete_run(
            session_id=self.session_id,
            run_id=run_id,
            output_artifact_ref=output_ref,
            output_mode=output_mode,
            finished_at=self.timestamp(),
            queries=queries,
        )

    def fail(
        self,
        run_id: str,
        *,
        repair: dict[str, object] | None = None,
        queries: list[dict[str, object]] | None = None,
    ) -> None:
        self.store.fail_run(
            session_id=self.session_id,
            run_id=run_id,
            failure={
                "error_type": "AnalysisError",
                "message": "safe failure",
                "expected": None,
                "received": None,
                "location": "test",
                "repair": repair,
            },
            failed_at=self.timestamp(),
            queries=queries,
        )

    def produced(
        self,
        run_id: str,
        artifact_ref: str,
        *,
        capability_id: str = "observe",
        inputs: tuple[str, ...] = (),
    ) -> None:
        self.begin_run(run_id, capability_id=capability_id, inputs=inputs)
        self.add_artifact(artifact_ref, producer=run_id, inputs=inputs)
        self.succeed(run_id, artifact_ref)


__all__ = ["RuntimeReadHarness"]
