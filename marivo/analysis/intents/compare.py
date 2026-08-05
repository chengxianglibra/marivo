"""Compare two MetricFrames into a DeltaFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

import numpy as np
import pandas as pd

from marivo.analysis._cumulative import (
    BASELINE_EVALUATION_END_COLUMN,
    CURRENT_EVALUATION_END_COLUMN,
    EVALUATION_END_COLUMN,
    AllHistoryLevelChangeV1,
    AllHistoryPairAlignmentV1,
    CumulativeAlignmentV1,
    CumulativePairSummaryV1,
    cumulative_alignment_evidence,
    cumulative_compare_anchor,
    cumulative_equivalent_comparison_semantics,
)
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.attribution_contract import basis_fingerprint
from marivo.analysis.calendar.align import _local_dates, align_calendar_frames
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.candidate_lineage import CandidateOrigin, merge_candidate_origins
from marivo.analysis.cumulative_attribution import (
    CumulativeAttributionContractV1,
    build_cumulative_attribution_contract,
    cumulative_over_ref,
    derive_cumulative_bridge,
)
from marivo.analysis.delta_math import PCT_CHANGE_STATUS_COLUMN, compute_delta_columns
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    AnalysisError,
    AnalysisRepair,
    AttributionBasisMismatchError,
    CalendarPolicyError,
    ComponentFrameMismatchError,
    ComponentFrameUnavailableError,
    CrossSessionFrameError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.identity import make_component_artifact_id
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
    rollback_committed_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.component import (
    ComponentFrame,
    ComponentFrameMeta,
    resolve_role_columns,
)
from marivo.analysis.frames.delta import (
    CumulativeDeltaFrameMetaV1,
    DeltaFrame,
    DeltaFrameMeta,
    _compatible_metric_semantics,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._validate import raise_first, require_single_metric, validate_compare
from marivo.analysis.intents._window_pairs import (
    _not_nan,
    _panel_grain,
    _panel_grains,
    _prepared_value_map,
    _walk_ordinal_pairs,
    _window_bucket_values,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_frame,
    persist_job_record,
    persist_reused_artifact_job,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    DeltaComparisonIdentity,
    DeltaComparisonSemantics,
    ExactComparisonSemanticsV1,
)
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint

EXPECTED_METRIC_FRAME_KIND = "metric_frame"
PRESENCE_STATUS_COLUMN = "presence_status"

# Result protocol columns every compare alignment path produces.  A semantic
# dimension column named like one of these would collide when the value column
# is renamed to ``current``/``baseline`` or ``delta``/``pct_change`` are
# appended, so the collision must fail closed before any pandas merge/rename
# instead of surfacing a raw duplicate-column exception (issue #39).
_COMPARE_RESULT_PROTOCOL_COLUMNS = frozenset(
    {
        "current",
        "baseline",
        "delta",
        "pct_change",
        PCT_CHANGE_STATUS_COLUMN,
        PRESENCE_STATUS_COLUMN,
        CURRENT_EVALUATION_END_COLUMN,
        BASELINE_EVALUATION_END_COLUMN,
    }
)


def _validate_compare_protocol_columns(
    columns: list[str],
    *,
    time_column: str | None = None,
    role: str = "dimension",
) -> None:
    """Fail closed when a semantic column collides with the compare result layout.

    ``columns`` are the semantic columns the current path keeps alongside the
    result protocol columns (dimension columns for segmented/panel, and the
    time column for time_series/panel).  A column named ``current``/``baseline``/
    ``delta``/``pct_change``/``pct_change_status``/``presence_status`` would
    collide when the value column is renamed or those fields are appended, so
    it must fail closed before any pandas merge/rename (issue #39).
    """
    reserved_columns = set(_COMPARE_RESULT_PROTOCOL_COLUMNS)
    if time_column is not None:
        # Panel ordinal alignment also emits a paired baseline bucket column;
        # a dimension/time column matching either name must be rejected too.
        reserved_columns.add(f"{time_column}_b")
    conflicting_columns = [column for column in columns if column in reserved_columns]
    if not conflicting_columns:
        return
    raise SemanticKindMismatchError(
        message="compare semantic column conflicts with a result protocol column",
        expected="a semantic column outside the compare result protocol namespace",
        received=", ".join(repr(column) for column in conflicting_columns),
        location="session.compare",
        repair=AnalysisRepair(
            kind="semantic_authoring",
            action=(
                f"Rename {role} column(s) {', '.join(repr(c) for c in conflicting_columns)} "
                "to non-reserved semantic names, reload the catalog, then re-observe "
                "and compare before retrying."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        ),
        hint=(
            "The compare result keeps the semantic columns and the current/baseline/"
            "delta/pct_change protocol fields as distinct columns."
        ),
        context={
            "argument": role,
            "reason": "protocol_column_collision",
            "conflicting_columns": conflicting_columns,
            "reserved_columns": sorted(reserved_columns),
        },
    )


def _presence_status(*, has_current: bool, has_baseline: bool) -> str | float:
    if has_current and has_baseline:
        return "matched"
    if has_current:
        return "new"
    if has_baseline:
        return "churned"
    return np.nan


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _display_kind(kind: str) -> str:
    return "".join(part.capitalize() for part in kind.split("_"))


def _frame_kind(frame: object) -> str | None:
    meta = getattr(frame, "meta", None)
    kind = getattr(meta, "kind", None)
    return kind if isinstance(kind, str) and kind else None


def _require_metric_frame(label: str, frame: object) -> MetricFrame:
    got_kind = _frame_kind(frame)
    if isinstance(frame, MetricFrame) and got_kind == EXPECTED_METRIC_FRAME_KIND:
        return frame
    if got_kind is None:
        got_kind = type(frame).__name__
    raise SemanticKindMismatchError(
        message=(
            f"compare(current, baseline) expected MetricFrame for `{label}`, got {_display_kind(got_kind)}."
        ),
        context={
            "parameter": label,
            "expected_kind": EXPECTED_METRIC_FRAME_KIND,
            "got_kind": got_kind,
        },
    )


# ---------------------------------------------------------------------------
# Component-aware compare helpers
# ---------------------------------------------------------------------------


def _component_composition_kind(frame: MetricFrame) -> str | None:
    """Return the composition kind if the frame is component-aware, else None."""
    comp = frame.meta.composition
    if isinstance(comp, dict) and comp.get("kind"):
        return str(comp["kind"])
    return None


def _component_fold_payload(
    frame: MetricFrame,
    *,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Collect time_fold metadata from the component frame rows, if any."""
    if frame.meta.component_ref is None or session is None:
        return []
    component = load_frame(frame.meta.component_ref, session=session)
    if not hasattr(component, "to_pandas"):
        return []
    rows = component._dataframe_copy().to_dict("records")
    return [
        {
            "component_metric_id": row.get("component_metric_id"),
            "time_fold": row.get("time_fold"),
            "status_time_dimension": row.get("status_time_dimension"),
        }
        for row in rows
        if row.get("time_fold") is not None
    ]


def _load_component_for_compare(frame: MetricFrame, session: Session, label: str) -> ComponentFrame:
    """Load and validate the component frame for a compare input."""
    if frame.meta.component_ref is None:
        raise ComponentFrameUnavailableError(
            message=(
                f"compare input '{label}' has decomposition metadata but no "
                "component_ref; component frame was not persisted by observe"
            ),
            context={"frame_ref": frame.ref, "label": label},
        )
    loaded = load_frame(frame.meta.component_ref, session=session)
    if not isinstance(loaded, ComponentFrame):
        raise ComponentFrameUnavailableError(
            message=(
                f"compare input '{label}' component_ref resolved to "
                f"{loaded.meta.kind!r}, expected component_frame"
            ),
            context={
                "frame_ref": frame.ref,
                "component_ref": frame.meta.component_ref,
                "loaded_kind": loaded.meta.kind,
            },
        )
    return loaded


def _require_compatible_components(
    current_comp: ComponentFrame,
    baseline_comp: ComponentFrame,
    current_parent: MetricFrame,
    baseline_parent: MetricFrame,
) -> None:
    """Validate that two component frames are compatible for delta computation."""
    if current_comp.meta.composition_kind != baseline_comp.meta.composition_kind:
        raise ComponentFrameMismatchError(
            message=(
                "compare inputs have incompatible decomposition kinds: "
                f"{current_comp.meta.composition_kind!r} vs "
                f"{baseline_comp.meta.composition_kind!r}"
            ),
            context={
                "current_kind": current_comp.meta.composition_kind,
                "baseline_kind": baseline_comp.meta.composition_kind,
            },
        )
    current_root_roles = _component_root_roles(current_comp)
    baseline_root_roles = _component_root_roles(baseline_comp)
    if current_root_roles is None and baseline_root_roles is None:
        if set(current_comp.meta.components) != set(baseline_comp.meta.components):
            raise ComponentFrameMismatchError(
                message="compare inputs have incompatible component roles",
                context={
                    "current_components": current_comp.meta.components,
                    "baseline_components": baseline_comp.meta.components,
                },
            )
    elif current_root_roles != baseline_root_roles:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component graph roots",
            context={
                "current_root_roles": current_root_roles,
                "baseline_root_roles": baseline_root_roles,
            },
        )
    if current_comp.meta.semantic_kind != baseline_comp.meta.semantic_kind:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component semantic kinds",
            context={
                "current_semantic_kind": current_comp.meta.semantic_kind,
                "baseline_semantic_kind": baseline_comp.meta.semantic_kind,
            },
        )
    if current_comp.meta.axes != baseline_comp.meta.axes:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component axes",
            context={
                "current_axes": current_comp.meta.axes,
                "baseline_axes": baseline_comp.meta.axes,
            },
        )
    # ``semantic_model`` is a derived display projection and is deliberately
    # empty for runtime-expression identities. Structured graph identities,
    # component roles, axes, and compatibility domains above own comparability.


def _component_axis_columns(component: ComponentFrame) -> list[str]:
    """Extract time and dimension column names from a component frame's axes."""
    columns: list[str] = []
    for axis in component.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        role = axis.get("role")
        if role not in {"time", "dimension"}:
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return columns


def _component_role_columns(component: ComponentFrame) -> list[str]:
    return resolve_role_columns(component.meta.components)


