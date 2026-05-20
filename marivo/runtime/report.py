"""Session analysis report generation.

Produces a self-contained static HTML report from session trace, state,
and evidence data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from marivo.contracts.ids import ArtifactId, SessionId

# ---------------------------------------------------------------------------
# Phase classification
# ---------------------------------------------------------------------------

ANALYSIS_PHASES: dict[str, dict[str, Any]] = {
    "observation": {
        "label": "Observation",
        "description": "Gathering baseline and current data for the metrics under investigation.",
        "step_types": ("observe",),
    },
    "comparison": {
        "label": "Comparison",
        "description": "Comparing current vs baseline to quantify changes.",
        "step_types": ("compare",),
    },
    "decomposition": {
        "label": "Decomposition",
        "description": "Breaking down changes by dimension to identify top contributors.",
        "step_types": ("decompose", "attribute"),
    },
    "anomaly_detection": {
        "label": "Anomaly Detection",
        "description": "Scanning for statistical anomalies and outlier candidates.",
        "step_types": ("detect", "diagnose"),
    },
    "statistical_testing": {
        "label": "Statistical Testing",
        "description": "Validating hypotheses with formal statistical tests.",
        "step_types": ("test", "validate", "correlate"),
    },
    "forecasting": {
        "label": "Forecasting",
        "description": "Projecting trends into future periods.",
        "step_types": ("forecast",),
    },
}

_NOISE_WARNING_CODES = frozenset(
    {
        "semantic_metadata_unavailable",
        "output_summary_unavailable",
    }
)

_MAX_PAYLOAD_ROWS = 100


def _utc_to_local(utc_str: str) -> str:
    """Convert a UTC ISO timestamp string to local timezone display.

    Accepts ISO-8601 strings with or without timezone info.
    Returns a formatted string like "2026-05-20 15:32" in the
    system's local timezone.
    """
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(utc_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_str


def _classify_step_phase(step_type: str) -> str:
    for phase_key, phase_meta in ANALYSIS_PHASES.items():
        if step_type in phase_meta["step_types"]:
            return phase_key
    return "other"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class StepReportData:
    step_id: str
    step_type: str
    summary: str
    created_at: str
    reasoning: str | None
    sql_texts: list[dict[str, str]] | None
    provenance: dict[str, Any] | None
    artifact_id: str | None
    artifact_type: str | None
    artifact_summary: dict[str, Any] | None
    artifact_payload: dict[str, Any] | None
    output_summary: dict[str, Any] | None = None
    analysis_phase: str = ""
    warnings: list[dict[str, str | None]] = field(default_factory=list)
    dependency_refs: list[dict[str, str]] | None = None


@dataclass
class PropositionReportData:
    proposition_id: str
    proposition_type: str
    subject: dict[str, Any]
    subject_display: str
    latest_assessment: dict[str, Any] | None
    supporting_findings: list[dict[str, Any]] = field(default_factory=list)
    opposing_findings: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExecutiveSummaryData:
    goal: str | None
    metrics_examined: list[str]
    total_steps: int
    key_findings: list[dict[str, Any]]
    phase_counts: dict[str, int]
    overall_conclusion: str


@dataclass
class DagNode:
    step_id: str
    step_type: str
    label: str
    phase: str
    index: int


@dataclass
class DagEdge:
    source_step_id: str
    target_step_id: str
    role: str


@dataclass
class ReportData:
    session_id: str
    goal: str | None
    lifecycle_status: str
    created_at: str
    updated_at: str
    steps: list[StepReportData] = field(default_factory=list)
    dag_nodes: list[DagNode] = field(default_factory=list)
    dag_edges: list[DagEdge] = field(default_factory=list)
    propositions: list[PropositionReportData] = field(default_factory=list)
    semantic_models: list[dict[str, Any]] | None = None
    executive_summary: ExecutiveSummaryData | None = None
    generated_at: str = ""


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _truncate_artifact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key in ("buckets", "rows", "segments", "candidates"):
        val = result.get(key)
        if isinstance(val, list) and len(val) > _MAX_PAYLOAD_ROWS:
            result[key] = val[:_MAX_PAYLOAD_ROWS]
            result[f"_{key}_truncated"] = f"Showing {_MAX_PAYLOAD_ROWS} of {len(val)}"
    content = result.get("content") or result.get("result")
    if isinstance(content, dict):
        for key in ("buckets", "rows", "segments", "candidates"):
            val = content.get(key)
            if isinstance(val, list) and len(val) > _MAX_PAYLOAD_ROWS:
                content[key] = val[:_MAX_PAYLOAD_ROWS]
                content[f"_{key}_truncated"] = f"Showing {_MAX_PAYLOAD_ROWS} of {len(val)}"
    return result


def _extract_artifact_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    # Determine content payload (may be nested under 'result' or 'content')
    content = payload.get("result") or payload.get("content")
    content_dict = content if isinstance(content, dict) else None

    # Detect/anomaly_candidates: extract scan summary and top candidate metadata
    detect_payload = None
    if payload.get("artifact_type") == "anomaly_candidates":
        detect_payload = payload
    elif content_dict and content_dict.get("artifact_type") == "anomaly_candidates":
        detect_payload = content_dict

    if detect_payload is not None:
        scan = detect_payload.get("scan_summary") or {}
        if scan.get("total_candidate_count") is not None:
            summary["candidate_count_total"] = scan["total_candidate_count"]
        if scan.get("returned_candidate_count") is not None:
            summary["candidate_count_returned"] = scan["returned_candidate_count"]
        if scan.get("eligible_series_count") is not None:
            summary["eligible_series_count"] = scan["eligible_series_count"]
        candidates = detect_payload.get("candidates") or []
        if candidates:
            top = candidates[0]
            win = top.get("window")
            if isinstance(win, dict) and win.get("start"):
                summary["top_candidate_period"] = win["start"]
            if top.get("candidate_score") is not None:
                summary["top_candidate_score"] = top["candidate_score"]
            if top.get("deviation_pct") is not None:
                summary["top_candidate_deviation_pct"] = top["deviation_pct"]
            if top.get("direction"):
                summary["top_candidate_direction"] = top["direction"]
            if top.get("flag_level"):
                summary["top_candidate_flag_level"] = top["flag_level"]
            if top.get("candidate_type"):
                summary["top_candidate_type"] = top["candidate_type"]
            slice_info = top.get("slice")
            if isinstance(slice_info, dict):
                summary["top_candidate_slice"] = ", ".join(
                    f"{k}={v}" for k, v in slice_info.items()
                )
        for key in ("strategy", "sensitivity", "granularity"):
            val = detect_payload.get(key)
            if val is not None:
                summary[key] = val

    # Comparison: extract only metadata (value keys are rendered in the
    # custom comparison table, so we skip them to avoid duplication)
    is_comparison = False
    for src in (payload, content_dict):
        if src and src.get("comparison_type"):
            is_comparison = True
            break
    if is_comparison:
        for key in ("comparison_type", "unit", "metric"):
            for src in (payload, content_dict):
                if src and key in src and key not in summary:
                    summary[key] = src[key]
        # comparability is a dict; extract status string only
        for src in (payload, content_dict):
            if src:
                comp = src.get("comparability")
                if isinstance(comp, dict) and comp.get("status"):
                    summary["comparability_status"] = comp["status"]

    # Correlation: extract only metadata (stat values are in the custom table)
    is_correlation = False
    for src in (payload, content_dict):
        if src and (
            src.get("association_type") == "pairwise_time_series_association"
            or src.get("intent_type") == "correlate"
        ):
            is_correlation = True
            break
    if is_correlation:
        for key in ("method", "sign", "significance", "left_metric", "right_metric"):
            for src in (payload, content_dict):
                if src and key in src and key not in summary:
                    summary[key] = src[key]

    # General metadata extraction (skip value keys for types with custom renderers)
    _skip_value_keys: frozenset[str] = frozenset(
        {
            "artifact_type",
            "artifact_schema_version",
            "schema_version",
            "observation_type",
            # comparison value keys (already handled above or in custom renderer)
            "current_value",
            "baseline_value",
            "absolute_delta",
            "relative_delta",
            "direction",
            "value",
            "scope_current_value",
            "scope_baseline_value",
            "scope_absolute_delta",
            "scope_relative_delta",
            "scope_direction",
            "summary_current_value",
            "summary_baseline_value",
            "summary_absolute_delta",
            "summary_relative_delta",
            "summary_direction",
            # correlation stat keys (in custom renderer)
            "coefficient",
            "p_value",
            "n_pairs",
        }
    )

    for key in (
        "comparison_type",
        "comparability",
        "unit",
        "dimension",
        "method",
        "strategy",
        "sensitivity",
        "granularity",
        "candidate_count_total",
        "candidate_count_returned",
    ):
        if key in summary:
            continue  # already extracted above
        for src in (payload, content_dict):
            if src and key in src:
                summary[key] = src[key]
                break

    # Count keys for array fields
    for key in ("buckets", "rows", "segments", "candidates"):
        for src in (payload, content_dict):
            if src and isinstance(src.get(key), list):
                summary[f"{key}_count"] = len(src[key])
                break

    # Remove any value keys that slipped through
    for key in _skip_value_keys:
        summary.pop(key, None)

    return summary


def _load_artifact_for_step(
    runtime: Any, session_id: str, artifact_id: str | None
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    if artifact_id is None:
        return None, None, None
    try:
        result = runtime.ports.artifact_store.resolve_artifact_with_type_by_id(
            session_id=SessionId(session_id), artifact_id=ArtifactId(artifact_id)
        )
        if result is None:
            return None, None, None
        art_type, payload = result
        if payload is None:
            return None, None, None
        summary = _extract_artifact_summary(payload)
        return _truncate_artifact_payload(payload), art_type, summary
    except Exception:
        return None, None, None


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_assessment(assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    if assessment is None:
        return None
    result = dict(assessment)
    _json_keys = {
        "confidence_rationale_json": "confidence_rationale",
        "gap_memberships_json": "gap_memberships",
        "supporting_finding_ids_json": "supporting_finding_ids",
        "opposing_finding_ids_json": "opposing_finding_ids",
    }
    for json_key, plain_key in _json_keys.items():
        if json_key in result:
            result[plain_key] = result[json_key]
    return result


def _normalize_gap(gap: dict[str, Any]) -> dict[str, Any]:
    result = dict(gap)
    _json_keys = {
        "missing_requirement_json": "missing_requirement",
        "satisfiable_by_json": "satisfiable_by",
    }
    for json_key, plain_key in _json_keys.items():
        if json_key in result:
            result[plain_key] = result[json_key]
    return result


def _subject_display_string(subject: dict[str, Any]) -> str:
    parts: list[str] = []
    metric = subject.get("metric")
    if metric:
        parts.append(metric)
    dimension = subject.get("dimension")
    if dimension:
        parts.append(f"by {dimension}")
    slice_info = subject.get("slice")
    if isinstance(slice_info, dict):
        for k, v in slice_info.items():
            parts.append(f"{k}={v}")
    return " | ".join(parts) if parts else "(unspecified)"


def _extract_dependency_refs(
    step_type: str, provenance: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Extract parent artifact/step references from provenance for DAG edges."""
    refs: list[dict[str, str]] = []
    if provenance is None:
        return refs
    # compare: depends on two observe artifacts
    if step_type == "compare":
        for role, key in (
            ("current", "current_artifact_id"),
            ("baseline", "baseline_artifact_id"),
        ):
            aid = provenance.get(key)
            if aid:
                refs.append({"role": role, "artifact_id": aid})
        for role, key in (
            ("current", "current_step_id"),
            ("baseline", "baseline_step_id"),
        ):
            sid = provenance.get(key)
            if sid:
                refs.append({"role": role, "step_id": sid})
    # decompose: depends on a compare artifact
    elif step_type == "decompose":
        aid = provenance.get("compare_artifact_id")
        if aid:
            refs.append({"role": "compare", "artifact_id": aid})
    # correlate: depends on two time-series observe artifacts
    elif step_type == "correlate":
        for role, key in (("left", "left_artifact_id"), ("right", "right_artifact_id")):
            aid = provenance.get(key)
            if aid:
                refs.append({"role": role, "artifact_id": aid})
    # diagnose: depends on detect step
    elif step_type == "diagnose":
        sid = provenance.get("detect_step_id")
        if sid:
            refs.append({"role": "detect", "step_id": sid})
    return refs


