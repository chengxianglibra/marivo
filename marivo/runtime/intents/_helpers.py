from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact
from marivo.contracts.envelope import ExecutionEnvelope, StepRef
from marivo.contracts.ids import ArtifactId
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.core.semantic.value_expr import extract_value_expression
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


def aoi_filter_to_scope(filter_raw: Any, *, label: str) -> dict[str, str] | None:
    """Convert AOI Expression filters to the runtime's predicate scope shape."""
    if filter_raw is None:
        return None
    if not isinstance(filter_raw, dict):
        raise ValueError(f"{label} must be an AOI Expression object")
    dialects = filter_raw.get("dialects")
    if not isinstance(dialects, list) or not dialects:
        raise ValueError(f"{label}.dialects must be non-empty")

    selected_expression: str | None = None
    for dialect in dialects:
        if not isinstance(dialect, dict):
            continue
        expression = dialect.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            continue
        if selected_expression is None:
            selected_expression = expression.strip()
        if str(dialect.get("dialect") or "ANSI_SQL").upper() == "ANSI_SQL":
            selected_expression = expression.strip()
            break

    if selected_expression is None:
        raise ValueError(f"{label}.dialects must include an expression")
    return {"predicate": selected_expression}


def resolve_time_scope(time_scope_raw: dict[str, Any]) -> tuple[str, str, str | None]:
    """Resolve a time scope dict into (start_str, end_str, field).

    Handles AOI-aligned McpTimeScope ({field, start, end}) and range scopes.
    """
    kind = time_scope_raw.get("kind")
    # AOI-aligned McpTimeScope uses {field, start, end} without kind.
    # Treat missing kind as "range" when start and end are present.
    if kind is None and "start" in time_scope_raw and "end" in time_scope_raw:
        kind = "range"
    if kind == "range":
        try:
            return (
                str(time_scope_raw["start"]),
                str(time_scope_raw["end"]),
                time_scope_raw.get("field"),
            )
        except KeyError as exc:
            raise ValueError("INVALID_ARGUMENT - range time_scope requires start and end") from exc

    raise ValueError(f"INVALID_ARGUMENT - unsupported time_scope.kind={kind!r}")


def build_scoped_query_for_window(
    runtime: MarivoRuntime,
    *,
    session_id: str,
    engine_type: str,
    metric_ref: str,
    table: str,
    start: str,
    end: str,
    grain: str | None = None,
    scope_raw: Any = None,
    all_dimensions: list[str] | None = None,
    time_scope_field: str | None = None,
) -> dict[str, Any]:
    """Build a scoped query dict for a single time window.

    Reused by observe and test (which passes grain=None for scalar mode).
    """
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_ref,
        "time_scope": {
            "mode": "single_window",
            "grain": grain,
            "current": {"start": start, "end": end},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw
    if time_scope_field:
        mq_params["time_scope_field"] = time_scope_field
    resolved = normalize_metric_query_request(mq_params)
    runtime.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions or [],
    )
    return runtime.build_scoped_query(session_id, resolved, engine_type=engine_type)


def extract_predicate_filter_lineage(compiled_query: Any) -> dict[str, Any] | None:
    """Extract predicate_filter_lineage from the first MeasurementNode in the IR bundle."""
    ir_bundle = getattr(compiled_query, "ir_bundle", None)
    if ir_bundle is None:
        return None
    for node in ir_bundle.get("plan", {}).get("nodes") or []:
        if node.get("node_type") == "measurement":
            lineage: dict[str, Any] | None = node.get("predicate_filter_lineage")
            if lineage is not None:
                return lineage
    return None


class SampleSummary:
    """Result of computing numeric sample summary statistics for one slice."""

    __slots__ = ("mean", "n", "predicate_filter_lineage", "standard_deviation")

    def __init__(
        self,
        n: int | None,
        mean: float | None,
        standard_deviation: float | None,
        predicate_filter_lineage: dict[str, Any] | None,
    ) -> None:
        self.n = n
        self.mean = mean
        self.standard_deviation = standard_deviation
        self.predicate_filter_lineage = predicate_filter_lineage


