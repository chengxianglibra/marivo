"""Private J4 source preparation followed by the Association Python kernel."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan, lower_j1_source
from marivo.analysis.compiler.placement import place_j1_source
from marivo.analysis.datasets.handles import LogicalRootHandle, _RunNodeBindings
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.ibis_batches import IbisBatchStream
from marivo.analysis.materialization.source_stage import J1IbisBackend, run_j1_source
from marivo.analysis.observation.dsl_j1 import J4Association
from marivo.analysis.operators.association_values import (
    EntitySpearmanResult,
    reduce_entity_spearman,
)
from marivo.analysis.operators.dsl_j1_contracts import J4_SPEARMAN


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
    return reduce_entity_spearman(
        left.primary,
        right.primary,
        ("metric:" + association.left.metric.path, "metric:" + association.right.metric.path),
    )
