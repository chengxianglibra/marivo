"""Bounded Lifecycle continuation proofs and native summary publication."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.domains.lifecycle_reducers import (
    REDUCER_TYPES,
    DistributionSemantics,
    DwellSemantics,
    ReducerSemantics,
    TransitionsSemantics,
    history_semantics,
)
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.lifecycle_reducer_codec import (
    TOTALS,
    LifecycleReducerEvidence,
    LifecycleSelectionEvidence,
    count,
)

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend

    from marivo.analysis.compiler.nodes import CompiledDataset
    from marivo.analysis.datasets.descriptors import DatasetRowContract


def output_proof(
    table: ir.Table, semantics: ReducerSemantics, *, filtered: bool = False
) -> ir.Table:
    """Check exact closed row equations without projecting identities."""
    history = history_semantics(semantics)
    valid: ir.BooleanValue = ibis.literal(True)
    for name in TOTALS[semantics.kind]:
        valid &= table[name].notnull() & (table[name] >= 0)
    if isinstance(semantics, DistributionSemantics):
        valid &= table.model_state.isin(history.states) & table.as_of.isin(tuple(semantics.at))
        valid &= (table.subject_count <= table.known_subject_count) & (
            table.coverage_censored_subject_count >= 0
        )
        valid &= table.share.identical_to(
            table.subject_count.cast("float64")
            / table.known_subject_count.nullif(0).cast("float64")
        )
    elif isinstance(semantics, DwellSemantics):
        valid &= table.model_state.isin(history.states) & (
            table.interval_count
            == table.completed_count + table.right_censored_count + table.coverage_censored_count
        )
        valid &= table.left_clipped_completed_count <= table.completed_count
        for name in ("mean_duration", "median_duration", "p90_duration"):
            valid &= table[name].notnull() == (table.completed_count > 0)
            valid &= table[name].isnull() | (
                (table[name] > 0)
                & ~table[name].cast("float64").isinf()
                & ~table[name].cast("float64").isnan()
            )
    elif isinstance(semantics, TransitionsSemantics):
        pairs = history.transition_pairs
        legal: ir.BooleanValue = ibis.literal(False)
        for a, b in pairs:
            legal |= (table.from_model_state == a) & (table.to_model_state == b)
        valid &= legal
        valid &= (
            table.share_of_modeled_transitions.isnull()
            | table.share_of_modeled_transitions.between(0, 1)
        )
    else:
        valid &= table.model_state_at_event.isin(history.states)
        valid &= table.violation_kind.isin(("illegal_transition", "transition_from_terminal"))
        valid &= (
            table.violation_kind == "transition_from_terminal"
        ) == table.model_state_at_event.isin(history.terminals)
    violations = (~valid.fill_null(False)).cast("int64").sum().fill_null(0)
    proof = table.aggregate(violations=violations)
    if not filtered and isinstance(semantics, DistributionSemantics):
        groups = table.group_by(
            "as_of", *(ref.rsplit(".", 1)[-1] for ref in semantics.axis_refs)
        ).aggregate(
            n=table.count(),
            subjects=table.subject_count.sum(),
            known_min=table.known_subject_count.min(),
            known_max=table.known_subject_count.max(),
            censored_min=table.coverage_censored_subject_count.min(),
            censored_max=table.coverage_censored_subject_count.max(),
        )
        bad = groups.filter(
            (groups.n != len(history.states))
            | (groups.subjects != groups.known_min)
            | (groups.known_min != groups.known_max)
            | (groups.censored_min != groups.censored_max)
        )
        extra = bad.count()
        if not semantics.axis_refs:
            extra += (groups.count() != len(semantics.at)).cast("int64")
        return proof.mutate(violations=proof.violations + extra)
    if not filtered and isinstance(semantics, TransitionsSemantics):
        pairs = history.transition_pairs
        total = table.transition_count.sum().fill_null(0)
        augmented = table.cross_join(table.aggregate(__total=total))
        bad = augmented.filter(
            ~augmented.share_of_modeled_transitions.identical_to(
                augmented.transition_count.cast("float64")
                / augmented.__total.nullif(0).cast("float64")
            )
        )
        return proof.mutate(
            violations=proof.violations + bad.count() + (table.count() != len(pairs)).cast("int64")
        )
    if not filtered and isinstance(semantics, DwellSemantics):
        return proof.mutate(
            violations=proof.violations + (table.count() != len(history.states)).cast("int64")
        )
    return proof


def native_summary(
    backend: Backend,
    recipe: CompiledDataset,
    row: DatasetRowContract,
    record: Callable[[str, str], None],
    *,
    filtered: bool = False,
) -> LifecycleReducerEvidence | LifecycleSelectionEvidence:
    coverage = recipe.lifecycle_reducer_coverage
    if coverage is None:
        raise invalid("missing exact Lifecycle continuation coverage")
    payload, proof = recipe.lifecycle_selection_payload, recipe.lifecycle_selection_proof
    if payload is not None:
        if proof is None or recipe.selection_input_definition is None:
            raise invalid("missing complete Lifecycle selection proof")
        record("lifecycle.selection_summary", backend.compile(proof))
        checked = backend.to_pyarrow(proof)
        if checked.num_rows != 1:
            raise invalid("invalid Lifecycle selection scalar proof")
        counts = checked.to_pylist()[0]
        if count(counts["unknown_subject_count"]) != 0:
            raise invalid("Lifecycle selection has unknown membership")
        selected = count(counts["selected_subject_count"])
        return LifecycleSelectionEvidence(
            "lifecycle_selection",
            coverage,
            selected,
            count(counts["input_subject_count"]),
            selected,
            payload.history,
            payload.selection.state.name,
            payload.selection.at.isoformat(),
            recipe.selection_input_definition,
        )
    semantics = row.family_semantics
    if not isinstance(semantics, REDUCER_TYPES):
        raise invalid("missing exact Lifecycle reducer meaning")
    proof = output_proof(recipe.expression, semantics, filtered=filtered)
    record("lifecycle.reducer_output", backend.compile(proof))
    if backend.to_pyarrow(proof)["violations"][0].as_py() != 0:
        raise invalid("invalid Lifecycle reducer row equations")
    table = recipe.expression
    summary = table.aggregate(
        row_count=table.count(),
        **{name: table[name].sum().fill_null(0) for name in TOTALS[semantics.kind]},
    )
    record("lifecycle.reducer_summary", backend.compile(summary))
    checked = backend.to_pyarrow(summary).to_pylist()[0]
    return LifecycleReducerEvidence(
        "lifecycle_reducer",
        coverage,
        count(checked["row_count"]),
        semantics,
        tuple((name, count(checked[name])) for name in TOTALS[semantics.kind]),
    )


def summary_from_batches(
    batches: Iterable[pa.RecordBatch], previous: LifecycleReducerEvidence
) -> LifecycleReducerEvidence:
    totals = dict.fromkeys(TOTALS[previous.semantics.kind], 0)
    rows = 0
    for batch in batches:
        rows += batch.num_rows
        for name in totals:
            totals[name] += sum(count(v) for v in batch[name].to_pylist())
    return replace(previous, row_count=rows, totals=tuple(totals.items()))