def _build_dag(steps: list[StepReportData]) -> tuple[list[DagNode], list[DagEdge]]:
    """Build DAG nodes and edges from step dependency refs.

    Nodes are laid out in step order with phase grouping.
    Edges connect parent steps to dependent steps via dependency_refs.
    For artifact_id-based refs, we resolve the artifact_id to the step_id
    of the step that produced that artifact.
    """
    # Build artifact_id → step_id lookup
    artifact_to_step: dict[str, str] = {}
    for s in steps:
        if s.artifact_id and s.step_id:
            artifact_to_step[s.artifact_id] = s.step_id

    # Build step_id → step index lookup
    step_id_to_index: dict[str, int] = {}
    for i, s in enumerate(steps):
        if s.step_id:
            step_id_to_index[s.step_id] = i

    nodes: list[DagNode] = []
    for i, s in enumerate(steps):
        # Short label: type + first ~20 chars of summary
        short_label = s.step_type
        if s.summary:
            summary_prefix = s.summary[:30]
            if len(s.summary) > 30:
                summary_prefix += "..."
            short_label = f"{s.step_type}: {summary_prefix}"
        nodes.append(
            DagNode(
                step_id=s.step_id,
                step_type=s.step_type,
                label=short_label,
                phase=s.analysis_phase,
                index=i,
            )
        )

    edges: list[DagEdge] = []
    for s in steps:
        if not s.dependency_refs:
            continue
        for ref in s.dependency_refs:
            # Resolve to a source step_id
            source_sid = ref.get("step_id")
            if not source_sid:
                aid = ref.get("artifact_id")
                if aid and aid in artifact_to_step:
                    source_sid = artifact_to_step[aid]
            if source_sid and source_sid in step_id_to_index and s.step_id:
                edges.append(
                    DagEdge(
                        source_step_id=source_sid,
                        target_step_id=s.step_id,
                        role=ref.get("role", ""),
                    )
                )

    return nodes, edges


