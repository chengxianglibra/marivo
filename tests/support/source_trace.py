"""Actual source calls and terminal output metadata for native regressions."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.datasource.adapters import (
    BoundSource,
    CompiledRead,
    PhysicalRequirement,
    QualifiedSource,
    SourceSession,
)


@dataclass
class SourceTrace:
    native_sql: list[str] = field(default_factory=list)
    owners: list[SourceSession] = field(default_factory=list)
    qualifications: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[dict[str, object]] = field(default_factory=list)

    def record(self, result: mv.Artifact) -> None:
        """Record committed output metadata without restoring or reading a plan."""
        manifest = result._manifest
        data = manifest.data
        self.artifacts.append(
            {
                "kind": "Artifact",
                "artifact": result.id,
                "session": result.session_id,
                "run": manifest.run_id,
                "schema": asdict(data.schema),
                "payload_digest": data.rows.payload_digest,
                "attachments": [
                    {
                        "name": item.name,
                        "schema": asdict(item.schema),
                        "payload_digest": item.rows.payload_digest,
                    }
                    for item in data.attachments
                ],
                "projections": [
                    {
                        "axis": item.axis_token,
                        "path": item.path,
                        "kind": item.kind,
                        "label": item.label,
                    }
                    for item in data.coordinate_projections
                ],
            }
        )

    def save(
        self,
        name: str,
        environment: dict[str, object],
        inputs: dict[str, object],
        oracle: dict[str, object],
        result: mv.Artifact | None,
        excluded: tuple[SourceSession, ...],
    ) -> None:
        executions = [owner for owner in self.owners if owner not in excluded]
        submissions = [asdict(item) for owner in executions for item in owner.submissions]
        assert submissions and self.native_sql
        assert all(owner._closed for owner in executions)
        assert all(item["connection_disconnected"] is True for item in submissions)
        assert all(item["state"] == "succeeded" for item in submissions)
        assert all(item["sql"] in self.native_sql for item in submissions)
        if result is not None:
            self.record(result)
        directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if directory:
            Path(directory, name + ".json").write_text(
                json.dumps(
                    {
                        "environment": environment,
                        "input": inputs,
                        "oracle": oracle,
                        "qualified_requirements": self.qualifications,
                        "artifacts": self.artifacts,
                        "submissions": submissions,
                        "actual_native_submissions": self.native_sql,
                        "connection_disconnected": True,
                        "boundary": "Actual source qualification/submission and committed output metadata; no persisted graph or general backend qualification is inferred",
                    },
                    sort_keys=True,
                )
            )


def capture_source(monkeypatch: pytest.MonkeyPatch) -> SourceTrace:
    trace = SourceTrace()
    execute = SourceSession.execute
    enter = SourceSession.__enter__
    qualify = SourceSession.qualify

    def captured_execute(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        submitted_before = len(owner.submissions)
        try:
            return execute(owner, read)
        finally:
            if len(owner.submissions) > submitted_before:
                trace.native_sql.append(read.sql)

    def entered(owner: SourceSession) -> SourceSession:
        result = enter(owner)
        trace.owners.append(owner)
        return result

    def qualified(
        owner: SourceSession, binding: BoundSource, need: PhysicalRequirement
    ) -> QualifiedSource:
        result = qualify(owner, binding, need)
        trace.qualifications.append(
            {
                "source": binding.facts.source_identity,
                "method": need.method_id,
                "version": need.version,
                "operations": sorted(need.operations),
                "schema": [[item.name, str(item.type)] for item in binding.facts.schema],
            }
        )
        return result

    monkeypatch.setattr(SourceSession, "execute", captured_execute)
    monkeypatch.setattr(SourceSession, "__enter__", entered)
    monkeypatch.setattr(SourceSession, "qualify", qualified)
    return trace
