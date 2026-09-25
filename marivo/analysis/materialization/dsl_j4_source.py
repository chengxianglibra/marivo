"""Private J4 source preparation followed by the Association Python kernel."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan, lower_j1_source
from marivo.analysis.compiler.placement import place_j1_source
from marivo.analysis.datasets.handles import LogicalRootHandle, _RunNodeBindings
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.ibis_batches import IbisBatchStream
from marivo.analysis.materialization.source_stage import J1IbisBackend, run_j1_source
from marivo.analysis.observation.dsl_j1 import (
    J4Association,
    J4CoefficientSelection,
    J4CoefficientStatistic,
)
from marivo.analysis.operators.association_values import (
    EntitySpearmanResult,
    reduce_entity_spearman,
)
from marivo.analysis.operators.dsl_j1_contracts import J4_SPEARMAN, j1_numeric_method
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult, admit_numeric_threshold


def _require_complete_keys(backend: J1IbisBackend, left: ir.Table, right: ir.Table) -> None:
    """Prove exact one-to-one Entity coverage before consuming paired values."""
    left_counts = left.group_by("member").aggregate(n=left.count())
    right_counts = right.group_by("member").aggregate(n=right.count())
    left_repeated = left_counts.filter(left_counts.n > 1)
    right_repeated = right_counts.filter(right_counts.n > 1)
    left_unmatched = left.filter(~left.member.isin(right.member))
    right_unmatched = right.filter(~right.member.isin(left.member))
    left_duplicate = left_repeated.aggregate(left_duplicate=left_repeated.count())
    right_duplicate = right_repeated.aggregate(right_duplicate=right_repeated.count())
    left_missing = left_unmatched.aggregate(left_missing=left_unmatched.count())
    right_missing = right_unmatched.aggregate(right_missing=right_unmatched.count())
    checked = (
        left_duplicate.cross_join(right_duplicate)
        .cross_join(left_missing)
        .cross_join(right_missing)
    )
    check = checked.select(
        invalid=checked.left_duplicate
        + checked.right_duplicate
        + checked.left_missing
        + checked.right_missing
    )
    backend.compile(check)
    native = backend.to_pyarrow_batches(check, chunk_size=1024)
    stream = IbisBatchStream(native, native.schema)
    try:
        table = pa.Table.from_batches(tuple(stream), schema=stream.schema)
    finally:
        stream.close()
    if table.column_names != ["invalid"] or table.column("invalid").to_pylist() != [0]:
        raise MaterializationError(
            expected="two complete unique Entity observation domains",
            received="Spearman endpoint keys differ or repeat",
            repair="Observe both Metrics over the same complete member realization.",
            stage="source_validation",
        )


def run_j4_source(
    association: J4Association,
    backend: J1IbisBackend,
    tables: Mapping[str, ir.Table],
) -> EntitySpearmanResult:
    """Read each admitted endpoint against one realized member set and score it locally."""
    left, right = capture_j4_endpoints(association, backend, tables)
    return reduce_entity_spearman(
        left.primary,
        right.primary,
        ("metric:" + association.left.metric.path, "metric:" + association.right.metric.path),
    )


def capture_j4_endpoints(
    association: J4Association,
    backend: J1IbisBackend,
    tables: Mapping[str, ir.Table],
) -> tuple[J1ExecutionResult, J1ExecutionResult]:
    """Realize two checked observations from one member implementation."""
    context = association.context
    place_j1_source(context, association.root, backend.name)
    J4_SPEARMAN.require_route("source", "duckdb", "entity", "float64")
    member = association.left.root.inputs[0].root
    if (
        not isinstance(member, LogicalRootHandle)
        or member is not association.right.root.inputs[0].root
    ):
        raise MaterializationError(
            expected="one logical shared member node",
            received="incompatible Spearman members",
            repair="Construct both observations from one members object.",
            stage="source_admission",
        )
    member_result = run_j1_source(context, member, backend, tables)
    shared: _RunNodeBindings[J1SourcePlan] = _RunNodeBindings(context.session_id)
    shared.bind(member, J1SourcePlan(ibis.memtable(member_result.primary)))
    left_plan = lower_j1_source(context, association.left.root, tables, memo=shared)
    right_plan = lower_j1_source(context, association.right.root, tables, memo=shared)
    _require_complete_keys(backend, left_plan.primary, right_plan.primary)
    left = run_j1_source(context, association.left.root, backend, tables, shared_plans=shared)
    right = run_j1_source(context, association.right.root, backend, tables, shared_plans=shared)
    return left, right


def j4_execution_result(
    association: J4Association, outcome: EntitySpearmanResult
) -> J1ExecutionResult:
    """Bind the method owner's finished result to the fixed private row schema."""
    schema = pa.schema(
        [
            pa.field("metric_key_a", pa.string(), nullable=False),
            pa.field("metric_key_b", pa.string(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("coefficient", pa.float64()),
            *(
                pa.field(name, pa.int64(), nullable=False)
                for name in (
                    "input_observation_count",
                    "matched_observation_count",
                    "null_pair_count",
                    "complete_pair_count",
                )
            ),
        ]
    )
    if outcome.method != "spearman" or outcome.method_version != 1:
        raise MaterializationError(
            expected="registered Spearman method version 1",
            received="different Association method result",
            repair="Recompute with the admitted J4 method owner.",
            stage="output_validation",
        )
    row = {
        "metric_key_a": outcome.metric_key_a,
        "metric_key_b": outcome.metric_key_b,
        "status": outcome.status,
        "coefficient": outcome.coefficient,
        "input_observation_count": outcome.input_observation_count,
        "matched_observation_count": outcome.matched_observation_count,
        "null_pair_count": outcome.null_pair_count,
        "complete_pair_count": outcome.complete_pair_count,
    }
    return J1ExecutionResult(
        association.root,
        pa.Table.from_pylist([row], schema=schema),
        completed_checks=("complete_pairing", "spearman_pairs"),
    )


def run_j4_local(
    association: J4Association, left: J1ExecutionResult, right: J1ExecutionResult
) -> J1ExecutionResult:
    """Score two exact retained observations after the pandas read boundary."""
    for endpoint in (left, right):
        physical = endpoint.primary.schema.field("value").type
        J4_SPEARMAN.require_route(
            "local", "pandas", "entity", "float64" if physical == pa.float64() else str(physical)
        )
    left_frame = left.primary.to_pandas(types_mapper=pd.ArrowDtype)
    right_frame = right.primary.to_pandas(types_mapper=pd.ArrowDtype)
    outcome = reduce_entity_spearman(
        pa.Table.from_pandas(left_frame, preserve_index=False),
        pa.Table.from_pandas(right_frame, preserve_index=False),
        ("metric:" + association.left.metric.path, "metric:" + association.right.metric.path),
    )
    return j4_execution_result(association, outcome)


def filter_j4_local(
    selection: J4CoefficientSelection, prior: J1ExecutionResult
) -> J1ExecutionResult:
    """Select saved coefficient rows in pandas without re-running Spearman."""
    if prior.root is not selection.association.root:
        raise MaterializationError(
            expected="exact committed Association predecessor",
            received="foreign coefficient input",
            repair="Select the Artifact for the bound Association.",
            stage="local_admission",
        )
    parameters = selection.root.parameters
    if type(parameters) is not tuple or len(parameters) != 2:
        raise MaterializationError(
            expected="one bound coefficient predicate",
            received="invalid predicate binding",
            repair="Rebuild the coefficient selection.",
            stage="local_admission",
        )
    operation, threshold = parameters
    admitted = admit_numeric_threshold("float64", threshold, "local_admission")
    frame = prior.primary.to_pandas(types_mapper=pd.ArrowDtype)
    comparisons = {
        "lt": lambda value: value < admitted,
        "lte": lambda value: value <= admitted,
        "gt": lambda value: value > admitted,
        "gte": lambda value: value >= admitted,
        "eq": lambda value: value == admitted,
    }
    if operation not in comparisons:
        raise MaterializationError(
            expected="registered coefficient predicate",
            received=str(operation),
            repair="Rebuild the coefficient selection.",
            stage="local_admission",
        )
    selected = frame.loc[comparisons[operation](frame["coefficient"])]
    return J1ExecutionResult(
        selection.root,
        pa.Table.from_pandas(selected, schema=prior.primary.schema, preserve_index=False),
        completed_checks=prior.completed_checks,
    )


def summarize_j4_local(
    statistic: J4CoefficientStatistic, prior: J1ExecutionResult
) -> J1ExecutionResult:
    """Reduce currently retained coefficient rows with pandas."""
    if prior.root is not statistic.predecessor.root:
        raise MaterializationError(
            expected="exact coefficient predecessor",
            received="foreign statistic input",
            repair="Select the matching committed coefficient Artifact.",
            stage="local_admission",
        )
    parameters = statistic.root.parameters
    if (
        type(parameters) is not tuple
        or len(parameters) != 1
        or parameters[0] not in ("sum", "count", "mean")
    ):
        raise MaterializationError(
            expected="registered coefficient row statistic",
            received="invalid statistic method",
            repair="Rebuild the coefficient statistic.",
            stage="local_admission",
        )
    method = parameters[0]
    registration = j1_numeric_method(statistic.root)
    if registration is None:
        raise MaterializationError(
            expected="registered coefficient row statistic",
            received="missing method registration",
            repair="Rebuild the statistic with a supported method.",
            stage="local_admission",
        )
    registration.require_route("local", "pandas", "singleton", "float64")
    frame = prior.primary.to_pandas(types_mapper=pd.ArrowDtype)
    count = len(frame)
    amount = float(frame["coefficient"].sum()) if count else 0.0
    row: dict[str, object]
    fields: list[pa.Field]
    if method == "count":
        row = {"value": count, "cell_tag": "defined", "cell_reason": None, "current_count": count}
        fields = [
            pa.field("value", pa.int64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
            pa.field("current_count", pa.int64()),
        ]
    elif method == "sum":
        row = {"value": amount, "cell_tag": "defined", "cell_reason": None, "current_sum": amount}
        fields = [
            pa.field("value", pa.float64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
            pa.field("current_sum", pa.float64()),
        ]
    else:
        row = {
            "value": amount / count if count else None,
            "cell_tag": "defined" if count else "undefined",
            "cell_reason": None if count else "empty_mean",
            "current_sum": amount,
            "current_count": count,
        }
        fields = [
            pa.field("value", pa.float64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
            pa.field("current_sum", pa.float64()),
            pa.field("current_count", pa.int64()),
        ]
    return J1ExecutionResult(
        statistic.root,
        pa.Table.from_pylist([row], schema=pa.schema(fields)),
        completed_checks=("strict_current_row_cell",),
    )
