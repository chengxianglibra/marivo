"""Stable full-digest identities for every CandidateSet shape."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, cast

import pandas as pd

from marivo.analysis.candidate_lineage import (
    SemanticEdgeContext,
    SemanticHypothesisExclusion,
    _semantic_edge_context_relation,
)
from marivo.analysis.errors import AnalysisRepair, FrameMetaInvalidError
from marivo.analysis.evidence.identity import canonical_json
from marivo.introspection.live.model import LiveHelpTarget
from marivo.ontology.types import SemanticEdgeRef, _restore_semantic_edge_ref
from marivo.refs import Ref, RefPayloadV1, SemanticKind, SemanticKindTag

_ITEM_ID_RE = re.compile(r"^candidate_[0-9a-f]{64}$")


def _coordinate(value: object) -> object:
    if type(value) is Ref:
        return RefPayloadV1.from_ref(cast_ref(value)).to_dict()
    if type(value) is SemanticEdgeRef:
        return value.to_dict()
    if type(value) is RefPayloadV1:
        return value.to_dict()
    if isinstance(value, Mapping):
        return {
            str(key): _coordinate(item)
            for key, item in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_coordinate(item) for item in value]
    return value


def cast_ref(value: object) -> Ref[SemanticKindTag]:
    return cast("Ref[SemanticKindTag]", value)


def _candidate_repair() -> AnalysisRepair:
    """Typed repair for a stale/malformed CandidateSet artifact."""
    return AnalysisRepair(
        kind="retry",
        action=(
            "the CandidateSet persisted metadata does not match its rows. "
            "Re-run the candidate-producing intent (session.discover) to rebuild it."
        ),
        help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
        snippet="session.discover(metric_ref, ...)",
    )


def make_candidate_item_id(tag: str, *coordinates: object) -> str:
    """Return ``candidate_`` plus a full canonical-JSON SHA-256 digest."""
    payload = [tag, *(_coordinate(value) for value in coordinates)]
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"candidate_{digest}"


def scored_candidate_coordinates(shape: str, row: Mapping[str, Any]) -> tuple[object, ...]:
    """Project one scorer row onto the shape's stable semantic coordinates."""
    keys = row.get("keys", {})
    window = row.get("window")
    if shape == "point_anomaly":
        return (keys, window)
    if shape == "period_shift":
        return (keys, window, row.get("baseline_window"))
    if shape == "driver_axis":
        return (row.get("axis_semantic_id") or row.get("axis"),)
    if shape == "slice":
        return (row.get("selector", {}), window)
    if shape == "window":
        return (keys, window)
    if shape == "cross_sectional_outlier":
        return (keys, tuple(row.get("peer_scope", ())))
    raise ValueError(f"unsupported CandidateSet shape {shape!r}")


def assign_scored_item_ids(
    *, shape: str, source_artifact_ref: str, rows: list[dict[str, Any]]
) -> None:
    """Replace scorer-local ids with stable artifact-bound identities in place."""
    tag = f"{shape}/v1"
    for row in rows:
        row["item_id"] = make_candidate_item_id(
            tag, source_artifact_ref, *scored_candidate_coordinates(shape, row)
        )


def _frame_json(row: Mapping[str, Any], column: str, default: object) -> object:
    value = row.get(column)
    if not isinstance(value, str) or not value:
        return default
    return json.loads(value)


def _frame_window(row: Mapping[str, Any], start: str, end: str) -> object:
    start_value = row.get(start)
    end_value = row.get(end)
    if pd.isna(start_value) or pd.isna(end_value):
        return None
    return {
        "start": pd.Timestamp(start_value).isoformat(),
        "end": pd.Timestamp(end_value).isoformat(),
    }