def _component_root_roles(component: ComponentFrame) -> tuple[tuple[str, str], ...] | None:
    """Return the first semantic branch roles below transparent unary roots."""

    graph = component.meta.component_graph
    if graph is None:
        return None
    roots = graph.get("root_node_ids")
    nodes = graph.get("nodes")
    if not isinstance(roots, list) or len(roots) != 1 or not isinstance(nodes, list):
        raise ComponentFrameMismatchError(
            message="compare requires an arity-one component graph",
            context={"component_ref": component.ref, "root_node_ids": roots},
        )
    node_by_id = {
        str(node["node_id"]): node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("node_id"), str)
    }
    node_id = str(roots[0])
    visited: set[str] = set()
    while True:
        if node_id in visited:
            raise ComponentFrameMismatchError(
                message="compare component graph contains a unary wrapper cycle",
                context={"component_ref": component.ref, "root_node_id": roots[0]},
            )
        visited.add(node_id)
        node = node_by_id.get(node_id)
        if node is None:
            raise ComponentFrameMismatchError(
                message="compare component graph is missing its root node",
                context={"component_ref": component.ref, "root_node_id": node_id},
            )
        ordered_children = node.get("ordered_children")
        if not isinstance(ordered_children, list):
            raise ComponentFrameMismatchError(
                message="compare component graph root has no ordered child roles",
                context={"component_ref": component.ref, "root_node_id": node_id},
            )
        if not ordered_children:
            return None
        if len(ordered_children) == 1 and ordered_children[0].get("role") in {
            "base",
            "child",
        }:
            node_id = str(ordered_children[0]["node_id"])
            continue
        return tuple((str(child["role"]), str(child["node_id"])) for child in ordered_children)


def _component_role_column_pairs(
    current: ComponentFrame,
    baseline: ComponentFrame,
) -> list[tuple[str, str, str]]:
    """Map canonical root roles to each side's presentation-specific columns."""

    current_root_roles = _component_root_roles(current)
    baseline_root_roles = _component_root_roles(baseline)
    if current_root_roles is None and baseline_root_roles is None:
        if set(current.meta.components) != set(baseline.meta.components):
            raise ComponentFrameMismatchError(
                message="compare inputs have incompatible component roles",
                context={
                    "current_component_roles": list(current.meta.components),
                    "baseline_component_roles": list(baseline.meta.components),
                },
            )
        current_columns = dict(
            zip(current.meta.components, _component_role_columns(current), strict=True)
        )
        baseline_columns = dict(
            zip(baseline.meta.components, _component_role_columns(baseline), strict=True)
        )
        return [
            (current_columns[role], current_columns[role], baseline_columns[role])
            for role in current.meta.components
        ]
    if current_root_roles is None or baseline_root_roles is None:
        raise ComponentFrameMismatchError(
            message="compare inputs must both persist canonical component graphs",
            context={
                "current_component_graph": current_root_roles is not None,
                "baseline_component_graph": baseline_root_roles is not None,
            },
        )
    if current_root_roles != baseline_root_roles:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component graph roots",
            context={
                "current_root_roles": current_root_roles,
                "baseline_root_roles": baseline_root_roles,
            },
        )
    current_roles = dict(current_root_roles)
    baseline_roles = dict(baseline_root_roles)
    if set(current.meta.components) != set(current_roles) or set(baseline.meta.components) != set(
        baseline_roles
    ):
        raise ComponentFrameMismatchError(
            message="compare component maps do not match their canonical graph roles",
            context={
                "current_component_roles": list(current.meta.components),
                "current_graph_roles": list(current_roles),
                "baseline_component_roles": list(baseline.meta.components),
                "baseline_graph_roles": list(baseline_roles),
            },
        )
    current_column_by_role = dict(
        zip(current.meta.components, _component_role_columns(current), strict=True)
    )
    baseline_column_by_role = dict(
        zip(baseline.meta.components, _component_role_columns(baseline), strict=True)
    )
    return [
        (
            current_column_by_role[role],
            current_column_by_role[role],
            baseline_column_by_role[role],
        )
        for role in current_roles
    ]


def _unmelt_component_frame(
    component: ComponentFrame,
    metric_name: str,
) -> ComponentFrame:
    """Un-melt a long-format (folded) component frame back to wide format.

    When ``_add_fold_metadata_to_component_df`` melted the frame during
    observe, role columns like ``"upstream_bw_p95"`` were replaced by a
    single ``"value"`` column with ``"component_metric_id"`` rows.  This
    reverses the melt so ``_align_component_frames`` can find role columns
    by name.  If the frame is already in wide format, it is returned as-is.
    """
    df = component._df
    if "component_metric_id" not in df.columns:
        return component  # already wide format — nothing to do

    axis_columns = _component_axis_columns(component)
    # In the melted format, "value" is the melted role column (pivoted by
    # component_metric_id).  The metric value column carries the overall
    # metric and is named after the metric (e.g. "p95_utilization").
    # Use the metric-name column as the pivot index.
    index_cols = axis_columns + ([metric_name] if metric_name in df.columns else [])
    pivoted = df.pivot_table(
        index=index_cols,
        columns="component_metric_id",
        values="value",
    ).reset_index()
    pivoted.columns.name = None
    # Preserve column order: axis columns, then role columns, then metric value
    role_cols = [c for c in pivoted.columns if c not in set(index_cols)]
    ordered = [*axis_columns, *role_cols, *[c for c in index_cols if c not in set(axis_columns)]]
    pivoted = pivoted[[c for c in ordered if c in pivoted.columns]]
    return ComponentFrame(_df=pivoted, meta=component.meta)


def _component_value_column(component: ComponentFrame, parent: MetricFrame) -> str | None:
    """Return one side's root value column without treating labels as identity."""

    measure_name = (
        parent.meta.measure.get("name") if isinstance(parent.meta.measure, dict) else None
    )
    if isinstance(measure_name, str) and measure_name in component._df.columns:
        return measure_name
    excluded = {
        *_component_axis_columns(component),
        *_component_role_columns(component),
    }
    candidates = [str(column) for column in component._df.columns if str(column) not in excluded]
    return candidates[0] if len(candidates) == 1 else None


def _component_role_metric_frame(
    parent: MetricFrame,
    component: ComponentFrame,
    *,
    role_column: str,
) -> MetricFrame:
    axis_columns = _component_axis_columns(component)
    df = component._dataframe_copy()[[*axis_columns, role_column]].copy()
    meta = parent.meta.model_copy(
        update={
            "ref": f"{component.ref}_{role_column}",
            "axes": component.meta.axes,
            "measure": {"name": role_column},
            "semantic_kind": component.meta.semantic_kind,
            "component_ref": None,
            "composition": None,
        }
    )
    return MetricFrame(_df=df, meta=meta)