def compute_numeric_sample_summary(
    runtime: MarivoRuntime,
    session_id: str,
    metric_ref: str,
    time_scope_raw: dict[str, Any],
    scope_raw: Any = None,
) -> SampleSummary:
    """Compute n/mean/stddev for one slice without creating intermediate artifacts.

    Used by the test intent (source-type) to compute sample summaries internally.
    Raises ValueError if the metric does not support sample-summary computation
    (requires aggregation_semantics='sum' and SUM(expr) definition_sql).
    """
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    # Resolve metric execution context
    execution_context = runtime.resolve_metric_execution_context(metric_ref, session_id=session_id)
    resolved_metric = runtime.resolve_metric(metric_name)
    _resolved_header = (
        (resolved_metric.semantic_object.get("header") or {}) if resolved_metric else {}
    )
    aggregation_semantics = _resolved_header.get("aggregation_semantics") or "sum"
    table = execution_context.table_name
    all_dimensions = runtime.resolve_metric_dimensions(metric_ref)

    # Resolve engine
    resolved = normalize_metric_query_request(
        {
            "table": table,
            "metric": metric_ref,
            "time_scope": {
                "mode": "single_window",
                "grain": None,
                "current": {"start": "", "end": ""},
            },
        }
    )
    engine_resolution = runtime.resolve_engine_for_session(session_id, [resolved.table])
    if not isinstance(engine_resolution, tuple) or len(engine_resolution) != 3:
        engine_resolution = runtime.resolve_engine([resolved.table])
    engine, engine_type, qualified = engine_resolution

    # Resolve time scope
    start_str, end_str, time_scope_field = resolve_time_scope(time_scope_raw)

    # Resolve metric SQL and extract value expression
    metric_sql = runtime.resolve_metric_sql_for_execution(
        metric_ref, execution_context, engine_type=engine_type
    )
    value_expr = extract_value_expression(metric_sql, aggregation_semantics)
    if value_expr is None:
        raise ValueError(
            f"test: INVALID_ARGUMENT - numeric kind requires a metric with "
            f"aggregation_semantics='sum' and definition_sql matching SUM(expr); "
            f"got aggregation_semantics='{aggregation_semantics}', "
            f"definition_sql='{metric_sql}'"
        )

    # Build scoped query
    scoped_query = build_scoped_query_for_window(
        runtime,
        session_id=session_id,
        engine_type=engine_type,
        metric_ref=metric_ref,
        table=table,
        start=start_str,
        end=end_str,
        grain=None,
        scope_raw=scope_raw,
        all_dimensions=all_dimensions,
        time_scope_field=time_scope_field,
    )

    qualified_table = qualified.get(resolved.table, resolved.table)

    # Compile and execute
    compiled_query = runtime.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="aggregate_query",
            params={
                "table": qualified_table,
                "time_scope": {
                    "mode": "single_window",
                    "grain": None,
                    "current": {"start": start_str, "end": end_str},
                },
                "select": [
                    "COUNT(*) AS n",
                    f"AVG({value_expr}) AS mean",
                    f"STDDEV_SAMP({value_expr}) AS standard_deviation",
                ],
                "scoped_query": scoped_query,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_execution_context": execution_context},
    )
    rows = list(execute_compiled(engine, compiled_query, session_id=session_id).rows)
    predicate_filter_lineage = extract_predicate_filter_lineage(compiled_query)

    n_val: int | None = None
    mean_val: float | None = None
    stddev_val: float | None = None
    if rows:
        row = rows[0]
        raw_n = row.get("n")
        if raw_n is not None:
            with contextlib.suppress(TypeError, ValueError):
                n_val = int(raw_n)
        raw_mean = row.get("mean")
        if raw_mean is not None:
            with contextlib.suppress(TypeError, ValueError):
                mean_val = float(raw_mean)
        raw_stddev = row.get("standard_deviation")
        if raw_stddev is not None:
            with contextlib.suppress(TypeError, ValueError):
                stddev_val = float(raw_stddev)

    return SampleSummary(
        n=n_val,
        mean=mean_val,
        standard_deviation=stddev_val,
        predicate_filter_lineage=predicate_filter_lineage,
    )


def commit_step_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    artifact_payload: dict[str, Any],
    summary: str,
    provenance: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commit an artifact and insert a step record.

    Replaces the repeated 5-8 line pattern across 10+ intent runners:
      1. runtime.commit_artifact_with_extraction(...) -> artifact_id
      2. Build result dict with step_ref and artifact_id
      3. runtime.insert_step(...)

    Returns the result dict with intent_type, step_type, step_ref, artifact_id,
    and all keys from artifact_payload merged in.
    """
    artifact_id: str = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        artifact_type,
        artifact_name,
        artifact_payload,
        step_type=step_type,
    )

    result: dict[str, Any] = {
        "intent_type": step_type,
        "step_type": step_type,
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": step_type,
        },
        "artifact_id": artifact_id,
        **artifact_payload,
    }

    runtime.insert_step(
        step_id,
        session_id,
        step_type,
        summary,
        result,
        provenance=provenance,
        semantic_metadata=semantic_metadata,
    )

    return result


def build_envelope(
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_id: str,
    artifact_payload: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Build an ExecutionEnvelope from intent execution results.

    This is the successor to commit_step_result()'s dict construction.
    Intent handlers should migrate to use this + runtime artifact commit
    separately.
    """
    return ExecutionEnvelope(
        intent_type=step_type,
        step_type=step_type,
        step_ref=StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type=step_type,
        ),
        artifact_id=artifact_id,
        result=artifact_payload,
        provenance=provenance,
        product_metadata=product_metadata,
    )


def commit_aoi_artifact_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    artifact_payload: dict[str, Any],
    summary: str,
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Commit a canonical AOI artifact and return an execution envelope."""
    canonical_artifact = artifact_to_envelope_result(validate_aoi_artifact(artifact_payload))
    artifact_body_key = "result" if "result" in canonical_artifact else "failure"
    artifact_body = canonical_artifact[artifact_body_key]
    artifact_id = ArtifactId(f"art_{uuid4().hex[:12]}")
    final_artifact = artifact_to_envelope_result(
        validate_aoi_artifact(
            {
                "artifact_id": artifact_id,
                artifact_body_key: artifact_body,
            }
        )
    )

    committed_artifact_id: str = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        artifact_type,
        artifact_name,
        final_artifact,
        step_type=step_type,
        artifact_id=artifact_id,
    )

    envelope = build_envelope(
        session_id=session_id,
        step_id=step_id,
        step_type=step_type,
        artifact_id=committed_artifact_id,
        artifact_payload=final_artifact,
        provenance=provenance,
        product_metadata=product_metadata,
    )

    runtime.insert_step(
        step_id,
        session_id,
        step_type,
        summary,
        envelope.model_dump(),
        provenance=provenance,
        semantic_metadata=semantic_metadata,
    )

    return envelope