def scored_frame_coordinates(shape: str, row: Mapping[str, Any]) -> tuple[object, ...]:
    keys = _frame_json(row, "keys_json", {})
    window = _frame_window(row, "window_start", "window_end")
    if shape == "point_anomaly":
        return (keys, window)
    if shape == "period_shift":
        return (
            keys,
            window,
            _frame_window(row, "baseline_window_start", "baseline_window_end"),
        )
    if shape == "driver_axis":
        semantic_id = row.get("axis_semantic_id")
        return (semantic_id if isinstance(semantic_id, str) and semantic_id else row.get("axis"),)
    if shape == "slice":
        return (_frame_json(row, "selector_json", {}), window)
    if shape == "window":
        return (keys, window)
    if shape == "cross_sectional_outlier":
        peer_scope = _frame_json(row, "peer_scope_json", [])
        if not isinstance(peer_scope, list):
            raise ValueError("peer_scope_json must decode to a list")
        return (keys, tuple(peer_scope))
    raise ValueError(f"unsupported CandidateSet shape {shape!r}")


def expected_scored_frame_item_id(
    *, shape: str, source_artifact_ref: str, row: Mapping[str, Any]
) -> str:
    return make_candidate_item_id(
        f"{shape}/v1",
        source_artifact_ref,
        *scored_frame_coordinates(shape, row),
    )


def assign_scored_frame_item_ids(
    *, shape: str, source_artifact_ref: str, dataframe: pd.DataFrame
) -> None:
    for index, row in dataframe.iterrows():
        dataframe.at[index, "item_id"] = expected_scored_frame_item_id(
            shape=shape,
            source_artifact_ref=source_artifact_ref,
            row=cast("dict[str, Any]", row.to_dict()),
        )


def semantic_hypothesis_item_id(
    *,
    source_artifact_ref: str,
    semantic_edge_ref: SemanticEdgeRef,
    candidate_semantic_ref: RefPayloadV1,
    metric_ref: RefPayloadV1,
) -> str:
    """Build the exact v1 semantic-hypothesis candidate identity."""
    return make_candidate_item_id(
        "semantic_hypothesis/v1",
        source_artifact_ref,
        semantic_edge_ref,
        candidate_semantic_ref,
        metric_ref,
    )


def decode_json_cell(value: object) -> object:
    if not isinstance(value, str) or not value:
        return None
    return json.loads(value)


def _ref_payload_cell(value: object) -> RefPayloadV1:
    decoded = decode_json_cell(value)
    if not isinstance(decoded, dict) or set(decoded) != {"schema", "kind", "path"}:
        raise ValueError("semantic ref cell must contain exactly schema, kind, and path")
    schema = decoded["schema"]
    kind = decoded["kind"]
    path = decoded["path"]
    if schema != "marivo.semantic_ref/v1" or not isinstance(kind, str) or not isinstance(path, str):
        raise ValueError("semantic ref cell is not a canonical marivo.semantic_ref/v1 payload")
    return RefPayloadV1(schema="marivo.semantic_ref/v1", kind=SemanticKind(kind), path=path)