def _align_component_role(
    current_role: MetricFrame,
    baseline_role: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> pd.DataFrame:
    if current_role.meta.semantic_kind == "segmented":
        aligned, _segment_info = _align_segmented(current_role, baseline_role)
        return aligned
    if current_role.meta.semantic_kind == "panel":
        aligned, _segment_info, _calendar_info, _window_info = _align_panel(
            current_role,
            baseline_role,
            alignment=alignment,
            session=session,
        )
        return aligned
    if current_role.meta.semantic_kind == "time_series":
        if alignment.kind == "window_bucket":
            aligned, _window_info = _align_time_series_window_bucket(
                current_role,
                baseline_role,
                alignment=alignment,
                track_presence_status=(
                    isinstance(cumulative_compare_anchor(current_role.meta.cumulative), tuple)
                ),
            )
            return aligned
        calendar_ref = alignment.calendar
        if not isinstance(calendar_ref, CalendarRef):
            raise CalendarPolicyError(
                message="calendar-backed alignment requires CalendarRef",
                context={
                    "kind": "CalendarRefMissing",
                    "alignment": alignment.model_dump(mode="json"),
                },
            )
        loaded_calendar = session._calendars.get(calendar_ref.ref)
        report_tz = session.report_tz_name
        policy = CalendarPolicy(
            mode=alignment.kind,
            align_period=alignment.period,
            fallback=alignment.fallback,
        )
        current_df = current_role._dataframe_copy()
        baseline_df = baseline_role._dataframe_copy()
        time_column = _time_axis_column(current_role)
        value_column = _value_column(current_role, current_df, time_column=time_column)
        baseline_value_column = _value_column(
            baseline_role,
            baseline_df,
            time_column=time_column,
        )
        aligned, _info = align_calendar_frames(
            current_df[[time_column, value_column]],
            baseline_df[[time_column, baseline_value_column]].rename(
                columns={baseline_value_column: value_column}
            ),
            time_column=time_column,
            value_column=value_column,
            calendar=loaded_calendar,
            policy=policy,
            report_tz=report_tz,
        )
        return aligned
    return _align_and_compute(current_role._dataframe_copy(), baseline_role._dataframe_copy())


def _aligned_key_columns(aligned: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in aligned.columns
        if str(column)
        not in {
            PRESENCE_STATUS_COLUMN,
            "current",
            "baseline",
            "delta",
            "pct_change",
            PCT_CHANGE_STATUS_COLUMN,
            CURRENT_EVALUATION_END_COLUMN,
            BASELINE_EVALUATION_END_COLUMN,
        }
    ]


def _restrict_component_to_parent_pairs(
    component: pd.DataFrame,
    parent: pd.DataFrame,
) -> pd.DataFrame:
    """Apply the parent's already-materialized business-coordinate pair set."""

    parent_keys = _aligned_key_columns(parent)
    component_keys = _aligned_key_columns(component)
    # Component alignment can retain the baseline-side coordinate (for example
    # ``bucket_start_b``) even after the parent all-history pair finalization
    # keeps only the canonical current coordinate.  The parent keys are the
    # authoritative pair contract; extra component coordinates are safe to
    # carry through the restricted component frame.
    if not set(parent_keys).issubset(component_keys):
        raise ComponentFrameMismatchError(
            message="component alignment does not match the parent pair-key contract",
            context={
                "expected_key_columns": parent_keys,
                "received_key_columns": component_keys,
            },
        )
    if not parent_keys:
        if len(parent) != len(component):
            raise ComponentFrameMismatchError(
                message="scalar component alignment does not match the parent pair count",
                context={"parent_rows": len(parent), "component_rows": len(component)},
            )
        return component.reset_index(drop=True)
    parent_pair_keys = parent[parent_keys].drop_duplicates().reset_index(drop=True)
    restricted = pd.merge(
        parent_pair_keys,
        component,
        on=parent_keys,
        how="inner",
        validate="one_to_one",
    )
    if len(restricted) != len(parent_pair_keys):
        raise ComponentFrameMismatchError(
            message="component alignment is missing a canonical parent pair key",
            context={
                "parent_pair_rows": len(parent_pair_keys),
                "component_pair_rows": len(restricted),
            },
        )
    return restricted


def _align_component_frames(
    current_comp: ComponentFrame,
    baseline_comp: ComponentFrame,
    current_parent: MetricFrame,
    baseline_parent: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> pd.DataFrame:
    """Merge current/baseline component data with delta columns using parent alignment logic."""
    # Un-melt folded (long-format) component frames so role columns are
    # accessible by name.  Wide-format frames pass through unchanged.
    current_metric_name = (
        current_parent.meta.measure.get("name")
        if isinstance(current_parent.meta.measure, dict)
        else None
    ) or ""
    baseline_metric_name = (
        baseline_parent.meta.measure.get("name")
        if isinstance(baseline_parent.meta.measure, dict)
        else None
    ) or ""
    current_comp = _unmelt_component_frame(current_comp, current_metric_name)
    baseline_comp = _unmelt_component_frame(baseline_comp, baseline_metric_name)

    column_pairs = _component_role_column_pairs(current_comp, baseline_comp)
    current_value_column = _component_value_column(current_comp, current_parent)
    baseline_value_column = _component_value_column(baseline_comp, baseline_parent)
    if (current_value_column is None) != (baseline_value_column is None):
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component root value columns",
            context={
                "current_value_column": current_value_column,
                "baseline_value_column": baseline_value_column,
            },
        )
    # The value column (e.g. "bandwidth_utilization") is aligned through the
    # same role-metric-frame path as decomposition roles (numerator, denominator,
    # weight) so it gets current_/baseline_/delta_ prefixed columns in the output.
    if current_value_column is not None and baseline_value_column is not None:
        column_pairs.append((current_value_column, current_value_column, baseline_value_column))
    result: pd.DataFrame | None = None
    key_columns: list[str] | None = None

    for output_column, current_column, baseline_column in column_pairs:
        current_role = _component_role_metric_frame(
            current_parent,
            current_comp,
            role_column=current_column,
        )
        baseline_role = _component_role_metric_frame(
            baseline_parent,
            baseline_comp,
            role_column=baseline_column,
        )
        aligned = _align_component_role(
            current_role,
            baseline_role,
            alignment=alignment,
            session=session,
        )
        role_keys = _aligned_key_columns(aligned)
        renamed = aligned[[*role_keys, "current", "baseline", "delta"]].rename(
            columns={
                "current": f"current_{output_column}",
                "baseline": f"baseline_{output_column}",
                "delta": f"delta_{output_column}",
            }
        )
        if result is None:
            result = renamed
            key_columns = role_keys
            continue
        if role_keys != key_columns:
            raise ComponentFrameMismatchError(
                message="component role alignment produced incompatible key columns",
                context={
                    "role_column": output_column,
                    "expected_key_columns": key_columns,
                    "got_key_columns": role_keys,
                },
            )
        if not role_keys:
            # Scalar (no-axis) component frames: merge by position instead of
            # by key columns, since there are no axis columns to join on.
            result = pd.concat(
                [result.reset_index(drop=True), renamed.reset_index(drop=True)], axis=1
            )
        else:
            result = pd.merge(result, renamed, on=role_keys, how="outer")

    if result is None:
        raise ComponentFrameMismatchError(
            message="component frame has no role columns to align",
            context={"component_ref": current_comp.ref},
        )
    return result


def _build_delta_component_frame(
    session: Session,
    df: pd.DataFrame,
    parent_ref: str,
    source_component: ComponentFrame,
    job_ref: str,
    candidate_origins: tuple[CandidateOrigin, ...],
) -> ComponentFrame:
    """Build a delta component frame after all fallible alignment succeeds."""
    comp_ref = make_component_artifact_id(parent_ref)
    meta = ComponentFrameMeta(
        ref=comp_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        candidate_origins=candidate_origins,
        parent_ref=parent_ref,
        parent_kind="delta_frame",
        metric_identity=source_component.meta.metric_identity,
        component_bindings=source_component.meta.component_bindings,
        axis_bindings=source_component.meta.axis_bindings,
        composition_kind=source_component.meta.composition_kind,
        linear_terms=source_component.meta.linear_terms,
        semantic_kind=source_component.meta.semantic_kind,
    )
    return ComponentFrame(_df=df, meta=meta)


def _rollback_compare_commit(
    *,
    session: Session,
    evidence_store: Any,
    root_artifact_id: str,
    component_artifact_id: str | None,
    job_ref: str,
) -> None:
    """Best-effort rollback for the bounded compare artifact set."""

    cleanup_actions: list[Callable[[], object]] = [
        lambda: session._store.delete_job(session.id, job_ref),
        lambda: (session._layout.jobs_dir / f"{job_ref}.json").unlink(missing_ok=True),
        lambda: session._store.delete_artifact(session.id, root_artifact_id),
        lambda: rollback_committed_result(
            store=evidence_store,
            frames_dir=session._layout.frames_dir,
            artifact_id=root_artifact_id,
        ),
    ]
    if component_artifact_id is not None:
        cleanup_actions.extend(
            [
                lambda: session._store.delete_artifact(
                    session.id,
                    component_artifact_id,
                ),
                lambda: rollback_committed_result(
                    store=None,
                    frames_dir=session._layout.frames_dir,
                    artifact_id=component_artifact_id,
                ),
            ]
        )
    for cleanup in cleanup_actions:
        try:
            cleanup()
        except BaseException:
            continue


def _finalize_comparable_period_pairs(
    df: pd.DataFrame,
    frame: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    calendar_info: dict[str, Any] | None,
) -> tuple[pd.DataFrame, CumulativePairSummaryV1 | None]:
    """Retain mechanically paired trailing/GTD rows and account for every removal."""

    anchor = cumulative_compare_anchor(frame.meta.cumulative)
    if not (isinstance(anchor, tuple) and anchor and anchor[0] in {"trailing", "grain_to_date"}):
        return df, None

    current_unpaired = 0
    baseline_unpaired = 0
    if PRESENCE_STATUS_COLUMN in df.columns:
        status = df[PRESENCE_STATUS_COLUMN]
        matched = status == "matched"
        current_unpaired = int((status == "new").sum())
        baseline_unpaired = int((status == "churned").sum())
    elif frame.meta.semantic_kind in {"time_series", "panel"}:
        if alignment.kind == "window_bucket":
            time_column = _time_axis_column(frame)
            baseline_time_column = f"{time_column}_b"
        else:
            time_column = "bucket_start_a"
            baseline_time_column = "bucket_start_b"
        if time_column not in df.columns or baseline_time_column not in df.columns:
            raise AlignmentFailedError(
                message="cumulative comparison lost its paired cutoff coordinates",
                context={
                    "kind": "CumulativePairCoordinatesMissing",
                    "current_coordinate": time_column,
                    "baseline_coordinate": baseline_time_column,
                    "columns": [str(column) for column in df.columns],
                },
            )
        has_current = df[time_column].notna()
        has_baseline = df[baseline_time_column].notna()
        matched = has_current & has_baseline
        current_unpaired = int((has_current & ~has_baseline).sum())
        baseline_unpaired = int((~has_current & has_baseline).sum())
    else:
        matched = pd.Series(True, index=df.index)

    fallback_rows = 0
    if calendar_info is not None:
        current_unpaired += int(calendar_info.get("dropped_rows_a", 0))
        baseline_unpaired += int(calendar_info.get("dropped_rows_b", 0))
        fallback_rows = int(calendar_info.get("fallback_rows", 0))

    retained = df.loc[matched].reset_index(drop=True)
    if retained.empty:
        raise AlignmentFailedError(
            message="cumulative comparable-period alignment produced no paired rows",
            expected="at least one mechanically paired current and baseline cutoff",
            received=(
                f"current_unpaired={current_unpaired}, baseline_unpaired={baseline_unpaired}"
            ),
            location="session.compare.alignment",
            repair=AnalysisRepair(
                kind="retry",
                action="Choose windows and alignment with at least one shared cumulative cutoff.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
            context={"kind": "CumulativeComparablePeriodNoPairs"},
        )
    if PRESENCE_STATUS_COLUMN in retained.columns:
        retained[PRESENCE_STATUS_COLUMN] = "matched"
    pairs = CumulativePairSummaryV1(
        schema="cumulative-pair-summary/v1",
        matched_rows=len(retained),
        matched_null_rows=int((retained["current"].isna() | retained["baseline"].isna()).sum()),
        current_unpaired_rows=current_unpaired,
        baseline_unpaired_rows=baseline_unpaired,
        fallback_rows=fallback_rows,
        unpaired_action="dropped",
    )
    return retained, pairs


def _evaluation_frame(frame: MetricFrame, *, label: str) -> pd.DataFrame:
    """Return source rows with a validated, timezone-aware UTC cutoff column."""

    source = frame._dataframe_copy()
    if EVALUATION_END_COLUMN not in source.columns:
        raise AnalysisError(
            message=f"all-history compare input '{label}' is missing evaluation_end",
            expected="every cumulative row to carry timezone-aware evaluation_end",
            received=f"columns={list(source.columns)!r}",
            location=f"session.compare.{label}",
            repair=AnalysisRepair(
                kind="retry",
                action=f"Re-observe the {label} cumulative frame under the current contract.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
            context={
                "kind": "CumulativeEvaluationEndMissing",
                "frame": label,
                "frame_ref": frame.ref,
            },
        )
    normalized: list[pd.Timestamp] = []
    for row_index, value in enumerate(source[EVALUATION_END_COLUMN]):
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise AnalysisError(
                message=f"all-history compare input '{label}' has invalid evaluation_end",
                expected="a timezone-aware timestamp on every row",
                received=f"row={row_index}, value={value!r}",
                location=f"session.compare.{label}",
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"Re-observe the {label} cumulative frame under the current contract.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
                ),
                context={"kind": "CumulativeEvaluationEndInvalid", "frame": label},
            ) from exc
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise AnalysisError(
                message=f"all-history compare input '{label}' has invalid evaluation_end",
                expected="a non-null timezone-aware timestamp on every row",
                received=f"row={row_index}, value={value!r}",
                location=f"session.compare.{label}",
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"Re-observe the {label} cumulative frame under the current contract.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
                ),
                context={"kind": "CumulativeEvaluationEndInvalid", "frame": label},
            )
        normalized.append(timestamp.tz_convert("UTC"))
    source[EVALUATION_END_COLUMN] = pd.Series(
        normalized,
        index=source.index,
        dtype="datetime64[ns, UTC]",
    )
    return source


def _attach_endpoint_side(
    result: pd.DataFrame,
    source_frame: MetricFrame,
    source: pd.DataFrame,
    *,
    result_time_column: str | None,
    endpoint_column: str,
    calendar_aligned: bool,
    report_tz: str,
) -> pd.DataFrame:
    dimensions = _dimension_columns(source_frame)
    source_keys = list(dimensions)
    result_keys = list(dimensions)
    temporary_time_key: str | None = None
    if source_frame.meta.semantic_kind in {"time_series", "panel"}:
        source_time_column = _time_axis_column(source_frame)
        if result_time_column is None or result_time_column not in result.columns:
            raise AlignmentFailedError(
                message="all-history compare alignment lost a paired time coordinate",
                context={
                    "kind": "CumulativeAlignedTimeMissing",
                    "result_time_column": result_time_column,
                },
            )
        source = source.copy()
        output = result.copy()
        temporary_time_key = f"__{endpoint_column}_time_key"
        if calendar_aligned:
            source[temporary_time_key] = _local_dates(
                source[source_time_column], report_tz=report_tz
            ).map(lambda value: value.isoformat())
            output[temporary_time_key] = output[result_time_column].map(
                lambda value: str(value) if pd.notna(value) else None
            )
        else:
            source[temporary_time_key] = source[source_time_column].map(
                lambda value: pd.Timestamp(value).isoformat() if pd.notna(value) else None
            )
            output[temporary_time_key] = output[result_time_column].map(
                lambda value: pd.Timestamp(value).isoformat() if pd.notna(value) else None
            )
        source_keys.append(temporary_time_key)
        result_keys.append(temporary_time_key)
    else:
        output = result
    if not source_keys:
        if len(source) != 1:
            raise AlignmentFailedError(
                message="all-history scalar compare requires exactly one row per frame",
                context={"kind": "ScalarCompareRequiresSingleRow", "rows": len(source)},
            )
        output = result.copy()
        output[endpoint_column] = source[EVALUATION_END_COLUMN].iloc[0]
        return output

    lookup = source[[*source_keys, EVALUATION_END_COLUMN]].rename(
        columns={
            **dict(zip(source_keys, result_keys, strict=True)),
            EVALUATION_END_COLUMN: endpoint_column,
        }
    )
    if lookup.duplicated(result_keys).any():
        raise AlignmentFailedError(
            message="all-history compare requires unique business coordinates",
            context={"kind": "CumulativeEvaluationCoordinateDuplicate", "keys": result_keys},
        )
    merged = pd.merge(output, lookup, on=result_keys, how="left", validate="many_to_one")
    if temporary_time_key is not None:
        merged = merged.drop(columns=[temporary_time_key])
    return merged


def _finalize_all_history_pairs(
    df: pd.DataFrame,
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    report_tz: str,
) -> tuple[pd.DataFrame, AllHistoryPairAlignmentV1]:
    """Attach exact cutoffs and retain only coordinates observed on both sides."""

    current_source = _evaluation_frame(current, label="current")
    baseline_source = _evaluation_frame(baseline, label="baseline")
    current_time_column: str | None = None
    baseline_time_column: str | None = None
    calendar_aligned = alignment.kind != "window_bucket"
    if current.meta.semantic_kind in {"time_series", "panel"}:
        time_column = _time_axis_column(current)
        if calendar_aligned:
            current_time_column = "bucket_start_a"
            baseline_time_column = "bucket_start_b"
        else:
            current_time_column = time_column
            baseline_candidate = f"{time_column}_b"
            baseline_time_column = (
                baseline_candidate if baseline_candidate in df.columns else time_column
            )
    paired = _attach_endpoint_side(
        df,
        current,
        current_source,
        result_time_column=current_time_column,
        endpoint_column=CURRENT_EVALUATION_END_COLUMN,
        calendar_aligned=calendar_aligned,
        report_tz=report_tz,
    )
    paired = _attach_endpoint_side(
        paired,
        baseline,
        baseline_source,
        result_time_column=baseline_time_column,
        endpoint_column=BASELINE_EVALUATION_END_COLUMN,
        calendar_aligned=calendar_aligned,
        report_tz=report_tz,
    )
    has_current = paired[CURRENT_EVALUATION_END_COLUMN].notna()
    has_baseline = paired[BASELINE_EVALUATION_END_COLUMN].notna()
    matched = has_current & has_baseline
    retained = paired.loc[matched].reset_index(drop=True)
    if retained.empty:
        raise AlignmentFailedError(
            message="all-history alignment produced no paired cumulative levels",
            expected="at least one business coordinate present in both frames",
            received=(
                f"current_only={int((has_current & ~has_baseline).sum())}, "
                f"baseline_only={int((~has_current & has_baseline).sum())}"
            ),
            location="session.compare.alignment",
            repair=AnalysisRepair(
                kind="retry",
                action="Choose windows and dimensions with at least one shared observed coordinate.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
            context={"kind": "AllHistoryNoPairedLevels"},
        )
    if PRESENCE_STATUS_COLUMN in retained.columns:
        retained[PRESENCE_STATUS_COLUMN] = "matched"
    pair_info = AllHistoryPairAlignmentV1(
        matched_rows=len(retained),
        matched_null_rows=int((retained["current"].isna() | retained["baseline"].isna()).sum()),
        current_unpaired_rows=int((has_current & ~has_baseline).sum()),
        baseline_unpaired_rows=int((~has_current & has_baseline).sum()),
    )
    return retained, pair_info


def compare(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    alignment: AlignmentPolicy | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> DeltaFrame:
    if session is None:
        session = require_current_session()
    # compare does not require a backend factory; it computes deltas from existing frames
    if alignment is None:
        alignment = AlignmentPolicy(kind="window_bucket")
    if not isinstance(alignment, AlignmentPolicy):
        raise SemanticKindMismatchError(
            message="compare requires alignment=AlignmentPolicy(...)",
            context={
                "expected_kind": "AlignmentPolicy",
                "got_kind": type(alignment).__name__,
            },
        )
    current = _require_metric_frame("current", current)
    baseline = _require_metric_frame("baseline", baseline)
    require_single_metric(current, intent="compare")
    require_single_metric(baseline, intent="compare")
    # compare operates on arity-1 metric frames; multi-metric frames are gated
    # out upstream. Narrow metric_id for downstream DeltaFrameMeta / Subject.
    assert current.meta.metric_id is not None
    assert baseline.meta.metric_id is not None
    for label, source_frame in (("current", current), ("baseline", baseline)):
        if source_frame.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"compare argument '{label}' belongs to session "
                    f"{source_frame.meta.session_id!r}, not {session.id!r}"
                ),
            )
    raise_first(
        validate_compare(
            current,
            baseline,
            alignment=alignment,
            report_tz=session.report_tz_name,
        )
    )

    # --- Component-aware validation ---
    current_decomp_kind = _component_composition_kind(current)
    baseline_decomp_kind = _component_composition_kind(baseline)
    current_component: ComponentFrame | None = None
    baseline_component: ComponentFrame | None = None
    if current_decomp_kind is not None or baseline_decomp_kind is not None:
        # At least one side declares decomposition; both must have component_ref
        current_component = _load_component_for_compare(current, session, "current")
        baseline_component = _load_component_for_compare(baseline, session, "baseline")
        _require_compatible_components(current_component, baseline_component, current, baseline)

    started_at = datetime.now(UTC)
    started = monotonic()
    calendar_info: dict[str, Any] | None = None
    segment_info: dict[str, Any] | None = None
    window_info: dict[str, Any] | None = None
    cumulative_pairs: AllHistoryPairAlignmentV1 | None = None
    cumulative_pair_summary: CumulativePairSummaryV1 | None = None
    cur_cumulative_anchor = cumulative_compare_anchor(current.meta.cumulative)
    comparable_period = isinstance(cur_cumulative_anchor, tuple) and cur_cumulative_anchor[0] in {
        "trailing",
        "grain_to_date",
    }
    if current.meta.semantic_kind == "segmented":
        df, segment_info = _align_segmented(current, baseline)
    elif current.meta.semantic_kind == "panel":
        df, segment_info, calendar_info, window_info = _align_panel(
            current, baseline, alignment=alignment, session=session
        )
    elif alignment.kind == "window_bucket":
        if current.meta.semantic_kind == "time_series":
            _require_matching_time_series_bucket_grain(current, baseline)
            df, window_info = _align_time_series_window_bucket(
                current,
                baseline,
                alignment=alignment,
                track_presence_status=comparable_period,
            )
        else:
            current_df = current._dataframe_copy()
            baseline_df = baseline._dataframe_copy()
            if current.meta.semantic_kind == "scalar" and (
                len(current_df) != 1 or len(baseline_df) != 1
            ):
                raise AlignmentFailedError(
                    message="scalar compare requires exactly one row per frame",
                    context={
                        "kind": "ScalarCompareRequiresSingleRow",
                        "current_rows": len(current_df),
                        "baseline_rows": len(baseline_df),
                    },
                )
            if current.meta.semantic_kind == "scalar":
                current_value = _value_column(current, current_df, time_column="")
                baseline_value = _value_column(baseline, baseline_df, time_column="")
                current_df = current_df[[current_value]]
                baseline_df = baseline_df[[baseline_value]]
            df = _align_and_compute(current_df, baseline_df)
    else:
        calendar_ref = alignment.calendar
        if not isinstance(calendar_ref, CalendarRef):
            raise CalendarPolicyError(
                message="calendar-backed alignment requires CalendarRef",
                context={
                    "kind": "CalendarRefMissing",
                    "alignment": alignment.model_dump(mode="json"),
                },
            )
        loaded_calendar = session._calendars.get(calendar_ref.ref)
        report_tz = session.report_tz_name
        policy = CalendarPolicy(
            mode=alignment.kind,
            align_period=alignment.period,
            fallback=alignment.fallback,
        )
        current_df = current._dataframe_copy()
        baseline_df = baseline._dataframe_copy()
        time_column = _time_axis_column(current)
        baseline_time_column = _time_axis_column(baseline)
        if baseline_time_column != time_column:
            raise AlignmentFailedError(
                message="calendar-backed compare alignment requires matching time axis columns",
                context={
                    "kind": "CalendarAlignTimeAxisMismatch",
                    "source_time_column": time_column,
                    "baseline_time_column": baseline_time_column,
                },
            )
        value_column = _value_column(current, current_df, time_column=time_column)
        _require_calendar_columns(
            current_df, frame_label="current", columns=(time_column, value_column)
        )
        _require_calendar_columns(
            baseline_df, frame_label="baseline", columns=(time_column, value_column)
        )
        df, info = align_calendar_frames(
            current_df,
            baseline_df,
            time_column=time_column,
            value_column=value_column,
            calendar=loaded_calendar,
            policy=policy,
            report_tz=report_tz,
        )
        calendar_info = info.model_dump(mode="json")
    df, cumulative_pair_summary = _finalize_comparable_period_pairs(
        df,
        current,
        alignment=alignment,
        calendar_info=calendar_info,
    )
    if cur_cumulative_anchor == "all_history":
        df, cumulative_pairs = _finalize_all_history_pairs(
            df,
            current,
            baseline,
            alignment=alignment,
            report_tz=session.report_tz_name,
        )
    if df.empty:
        raise AlignmentFailedError(message=f"alignment '{alignment.kind}' produced no rows")
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    alignment_dump = alignment.model_dump(mode="json")
    if alignment.kind == "window_bucket" and "bucket_start_b" in df.columns:
        alignment_dump["baseline_bucket_column"] = "bucket_start_b"
    if calendar_info is not None:
        alignment_dump["calendar_info"] = calendar_info
    if window_info is not None:
        alignment_dump["coverage"] = window_info
    if segment_info is not None:
        alignment_dump["segment_info"] = segment_info
    if cumulative_pairs is not None:
        alignment_dump["cumulative_pairs"] = cumulative_pairs.model_dump(mode="json")
    if current.meta.semantic_kind in {"segmented", "panel", "time_series"}:
        alignment_dump["axes"] = current.meta.axes
    additivity, aggregation, status_time_dimension = _compatible_metric_semantics(
        current.meta,
        baseline.meta,
    )
    if current.meta.attribution_basis != baseline.meta.attribution_basis:
        raise AttributionBasisMismatchError(
            message="compare inputs carry incompatible attribution reproduction bases",
            expected=repr(basis_fingerprint(current.meta.attribution_basis)),
            received=repr(basis_fingerprint(baseline.meta.attribution_basis)),
            location="session.compare attribution basis",
            context={"current_ref": current.ref, "baseline_ref": baseline.ref},
        )
    attribution_basis = current.meta.attribution_basis
    cur_cumulative = current.meta.cumulative
    cur_cumulative_anchor = cumulative_compare_anchor(cur_cumulative)
    baseline_cumulative_anchor = cumulative_compare_anchor(baseline.meta.cumulative)
    cumulative_change = (
        AllHistoryLevelChangeV1() if cur_cumulative_anchor == "all_history" else None
    )
    cumulative_alignment: CumulativeAlignmentV1 | None = None
    if cumulative_pair_summary is not None:
        if not isinstance(cur_cumulative_anchor, tuple) or not isinstance(
            baseline_cumulative_anchor, tuple
        ):
            raise AnalysisError(
                message="cumulative pair evidence requires two comparable-period anchors",
                context={
                    "kind": "CumulativePairAnchorMissing",
                    "current_anchor": cur_cumulative_anchor,
                    "baseline_anchor": baseline_cumulative_anchor,
                },
            )
        cumulative_alignment = cumulative_alignment_evidence(
            current_anchor=cur_cumulative_anchor,
            baseline_anchor=baseline_cumulative_anchor,
            pairs=cumulative_pair_summary,
        )
    cumulative_attribution: CumulativeAttributionContractV1 | None = None
    if cur_cumulative is not None:
        baseline_cumulative = baseline.meta.cumulative
        current_graph = current.meta.expression_graph
        baseline_graph = baseline.meta.expression_graph
        if baseline_cumulative is None or current_graph is None or baseline_graph is None:
            raise AnalysisError(
                message="cumulative attribution requires two current expression contracts",
                context={
                    "kind": "CumulativeAttributionSourceContractMissing",
                    "current_ref": current.ref,
                    "baseline_ref": baseline.ref,
                },
            )
        try:
            current_over = cumulative_over_ref(cur_cumulative)
            baseline_over = cumulative_over_ref(baseline_cumulative)
            if current_over != baseline_over:
                raise ValueError("cumulative attribution sources have different over refs")
            bridge = derive_cumulative_bridge(
                current_semantic_kind=current.meta.semantic_kind,
                baseline_semantic_kind=baseline.meta.semantic_kind,
                current_axis_bindings=current.meta.axis_bindings,
                baseline_axis_bindings=baseline.meta.axis_bindings,
                over_ref=current_over,
                current_declared_over_grain=_declared_over_granularity(session, current_over),
                baseline_declared_over_grain=_declared_over_granularity(session, baseline_over),
                current_report_timezone=(_observe_report_tz(current) or session.report_tz_name),
                baseline_report_timezone=(_observe_report_tz(baseline) or session.report_tz_name),
            )
            cumulative_attribution = build_cumulative_attribution_contract(
                current_graph=current_graph,
                baseline_graph=baseline_graph,
                current_cumulative=cur_cumulative,
                baseline_cumulative=baseline_cumulative,
                bridge=bridge,
            )
        except ValueError as exc:
            raise AnalysisError(
                message="cumulative attribution source contracts are incompatible",
                context={
                    "kind": "CumulativeAttributionContractMismatch",
                    "current_ref": current.ref,
                    "baseline_ref": baseline.ref,
                    "reason": str(exc),
                },
            ) from exc
    params: dict[str, Any] = {
        "source_current_ref": current.ref,
        "source_baseline_ref": baseline.ref,
        "alignment": alignment_dump,
        "additivity": additivity,
        "aggregation": aggregation,
        "status_time_dimension_ref": (
            RefPayloadV1.from_ref(ref_factory.time_dimension(status_time_dimension)).to_dict()
            if status_time_dimension is not None
            else None
        ),
        "attribution_basis": (
            attribution_basis.model_dump(mode="json") if attribution_basis is not None else None
        ),
        "cumulative_change": (
            cumulative_change.model_dump(mode="json") if cumulative_change is not None else None
        ),
        "cumulative_alignment": (
            cumulative_alignment.model_dump(mode="json", by_alias=True)
            if cumulative_alignment is not None
            else None
        ),
        "cumulative_attribution": (
            cumulative_attribution.model_dump(mode="json", by_alias=True)
            if cumulative_attribution is not None
            else None
        ),
    }
    assert current.meta.metric_id is not None
    assert baseline.meta.metric_id is not None
    current_identity = current.meta.metric_identity or CatalogMetricIdentity(
        kind="catalog", metric_ref=RefPayloadV1.from_ref(ref_factory.metric(current.meta.metric_id))
    )
    baseline_identity = baseline.meta.metric_identity or CatalogMetricIdentity(
        kind="catalog",
        metric_ref=RefPayloadV1.from_ref(ref_factory.metric(baseline.meta.metric_id)),
    )
    current_comparable = current.meta.comparable_value_semantics
    baseline_comparable = baseline.meta.comparable_value_semantics
    assert current_comparable is not None
    assert baseline_comparable is not None
    comparison_semantics: DeltaComparisonSemantics
    if cumulative_alignment is not None:
        current_graph = current.meta.expression_graph
        baseline_graph = baseline.meta.expression_graph
        if current_graph is None or baseline_graph is None:
            raise AnalysisError(
                message="cumulative comparison identity requires persisted expression graphs",
                context={"kind": "CumulativeExpressionGraphMissing"},
            )
        assert isinstance(cur_cumulative_anchor, tuple)
        assert isinstance(baseline_cumulative_anchor, tuple)
        comparison_semantics = cumulative_equivalent_comparison_semantics(
            current_graph=current_graph,
            baseline_graph=baseline_graph,
            current_comparable=current_comparable,
            baseline_comparable=baseline_comparable,
            current_anchor=cur_cumulative_anchor,
            baseline_anchor=baseline_cumulative_anchor,
        )
    else:
        assert current_comparable.fingerprint == baseline_comparable.fingerprint
        comparison_semantics = ExactComparisonSemanticsV1(
            schema="exact-comparison-semantics/v1",
            comparable_semantics_fingerprint=current_comparable.fingerprint,
        )
    comparison_identity = DeltaComparisonIdentity(
        schema="delta-comparison/v2",
        current=current_identity,
        baseline=baseline_identity,
        current_artifact_id=current.meta.artifact_id or current.ref,
        baseline_artifact_id=baseline.meta.artifact_id or baseline.ref,
        semantics=comparison_semantics,
        alignment_policy_fingerprint=fingerprint(alignment.model_dump(mode="json")),
        attribution_basis_fingerprint=basis_fingerprint(attribution_basis),
    )
    params["comparison_identity"] = canonical_value(comparison_identity)
    compare_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=current.meta.catalog_definition_fingerprint,
        metric_identities=(current_identity,),
        comparison_identity=comparison_identity,
        axis_refs=tuple(binding.ref for binding in current.meta.axis_bindings),
        slice_predicates=current.meta.slice_predicates,
    )

    # Check cache before constructing the frame and committing.
    prospective_id = compute_prospective_artifact_id(
        step_type="compare",
        inputs=CommitInputs(input_refs=[current.ref, baseline.ref]),
        params=CommitParams(values=params),
        semantic_anchors=compare_anchors,
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        reused_delta = cast("DeltaFrame", load_frame(prospective_id, session=session))
        # The numeric artifact identity dedups, but every invocation must keep
        # an independent, recoverable job record carrying its own
        # analysis_purpose (issue #38).  The frame meta is not rewritten, so
        # the artifact keeps its original producer/purpose.
        persist_reused_artifact_job(
            session,
            intent="compare",
            analysis_purpose=analysis_purpose,
            params=params,
            input_frame_refs=[current.ref, baseline.ref],
            output_frame_ref=reused_delta.meta.artifact_id or reused_delta.ref,
            semantics=job_semantics_from_frames(reused_delta),
            started_at=started_at,
            started_monotonic=started,
            semantic_project_root=str(session.catalog._project.semantic_root),
        )
        return reused_delta

    delta_component: ComponentFrame | None = None
    if current_component is not None and baseline_component is not None:
        comp_df = _align_component_frames(
            current_component,
            baseline_component,
            current,
            baseline,
            alignment=alignment,
            session=session,
        )
        if cumulative_pair_summary is not None or cur_cumulative_anchor == "all_history":
            comp_df = _restrict_component_to_parent_pairs(comp_df, df)
        delta_component = _build_delta_component_frame(
            session,
            comp_df,
            parent_ref=prospective_id,
            source_component=current_component,
            job_ref=job_ref,
            candidate_origins=merge_candidate_origins(
                current.meta.candidate_origins,
                baseline.meta.candidate_origins,
            ),
        )

    digest = f"sha256:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()}"
    assert current.meta.catalog_definition_fingerprint is not None
    meta = DeltaFrameMeta(
        kind="delta_frame",
        catalog_definition_fingerprint=current.meta.catalog_definition_fingerprint,
        source_dependency_digests=tuple(
            digest
            for digest in (
                current.meta.semantic_dependency_digest,
                baseline.meta.semantic_dependency_digest,
            )
            if digest is not None
        ),
        axis_bindings=current.meta.axis_bindings,
        slice_predicates=current.meta.slice_predicates,
        status_time_dimension_ref=(
            RefPayloadV1.from_ref(ref_factory.time_dimension(status_time_dimension))
            if status_time_dimension is not None
            else None
        ),
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage.compose(
            current.lineage,
            baseline.lineage,
            new_step=LineageStep(
                intent="compare",
                job_ref=job_ref,
                inputs=[current.ref, baseline.ref],
                params_digest=digest,
                analysis_purpose=analysis_purpose,
            ),
        ),
        candidate_origins=merge_candidate_origins(
            current.meta.candidate_origins,
            baseline.meta.candidate_origins,
        ),
        metric_identity=current_identity,
        baseline_metric_identity=baseline_identity,
        comparison_identity=comparison_identity,
        source_current_ref=current.ref,
        source_baseline_ref=baseline.ref,
        alignment=alignment_dump,
        semantic_kind=current.meta.semantic_kind,
        unit=current.meta.unit,
        composition=current.meta.composition if current_component is not None else None,
        fold=getattr(current.meta, "fold", None),
        component_folds=_component_fold_payload(current, session=session),
        additivity=additivity,
        aggregation=aggregation,
        cumulative=cur_cumulative,
        cumulative_change=cumulative_change,
        cumulative_alignment=cumulative_alignment,
        component_ref=delta_component.ref if delta_component is not None else None,
        attribution_basis=attribution_basis,
    )
    if cumulative_attribution is not None:
        meta = CumulativeDeltaFrameMetaV1(
            **{field_name: getattr(meta, field_name) for field_name in DeltaFrameMeta.model_fields},
            cumulative_attribution=cumulative_attribution,
        )
    output_frame = DeltaFrame(_df=df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    subject = Subject(
        grain=_grain_from_axes(current),
        analysis_axis="change",
    )
    comparison_window_dict = _scope_for_window(current)
    evidence_store = session._evidence_store()
    try:
        commit_result(
            store=evidence_store,
            frames_dir=session._layout.frames_dir,
            frame=output_frame,
            step_type="compare",
            inputs=CommitInputs(input_refs=[current.ref, baseline.ref]),
            params=CommitParams(values=params),
            semantic_anchors=compare_anchors,
            subject=subject,
            extractor_family="delta_frame",
            comparison_window=comparison_window_dict,
            comparison_basis="left_vs_right",
        )
        if delta_component is not None:
            delta_component.meta = cast(
                "ComponentFrameMeta",
                persist_frame(session, delta_component),
            )
        register_frame_artifact(session, output_frame)
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "compare",
                **job_semantics_from_frames(output_frame),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [current.ref, baseline.ref],
                "output_frame_ref": output_frame.meta.artifact_id or output_frame.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "reused_artifact": False,
                "error": None,
                "semantic_project_root": str(session.catalog._project.semantic_root),
            },
        )
    except BaseException:
        _rollback_compare_commit(
            session=session,
            evidence_store=evidence_store,
            root_artifact_id=prospective_id,
            component_artifact_id=(delta_component.ref if delta_component is not None else None),
            job_ref=job_ref,
        )
        raise
    return output_frame


