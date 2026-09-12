"""Opt-in terminal capture for candidate-bound backend acceptance.

Load with ``-p tests.lazy_acceptance_capture`` and set MARIVO_SLICE9B_EVIDENCE_DIR.
The observer does not execute queries, read result rows, or change dispatch.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import pytest

from marivo.analysis.compiler.placement import (
    ArtifactReadStep,
    ExecutionBinding,
    PandasStep,
    PhysicalStageGraph,
    SourceStep,
)
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.operators import registry
from tests.lazy_materialization_crash_worker import record_evidence, statistics


class _Placement(Protocol):
    def __call__(
        self,
        dataset: LogicalDataset,
        *,
        artifact_binding: Callable[[MaterializedDataset], ExecutionBinding | None] | None = None,
    ) -> PhysicalStageGraph: ...


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    destination = os.environ.get("MARIVO_SLICE9B_EVIDENCE_DIR")
    if destination is None:
        return
    directory = Path(destination) / "tests"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(report.nodeid.encode()).hexdigest()[:20]
    (directory / f"{key}-{report.when}.json").write_text(
        json.dumps(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "duration_seconds": report.duration,
            },
            sort_keys=True,
        )
        + "\n"
    )


def counts(runtime: DatasetRuntime) -> dict[str, int]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        return {
            name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("analysis_action_runs", "dataset_artifacts")
        }


@pytest.fixture(autouse=True)
def capture_terminal_actions(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = os.environ.get("MARIVO_SLICE9B_EVIDENCE_DIR")
    if destination is None:
        return
    original = DatasetRuntime._execute
    sequence = 0

    def observe(runtime: DatasetRuntime, dataset: LogicalDataset) -> MaterializedDataset:
        nonlocal sequence
        sequence += 1
        ordinal = sequence
        before = counts(runtime)
        registrations: dict[str, object] = {}
        stages: list[dict[str, object]] = []
        place_name = "place"
        selected_place: _Placement = getattr(admission, place_name)

        def capture_placement(
            value: LogicalDataset,
            *,
            artifact_binding: Callable[[MaterializedDataset], ExecutionBinding | None]
            | None = None,
        ) -> PhysicalStageGraph:
            graph = selected_place(value, artifact_binding=artifact_binding)
            for step in graph.steps:
                facts: dict[str, object] = {
                    "kind": type(step).__name__,
                    "definition": step.dataset.definition_fingerprint,
                }
                if isinstance(step, SourceStep):
                    facts.update(
                        binding=type(step.binding).__name__,
                        adapter=step.binding.adapter,
                        correlation_preparation=step.correlation_preparation,
                        distribution_preparation=step.distribution_preparation,
                    )
                elif isinstance(step, PandasStep):
                    facts["implementation"] = asdict(step.implementation)
                elif isinstance(step, ArtifactReadStep):
                    facts["artifact"] = str(step.dataset.state.artifact_ref)
                stages.append(facts)
            return graph

        def visit(value: Dataset) -> None:
            if not isinstance(value, LogicalDataset):
                return
            root = value._root
            if isinstance(root, LogicalRootHandle):
                registrations[value.definition_fingerprint] = {
                    "shape": str(value.row_contract.shape_id),
                    "registration": asdict(registry.implementation(value)),
                }
            for operand in value._inputs:
                visit(operand)

        started = time.monotonic()
        result: MaterializedDataset | None = None
        failure: str | None = None
        failure_detail: str | None = None
        try:
            with patch.object(admission, "place", capture_placement):
                result = original(runtime, dataset)
            return result
        except Exception as error:
            failure = type(error).__name__
            failure_detail = str(error)
            raise
        finally:
            elapsed = time.monotonic() - started
            record = (
                None if result is None else runtime.store.artifact(result.state.artifact_ref.ref)
            )
            if result is not None and stages:
                visit(dataset)
            receipt = None if record is None else record.descriptor.storage_receipt
            payload = {
                "nodeid": request.node.nodeid,
                "ordinal": ordinal,
                "session": runtime.session_ref,
                "candidate": os.environ.get("MARIVO_SLICE9B_CANDIDATE"),
                "before": before,
                "after": counts(runtime),
                "error_type": failure,
                "error_detail": failure_detail,
                "registrations": registrations,
                "selected_steps": stages,
                "selected_query_stage_count": sum(s["kind"] == "SourceStep" for s in stages),
                "selected_local_worker_stage_count": int(
                    any(s["kind"] == "PandasStep" for s in stages)
                ),
                "stage_scope": "Selected graph; zero steps on exact recovery or pre-placement rejection. Query completion is reported separately in statistics.",
                "statistics": statistics(runtime),
                "elapsed_seconds": elapsed,
                "local_step_count": len(runtime.statistics.local_handoffs),
                "worker_peak_rss_bytes": runtime.statistics.worker_peak_rss
                if runtime.statistics.worker_pid is not None
                else None,
                "rss_scope": "local worker peak; source engine and unspawned worker unavailable",
                "output_rows": None if receipt is None else receipt.realized_row_count,
                "output_stored_bytes": None if receipt is None else receipt.realized_byte_count,
                "writer_target": type(runtime.target).__name__,
                "storage_wire_operation_count": None,
                "storage_measurement_scope": "Receipts enumerate committed primary and private parts. Storage wire calls and adapter metadata requests are not instrumented and are not zero queries.",
                "terminal": None if record is None else record_evidence(record),
                "row_assertion_owner": request.node.nodeid,
            }
            directory = Path(destination) / "actions"
            directory.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:20]
            (directory / f"{key}-{os.getpid()}-{ordinal}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
            )

    monkeypatch.setattr(DatasetRuntime, "_execute", observe)
