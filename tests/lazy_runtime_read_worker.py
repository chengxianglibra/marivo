"""Fresh-process Slice 4 retained production, failure, and read-only acceptance."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch

from marivo.analysis.materialization import admission, object_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.targets import (
    EngineTarget,
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.session._lazy_read_model import FailedRun, SessionGraph, SucceededRun
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden
from tests.lazy_materialization_crash_worker import snapshot, statistics, versions
from tests.lazy_retained_fixtures import setup_retained

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

Kind = Literal["local", "engine", "object"]
REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")


def _access(kind: Kind) -> tuple[S3Access, ...]:
    return (
        (
            S3Access(
                "fixture",
                os.environ["MARIVO_TEST_S3_ENDPOINT"],
                os.environ["MARIVO_TEST_S3_BUCKET"],
                "minioadmin",
                "minioadmin",
            ),
        )
        if kind == "object"
        else ()
    )


def _open(project: Path, session: str, kind: Kind) -> DatasetRuntime:
    return DatasetRuntime.open(
        project,
        session,
        target=EngineTarget("warehouse")
        if kind == "engine"
        else ObjectTarget("fixture")
        if kind == "object"
        else LocalTarget(),
        object_bindings=_access(kind),
    )


def _continuation(retained: MaterializedMetricDataset, threshold: int) -> LogicalMetricDataset:
    return retained.where(gt(REVENUE, threshold)).aggregate()


def _statistics(runtime: DatasetRuntime) -> dict[str, object]:
    return {
        **statistics(runtime),
        "worker_pid": runtime.statistics.worker_pid,
        "handoffs": runtime.statistics.local_handoffs,
    }


def _graph(value: SessionGraph) -> dict[str, object]:
    return {
        "session": value.session_id,
        "artifacts": [
            {"artifact": str(item.artifact_ref), "owner": item.artifact_session_ref}
            for item in value.artifacts
        ],
        "runs": [item.run_id for item in value.runs],
        "edges": [
            {"kind": item.kind, "run": item.run_id, "artifact": str(item.artifact_ref)}
            for item in value.edges
        ],
        "failed": list(value.failed_run_ids),
        "truncated": value.truncated,
    }


def _read(runtime: DatasetRuntime, artifact: str) -> dict[str, object]:
    before = snapshot(runtime)
    retained = runtime.artifact(artifact)
    assert isinstance(retained, MaterializedMetricDataset)
    digest = retained.evidence_digest
    findings = retained.findings()
    assert digest.finding_count == 0 and findings.items == () and not findings.has_more
    report = runtime.revalidate(artifact)
    assert (
        report.artifact_integrity == report.evidence_integrity == "valid"
        and report.storage_authority == "readable"
        and report.issues == ()
    )
    page = runtime.runs(limit=1)
    records = list(page.items)
    while page.has_more:
        page = runtime.runs(limit=1, cursor=page.next_cursor)
        records.extend(page.items)
    assert len({item.run_id for item in records}) == len(records)
    for item in records:
        assert runtime.get_run(item.run_id) == item
    with contextlib.redirect_stdout(io.StringIO()) as output:
        runtime.show_session()
        retained.show()
        digest.show()
        runtime.graph().show()
    frame = retained.to_pandas()
    assert snapshot(runtime) == before
    return {
        "artifact": artifact,
        "rows": [
            [None if value is None else float(value) for value in row]
            for row in frame.itertuples(index=False, name=None)
        ],
        "finding_count": digest.finding_count,
        "evidence_digest": digest.evidence_digest,
        "revalidation": {
            "artifact_integrity": report.artifact_integrity,
            "storage_authority": report.storage_authority,
            "evidence_integrity": report.evidence_integrity,
        },
        "runs": [{"run": item.run_id, "lifecycle": item.lifecycle} for item in records],
        "graph": _graph(runtime.graph()),
        "show": output.getvalue(),
        "before": before,
        "after": snapshot(runtime),
    }


def run(mode: str, kind: Kind, project: Path) -> dict[str, object]:
    state_path = project / "read-journey.json"
    requests: list[dict[str, object]] = []
    base_client = object_storage.client

    def observed(params: dict[str, object], **kwargs: object) -> None:
        requests.append({"version_pinned": isinstance(params.get("VersionId"), str)})

    @contextlib.contextmanager
    def traced_client(access: S3Access) -> Iterator[S3Client]:
        with base_client(access) as client:
            client.meta.events.register("before-parameter-build.s3.GetObject", observed)
            yield client

    object_storage.client = traced_client
    if mode == "produce":
        bindings = _access(kind)
        fixture = setup_retained(project, kind, access=bindings[0] if bindings else None)
        runtime = fixture.runtime
        checkpoint = fixture.sources.observe(
            (REVENUE, MEAN), population=fixture.sources.population(ref.entity("sales.customers"))
        ).execute()
        producer_statistics = _statistics(runtime)
        artifact = str(checkpoint.state.artifact_ref)
        fixture.database.rename(project / "warehouse.offline")

        def fail_before_commit(point: str) -> None:
            if point == "before_commit":
                raise RuntimeError("controlled unpublished output failure")

        failing = DatasetRuntime.open(
            project,
            runtime.session_ref,
            event=fail_before_commit,
            target=runtime.target,
            object_bindings=bindings,
        )
        retained = failing.artifact(artifact)
        assert isinstance(retained, MaterializedMetricDataset)
        try:
            _continuation(retained, 0).execute()
        except MaterializationError as error:
            assert error.run_ref == failing.last_run_ref
        else:
            raise AssertionError("The admitted failure was not raised")
        assert failing.last_run_ref is not None
        failure = failing.get_run(failing.last_run_ref)
        assert isinstance(failure, FailedRun)
        state = {
            "session": runtime.session_ref,
            "checkpoint": artifact,
            "failed_run": failure.run_id,
        }
        state_path.write_text(json.dumps(state, sort_keys=True) + "\n")
        result: dict[str, object] = {
            **state,
            "after": snapshot(runtime),
            "producer_statistics": producer_statistics,
            "failure_statistics": _statistics(failing),
            "failure_phase": failure.failure.phase,
        }
    else:
        state = json.loads(state_path.read_text())
        assert isinstance(state, dict)
        session, artifact = str(state["session"]), str(state["checkpoint"])
        runtime = _open(project, session, kind)
        retained = runtime.artifact(artifact)
        assert isinstance(retained, MaterializedMetricDataset)
        if mode == "continue":
            with patch.object(admission, "_build_backend_from_effective", forbidden):
                continued = _continuation(retained, 10).execute()
            continuation_statistics = _statistics(runtime)
            consumer = DatasetRuntime.create(
                project, "foreign-consumer", target=runtime.target, object_bindings=_access(kind)
            )
            before_foreign = snapshot(consumer)
            foreign = consumer.artifact(artifact)
            assert isinstance(foreign, MaterializedMetricDataset)
            graph_after_read = consumer.graph()
            assert graph_after_read.artifacts == () and graph_after_read.runs == ()
            assert snapshot(consumer) == before_foreign
            with patch.object(admission, "_build_backend_from_effective", forbidden):
                consumed = _continuation(foreign, 50).execute()
            consumer_statistics = _statistics(consumer)
            graph = consumer.graph()
            assert len(graph.runs) == 1 and isinstance(graph.runs[0], SucceededRun)
            assert graph.runs[0].input_artifact_refs == (retained.state.artifact_ref,)
            summary = next(item for item in graph.artifacts if str(item.artifact_ref) == artifact)
            assert summary.artifact_session_ref == session
            state.update(
                output=str(continued.state.artifact_ref),
                consumer=consumer.session_ref,
                consumed=str(consumed.state.artifact_ref),
            )
            state_path.write_text(json.dumps(state, sort_keys=True) + "\n")
            result = {
                **state,
                "origin": _read(runtime, str(state["output"])),
                "consumer_read": _read(consumer, str(state["consumed"])),
                "graph_after_foreign_read": _graph(graph_after_read),
                "continuation_statistics": continuation_statistics,
                "consumer_statistics": consumer_statistics,
                "after": snapshot(runtime),
            }
        else:
            consumer = _open(project, str(state["consumer"]), kind)
            before = snapshot(runtime)
            reads = {
                "origin": _read(runtime, str(state["output"])),
                "consumer_read": _read(consumer, str(state["consumed"])),
            }
            sessions = DatasetRuntime.recent(project, limit=1)
            assert sessions.has_more and sessions.next_cursor is not None
            older = DatasetRuntime.recent(project, limit=1, cursor=sessions.next_cursor)
            assert {sessions[0].id, older[0].id} == {runtime.session_ref, consumer.session_ref}
            origin = DatasetRuntime.inspect(project, "retained", run_limit=1)
            assert origin.summary.id == runtime.session_ref and origin.summary.run_count == 3
            assert origin.runs.has_more
            object_read_count = len(requests)
            with contextlib.ExitStack() as guards:
                for name in ("place", "_build_backend_from_effective", "supervise"):
                    guards.enter_context(patch.object(admission, name, forbidden))
                for selected, threshold, expected in (
                    (runtime, 10, str(state["output"])),
                    (consumer, 50, str(state["consumed"])),
                ):
                    selected.target, selected.object_bindings = ObjectTarget("unconfigured"), ()
                    bound_input = selected.artifact(artifact)
                    assert isinstance(bound_input, MaterializedMetricDataset)
                    recovered = _continuation(bound_input, threshold).execute()
                    assert str(recovered.state.artifact_ref) == expected
                    assert selected.statistics.primary_queries == 0
                    assert selected.statistics.worker_pid is None
                    assert selected.statistics.events == {"reconciliation": 1}
            assert snapshot(runtime) == before
            assert len(requests) == object_read_count
            result = {
                **state,
                **reads,
                "before": before,
                "after": snapshot(runtime),
                "binding_statistics": _statistics(runtime),
                "consumer_binding_statistics": _statistics(consumer),
                "binding_object_requests": len(requests) - object_read_count,
                "session_ids": [sessions[0].id, older[0].id],
            }
    return {**result, "pid": os.getpid(), "object_requests": requests, "versions": versions()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("kind", choices=("local", "engine", "object"))
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.mode, args.kind, args.project), sort_keys=True, allow_nan=False))