def _dimension_columns(frame: MetricFrame) -> list[str]:
    columns: list[str] = []
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "dimension":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return sorted(columns)


def _observe_params(frame: MetricFrame) -> dict[str, Any]:
    """Return the typed observe params that own the frame's requested axes."""

    for step in reversed(frame.meta.lineage.steps):
        if step.intent == "observe" and isinstance(step.params, dict):
            return step.params
    return {}


def _requested_dimension_ids(frame: MetricFrame) -> tuple[str, ...]:
    """Return exact requested dimension identities for final alignment."""

    dimensions = _observe_params(frame).get("dimensions")
    if isinstance(dimensions, list):
        semantic_ids = []
        for item in dimensions:
            if not isinstance(item, dict):
                continue
            semantic_id = item.get("semantic_id")
            if isinstance(semantic_id, str) and semantic_id:
                semantic_ids.append(semantic_id)
        if len(semantic_ids) == len(dimensions):
            return tuple(sorted(semantic_ids))
    return tuple(_dimension_columns(frame))


def _time_axis_identity(frame: MetricFrame) -> str | None:
    """Return the exact explicit time dimension, with axis metadata fallback."""

    window = frame.meta.window
    if isinstance(window, dict):
        time_dimension = window.get("time_dimension")
        if isinstance(time_dimension, str) and time_dimension:
            return time_dimension
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict) or axis.get("role") != "time":
            continue
        time_dimension = axis.get("time_dimension")
        if isinstance(time_dimension, str) and time_dimension:
            return time_dimension
    return None


