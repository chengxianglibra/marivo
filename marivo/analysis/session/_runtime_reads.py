"""Public Session runtime reads and factual graph projection."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from marivo.analysis._capabilities.model import ArtifactFamily
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import (
    AnalysisRepair,
    ArtifactNotFoundError,
    EvidenceStoreUnavailableError,
    FrameMetaInvalidError,
    RunNotFoundError,
    SessionGraphArgumentError,
    SessionGraphIntegrityError,
    SessionGraphLimitError,
    SessionGraphTooLargeError,
)
from marivo.analysis.session._artifact_meta import parse_current_artifact_meta
from marivo.analysis.session._read_model import (
    ArtifactEvidenceSummary,
    ArtifactIssueCounts,
    ArtifactSummary,
    FailedRun,
    GraphDirection,
    IncompleteRun,
    RunArgument,
    RunFailure,
    RunPage,
    RunRecord,
    SessionGraph,
    SessionGraphEdge,
    SessionRuntimeRecap,
    SucceededRun,
)
from marivo.analysis.session._runs import JsonValue
from marivo.analysis.session._store import (
    _FocusedRuntimeRows,
    _RuntimeSnapshotTooLargeError,
)
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.evidence.types import ArtifactRevalidation
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.session.core import Session

_OVERALL_GRAPH_SCAN_LIMIT = 5_000
_SIDECAR_KINDS = frozenset({"component_frame", "coverage_frame"})
_FAMILY_BY_KIND: dict[str, ArtifactFamily] = {
    "metric_frame": "MetricFrame",
    "event_frame": "EventFrame",
    "lifecycle_frame": "LifecycleFrame",
    "subject_set": "SubjectSet",
    "delta_frame": "DeltaFrame",
    "attribution_frame": "AttributionFrame",
    "forecast_frame": "ForecastFrame",
    "candidate_set": "CandidateSet",
    "association_result": "AssociationResult",
    "component_frame": "ComponentFrame",
    "coverage_frame": "CoverageFrame",
    "hypothesis_test_result": "HypothesisTestResult",
}


@dataclass(frozen=True, slots=True)
class _ArtifactFacts:
    summary: ArtifactSummary
    lineage_job_ref: str | None
    lineage_inputs: tuple[str, ...]


def _artifact_metadata_repair(ref: str) -> AnalysisRepair:
    return AnalysisRepair(
        kind="environment",
        action=(
            f"Preserve the corrupt Artifact {ref!r}, create a fresh Session, and rerun its "
            "producing computation under the current schema."
        ),
        help_target=LiveHelpTarget(surface="analysis", canonical_id="session.artifact"),
    )


def _aware_datetime(value: object, *, location: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted runtime timestamp is not readable",
            expected="one ISO-8601 timestamp",
            received=repr(value),
            location=location,
        ) from exc
    if parsed.tzinfo is None:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted runtime timestamp has no timezone",
            expected="one timezone-aware timestamp",
            received=parsed.isoformat(),
            location=location,
        )
    return parsed


def _json_value(value: object, *, location: str, depth: int = 0) -> JsonValue:
    if depth > 8:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run JSON exceeds the read-model depth bound",
            expected="bounded safe JSON",
            received="depth exceeded",
            location=location,
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise SessionGraphIntegrityError.mismatch(
                message="persisted Run JSON contains a non-finite number",
                expected="finite JSON number",
                received=repr(value),
                location=location,
            )
        return value
    if isinstance(value, list):
        return [_json_value(item, location=location, depth=depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            str(key): _json_value(item, location=location, depth=depth + 1)
            for key, item in value.items()
        }
    raise SessionGraphIntegrityError.mismatch(
        message="persisted Run field is not safe JSON",
        expected="null, scalar, list, or string-keyed object",
        received=type(value).__name__,
        location=location,
    )


def _decode_arguments(raw: object, *, run_id: str) -> tuple[RunArgument, ...]:
    location = f"Run {run_id!r} arguments"
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run arguments are not readable JSON",
            expected="a list of normalized Run arguments",
            received=type(exc).__name__,
            location=location,
        ) from exc
    if not isinstance(payload, list):
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run arguments have the wrong shape",
            expected="a list of {name, value} entries",
            received=type(payload).__name__,
            location=location,
        )
    result: list[RunArgument] = []
    previous = ""
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise SessionGraphIntegrityError.mismatch(
                message="persisted Run argument entry has the wrong shape",
                expected="exact fields name and value",
                received=repr(item),
                location=location,
            )
        name = item["name"]
        if not isinstance(name, str) or not name or name <= previous:
            raise SessionGraphIntegrityError.mismatch(
                message="persisted Run arguments are not canonically ordered",
                expected="unique non-empty names in ascending order",
                received=repr(name),
                location=location,
            )
        previous = name
        result.append(
            RunArgument(
                name=name,
                value=_json_value(item["value"], location=f"{location}.{name}"),
            )
        )
    return tuple(result)


def _decode_string_tuple(raw: object, *, location: str) -> tuple[str, ...]:
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run string collection is not readable JSON",
            expected="a JSON list of unique strings",
            received=type(exc).__name__,
            location=location,
        ) from exc
    if (
        not isinstance(payload, list)
        or any(not isinstance(item, str) or not item for item in payload)
        or payload != sorted(set(payload))
    ):
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run string collection is not canonical",
            expected="unique non-empty strings in ascending order",
            received=repr(payload),
            location=location,
        )
    return tuple(payload)


def _decode_failure(raw: object, *, run_id: str) -> RunFailure:
    location = f"Run {run_id!r} failure"
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run failure is not readable JSON",
            expected="one sanitized RunFailure object",
            received=type(exc).__name__,
            location=location,
        ) from exc
    expected_keys = {"error_type", "message", "expected", "received", "location", "repair"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run failure has the wrong shape",
            expected=f"exact fields {sorted(expected_keys)!r}",
            received=repr(payload),
            location=location,
        )
    error_type = payload["error_type"]
    message = payload["message"]
    failure_location = payload["location"]
    if not isinstance(error_type, str) or not error_type or not isinstance(message, str):
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run failure identity is invalid",
            expected="non-empty error_type and string message",
            received=repr((error_type, message)),
            location=location,
        )
    if failure_location is not None and not isinstance(failure_location, str):
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run failure location is invalid",
            expected="string or null",
            received=repr(failure_location),
            location=location,
        )
    raw_repair = payload["repair"]
    repair: AnalysisRepair | None = None
    if raw_repair is not None:
        if not isinstance(raw_repair, dict):
            raise SessionGraphIntegrityError.mismatch(
                message="persisted Run repair has the wrong shape",
                expected="one AnalysisRepair object or null",
                received=repr(raw_repair),
                location=f"{location}.repair",
            )
        try:
            repair = AnalysisRepair.model_validate(raw_repair)
        except ValueError as exc:
            raise SessionGraphIntegrityError.mismatch(
                message="persisted Run repair is invalid",
                expected="one exact current AnalysisRepair object",
                received=type(exc).__name__,
                location=f"{location}.repair",
            ) from exc
    return RunFailure(
        error_type=error_type,
        message=message,
        expected=_json_value(payload["expected"], location=f"{location}.expected"),
        received=_json_value(payload["received"], location=f"{location}.received"),
        location=failure_location,
        repair=repair,
    )


def _row_to_run(row: sqlite3.Row, *, input_refs: tuple[str, ...]) -> RunRecord:
    run_id = str(row["run_id"])
    capability_id = str(row["capability_id"])
    if capability_id not in REGISTRY.capability_ids:
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run capability id is not canonical",
            expected="one current Help-resolvable materializing capability id",
            received=capability_id,
            location=f"Run {run_id!r} capability",
        )
    if len(input_refs) != len(set(input_refs)):
        raise SessionGraphIntegrityError.mismatch(
            message="persisted Run contains duplicate normalized inputs",
            expected="unique input Artifact refs",
            received=repr(input_refs),
            location=f"Run {run_id!r} inputs",
        )
    analysis_purpose = str(row["analysis_purpose"]) if row["analysis_purpose"] is not None else None
    arguments = _decode_arguments(row["arguments_json"], run_id=run_id)
    omitted_argument_names = _decode_string_tuple(
        row["omitted_argument_names_json"],
        location=f"Run {run_id!r} omitted arguments",
    )
    started_at = _aware_datetime(row["started_at"], location=f"Run {run_id!r} start")
    lifecycle = str(row["lifecycle"])
    if lifecycle == "incomplete":
        if any(
            row[name] is not None
            for name in ("finished_at", "output_artifact_ref", "output_mode", "failure_json")
        ):
            raise SessionGraphIntegrityError.mismatch(
                message="incomplete Run carries terminal fields",
                expected="no finish, output, mode, or failure",
                received=run_id,
                location=f"Run {run_id!r} lifecycle",
            )
        return IncompleteRun(
            run_id=run_id,
            capability_id=capability_id,
            analysis_purpose=analysis_purpose,
            input_artifact_refs=input_refs,
            arguments=arguments,
            omitted_argument_names=omitted_argument_names,
            started_at=started_at,
        )
    if lifecycle == "succeeded":
        output_ref = row["output_artifact_ref"]
        output_mode = row["output_mode"]
        finished_at = row["finished_at"]
        if (
            not isinstance(output_ref, str)
            or not output_ref
            or output_mode not in {"produced", "reused"}
            or finished_at is None
            or row["failure_json"] is not None
        ):
            raise SessionGraphIntegrityError.mismatch(
                message="succeeded Run has incomplete or contradictory terminal fields",
                expected="one output ref/mode/finish and no failure",
                received=run_id,
                location=f"Run {run_id!r} lifecycle",
            )
        return SucceededRun(
            run_id=run_id,
            capability_id=capability_id,
            analysis_purpose=analysis_purpose,
            input_artifact_refs=input_refs,
            arguments=arguments,
            omitted_argument_names=omitted_argument_names,
            started_at=started_at,
            output_artifact_ref=output_ref,
            output_mode=cast("Literal['produced', 'reused']", output_mode),
            finished_at=_aware_datetime(finished_at, location=f"Run {run_id!r} finish"),
        )
    if lifecycle == "failed":
        if (
            row["finished_at"] is None
            or row["failure_json"] is None
            or row["output_artifact_ref"] is not None
            or row["output_mode"] is not None
        ):
            raise SessionGraphIntegrityError.mismatch(
                message="failed Run has incomplete or contradictory terminal fields",
                expected="one finish/failure and no output",
                received=run_id,
                location=f"Run {run_id!r} lifecycle",
            )
        return FailedRun(
            run_id=run_id,
            capability_id=capability_id,
            analysis_purpose=analysis_purpose,
            input_artifact_refs=input_refs,
            arguments=arguments,
            omitted_argument_names=omitted_argument_names,
            started_at=started_at,
            failed_at=_aware_datetime(row["finished_at"], location=f"Run {run_id!r} failure"),
            failure=_decode_failure(row["failure_json"], run_id=run_id),
        )
    raise SessionGraphIntegrityError.mismatch(
        message="persisted Run lifecycle is unsupported",
        expected="incomplete, succeeded, or failed",
        received=lifecycle,
        location=f"Run {run_id!r} lifecycle",
    )


def _read_artifact_facts(*, project_root: Path, row: sqlite3.Row) -> _ArtifactFacts:
    ref = str(row["artifact_id"])
    meta_path = (project_root / str(row["meta_path"])).resolve()
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FrameMetaInvalidError(
            message=f"Artifact {ref!r} metadata cannot be read for SessionGraph",
            expected="readable analysis-artifact/v13 metadata",
            received=type(exc).__name__,
            location=str(meta_path),
            repair=_artifact_metadata_repair(ref),
        ) from exc
    if not isinstance(payload, dict):
        raise FrameMetaInvalidError(
            message=f"Artifact {ref!r} metadata has the wrong shape",
            expected="one metadata object",
            received=type(payload).__name__,
            location=str(meta_path),
            repair=_artifact_metadata_repair(ref),
        )

    def require(name: str, expected_type: type[object]) -> object:
        value = payload.get(name)
        if not isinstance(value, expected_type):
            raise SessionGraphIntegrityError.mismatch(
                message=f"Artifact metadata field {name!r} is invalid",
                expected=expected_type.__name__,
                received=repr(value),
                location=f"Artifact {ref!r} metadata",
            )
        return value

    schema = require("artifact_schema_version", str)
    if schema != "analysis-artifact/v13":
        raise FrameMetaInvalidError(
            message=f"Artifact {ref!r} metadata schema is incompatible",
            expected="analysis-artifact/v13",
            received=str(schema),
            location=str(meta_path),
            repair=_artifact_metadata_repair(ref),
        )
    try:
        parsed_meta = parse_current_artifact_meta(payload)
    except ValueError as exc:
        raise FrameMetaInvalidError(
            message=f"Artifact {ref!r} concrete v13 metadata is invalid",
            expected="one exact current concrete Artifact Meta payload",
            received=type(exc).__name__,
            location=str(meta_path),
            repair=_artifact_metadata_repair(ref),
        ) from exc
    meta_ref = parsed_meta.ref
    session_id = parsed_meta.session_id
    kind = parsed_meta.kind
    created_at = _aware_datetime(
        parsed_meta.created_at.isoformat(), location=f"Artifact {ref!r} creation time"
    )
    row_count = parsed_meta.row_count
    evidence_status = parsed_meta.evidence_status
    finding_count = parsed_meta.finding_count
    if kind not in _FAMILY_BY_KIND:
        raise SessionGraphIntegrityError.mismatch(
            message="Artifact kind is unsupported by the runtime graph",
            expected=f"one of {sorted(_FAMILY_BY_KIND)!r}",
            received=str(kind),
            location=f"Artifact {ref!r} kind",
        )
    if evidence_status not in {"complete", "partial", "unavailable"}:
        raise SessionGraphIntegrityError.mismatch(
            message="Artifact Evidence status is unsupported",
            expected="complete, partial, or unavailable",
            received=str(evidence_status),
            location=f"Artifact {ref!r} Evidence status",
        )
    if row_count < 0 or finding_count < 0:
        raise SessionGraphIntegrityError.mismatch(
            message="Artifact count metadata is negative",
            expected="non-negative row_count and finding_count",
            received=f"rows={row_count}, findings={finding_count}",
            location=f"Artifact {ref!r} metadata",
        )
    store_checks = {
        "ref": (meta_ref, ref),
        "session_id": (session_id, str(row["session_id"])),
        "kind": (kind, str(row["kind"])),
        "content_hash": (parsed_meta.content_hash, row["content_hash"]),
        "producer": (parsed_meta.produced_by_job, row["produced_by_job"]),
        "evidence_status": (evidence_status, row["evidence_status"]),
        "finding_count": (finding_count, row["finding_count"]),
    }
    mismatches = [name for name, (meta, stored) in store_checks.items() if meta != stored]
    if mismatches:
        raise SessionGraphIntegrityError.mismatch(
            message="Session Store and Artifact metadata disagree",
            expected="matching immutable Artifact identity facts",
            received=", ".join(mismatches),
            location=f"Artifact {ref!r}",
        )
    digest = parsed_meta.evidence_digest
    quality = parsed_meta.quality_summary
    issues = parsed_meta.issues
    digest_item_count = len(digest.items) if digest is not None else 0
    omitted_item_count = digest.omissions.omitted_items if digest is not None else 0
    semantic_shape = getattr(parsed_meta, "semantic_kind", getattr(parsed_meta, "shape", None))
    if semantic_shape is not None and not isinstance(semantic_shape, str):
        semantic_shape = None
    lineage_job_ref: str | None = None
    lineage_inputs: tuple[str, ...] = ()
    if parsed_meta.lineage.steps:
        current = parsed_meta.lineage.steps[-1]
        lineage_job_ref = current.job_ref
        lineage_inputs = tuple(current.inputs)
    summary = ArtifactSummary(
        ref=ref,
        family=_FAMILY_BY_KIND[str(kind)],
        semantic_shape=semantic_shape,
        created_at=created_at,
        produced_by_run=(
            parsed_meta.produced_by_job if parsed_meta.produced_by_job is not None else None
        ),
        analysis_purpose=parsed_meta.analysis_purpose,
        row_count=row_count,
        content_hash=parsed_meta.content_hash,
        materialization="materialized",
        evidence=ArtifactEvidenceSummary(
            status=evidence_status,
            digest_present=digest is not None,
            digest_item_count=digest_item_count,
            omitted_item_count=omitted_item_count,
            finding_count=finding_count,
        ),
        quality=quality,
        issue_counts=ArtifactIssueCounts.from_issues(issues),
    )
    return _ArtifactFacts(
        summary=summary,
        lineage_job_ref=lineage_job_ref,
        lineage_inputs=lineage_inputs,
    )


def _inputs_by_run(rows: Iterable[sqlite3.Row]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["run_id"])].append((int(row["position"]), str(row["artifact_ref"])))
    return {run_id: tuple(ref for _, ref in sorted(entries)) for run_id, entries in grouped.items()}


def _runs_from_rows(
    rows: Iterable[sqlite3.Row], *, inputs: Mapping[str, tuple[str, ...]]
) -> dict[str, RunRecord]:
    return {
        str(row["run_id"]): _row_to_run(
            row,
            input_refs=inputs.get(str(row["run_id"]), ()),
        )
        for row in rows
    }


def _validate_graph_integrity(
    *,
    runs: Mapping[str, RunRecord],
    artifacts: Mapping[str, _ArtifactFacts],
    tolerate_missing_boundary_artifacts: frozenset[str] = frozenset(),
    tolerate_missing_boundary_runs: frozenset[str] = frozenset(),
) -> None:
    for run in runs.values():
        for ref in run.input_artifact_refs:
            if ref not in artifacts and ref not in tolerate_missing_boundary_artifacts:
                raise SessionGraphIntegrityError.mismatch(
                    message="Run input Artifact is missing from the selected runtime authority",
                    expected="one same-Session Artifact record",
                    received=ref,
                    location=f"Run {run.run_id!r} input",
                )
        if isinstance(run, SucceededRun):
            facts = artifacts.get(run.output_artifact_ref)
            if facts is None:
                if run.run_id in tolerate_missing_boundary_runs:
                    continue
                raise SessionGraphIntegrityError.mismatch(
                    message="succeeded Run output Artifact is missing",
                    expected="one same-Session committed Artifact",
                    received=run.output_artifact_ref,
                    location=f"Run {run.run_id!r} output",
                )
            producer = facts.summary.produced_by_run
            if run.output_mode == "produced" and producer != run.run_id:
                raise SessionGraphIntegrityError.mismatch(
                    message="produced Run output names a different canonical producer",
                    expected=run.run_id,
                    received=repr(producer),
                    location=f"Artifact {facts.summary.ref!r} producer",
                )
            if run.output_mode == "reused" and (producer is None or producer == run.run_id):
                raise SessionGraphIntegrityError.mismatch(
                    message="reused Run has no different canonical producing Run",
                    expected="a different canonical producing Run",
                    received=repr(producer),
                    location=f"Artifact {facts.summary.ref!r} producer",
                )
    for facts in artifacts.values():
        producer = facts.summary.produced_by_run
        if producer is None or producer in tolerate_missing_boundary_runs:
            continue
        producer_run = runs.get(producer)
        if producer_run is None:
            raise SessionGraphIntegrityError.mismatch(
                message="Artifact canonical producer Run is missing",
                expected="one same-Session Run record",
                received=producer,
                location=f"Artifact {facts.summary.ref!r} producer",
            )
        if not isinstance(producer_run, SucceededRun) or producer_run.output_mode != "produced":
            raise SessionGraphIntegrityError.mismatch(
                message="Artifact canonical producer is not a produced succeeded Run",
                expected="SucceededRun output_mode='produced'",
                received=type(producer_run).__name__,
                location=f"Artifact {facts.summary.ref!r} producer",
            )
        if facts.lineage_job_ref != producer or set(facts.lineage_inputs) != set(
            producer_run.input_artifact_refs
        ):
            raise SessionGraphIntegrityError.mismatch(
                message="Artifact lineage and canonical producing Run disagree",
                expected=f"producer={producer!r}, inputs={producer_run.input_artifact_refs!r}",
                received=(f"producer={facts.lineage_job_ref!r}, inputs={facts.lineage_inputs!r}"),
                location=f"Artifact {facts.summary.ref!r} lineage",
            )


def _edge_values(
    *, runs: Mapping[str, RunRecord], artifacts: Mapping[str, _ArtifactFacts]
) -> tuple[SessionGraphEdge, ...]:
    edges: list[SessionGraphEdge] = []
    for run in runs.values():
        for ref in run.input_artifact_refs:
            if ref in artifacts:
                edges.append(SessionGraphEdge(kind="consumes", run_id=run.run_id, artifact_ref=ref))
        if isinstance(run, SucceededRun) and run.output_artifact_ref in artifacts:
            edges.append(
                SessionGraphEdge(
                    kind="produces" if run.output_mode == "produced" else "reuses",
                    run_id=run.run_id,
                    artifact_ref=run.output_artifact_ref,
                )
            )
    return tuple(edges)


def _topological_order(
    *,
    runs: Mapping[str, RunRecord],
    artifacts: Mapping[str, _ArtifactFacts],
    edges: tuple[SessionGraphEdge, ...],
) -> tuple[tuple[str, str], ...]:
    nodes = {*(("run", run_id) for run_id in runs), *(("artifact", ref) for ref in artifacts)}
    outgoing: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    indegree = dict.fromkeys(nodes, 0)
    for edge in edges:
        if edge.kind == "consumes":
            source = ("artifact", edge.artifact_ref)
            target = ("run", edge.run_id)
        else:
            source = ("run", edge.run_id)
            target = ("artifact", edge.artifact_ref)
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    def key(node: tuple[str, str]) -> tuple[datetime, str, str]:
        kind, identity = node
        timestamp = (
            runs[identity].started_at if kind == "run" else artifacts[identity].summary.created_at
        )
        return timestamp, identity, kind

    ready = sorted((node for node, count in indegree.items() if count == 0), key=key)
    ordered: list[tuple[str, str]] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for target in sorted(outgoing[node], key=key):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=key)
    if len(ordered) != len(nodes):
        cyclic = sorted(identity for (kind, identity), count in indegree.items() if count > 0)
        raise SessionGraphIntegrityError.mismatch(
            message="Session runtime graph contains a directed cycle",
            expected="one acyclic Artifact/Run graph",
            received=repr(cyclic),
            location="session.graph() topology",
        )
    return tuple(ordered)


def _overall_selection(
    *,
    runs: Mapping[str, RunRecord],
    artifacts: Mapping[str, _ArtifactFacts],
    max_nodes: int,
) -> tuple[set[str], set[str], set[str], set[str], bool]:
    succeeded_consumers = {
        ref
        for run in runs.values()
        if isinstance(run, SucceededRun)
        for ref in run.input_artifact_refs
    }
    heads = [facts.summary for ref, facts in artifacts.items() if ref not in succeeded_consumers]
    heads.sort(key=lambda item: (item.created_at, item.ref), reverse=True)
    attention = sorted(
        (run for run in runs.values() if not isinstance(run, SucceededRun)),
        key=lambda item: (item.started_at, item.run_id),
        reverse=True,
    )
    remaining = sorted(
        (run for run in runs.values() if isinstance(run, SucceededRun)),
        key=lambda item: (item.started_at, item.run_id),
        reverse=True,
    )
    selected_runs: set[str] = set()
    selected_artifacts: set[str] = set()
    boundary_artifacts: set[str] = set()
    boundary_runs: set[str] = set()

    def room() -> bool:
        return len(selected_runs) + len(selected_artifacts) < max_nodes

    def add_run(run_id: str, *, owner_ref: str | None = None) -> bool:
        if run_id in selected_runs:
            return True
        if not room():
            if owner_ref is not None:
                boundary_artifacts.add(owner_ref)
            return False
        selected_runs.add(run_id)
        return True

    def add_artifact(ref: str, *, owner_run: str | None = None) -> bool:
        if ref in selected_artifacts:
            return True
        if not room():
            if owner_run is not None:
                boundary_runs.add(owner_run)
            return False
        selected_artifacts.add(ref)
        return True

    for run in attention:
        add_run(run.run_id)
    for artifact in heads:
        add_artifact(artifact.ref)

    queue = deque(artifact.ref for artifact in heads if artifact.ref in selected_artifacts)
    visited: set[str] = set()
    while queue:
        ref = queue.popleft()
        if ref in visited:
            continue
        visited.add(ref)
        producer = artifacts[ref].summary.produced_by_run
        if producer is None or producer not in runs:
            continue
        if not add_run(producer, owner_ref=ref):
            continue
        for input_ref in runs[producer].input_artifact_refs:
            if input_ref in artifacts and add_artifact(input_ref, owner_run=producer):
                queue.append(input_ref)

    for succeeded_run in remaining:
        if not add_run(succeeded_run.run_id):
            continue
        add_artifact(
            succeeded_run.output_artifact_ref,
            owner_run=succeeded_run.run_id,
        )

    for run_id in selected_runs:
        selected_run = runs[run_id]
        if any(ref not in selected_artifacts for ref in selected_run.input_artifact_refs):
            boundary_runs.add(run_id)
        if (
            isinstance(selected_run, SucceededRun)
            and selected_run.output_artifact_ref not in selected_artifacts
        ):
            boundary_runs.add(run_id)
    for ref in selected_artifacts:
        producer = artifacts[ref].summary.produced_by_run
        if producer is not None and producer not in selected_runs:
            boundary_artifacts.add(ref)
        if any(
            ref in run.input_artifact_refs and run.run_id not in selected_runs
            for run in runs.values()
        ):
            boundary_artifacts.add(ref)
    truncated = len(selected_runs) + len(selected_artifacts) < len(runs) + len(artifacts)
    return selected_runs, selected_artifacts, boundary_artifacts, boundary_runs, truncated


def _build_graph(
    *,
    session_id: str,
    project_root: Path,
    run_rows: Iterable[sqlite3.Row],
    input_rows: Iterable[sqlite3.Row],
    artifact_rows: Iterable[sqlite3.Row],
    max_nodes: int,
    focused: _FocusedRuntimeRows | None,
) -> SessionGraph:
    inputs = _inputs_by_run(input_rows)
    all_runs = _runs_from_rows(run_rows, inputs=inputs)
    all_artifacts = {
        str(row["artifact_id"]): _read_artifact_facts(project_root=project_root, row=row)
        for row in artifact_rows
        if str(row["kind"]) not in _SIDECAR_KINDS
    }
    if focused is None:
        selected_run_ids, selected_artifact_refs, boundary_artifacts, boundary_runs, truncated = (
            _overall_selection(runs=all_runs, artifacts=all_artifacts, max_nodes=max_nodes)
        )
    else:
        selected_run_ids = set(all_runs)
        selected_artifact_refs = set(all_artifacts)
        boundary_artifacts = set(focused.boundary_artifact_refs)
        boundary_runs = set(focused.boundary_run_ids)
        truncated = focused.truncated
    selected_runs = {run_id: all_runs[run_id] for run_id in selected_run_ids}
    selected_artifacts = {ref: all_artifacts[ref] for ref in selected_artifact_refs}
    tolerated_artifacts = set(boundary_artifacts)
    tolerated_runs = set(boundary_runs)
    for run_id in boundary_runs:
        boundary_run = selected_runs.get(run_id)
        if boundary_run is not None:
            tolerated_artifacts.update(
                ref for ref in boundary_run.input_artifact_refs if ref not in selected_artifacts
            )
    for ref in boundary_artifacts:
        boundary_facts = selected_artifacts.get(ref)
        if (
            boundary_facts is not None
            and boundary_facts.summary.produced_by_run is not None
            and boundary_facts.summary.produced_by_run not in selected_runs
        ):
            tolerated_runs.add(boundary_facts.summary.produced_by_run)
    if focused is not None:
        for run in selected_runs.values():
            tolerated_artifacts.update(
                ref for ref in run.input_artifact_refs if ref not in selected_artifacts
            )
            if isinstance(run, SucceededRun) and run.output_artifact_ref not in selected_artifacts:
                tolerated_runs.add(run.run_id)
        tolerated_runs.update(
            facts.summary.produced_by_run
            for facts in selected_artifacts.values()
            if facts.summary.produced_by_run is not None
            and facts.summary.produced_by_run not in selected_runs
        )
    _validate_graph_integrity(
        runs=selected_runs,
        artifacts=selected_artifacts,
        tolerate_missing_boundary_artifacts=frozenset(tolerated_artifacts),
        tolerate_missing_boundary_runs=frozenset(tolerated_runs),
    )
    edges = _edge_values(runs=selected_runs, artifacts=selected_artifacts)
    order = _topological_order(runs=selected_runs, artifacts=selected_artifacts, edges=edges)
    positions = {node: index for index, node in enumerate(order)}
    ordered_runs = tuple(selected_runs[identity] for kind, identity in order if kind == "run")
    ordered_artifacts = tuple(
        selected_artifacts[identity].summary for kind, identity in order if kind == "artifact"
    )
    ordered_edges = tuple(
        sorted(
            edges,
            key=lambda edge: (
                positions[
                    ("artifact", edge.artifact_ref)
                    if edge.kind == "consumes"
                    else ("run", edge.run_id)
                ],
                positions[
                    ("run", edge.run_id)
                    if edge.kind == "consumes"
                    else ("artifact", edge.artifact_ref)
                ],
                edge.kind,
            ),
        )
    )
    succeeded_consumers = {
        ref
        for run in all_runs.values()
        if isinstance(run, SucceededRun)
        for ref in run.input_artifact_refs
    }
    if focused is not None:
        succeeded_consumers.update(focused.non_head_artifact_refs)
    root_run_ids = tuple(run.run_id for run in ordered_runs if not run.input_artifact_refs)
    head_artifact_refs = tuple(
        artifact.ref for artifact in ordered_artifacts if artifact.ref not in succeeded_consumers
    )
    return SessionGraph(
        session_id=session_id,
        artifacts=ordered_artifacts,
        runs=ordered_runs,
        edges=ordered_edges,
        root_run_ids=root_run_ids,
        head_artifact_refs=head_artifact_refs,
        failed_run_ids=tuple(run.run_id for run in ordered_runs if isinstance(run, FailedRun)),
        incomplete_run_ids=tuple(
            run.run_id for run in ordered_runs if isinstance(run, IncompleteRun)
        ),
        boundary_artifact_refs=tuple(sorted(boundary_artifacts & selected_artifact_refs)),
        boundary_run_ids=tuple(sorted(boundary_runs & selected_run_ids)),
        truncated=truncated,
    )


def _recap_int(values: Mapping[str, object], key: str) -> int:
    value = values[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise SessionGraphIntegrityError.mismatch(
            message="Session runtime recap count is invalid",
            expected="one non-negative integer",
            received=repr(value),
            location=f"Session recap {key}",
        )
    return value


def read_run_page(
    *,
    store: object,
    session_id: str,
    status: Literal["incomplete", "succeeded", "failed"] | None = None,
    capability_id: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> RunPage:
    """Project a bounded Run page from a schema-validated Session Store."""
    if not 1 <= limit <= 100:
        raise ValueError("runs limit must be within [1, 100]")
    if status not in {None, "incomplete", "succeeded", "failed"}:
        raise ValueError("runs status must be incomplete, succeeded, failed, or None")
    if capability_id is not None and capability_id not in REGISTRY.capability_ids:
        raise ValueError("runs capability_id must be one exact current capability id")
    after: tuple[str, str] | None = None
    if cursor is not None:
        started_at, run_id = decode_keyset_cursor(cursor)
        if not isinstance(started_at, str):
            raise ValueError("runs cursor has an invalid sort key")
        after = started_at, run_id
    rows = store.page_runs(  # type: ignore[attr-defined]
        session_id,
        status=status,
        capability_id=capability_id,
        limit=limit,
        after=after,
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    items = tuple(
        _row_to_run(
            row,
            input_refs=store.run_input_refs(  # type: ignore[attr-defined]
                session_id, str(row["run_id"])
            ),
        )
        for row in visible
    )
    next_cursor = None
    if has_more:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(str(last["started_at"]), str(last["run_id"]))
    return RunPage(items=items, limit=limit, has_more=has_more, next_cursor=next_cursor)


class SessionRuntimeReads:
    """Read the exact current persisted Run, Artifact, and graph projection."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _validate_schema(self) -> None:
        self._session._store.validate_session_runtime_read_schema(self._session.id)

    def runs(
        self,
        *,
        status: Literal["incomplete", "succeeded", "failed"] | None = None,
        capability_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> RunPage:
        self._validate_schema()
        return read_run_page(
            store=self._session._store,
            session_id=self._session.id,
            status=status,
            capability_id=capability_id,
            limit=limit,
            cursor=cursor,
        )

    def get_run(self, run_id: str) -> RunRecord:
        self._validate_schema()
        row = self._session._store.get_run(self._session.id, run_id)
        if row is None:
            raise RunNotFoundError.for_id(run_id)
        return _row_to_run(
            row,
            input_refs=self._session._store.run_input_refs(self._session.id, run_id),
        )

    def artifact(self, ref: str) -> BaseFrame:
        from marivo.analysis.session._load import load_frame

        self._validate_schema()
        if self._session._store.get_artifact(self._session.id, ref) is None:
            raise ArtifactNotFoundError.for_ref(ref)
        return load_frame(ref, session=self._session)

    def revalidate(self, ref: str) -> ArtifactRevalidation:
        from marivo.analysis._artifact_revalidation import evaluate_artifact_revalidation

        frame = self.artifact(ref)
        store = self._session._evidence_store()
        if store is None:
            raise EvidenceStoreUnavailableError(
                message="Evidence Store is unavailable for Artifact revalidation",
                expected="a readable exact-current judgment.db",
                received="unavailable",
                location=f"Artifact {ref!r} revalidation",
            )
        return evaluate_artifact_revalidation(session=self._session, store=store, frame=frame)

    def graph(
        self,
        *,
        artifact_ref: str | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        if (
            not isinstance(max_nodes, int)
            or isinstance(max_nodes, bool)
            or not 1 <= max_nodes <= 500
        ):
            raise SessionGraphLimitError.for_value(max_nodes)
        if direction not in {"ancestors", "descendants"} or (
            artifact_ref is None and direction != "ancestors"
        ):
            raise SessionGraphArgumentError.invalid(
                artifact_ref=artifact_ref,
                direction=direction,
            )
        self._validate_schema()
        if artifact_ref is None:
            try:
                run_rows, input_rows, artifact_rows = self._session._store.runtime_snapshot(
                    self._session.id,
                    max_records=_OVERALL_GRAPH_SCAN_LIMIT,
                )
            except _RuntimeSnapshotTooLargeError as exc:
                raise SessionGraphTooLargeError.for_count(
                    count=exc.count,
                    limit=_OVERALL_GRAPH_SCAN_LIMIT,
                ) from exc
            return _build_graph(
                session_id=self._session.id,
                project_root=self._session.project_root,
                run_rows=run_rows,
                input_rows=input_rows,
                artifact_rows=artifact_rows,
                max_nodes=max_nodes,
                focused=None,
            )
        focused = self._session._store.focused_runtime_snapshot(
            self._session.id,
            artifact_ref=artifact_ref,
            direction=direction,
            max_nodes=max_nodes,
        )
        if artifact_ref in focused.missing_artifact_refs:
            raise ArtifactNotFoundError.for_ref(artifact_ref)
        if focused.missing_artifact_refs or focused.missing_run_ids:
            raise SessionGraphIntegrityError.mismatch(
                message="focused Session graph references missing canonical records",
                expected="same-Session Run and Artifact records for every indexed adjacency",
                received=(
                    f"artifacts={focused.missing_artifact_refs!r}, runs={focused.missing_run_ids!r}"
                ),
                location=f"Artifact {artifact_ref!r} focused traversal",
            )
        return _build_graph(
            session_id=self._session.id,
            project_root=self._session.project_root,
            run_rows=focused.runs,
            input_rows=focused.inputs,
            artifact_rows=focused.artifacts,
            max_nodes=max_nodes,
            focused=focused,
        )

    def recap(self) -> SessionRuntimeRecap:
        self._validate_schema()
        values = self._session._store.runtime_recap(self._session.id)
        return SessionRuntimeRecap(
            session_id=self._session.id,
            artifact_count=_recap_int(values, "artifact_count"),
            head_artifact_count=_recap_int(values, "head_artifact_count"),
            head_artifact_refs=cast("tuple[str, ...]", values["head_artifact_refs"]),
            succeeded_run_count=_recap_int(values, "succeeded_count"),
            failed_run_count=_recap_int(values, "failed_count"),
            incomplete_run_count=_recap_int(values, "incomplete_count"),
            evidence_complete_count=_recap_int(values, "evidence_complete_count"),
            evidence_partial_count=_recap_int(values, "evidence_partial_count"),
            evidence_unavailable_count=_recap_int(values, "evidence_unavailable_count"),
            attention_run_ids=cast("tuple[str, ...]", values["attention_run_ids"]),
            overall_graph_available=_recap_int(values, "graph_record_count")
            <= _OVERALL_GRAPH_SCAN_LIMIT,
        )


__all__ = [
    "SessionRuntimeReads",
    "read_run_page",
]
