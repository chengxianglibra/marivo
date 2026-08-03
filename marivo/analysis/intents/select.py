"""CandidateSet.select - one closed typed value by stable item identity."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.candidate import (
    CandidateSelection,
    CandidateSet,
    CrossSectionalOutlierSelection,
    DriverAxisSelection,
    PeriodShiftSelection,
    PointAnomalySelection,
    SemanticHypothesisCandidateSetMeta,
    SliceSelection,
    WindowSelection,
    _make_semantic_metric_candidate,
)
from marivo.analysis.windows import AbsoluteWindow
from marivo.ontology.types import _restore_semantic_edge_ref
from marivo.refs import DimensionKind, Ref, RefPayloadV1, SemanticKind
from marivo.refs import ref as ref_factory


def _ref_payload(raw: object) -> RefPayloadV1:
    if not isinstance(raw, str) or not raw:
        raise ValueError("candidate ref cell must be canonical JSON")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"schema", "kind", "path"}:
        raise ValueError("candidate ref cell has invalid fields")
    return RefPayloadV1(
        schema=payload["schema"],
        kind=SemanticKind(payload["kind"]),
        path=payload["path"],
    )


def select(candidate_set: CandidateSet, *, item_id: str) -> CandidateSelection:
    """Return the immutable candidate whose exact stable item id matches."""
    if not isinstance(candidate_set, CandidateSet):
        raise SemanticKindMismatchError(
            message="select requires a CandidateSet input",
            context={"expected_kind": "candidate_set", "got_kind": type(candidate_set).__name__},
        )
    if type(item_id) is not str or not item_id:
        raise SemanticKindMismatchError(
            message="select item_id must be a non-empty string",
            context={"expected_kind": "candidate item_id", "got_kind": type(item_id).__name__},
        )
    matches = candidate_set._df.index[candidate_set._df["item_id"] == item_id].tolist()
    if len(matches) != 1:
        raise SemanticKindMismatchError(
            message=f"select item_id {item_id!r} was not found exactly once",
            context={
                "item_id": item_id,
                "match_count": len(matches),
                "row_count": len(candidate_set),
            },
        )
    row = candidate_set._df.loc[matches[0]]
    candidate_set_ref = candidate_set.meta.artifact_id or candidate_set.meta.ref
    common: dict[str, Any] = {
        "candidate_set_ref": candidate_set_ref,
        "item_id": item_id,
        "source_artifact_ref": candidate_set.meta.source_ref,
    }
    shape = candidate_set.meta.shape
    if shape == "semantic_hypothesis":
        meta = candidate_set.meta
        assert isinstance(meta, SemanticHypothesisCandidateSetMeta)
        edge_ref = _restore_semantic_edge_ref(json.loads(str(row["semantic_edge_ref"])))
        contexts = {context.semantic_edge_ref: context for context in meta.edge_contexts}
        readiness = {binding.metric_ref: binding.fingerprint for binding in meta.readiness_bindings}
        metric_ref = _ref_payload(row["metric_ref"])
        return _make_semantic_metric_candidate(
            **common,
            metric_ref=metric_ref,
            semantic_edge_ref=edge_ref,
            edge_relation=cast("Literal['influences', 'related_to']", str(row["edge_relation"])),
            candidate_semantic_ref=_ref_payload(row["candidate_semantic_ref"]),
            edge_context=contexts[edge_ref],
            inherited_scope=meta.inherited_scope,
            readiness_fingerprint=readiness[metric_ref],
            ontology_catalog_fingerprint=meta.ontology_catalog_fingerprint,
            semantic_catalog_fingerprint=meta.semantic_catalog_fingerprint,
            source_metric_ref=meta.source_metric_ref,
            upstream_origins=meta.upstream_origins,
        )
    scored = {
        **common,
        "score": float(row["score"]),
        "reason_codes": tuple(_json_list(row["reason_codes_json"])),
    }
    if shape == "point_anomaly":
        return PointAnomalySelection(
            **scored,
            window=_optional_window(row, "window_start", "window_end"),
            keys=_selector(row, "keys_json"),
            direction=str(row["direction"]),
            observed_value=float(row["observed_value"]),
            baseline_value=float(row["baseline_value"]),
            delta=float(row["delta"]),
        )
    if shape == "period_shift":
        return PeriodShiftSelection(
            **scored,
            window=_required_window(row, "window_start", "window_end", shape),
            baseline_window=_required_window(
                row, "baseline_window_start", "baseline_window_end", shape
            ),
            keys=_selector(row, "keys_json"),
            direction=str(row["direction"]),
        )
    if shape == "driver_axis":
        semantic_id = row.get("axis_semantic_id")
        axis: Ref[DimensionKind] | str = (
            ref_factory.dimension(semantic_id)
            if isinstance(semantic_id, str) and semantic_id
            else str(row["axis"])
        )
        return DriverAxisSelection(**scored, axis=axis)
    if shape == "slice":
        return SliceSelection(
            **scored,
            selector=_selector(row, "selector_json", required=True),
            window=_optional_window(row, "window_start", "window_end"),
        )
    if shape == "window":
        return WindowSelection(
            **scored,
            window=_required_window(row, "window_start", "window_end", shape),
            keys=_selector(row, "keys_json"),
        )
    if shape == "cross_sectional_outlier":
        return CrossSectionalOutlierSelection(
            **scored,
            keys=_selector(row, "keys_json", required=True),
            direction=str(row["direction"]),
            peer_scope=tuple(_json_list(row["peer_scope_json"])),
        )
    raise AssertionError(f"unhandled CandidateSet shape {shape!r}")


def _json_list(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw:
        return []
    decoded = json.loads(raw)
    return [str(value) for value in decoded] if isinstance(decoded, list) else []


def _selector(
    row: pd.Series, column: str, *, required: bool = False
) -> dict[Ref[DimensionKind] | str, str | int | float | bool | None]:
    raw = row[column]
    if not isinstance(raw, str) or not raw:
        if required:
            raise SemanticKindMismatchError(
                message=f"candidate row has no {column}",
                context={"shape": column, "selector_column": column},
            )
        return {}
    decoded = cast("dict[str, str | int | float | bool | None]", json.loads(raw))
    return {_selector_key(name): value for name, value in decoded.items()}


def _selector_key(name: str) -> Ref[DimensionKind] | str:
    return ref_factory.dimension(name) if name.count(".") >= 2 else name


def _optional_window(row: pd.Series, start: str, end: str) -> AbsoluteWindow | None:
    if pd.isna(row[start]) or pd.isna(row[end]):
        return None
    return _absolute_window(row[start], row[end])


def _required_window(row: pd.Series, start: str, end: str, shape: str) -> AbsoluteWindow:
    window = _optional_window(row, start, end)
    if window is None:
        raise SemanticKindMismatchError(
            message=f"CandidateSet[{shape}] row has no required window",
            context={"shape": shape, "window_columns": [start, end]},
        )
    return window


def _absolute_window(start_value: Any, end_value: Any) -> AbsoluteWindow:
    return AbsoluteWindow(
        start=pd.Timestamp(start_value).isoformat(), end=pd.Timestamp(end_value).isoformat()
    )


__all__ = ["select"]