def _observe_report_tz(frame: MetricFrame) -> str | None:
    timescope = _observe_params(frame).get("timescope")
    if isinstance(timescope, dict):
        report_tz = timescope.get("report_tz")
        if isinstance(report_tz, str) and report_tz:
            return report_tz
    return None


def _declared_over_granularity(session: Session, over_ref: RefPayloadV1) -> str | None:
    """Read the current time-dimension granularity used for scalar bridges."""

    member = session.catalog.require(ref_factory.time_dimension(over_ref.path))
    granularity = getattr(member.details(), "granularity", None)
    return granularity if isinstance(granularity, str) and granularity else None


def _time_axis_column(frame: MetricFrame) -> str:
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "time":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            return column
    raise AlignmentFailedError(
        message="time axis column is required for calendar-backed alignment",
        context={"kind": "NoTimeAxis"},
    )


def _time_column_for_frame(frame: MetricFrame) -> str:
    return _time_axis_column(frame)


def _require_matching_time_series_bucket_grain(a: MetricFrame, b: MetricFrame) -> None:
    a_time_column = _time_axis_column(a)
    b_time_column = _time_axis_column(b)
    if a_time_column != b_time_column:
        raise AlignmentFailedError(
            message="window_bucket time_series alignment requires matching time axis columns",
            context={
                "kind": "WindowBucketTimeAxisMismatch",
                "current_time_column": a_time_column,
                "baseline_time_column": b_time_column,
            },
        )
    a_grain, b_grain = _panel_grains(a, b)
    if a_grain != b_grain:
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires same-grain time_series windows",
            context={
                "kind": "WindowBucketGrainMismatch",
                "current_grain": a_grain,
                "baseline_grain": b_grain,
            },
        )