def validate_candidate_frame_identity(
    *, shape: str, source_artifact_ref: str, dataframe: pd.DataFrame
) -> None:
    """Reject malformed, duplicate, or coordinate-mismatched persisted item ids."""
    item_ids = dataframe["item_id"].astype("string").tolist()
    if len(set(item_ids)) != len(item_ids):
        raise FrameMetaInvalidError(
            message="CandidateSet item_id values must be unique",
            repair=_candidate_repair(),
            context={"kind": "CandidateIdentityInvalid", "reason": "duplicate"},
        )
    for index, row in dataframe.iterrows():
        item_id = row["item_id"]
        if not isinstance(item_id, str) or _ITEM_ID_RE.fullmatch(item_id) is None:
            raise FrameMetaInvalidError(
                message=f"candidate row {index} has a malformed full-digest item_id",
                repair=_candidate_repair(),
                context={
                    "kind": "CandidateIdentityInvalid",
                    "reason": "malformed",
                    "row_index": str(index),
                },
            )
        try:
            if shape == "semantic_hypothesis":
                edge_payload = decode_json_cell(row["semantic_edge_ref"])
                edge_ref = _restore_semantic_edge_ref(edge_payload)
                candidate_ref = _ref_payload_cell(row["candidate_semantic_ref"])
                metric_ref = _ref_payload_cell(row["metric_ref"])
                expected = semantic_hypothesis_item_id(
                    source_artifact_ref=source_artifact_ref,
                    semantic_edge_ref=edge_ref,
                    candidate_semantic_ref=candidate_ref,
                    metric_ref=metric_ref,
                )
            else:
                expected = expected_scored_frame_item_id(
                    shape=shape,
                    source_artifact_ref=source_artifact_ref,
                    row=cast("dict[str, Any]", row.to_dict()),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise FrameMetaInvalidError(
                message=f"candidate row {index} has invalid identity coordinates",
                repair=_candidate_repair(),
                context={
                    "kind": "CandidateIdentityInvalid",
                    "reason": str(error),
                    "row_index": str(index),
                },
            ) from error
        if item_id != expected:
            raise FrameMetaInvalidError(
                message=f"candidate row {index} item_id does not match its coordinates",
                repair=_candidate_repair(),
                context={
                    "kind": "CandidateIdentityInvalid",
                    "reason": "digest_mismatch",
                    "row_index": str(index),
                    "expected_item_id": expected,
                },
            )


def validate_semantic_hypothesis_frame_integrity(
    *,
    dataframe: pd.DataFrame,
    edge_contexts: tuple[SemanticEdgeContext, ...],
    readiness_fingerprints: Mapping[RefPayloadV1, str],
    exclusions: tuple[SemanticHypothesisExclusion, ...],
) -> None:
    """Reject semantic candidate rows whose persisted metadata is not an exact closure."""

    def invalid(reason: str, *, row_index: object | None = None) -> FrameMetaInvalidError:
        context: dict[str, object] = {
            "kind": "CandidateFrameIntegrityInvalid",
            "reason": reason,
        }
        if row_index is not None:
            context["row_index"] = str(row_index)
        return FrameMetaInvalidError(
            message="semantic-hypothesis CandidateSet metadata does not match its rows",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "the semantic-hypothesis CandidateSet persisted metadata does "
                    "not match its rows. Re-run the candidate-producing intent "
                    "(session.discover) to rebuild it."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                snippet="session.discover(metric_ref, ...)",
            ),
            context=context,
        )

    contexts = {context.semantic_edge_ref: context for context in edge_contexts}
    row_edge_refs: set[SemanticEdgeRef] = set()
    row_metric_refs: set[RefPayloadV1] = set()
    for index, row in dataframe.iterrows():
        try:
            edge_ref = _restore_semantic_edge_ref(decode_json_cell(row["semantic_edge_ref"]))
            candidate_ref = _ref_payload_cell(row["candidate_semantic_ref"])
            metric_ref = _ref_payload_cell(row["metric_ref"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise invalid("invalid_semantic_coordinates", row_index=index) from error
        if candidate_ref.kind not in {
            SemanticKind.ENTITY,
            SemanticKind.MEASURE,
            SemanticKind.METRIC,
        }:
            raise invalid("unsupported_candidate_semantic_kind", row_index=index)
        if metric_ref.kind is not SemanticKind.METRIC:
            raise invalid("resolved_ref_is_not_metric", row_index=index)
        context = contexts.get(edge_ref)
        if context is None:
            raise invalid("missing_edge_context", row_index=index)
        relation = row["edge_relation"]
        if relation not in {"influences", "related_to"}:
            raise invalid("unsupported_edge_relation", row_index=index)
        if relation != _semantic_edge_context_relation(context):
            raise invalid("edge_relation_context_mismatch", row_index=index)
        if metric_ref not in readiness_fingerprints:
            raise invalid("missing_readiness_binding", row_index=index)
        row_edge_refs.add(edge_ref)
        row_metric_refs.add(metric_ref)

    expected_context_refs = row_edge_refs | {
        exclusion.semantic_edge_ref for exclusion in exclusions
    }
    if set(contexts) != expected_context_refs:
        raise invalid("edge_context_closure_mismatch")
    if set(readiness_fingerprints) != row_metric_refs:
        raise invalid("readiness_binding_closure_mismatch")


__all__ = [
    "assign_scored_frame_item_ids",
    "assign_scored_item_ids",
    "expected_scored_frame_item_id",
    "make_candidate_item_id",
    "scored_candidate_coordinates",
    "semantic_hypothesis_item_id",
    "validate_candidate_frame_identity",
    "validate_semantic_hypothesis_frame_integrity",
]
