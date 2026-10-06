"""Native source witnesses shared by execution and resource regression tests."""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.datasource.adapters as adapters
from marivo.analysis.core.graph import Node
from marivo.analysis.materialization.graph_protocol import descriptor_plan, schema_from
from marivo.datasource.adapters import SourceSession, _Cursor
from tests.support.json import key_json


@dataclass
class SourceTrace:
    native_sql: list[str] = field(default_factory=list)
    owners: list[SourceSession] = field(default_factory=list)
    physical_keys: list[Mapping[str, object]] = field(default_factory=list)
    retained: list[dict[str, object]] = field(default_factory=list)

    def record(
        self,
        result: mv.MaterializedAnalysisDomain
        | mv.MaterializedNumericRelation
        | mv.MaterializedSelectedNumericRelation
        | mv.MaterializedCategoryRelation
        | mv.MaterializedBooleanRelation
        | mv.MaterializedTemporalRelation
        | mv.MaterializedRatioRelation
        | mv.MaterializedStatisticRelation
        | mv.MaterializedGroupedNumericRelation
        | mv.MaterializedRolledNumericRelation
        | mv.MaterializedRolledRatioRelation
        | mv.MaterializedDifferenceRelation
        | mv.MaterializedAttributionResult,
        *,
        definition: Node | None = None,
    ) -> None:
        assert result._dataset is not None
        checked = result._dataset.verified()
        descriptor = result._dataset.artifact.descriptor
        roles = {part.role for part in checked.parts}
        if descriptor.method_state.kind in ("share", "penetration", "standardized"):
            assert {"fixed_reference", "reference_proof", "stratum_values"} <= roles
        elif descriptor.method_state.kind == "ranking":
            assert {"values", "ranks", "ranking_domain", "partitions", "ordering"} <= roles
        elif isinstance(result, mv.MaterializedAttributionResult):
            assert {
                "current_endpoint",
                "baseline_endpoint",
                "basis",
                "allocation",
                "reconciliation",
                "selection_scope",
            } <= roles
        elif isinstance(
            result,
            (
                mv.MaterializedStatisticRelation,
                mv.MaterializedGroupedNumericRelation,
                mv.MaterializedRolledNumericRelation,
                mv.MaterializedRolledRatioRelation,
            ),
        ):
            assert checked.parts
        else:
            assert "subject" in roles
        schema = schema_from(descriptor.realized_schema)
        self.retained.append(
            {
                "kind": type(result).__name__,
                "schema": [[item.name, str(item.type)] for item in schema],
                "parts": [
                    {"role": part.role, "key_fields": list(part.key_fields)}
                    for part in descriptor.parts
                ],
                "verified": True,
            }
        )
        self.physical_keys.extend(
            key_json(item.key)
            for item in descriptor_plan(
                result._dataset.artifact.descriptor,
                result._node.definition if definition is None else definition,
            ).physical_requirements
        )

    def save(
        self,
        name: str,
        environment: dict[str, object],
        inputs: dict[str, object],
        oracle: dict[str, object],
        result: mv.MaterializedAnalysisDomain
        | mv.MaterializedNumericRelation
        | mv.MaterializedSelectedNumericRelation
        | mv.MaterializedRatioRelation
        | mv.MaterializedStatisticRelation
        | mv.MaterializedGroupedNumericRelation
        | mv.MaterializedRolledNumericRelation
        | mv.MaterializedRolledRatioRelation
        | mv.MaterializedDifferenceRelation
        | mv.MaterializedAttributionResult
        | None,
        excluded: tuple[SourceSession, ...],
    ) -> None:
        executions = [owner for owner in self.owners if owner not in excluded]
        submissions = [asdict(item) for owner in executions for item in owner.submissions]
        assert submissions and self.native_sql
        assert all(owner._closed for owner in executions)
        assert all(item["connection_disconnected"] is True for item in submissions)
        assert all(item["cursor_state"] in ("closed", "connection_owned") for item in submissions)
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
                        "physical_keys": self.physical_keys,
                        "retained": self.retained,
                        "submissions": submissions,
                        "actual_native_submissions": self.native_sql,
                        "connection_disconnected": True,
                        "boundary": "Source execution witness; independent cancellation and complete family binding remain required",
                    },
                    sort_keys=True,
                )
            )


def capture_source(monkeypatch: pytest.MonkeyPatch) -> SourceTrace:
    trace = SourceTrace()
    native = adapters._native_cursor
    enter = SourceSession.__enter__

    def capture(owner: BaseBackend, name: str, sql: str) -> _Cursor:
        trace.native_sql.append(sql)
        return native(owner, name, sql)

    def entered(owner: SourceSession) -> SourceSession:
        result = enter(owner)
        trace.owners.append(owner)
        return result

    monkeypatch.setattr(adapters, "_native_cursor", capture)
    monkeypatch.setattr(SourceSession, "__enter__", entered)
    return trace