def _compute_delta_columns(df: pd.DataFrame) -> pd.DataFrame:
    df["current"] = pd.to_numeric(df["current"], errors="coerce")
    df["baseline"] = pd.to_numeric(df["baseline"], errors="coerce")
    current_for_delta = df["current"]
    baseline_for_delta = df["baseline"]
    if PRESENCE_STATUS_COLUMN in df.columns:
        current_for_delta = current_for_delta.mask(
            df[PRESENCE_STATUS_COLUMN] == "churned",
            0.0,
        )
        baseline_for_delta = baseline_for_delta.mask(
            df[PRESENCE_STATUS_COLUMN] == "new",
            0.0,
        )
        df["current"] = df["current"].mask(df[PRESENCE_STATUS_COLUMN] == "churned", 0.0)
        df["baseline"] = df["baseline"].mask(df[PRESENCE_STATUS_COLUMN] == "new", 0.0)
    df["current"] = current_for_delta
    df["baseline"] = baseline_for_delta
    return compute_delta_columns(df)


def _align_calendar_window_bucket(
    a_values: dict[str, tuple[object, object]],
    b_values: dict[str, tuple[object, object]],
    *,
    time_column: str,
    track_presence_status: bool,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for key in sorted(set(a_values) | set(b_values)):
        has_current = key in a_values
        has_baseline = key in b_values
        a_time, current_value = a_values.get(key, (None, np.nan))
        b_time, baseline_value = b_values.get(key, (None, np.nan))
        row = {
            time_column: a_time if a_time is not None else b_time,
            "current": current_value,
            "baseline": baseline_value,
        }
        if track_presence_status:
            row[PRESENCE_STATUS_COLUMN] = _presence_status(
                has_current=has_current,
                has_baseline=has_baseline,
            )
        rows.append(row)
    result = _compute_delta_columns(pd.DataFrame(rows))
    result_columns = [
        time_column,
        "current",
        "baseline",
        "delta",
        "pct_change",
        PCT_CHANGE_STATUS_COLUMN,
    ]
    if track_presence_status:
        result_columns.insert(1, PRESENCE_STATUS_COLUMN)
    return result[result_columns]


def _align_ordinal_window_bucket(
    a_values: dict[str, tuple[object, object]],
    b_values: dict[str, tuple[object, object]],
    *,
    time_column: str,
    grain: str,
    current_frame: MetricFrame,
    baseline_frame: MetricFrame,
    track_presence_status: bool,
    strict_lengths: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    current_buckets = _window_bucket_values(current_frame)
    baseline_buckets = _window_bucket_values(baseline_frame)
    if strict_lengths and len(current_buckets) != len(baseline_buckets):
        raise AlignmentFailedError(
            message=(
                "window_bucket ordinal alignment requires equal expected bucket counts; "
                f"current window has {len(current_buckets)} buckets, baseline window has "
                f"{len(baseline_buckets)} buckets"
            ),
            context={
                "kind": "WindowBucketExpectedCountMismatch",
                "current_expected_buckets": len(current_buckets),
                "baseline_expected_buckets": len(baseline_buckets),
            },
        )

    rows: list[dict[str, object]] = []
    current_present = 0
    baseline_present = 0
    paired_buckets = min(len(current_buckets), len(baseline_buckets))
    for pair in _walk_ordinal_pairs(
        a_values, b_values, grain=grain, frame_a=current_frame, frame_b=baseline_frame
    ):
        current_value = pair.a_value if pair.a_present else np.nan
        baseline_value = pair.b_value if pair.b_present else np.nan
        if _not_nan(current_value):
            current_present += 1
        if _not_nan(baseline_value):
            baseline_present += 1
        row: dict[str, object] = {
            time_column: pair.a_bucket,
            f"{time_column}_b": pair.b_bucket,
            "current": current_value,
            "baseline": baseline_value,
        }
        if track_presence_status:
            row[PRESENCE_STATUS_COLUMN] = _presence_status(
                has_current=pair.a_present,
                has_baseline=pair.b_present,
            )
        rows.append(row)
    result = _compute_delta_columns(pd.DataFrame(rows))
    coverage = {
        "current": {
            "expected_buckets": len(current_buckets),
            "present_buckets": current_present,
            "missing_buckets": len(current_buckets) - current_present,
        },
        "baseline": {
            "expected_buckets": len(baseline_buckets),
            "present_buckets": baseline_present,
            "missing_buckets": len(baseline_buckets) - baseline_present,
        },
        "paired_buckets": paired_buckets,
        "current_unpaired_buckets": max(len(current_buckets) - len(baseline_buckets), 0),
        "baseline_unpaired_buckets": max(len(baseline_buckets) - len(current_buckets), 0),
    }
    result_columns = [
        time_column,
        f"{time_column}_b",
        "current",
        "baseline",
        "delta",
        "pct_change",
        PCT_CHANGE_STATUS_COLUMN,
    ]
    if track_presence_status:
        result_columns.insert(2, PRESENCE_STATUS_COLUMN)
    return result[result_columns], coverage


def _align_prepared_window_bucket(
    a_prepared: pd.DataFrame,
    b_prepared: pd.DataFrame,
    *,
    time_column: str,
    a_value_column: str,
    b_value_column: str,
    current_frame: MetricFrame,
    baseline_frame: MetricFrame,
    alignment: AlignmentPolicy,
    track_presence_status: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    grain = _panel_grain(current_frame)
    if grain != _panel_grain(baseline_frame) or not isinstance(grain, str):
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires same-grain windows",
            context={
                "kind": "WindowBucketGrainMismatch",
                "current_grain": grain,
                "baseline_grain": _panel_grain(baseline_frame),
            },
        )
    a_values = _prepared_value_map(
        a_prepared,
        time_column=time_column,
        value_column=a_value_column,
        grain=grain,
    )
    b_values = _prepared_value_map(
        b_prepared,
        time_column=time_column,
        value_column=b_value_column,
        grain=grain,
    )
    if alignment.mode == "calendar_bucket":
        return (
            _align_calendar_window_bucket(
                a_values,
                b_values,
                time_column=time_column,
                track_presence_status=track_presence_status,
            ),
            None,
        )
    return _align_ordinal_window_bucket(
        a_values,
        b_values,
        time_column=time_column,
        grain=grain,
        current_frame=current_frame,
        baseline_frame=baseline_frame,
        track_presence_status=track_presence_status,
        strict_lengths=alignment.strict_lengths,
    )


def _aggregate_window_info(infos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not infos:
        return None
    result: dict[str, Any] = {}
    for side in ("current", "baseline"):
        result[side] = {
            field: sum(int(info.get(side, {}).get(field, 0)) for info in infos)
            for field in ("expected_buckets", "present_buckets", "missing_buckets")
        }
    for field in ("paired_buckets", "current_unpaired_buckets", "baseline_unpaired_buckets"):
        result[field] = sum(int(info.get(field, 0)) for info in infos)
    return result


def _value_column(frame: MetricFrame, df: pd.DataFrame, *, time_column: str) -> str:
    # Canonical "value" column (current frames) takes priority.
    if "value" in df.columns and time_column != "value":
        return "value"
    non_time_columns = [
        str(column)
        for column in df.columns
        if str(column) not in {time_column, EVALUATION_END_COLUMN}
    ]
    measure_name = frame.meta.measure.get("name")
    if (
        isinstance(measure_name, str)
        and measure_name
        and measure_name != time_column
        and measure_name in df.columns
    ):
        return measure_name
    if len(non_time_columns) == 1:
        return non_time_columns[0]
    if not non_time_columns:
        raise AlignmentFailedError(
            message="calendar-backed compare alignment requires at least one value column",
            context={"kind": "CalendarAlignValueColumnMissing", "time_column": time_column},
        )
    raise AlignmentFailedError(
        message="calendar-backed compare alignment requires exactly one value column",
        context={
            "kind": "CalendarAlignValueColumnAmbiguous",
            "time_column": time_column,
            "value_candidates": non_time_columns,
            "measure_name": measure_name if isinstance(measure_name, str) else None,
        },
    )


def _value_column_segmented(frame: MetricFrame, df: pd.DataFrame, *, dim_columns: list[str]) -> str:
    missing_dimensions = [column for column in dim_columns if column not in df.columns]
    if missing_dimensions:
        raise AlignmentFailedError(
            message="segmented compare alignment frame is missing dimension columns",
            context={
                "kind": "SegmentDimensionColumnMissing",
                "missing_columns": missing_dimensions,
                "available_columns": [str(column) for column in df.columns],
            },
        )
    # Canonical "value" column (current frames) takes priority.
    if "value" in df.columns and "value" not in dim_columns:
        return "value"
    non_dimension_columns = [
        str(column)
        for column in df.columns
        if str(column) not in {*dim_columns, EVALUATION_END_COLUMN}
    ]
    measure_name = frame.meta.measure.get("name")
    if (
        isinstance(measure_name, str)
        and measure_name
        and measure_name not in dim_columns
        and measure_name in df.columns
    ):
        return measure_name
    if len(non_dimension_columns) == 1:
        return non_dimension_columns[0]
    if not non_dimension_columns:
        raise AlignmentFailedError(
            message="segmented compare alignment requires at least one value column",
            context={"kind": "SegmentValueColumnMissing", "dimension_columns": dim_columns},
        )
    raise AlignmentFailedError(
        message="segmented compare alignment requires exactly one value column",
        context={
            "kind": "SegmentValueColumnAmbiguous",
            "dimension_columns": dim_columns,
            "value_candidates": non_dimension_columns,
            "measure_name": measure_name if isinstance(measure_name, str) else None,
        },
    )


def _align_segmented(a: MetricFrame, b: MetricFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    dim_columns = _dimension_columns(a)
    b_dim_columns = _dimension_columns(b)
    if dim_columns != b_dim_columns:
        raise SegmentDimensionMismatchError(
            message="compare requires matching segment dimension columns",
            context={
                "kind": "SegmentDimensionMismatch",
                "current_dimensions": dim_columns,
                "baseline_dimensions": b_dim_columns,
            },
        )
    if not dim_columns:
        raise AlignmentFailedError(
            message="segmented compare requires at least one dimension axis",
            context={"kind": "SegmentDimensionMissing"},
        )
    _validate_compare_protocol_columns(dim_columns)
    a_df = a._dataframe_copy()
    b_df = b._dataframe_copy()
    a_value = _value_column_segmented(a, a_df, dim_columns=dim_columns)
    b_value = _value_column_segmented(b, b_df, dim_columns=dim_columns)
    a_prepared = a_df[[*dim_columns, a_value]].rename(columns={a_value: "current"})
    b_prepared = b_df[[*dim_columns, b_value]].rename(columns={b_value: "baseline"})
    merged = pd.merge(
        a_prepared,
        b_prepared,
        how="outer",
        on=dim_columns,
        indicator="_segment_presence",
    )
    merged = merged.sort_values(dim_columns).reset_index(drop=True)
    merged[PRESENCE_STATUS_COLUMN] = merged["_segment_presence"].map(
        {"both": "matched", "left_only": "new", "right_only": "churned"}
    )
    merged = _compute_delta_columns(merged)
    result_columns = [
        *dim_columns,
        PRESENCE_STATUS_COLUMN,
        "current",
        "baseline",
        "delta",
        "pct_change",
        PCT_CHANGE_STATUS_COLUMN,
    ]
    result = merged[result_columns]
    segment_info = {
        "segment_count": len(result),
        "a_only_segments_count": int((merged["_segment_presence"] == "left_only").sum()),
        "b_only_segments_count": int((merged["_segment_presence"] == "right_only").sum()),
    }
    return result, segment_info


def _align_panel(
    a: MetricFrame,
    b: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    dim_columns = _dimension_columns(a)
    if not dim_columns:
        raise AlignmentFailedError(
            message="panel compare requires at least one dimension axis",
            context={"kind": "PanelDimensionMissing"},
        )
    time_column = _time_column_for_frame(a)
    b_time_column = _time_column_for_frame(b)
    if b_time_column != time_column:
        raise AlignmentFailedError(
            message="panel compare requires matching time axis columns",
            context={
                "kind": "PanelTimeAxisMismatch",
                "source_time_column": time_column,
                "baseline_time_column": b_time_column,
            },
        )
    _validate_compare_protocol_columns(
        [*dim_columns, time_column],
        time_column=time_column,
        role="dimension",
    )

    a_df = a._dataframe_copy()
    b_df = b._dataframe_copy()
    a_value = _value_column_segmented(a, a_df, dim_columns=[*dim_columns, time_column])
    b_value = _value_column_segmented(b, b_df, dim_columns=[*dim_columns, time_column])
    _require_calendar_columns(
        a_df, frame_label="current", columns=(*dim_columns, time_column, a_value)
    )
    _require_calendar_columns(
        b_df, frame_label="baseline", columns=(*dim_columns, time_column, b_value)
    )

    a_groups = _panel_groups(a_df, dim_columns=dim_columns)
    b_groups = _panel_groups(b_df, dim_columns=dim_columns)
    segment_keys = sorted(
        set(a_groups) | set(b_groups),
        key=lambda key: tuple("" if item is None else str(item) for item in key),
    )
    pieces: list[pd.DataFrame] = []
    calendar_infos: list[dict[str, Any]] = []
    window_infos: list[dict[str, Any]] = []
    calendar_context = (
        _calendar_context(alignment, session=session) if alignment.kind != "window_bucket" else None
    )

    for key in segment_keys:
        a_part = a_groups.get(key)
        b_part = b_groups.get(key)
        if a_part is None and b_part is None:
            continue
        if a_part is None:
            assert b_part is not None
            if alignment.kind == "window_bucket":
                delta, window_info_piece = _align_panel_window_bucket(
                    pd.DataFrame(columns=[time_column, a_value]),
                    b_part,
                    time_column=time_column,
                    a_value_column=a_value,
                    b_value_column=b_value,
                    current_frame=a,
                    baseline_frame=b,
                    alignment=alignment,
                )
                if window_info_piece is not None:
                    window_infos.append(window_info_piece)
            else:
                assert calendar_context is not None
                delta = _one_sided_panel_calendar_delta(
                    b_part,
                    time_column=time_column,
                    value_column=b_value,
                    side="baseline",
                    report_tz=calendar_context[2],
                )
        elif b_part is None:
            if alignment.kind == "window_bucket":
                delta, window_info_piece = _align_panel_window_bucket(
                    a_part,
                    pd.DataFrame(columns=[time_column, b_value]),
                    time_column=time_column,
                    a_value_column=a_value,
                    b_value_column=b_value,
                    current_frame=a,
                    baseline_frame=b,
                    alignment=alignment,
                )
                if window_info_piece is not None:
                    window_infos.append(window_info_piece)
            else:
                assert calendar_context is not None
                delta = _one_sided_panel_calendar_delta(
                    a_part,
                    time_column=time_column,
                    value_column=a_value,
                    side="current",
                    report_tz=calendar_context[2],
                )
        elif alignment.kind == "window_bucket":
            delta, window_info_piece = _align_panel_window_bucket(
                a_part,
                b_part,
                time_column=time_column,
                a_value_column=a_value,
                b_value_column=b_value,
                current_frame=a,
                baseline_frame=b,
                alignment=alignment,
            )
            if window_info_piece is not None:
                window_infos.append(window_info_piece)
        else:
            assert calendar_context is not None
            loaded_calendar, policy, report_tz = calendar_context
            delta, calendar_alignment_info = align_calendar_frames(
                a_part[[time_column, a_value]],
                b_part[[time_column, b_value]].rename(columns={b_value: a_value}),
                time_column=time_column,
                value_column=a_value,
                calendar=loaded_calendar,
                policy=policy,
                report_tz=report_tz,
            )
            calendar_infos.append(calendar_alignment_info.model_dump(mode="json"))

        for column, value in zip(dim_columns, key, strict=True):
            delta[column] = cast("Any", value)
        pieces.append(delta)

    if pieces:
        result = pd.concat(pieces, ignore_index=True)
    else:
        result = pd.DataFrame(
            columns=[
                time_column,
                *dim_columns,
                PRESENCE_STATUS_COLUMN,
                "current",
                "baseline",
                "delta",
                "pct_change",
                PCT_CHANGE_STATUS_COLUMN,
            ]
        )

    if alignment.kind == "window_bucket":
        time_columns = [time_column]
        baseline_time_column = f"{time_column}_b"
        if baseline_time_column in result.columns:
            time_columns.append(baseline_time_column)
        result = result[
            [
                *time_columns,
                *dim_columns,
                PRESENCE_STATUS_COLUMN,
                "current",
                "baseline",
                "delta",
                "pct_change",
                PCT_CHANGE_STATUS_COLUMN,
            ]
        ]
        sort_columns = [*dim_columns, time_column]
    else:
        leading_columns = [*dim_columns]
        result = result[
            [*leading_columns, *[c for c in result.columns if c not in leading_columns]]
        ]
        sort_columns = [*dim_columns]
        if "bucket_start_a" in result.columns:
            sort_columns.append("bucket_start_a")
    result = result.sort_values(sort_columns, na_position="last").reset_index(drop=True)

    segment_info: dict[str, Any] = {
        "segment_count": len(segment_keys),
        "a_only_segments_count": sum(
            1 for key in segment_keys if key in a_groups and key not in b_groups
        ),
        "b_only_segments_count": sum(
            1 for key in segment_keys if key in b_groups and key not in a_groups
        ),
    }
    window_info = _aggregate_window_info(window_infos)
    if window_info is not None:
        segment_info["coverage"] = window_info
    return result, segment_info, _aggregate_calendar_info(calendar_infos), window_info


def _panel_groups(
    df: pd.DataFrame,
    *,
    dim_columns: list[str],
) -> dict[tuple[object, ...], pd.DataFrame]:
    groups: dict[tuple[object, ...], pd.DataFrame] = {}
    grouped = df.groupby(dim_columns, dropna=False, sort=False)
    for raw_key, group in grouped:
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        groups[tuple(None if not _not_nan(value) else value for value in key)] = group.copy()
    return groups


def _one_sided_panel_calendar_delta(
    df: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    side: str,
    report_tz: str,
) -> pd.DataFrame:
    prepared = df[[time_column, value_column]].sort_values(time_column).reset_index(drop=True)
    bucket_starts = _local_dates(prepared[time_column], report_tz=report_tz).map(
        lambda value: value.isoformat()
    )
    values = pd.to_numeric(prepared[value_column], errors="coerce")
    result = pd.DataFrame(
        {
            PRESENCE_STATUS_COLUMN: "new" if side == "current" else "churned",
            "align_key": np.nan,
            "align_quality": "unmatched",
            "bucket_start_a": bucket_starts if side == "current" else np.nan,
            "bucket_start_b": bucket_starts if side == "baseline" else np.nan,
        }
    )
    if side == "current":
        result["current"] = values
        result["baseline"] = 0.0
    else:
        result["current"] = 0.0
        result["baseline"] = values
    return _compute_delta_columns(result)


def _align_panel_window_bucket(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    time_column: str,
    a_value_column: str,
    b_value_column: str,
    current_frame: MetricFrame,
    baseline_frame: MetricFrame,
    alignment: AlignmentPolicy,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    a_prepared = (
        a_df[[time_column, a_value_column]]
        .sort_values(time_column)
        .rename(columns={a_value_column: "current"})
        .reset_index(drop=True)
    )
    b_prepared = (
        b_df[[time_column, b_value_column]]
        .sort_values(time_column)
        .rename(columns={b_value_column: "baseline"})
        .reset_index(drop=True)
    )
    return _align_prepared_window_bucket(
        a_prepared,
        b_prepared,
        time_column=time_column,
        a_value_column="current",
        b_value_column="baseline",
        current_frame=current_frame,
        baseline_frame=baseline_frame,
        alignment=alignment,
        track_presence_status=True,
    )


def _calendar_context(
    alignment: AlignmentPolicy, *, session: Session
) -> tuple[Any, CalendarPolicy, str]:
    if alignment.kind == "window_bucket":
        raise AlignmentPolicyNotApplicableError(
            message="window_bucket alignment does not require calendar context",
            context={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": alignment.kind},
        )
    calendar_ref = alignment.calendar
    if not isinstance(calendar_ref, CalendarRef):
        raise CalendarPolicyError(
            message="calendar-backed alignment requires CalendarRef",
            context={
                "kind": "CalendarRefMissing",
                "alignment": alignment.model_dump(mode="json"),
            },
        )
    loaded_calendar = session._calendars.get(calendar_ref.ref)
    report_tz = session.report_tz_name
    policy = CalendarPolicy(
        mode=alignment.kind,
        align_period=alignment.period,
        fallback=alignment.fallback,
    )
    return loaded_calendar, policy, report_tz


def _aggregate_calendar_info(infos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not infos:
        return None
    aggregated = dict(infos[0])
    for field in ("matched_rows", "fallback_rows", "dropped_rows_a", "dropped_rows_b"):
        aggregated[field] = sum(int(info.get(field, 0)) for info in infos)
    return aggregated


def _require_calendar_columns(
    df: pd.DataFrame, *, frame_label: str, columns: tuple[str, ...]
) -> None:
    missing_columns = [column for column in columns if column not in df.columns]
    if not missing_columns:
        return
    raise AlignmentFailedError(
        message=(
            f"calendar-backed compare alignment frame '{frame_label}' is missing required columns"
        ),
        context={
            "kind": "CalendarAlignColumnMissing",
            "frame": frame_label,
            "missing_columns": missing_columns,
            "available_columns": [str(column) for column in df.columns],
        },
    )


def _align_time_series_window_bucket(
    a: MetricFrame,
    b: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    track_presence_status: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    time_column = _time_axis_column(a)
    b_time_column = _time_axis_column(b)
    if b_time_column != time_column:
        raise AlignmentFailedError(
            message="window_bucket time_series alignment requires matching time axis columns",
            context={
                "kind": "WindowBucketTimeAxisMismatch",
                "current_time_column": time_column,
                "baseline_time_column": b_time_column,
            },
        )
    # The time column is kept in the result and the value column is renamed to
    # current/baseline, so a time column named like a protocol column must fail
    # closed here too (issue #39, time_series path).
    _validate_compare_protocol_columns(
        [time_column],
        time_column=time_column,
        role="time",
    )
    a_df = a._dataframe_copy()
    b_df = b._dataframe_copy()
    a_value = _value_column(a, a_df, time_column=time_column)
    b_value = _value_column(b, b_df, time_column=time_column)
    a_prepared = (
        a_df[[time_column, a_value]]
        .rename(columns={a_value: "current"})
        .sort_values(time_column)
        .reset_index(drop=True)
    )
    b_prepared = (
        b_df[[time_column, b_value]]
        .rename(columns={b_value: "baseline"})
        .sort_values(time_column)
        .reset_index(drop=True)
    )
    return _align_prepared_window_bucket(
        a_prepared,
        b_prepared,
        time_column=time_column,
        a_value_column="current",
        b_value_column="baseline",
        current_frame=a,
        baseline_frame=b,
        alignment=alignment,
        track_presence_status=track_presence_status,
    )


def _align_and_compute(a_df: pd.DataFrame, b_df: pd.DataFrame) -> pd.DataFrame:
    if len(a_df.columns) == 1 and len(b_df.columns) == 1:
        return _sample_align(a_df, b_df)
    key = a_df.columns[0]
    merged = pd.merge(a_df, b_df, on=key, suffixes=("_a", "_b"))
    if merged.empty:
        return _ordinal_bucket_align(a_df, b_df, key=key)
    value_cols_a = [col for col in merged.columns if col.endswith("_a")]
    value_cols_b = [col for col in merged.columns if col.endswith("_b")]
    if not value_cols_a or not value_cols_b:
        raise AlignmentFailedError(
            message="window_bucket alignment could not find paired value columns"
        )
    current = merged[value_cols_a[0]].to_numpy()
    baseline = merged[value_cols_b[0]].to_numpy()
    return compute_delta_columns(
        pd.DataFrame(
            {
                key: merged[key],
                "current": current,
                "baseline": baseline,
            }
        )
    )


def _ordinal_bucket_align(a_df: pd.DataFrame, b_df: pd.DataFrame, *, key: str) -> pd.DataFrame:
    if len(a_df) != len(b_df):
        raise AlignmentFailedError(
            message=_window_bucket_unequal_length_message(
                current_rows=len(a_df),
                baseline_rows=len(b_df),
            ),
            context={
                "kind": "WindowBucketNoComparableBuckets",
                "current_rows": len(a_df),
                "baseline_rows": len(b_df),
            },
        )
    if a_df[key].duplicated().any() or b_df[key].duplicated().any():
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires unique bucket_start values",
            context={"kind": "WindowBucketDuplicateBuckets"},
        )
    value_cols_a = [column for column in a_df.columns if column != key]
    value_cols_b = [column for column in b_df.columns if column != key]
    if len(value_cols_a) != 1 or len(value_cols_b) != 1:
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires exactly one value column per frame",
            context={
                "kind": "WindowBucketValueColumnAmbiguous",
                "current_value_columns": [str(column) for column in value_cols_a],
                "baseline_value_columns": [str(column) for column in value_cols_b],
            },
        )
    a_sorted = a_df.sort_values(key).reset_index(drop=True)
    b_sorted = b_df.sort_values(key).reset_index(drop=True)
    current = pd.to_numeric(a_sorted[value_cols_a[0]], errors="coerce")
    baseline = pd.to_numeric(b_sorted[value_cols_b[0]], errors="coerce")
    return compute_delta_columns(
        pd.DataFrame(
            {
                key: a_sorted[key],
                f"{key}_b": b_sorted[key],
                "current": current,
                "baseline": baseline,
            }
        )
    )


def _window_bucket_unequal_length_message(*, current_rows: int, baseline_rows: int) -> str:
    return (
        "window_bucket alignment requires equal-length rows for generic ordinal alignment; "
        f"current has {current_rows} rows, baseline has {baseline_rows} rows; "
        "use time_series or panel MetricFrames for non-strict ordinal window bucket alignment"
    )


def _sample_align(a_df: pd.DataFrame, b_df: pd.DataFrame) -> pd.DataFrame:
    n = min(len(a_df), len(b_df))
    current = a_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    baseline = b_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    return compute_delta_columns(pd.DataFrame({"current": current, "baseline": baseline}))


def _scope_for_window(frame: MetricFrame) -> dict[str, Any] | None:
    """Extract comparison_window dict from a MetricFrame's window metadata."""
    window = getattr(frame.meta, "window", None)
    if window is None:
        return None
    if isinstance(window, dict):
        return window
    return None


def _grain_from_axes(frame: MetricFrame) -> str | None:
    """Extract the grain token from a MetricFrame's axes metadata."""
    axes = getattr(frame.meta, "axes", {})
    for axis in axes.values():
        if isinstance(axis, dict) and axis.get("role") == "time":
            grain = axis.get("grain")
            if isinstance(grain, str):
                return grain
    return None