# ---------------------------------------------------------------------------
# Lookups from state view
# ---------------------------------------------------------------------------


def _build_finding_lookup(backing_findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for f in backing_findings:
        fid = f.get("finding_id")
        if fid:
            # Normalize subject_json to subject for template convenience
            normalized = dict(f)
            if "subject_json" in normalized:
                normalized["subject"] = normalized["subject_json"]
            if "payload_json" in normalized:
                normalized["payload"] = normalized["payload_json"]
            lookup[fid] = normalized
    return lookup


def _build_gap_lookup(blocking_gaps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for g in blocking_gaps:
        gid = g.get("gap_id")
        if gid:
            lookup[gid] = _normalize_gap(g)
    return lookup


# ---------------------------------------------------------------------------
# Proposition data collection
# ---------------------------------------------------------------------------


def _collect_proposition_data(
    runtime: Any,
    session_id: str,
    state_view: dict[str, Any],
    finding_lookup: dict[str, dict[str, Any]] | None = None,
    gap_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[PropositionReportData]:
    propositions: list[PropositionReportData] = []
    active_props = state_view.get("active_propositions") or []

    for entry in active_props:
        prop = entry.get("proposition") or {}
        prop_id = prop.get("proposition_id")
        if not prop_id:
            continue

        # subject_json is the canonical key after repository deserialization
        subject = prop.get("subject_json") or {}
        subject_display = _subject_display_string(subject)

        assessment = _normalize_assessment(entry.get("latest_assessment"))

        # Hydrate supporting findings from refs
        supporting_findings: list[dict[str, Any]] = []
        for ref in entry.get("supporting_finding_refs") or []:
            fid = ref.get("finding_id")
            if finding_lookup and fid and fid in finding_lookup:
                supporting_findings.append(finding_lookup[fid])
            else:
                supporting_findings.append(ref)

        # Hydrate opposing findings from refs
        opposing_findings: list[dict[str, Any]] = []
        for ref in entry.get("opposing_finding_refs") or []:
            fid = ref.get("finding_id")
            if finding_lookup and fid and fid in finding_lookup:
                opposing_findings.append(finding_lookup[fid])
            else:
                opposing_findings.append(ref)

        # Hydrate gaps from refs
        gaps: list[dict[str, Any]] = []
        for ref in entry.get("blocking_gap_refs") or []:
            gid = ref.get("gap_id")
            if gap_lookup and gid and gid in gap_lookup:
                gaps.append(gap_lookup[gid])
            else:
                gaps.append(ref)
        for ref in entry.get("non_blocking_gap_refs") or []:
            gid = ref.get("gap_id")
            if gap_lookup and gid and gid in gap_lookup:
                gaps.append(gap_lookup[gid])
            else:
                gaps.append(ref)

        propositions.append(
            PropositionReportData(
                proposition_id=prop_id,
                proposition_type=prop.get("proposition_type", ""),
                subject=subject,
                subject_display=subject_display,
                latest_assessment=assessment,
                supporting_findings=supporting_findings,
                opposing_findings=opposing_findings,
                gaps=gaps,
            )
        )
    return propositions


# ---------------------------------------------------------------------------
# Semantic model collection
# ---------------------------------------------------------------------------


def _collect_semantic_models(runtime: Any) -> list[dict[str, Any]] | None:
    try:
        model_store = runtime.ports.model_store
        if model_store is None:
            return None
        models = model_store.list_models() if hasattr(model_store, "list_models") else []
        result = []
        for m in models:
            entry: dict[str, Any] = {"name": m.get("name", "")}
            if m.get("description"):
                entry["description"] = m["description"]
            metrics = m.get("metrics") or []
            if metrics:
                entry["metric_count"] = len(metrics)
                entry["metric_names"] = [me.get("name", "") for me in metrics[:10]]
            result.append(entry)
        return result or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------


def _build_executive_summary(report: ReportData) -> ExecutiveSummaryData:
    metrics = sorted(
        {
            s.artifact_summary.get("metric", "") or ""
            for s in report.steps
            if s.artifact_summary and s.artifact_summary.get("metric")
        }
    )

    phase_counts: dict[str, int] = {}
    for s in report.steps:
        phase = s.analysis_phase
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    key_findings: list[dict[str, Any]] = []
    for prop in report.propositions:
        assessment = prop.latest_assessment
        if assessment and assessment.get("status") == "supported":
            confidence = assessment.get("confidence_grade", "low")
            if confidence in ("very_high", "high", "medium"):
                key_findings.append(
                    {
                        "type": prop.proposition_type,
                        "subject": prop.subject_display,
                        "confidence": confidence,
                        "rationale": assessment.get("confidence_rationale", ""),
                    }
                )

    supported = sum(
        1
        for p in report.propositions
        if p.latest_assessment and p.latest_assessment.get("status") == "supported"
    )
    contradicted = sum(
        1
        for p in report.propositions
        if p.latest_assessment and p.latest_assessment.get("status") == "contradicted"
    )
    if contradicted > 0:
        conclusion = (
            f"Analysis found {contradicted} contradicted and {supported} supported proposition(s)."
        )
    elif supported > 0:
        conclusion = f"Analysis confirmed {supported} proposition(s) with sufficient evidence."
    else:
        conclusion = "Analysis is ongoing; no propositions have been fully assessed yet."

    return ExecutiveSummaryData(
        goal=report.goal,
        metrics_examined=metrics,
        total_steps=len(report.steps),
        key_findings=key_findings,
        phase_counts=phase_counts,
        overall_conclusion=conclusion,
    )


# ---------------------------------------------------------------------------
# Main report generation
# ---------------------------------------------------------------------------


def generate_session_report(runtime: Any, session_id: str, output_path: str) -> dict[str, Any]:
    """Generate a static HTML report for an analysis session.

    Can be called at any time (active or terminated session).
    """
    from marivo.runtime import session as session_ops

    sid = SessionId(session_id)

    # 1. Get session trace
    trace = session_ops.get_session_trace(runtime, sid)

    # 2. Get session state
    try:
        state_view = session_ops.get_session_state_view(runtime, sid, {})
    except Exception:
        state_view = {}

    # 3. Build lookups from state view for hydrating refs
    backing_findings = state_view.get("backing_findings") or []
    finding_lookup = _build_finding_lookup(backing_findings)

    blocking_gaps = state_view.get("blocking_gaps") or []
    gap_lookup = _build_gap_lookup(blocking_gaps)

    # 4. Build step data
    steps: list[StepReportData] = []
    for step_dict in trace.get("steps") or []:
        artifact_id = step_dict.get("artifact_id")
        payload, art_type, art_summary = _load_artifact_for_step(runtime, session_id, artifact_id)
        filtered_warnings = [
            w
            for w in (step_dict.get("warnings") or [])
            if w.get("code") not in _NOISE_WARNING_CODES
        ]
        step_type = step_dict.get("step_type", "")
        provenance = step_dict.get("provenance")
        steps.append(
            StepReportData(
                step_id=step_dict.get("step_id", ""),
                step_type=step_type,
                summary=step_dict.get("summary", ""),
                created_at=_utc_to_local(step_dict.get("created_at", "")),
                reasoning=step_dict.get("reasoning"),
                sql_texts=step_dict.get("sql_texts"),
                provenance=provenance,
                artifact_id=artifact_id,
                artifact_type=art_type,
                artifact_summary=art_summary,
                artifact_payload=payload,
                output_summary=step_dict.get("output_summary"),
                analysis_phase=_classify_step_phase(step_type),
                warnings=filtered_warnings,
                dependency_refs=_extract_dependency_refs(step_type, provenance),
            )
        )

    # 5. Build proposition data
    propositions = _collect_proposition_data(
        runtime,
        session_id,
        state_view,
        finding_lookup=finding_lookup,
        gap_lookup=gap_lookup,
    )

    # 6. Semantic models
    semantic_models = _collect_semantic_models(runtime)

    # 7. Build DAG from step dependencies
    dag_nodes, dag_edges = _build_dag(steps)

    # 8. Assemble report data
    report = ReportData(
        session_id=session_id,
        goal=trace.get("goal"),
        lifecycle_status=trace.get("lifecycle_status", ""),
        created_at=_utc_to_local(trace.get("created_at", "")),
        updated_at=_utc_to_local(trace.get("updated_at", "")),
        steps=steps,
        dag_nodes=dag_nodes,
        dag_edges=dag_edges,
        propositions=propositions,
        semantic_models=semantic_models,
        generated_at=_utc_to_local(datetime.now(UTC).isoformat()),
    )

    # 8. Build executive summary
    report.executive_summary = _build_executive_summary(report)

    # 9. Render HTML
    template_dir = Path(__file__).parent / "report_templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("session_report.html")
    html = template.render(report=report, phases=ANALYSIS_PHASES)

    # 10. Write to file
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")

    return {"output_path": str(path), "session_id": session_id}
