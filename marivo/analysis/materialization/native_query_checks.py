"""Retain native Query diagnostics through fusion and restore the admitted row layout."""

from __future__ import annotations

from dataclasses import dataclass, replace

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo.analysis.compiler.lowered_query import LoweredRead, PopulationCheck
from marivo.analysis.compiler.native_query import NativeQueryFinish
from marivo.analysis.compiler.query_program import SourceParameter
from marivo.analysis.compiler.query_stages import (
    QueryPlan,
    SourceReadStage,
    read_members,
    read_native_reductions,
    read_projections,
    read_reduction,
    require_reduction,
)
from marivo.analysis.compiler.reduction_input import ReductionInput
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.datasource.spi import query_policy
from marivo.semantic.spi import NumericCheck


@dataclass(frozen=True, slots=True)
class NativeQueryRelation:
    expression: ir.Table
    stages: frozenset[str]
    sources: tuple[LoweredRead, ...]
    checks: tuple[NumericCheck, ...] = ()
    parameters: tuple[SourceParameter, ...] = ()

    def reductions(self, plan: QueryPlan) -> tuple[ReductionInput, ...]:
        return tuple(
            reduction
            for source in self.sources
            for reduction in (
                *(
                    require_reduction(member)
                    for member in read_members(plan.source_read_stage(source.stage_id))
                    or (plan.source_read_stage(source.stage_id),)
                    if read_reduction(member) is not None
                ),
                *read_native_reductions(plan.source_read_stage(source.stage_id)),
            )
        )


def _check_summary(
    relation: ir.Table,
    checks: tuple[tuple[str, ir.BooleanValue, tuple[str, ...]], ...],
    *,
    combined: bool,
    provider: str = "",
) -> ir.Table:
    """Aggregate flags and select actual complete failure tuples from one scope."""
    # DuckDB struct comparison drops string collations that scalar ORDER BY
    # preserves. Ibis types cannot prove a key's physical/session collation;
    # only the qualified remote providers admit strings in the tuple path.
    combined = combined and (
        query_policy(provider).uses_identity_alignment_domain
        or not any(relation[key].type().is_string() for _, _, keys in checks for key in keys)
    )
    metrics: dict[str, ir.Value] = {
        alias: violation.any().fill_null(False) for alias, violation, _ in checks
    }
    if combined:
        for alias, violation, keys in checks:
            if not keys:
                continue
            payload = ibis.struct({f"k{i}": relation[key] for i, key in enumerate(keys)})
            # Explicit null flags preserve lexicographic ASC NULLS LAST without
            # constructing a nonexistent tuple from independent column minima.
            order = ibis.struct(
                {
                    name: value
                    for i, key in enumerate(keys)
                    for name, value in ((f"n{i}", relation[key].isnull()), (f"k{i}", relation[key]))
                }
            )
            metrics[f"{alias}.tuple"] = payload.argmin(order, where=violation.fill_null(False))
        summary = relation.aggregate(**metrics)
        return summary.select(
            *(alias for alias, _, _ in checks),
            **{
                # Some engines return default tuple fields for an empty argmin.
                f"{alias}.witness.{i}": summary[alias].ifelse(
                    summary[f"{alias}.tuple"][f"k{i}"], ibis.null()
                )
                for alias, _, keys in checks
                for i in range(len(keys))
            },
        )
    summary = relation.aggregate(**metrics)
    for alias, violation, keys in checks:
        if not keys:
            continue
        failed = relation.filter(violation.fill_null(False))
        witness = failed.order_by(*(failed[key].asc(nulls_first=False) for key in keys)).limit(1)
        witness = witness.select(
            **{f"{alias}.witness.{i}": witness[key] for i, key in enumerate(keys)}
        )
        summary = summary.left_join(witness, ibis.literal(True))
    return summary


def _population_check_summary(
    obligations: tuple[PopulationCheck, ...],
    provider: str,
    offset: int,
) -> tuple[ir.Table | None, tuple[tuple[str, NumericCheck], ...]]:
    """Summarize each checked scope once, outside the business relation."""
    checks: list[tuple[str, NumericCheck]] = []
    seen: set[tuple[ops.Node, str, str, str, tuple[str, ...]]] = set()
    grouped: dict[ops.Node, list[tuple[str, PopulationCheck]]] = {}
    for obligation in obligations:
        relation = obligation.relation.op()
        key = (relation, obligation.flag, obligation.expected, obligation.received, obligation.keys)
        if key in seen:
            continue
        alias = f"__population_check_{offset + len(seen)}"
        seen.add(key)
        grouped.setdefault(relation, []).append((alias, obligation))
    if not grouped:
        return None, ()
    summaries = []
    # Keep the original check order even when scopes are shared.
    ordered: dict[str, NumericCheck] = {}
    for batch in grouped.values():
        relation_expr = batch[0][1].relation
        summary = _check_summary(
            relation_expr,
            tuple((alias, relation_expr[check.flag], check.keys) for alias, check in batch),
            combined=query_policy(provider).supports_fused_arithmetic,
            provider=provider,
        )
        summaries.append(summary)
        for alias, check in batch:
            ordered[alias] = NumericCheck(summary[alias], check.expected, check.received)
    for index in range(offset, offset + len(ordered)):
        alias = f"__population_check_{index}"
        checks.append((alias, ordered[alias]))
    summary = summaries[0]
    for other in summaries[1:]:
        summary = summary.cross_join(other)
    return summary, tuple(checks)


def _attach_population_checks(
    expression: ir.Table,
    obligations: tuple[PopulationCheck, ...],
    provider: str,
    offset: int,
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    summary, checks = _population_check_summary(obligations, provider, offset)
    if summary is None:
        return expression, ()
    if "__numeric_present" not in expression.columns:
        expression = expression.mutate(__numeric_present=ibis.literal(True))
    return summary.left_join(expression, ibis.literal(True)), checks


def finish_population_checks(
    stage: SourceReadStage, read: LoweredRead, provider: str
) -> tuple[SourceReadStage, LoweredRead]:
    """Render deferred Population diagnostics for a standalone physical read."""
    if not read.population_checks:
        return stage, read
    expression, checks = _attach_population_checks(
        read.expression, read.population_checks, provider, len(read.numeric_checks)
    )
    return (
        replace(
            stage,
            preparation_checks=(
                *stage.preparation_checks,
                *((name, check.expected, check.received) for name, check in checks),
            ),
        ),
        replace(
            read,
            expression=expression,
            numeric_checks=(*read.numeric_checks, *checks),
            population_checks=(),
        ),
    )


def _source_check_summary(
    source: LoweredRead,
    source_stage: SourceReadStage,
    aliases: tuple[str, ...],
    prepared: PreparedSources | None,
    *,
    combined: bool,
    provider: str,
) -> ir.Table:
    direct: dict[str, tuple[str, ...]] = {}
    external: list[tuple[str, str, ir.Table, tuple[str, ...]]] = []
    for alias, (name, _check) in zip(aliases, source.numeric_checks, strict=True):
        scope = source.expression
        source_keys = source_stage.keys
        witness_keys = tuple(
            column for column in scope.columns if column.startswith(name + ".witness.")
        )
        if witness_keys:
            source_keys = tuple(
                sorted(witness_keys, key=lambda column: int(column.rsplit(".", 1)[1]))
            )
        elif any(name == item[0] for item in source_stage.preparation_checks):
            raw = (
                prepared.relations.get(
                    source.stage_id, prepared.relations.get(source_stage.source_id)
                )
                if prepared is not None and not source_stage.parameters
                else None
            )
            if raw is None or name not in raw.columns:
                continue
            projection = dict(read_projections(source_stage))
            reduction = read_reduction(source_stage)
            if reduction is not None:
                fields = dict(source_stage.field_columns)
                projection.update(
                    (slot, fields[path]) for slot, path in reduction.axes if path in fields
                )
            if any(key not in projection for key in source_keys):
                continue
            source_keys = tuple(projection[key] for key in source_keys)
            scope = raw
        if not source_keys:
            continue
        if scope.op() == source.expression.op():
            direct[alias] = source_keys
        else:
            external.append((alias, name, scope, source_keys))
    summary = _check_summary(
        source.expression,
        tuple(
            (alias, source.expression[name], direct.get(alias, ()))
            for alias, (name, _) in zip(aliases, source.numeric_checks, strict=True)
        ),
        combined=combined,
        provider=provider,
    )
    if combined:
        raw_groups: dict[ops.Relation, list[tuple[str, str, tuple[str, ...]]]] = {}
        for alias, name, scope, source_keys in external:
            raw_groups.setdefault(scope.op(), []).append((alias, name, source_keys))
        for raw_relation, raw_checks in raw_groups.items():
            raw_scope = raw_relation.to_expr()
            assert isinstance(raw_scope, ir.Table)
            witness = _check_summary(
                raw_scope,
                tuple((alias, raw_scope[name], keys) for alias, name, keys in raw_checks),
                combined=True,
                provider=provider,
            ).select(
                *(f"{alias}.witness.{i}" for alias, _, keys in raw_checks for i in range(len(keys)))
            )
            summary = summary.left_join(witness, ibis.literal(True))
    else:
        for alias, name, scope, source_keys in external:
            # Raw preparation evidence can belong to a different row domain.
            failed = scope.filter(scope[name].fill_null(False))
            witness = failed.order_by(
                *(failed[key].asc(nulls_first=False) for key in source_keys)
            ).limit(1)
            witness = witness.select(
                **{f"{alias}.witness.{i}": witness[key] for i, key in enumerate(source_keys)}
            )
            summary = summary.left_join(witness, ibis.literal(True))
    return summary


def _attach_relation_checks(
    expression: ir.Table,
    native: NativeQueryRelation,
    plan: QueryPlan,
    preparation: tuple[tuple[str, str, str], ...] = (),
    numeric: tuple[tuple[str, NumericCheck], ...] = (),
    *,
    prepared: PreparedSources | None = None,
    provider: str = "",
) -> tuple[ir.Table, tuple[tuple[str, str, str], ...], tuple[tuple[str, NumericCheck], ...]]:
    """Keep full parent checks visible even when the selected relation is empty."""
    prepared_checks = list(preparation)
    checks = list(numeric)
    if prepared is not None and native.sources:
        source_id = plan.source_read_stage(native.sources[0].stage_id).source_id
        provider = prepared.sources.get(source_id, prepared.source).provider.name
    # Other providers retain the qualified branch until actual execution and
    # cost evidence admits the tuple aggregate on their native carriers.
    combined = query_policy(provider).supports_fused_arithmetic
    for source in native.sources:
        if not source.numeric_checks:
            continue
        aliases = tuple(
            f"__query_preparation_{len(checks) + index}"
            for index in range(len(source.numeric_checks))
        )
        source_stage = plan.source_read_stage(source.stage_id)
        summary = _source_check_summary(
            source, source_stage, aliases, prepared, combined=combined, provider=provider
        )
        if "__numeric_present" not in expression.columns:
            expression = expression.mutate(__numeric_present=ibis.literal(True))
        expression = summary.left_join(expression, ibis.literal(True))
        for alias, (name, check) in zip(aliases, source.numeric_checks, strict=True):
            checks.append((alias, NumericCheck(summary[alias], check.expected, check.received)))
            if any(
                name == item[0]
                for item in plan.source_read_stage(source.stage_id).preparation_checks
            ):
                prepared_checks.append((alias, check.expected, check.received))
    grouped: dict[frozenset[ops.Relation], list[tuple[str, NumericCheck]]] = {}
    for index, check in enumerate(native.checks):
        alias = f"__query_numeric_{len(checks) + index}"
        grouped.setdefault(check.violation.op().relations, []).append((alias, check))
    for relations, batch in grouped.items():
        if len(relations) == 1:
            relation = next(iter(relations)).to_expr()
            assert isinstance(relation, ir.Table)
            keys = tuple(
                sorted(
                    (
                        column
                        for column in relation.columns
                        if column.startswith("key.") and column.removeprefix("key.").isdigit()
                    ),
                    key=lambda column: int(column.removeprefix("key.")),
                )
            )
            summary = _check_summary(
                relation,
                tuple((alias, check.violation, keys) for alias, check in batch),
                combined=combined,
                provider=provider,
            )
        else:
            # Scalar checks have no row relation; independent multi-relation
            # scopes cannot be combined into an inferred common relation.
            summary = batch[0][1].violation.any().fill_null(False).name(batch[0][0]).as_table()
            for alias, check in batch[1:]:
                other = check.violation.any().fill_null(False).name(alias).as_table()
                summary = summary.cross_join(other)
        if "__numeric_present" not in expression.columns:
            expression = expression.mutate(__numeric_present=ibis.literal(True))
        expression = summary.left_join(expression, ibis.literal(True))
        checks.extend(
            (alias, NumericCheck(summary[alias], check.expected, check.received))
            for alias, check in batch
        )
    expression, population_checks = _attach_population_checks(
        expression,
        tuple(check for source in native.sources for check in source.population_checks),
        provider,
        len(checks),
    )
    prepared_checks.extend(
        (name, check.expected, check.received) for name, check in population_checks
    )
    checks.extend(population_checks)
    return expression, tuple(prepared_checks), tuple(checks)


def finish_native_query(finish: NativeQueryFinish, frame: pd.DataFrame) -> pd.DataFrame:
    """Restore deterministic union order after the qualified source read."""
    if finish.frame_top is not None:
        for name, expected, received in finish.frame_top.integrity_checks:
            if frame[name].astype("boolean").fillna(False).any():
                raise MaterializationError(
                    expected=expected,
                    received=received,
                    repair="Correct the source field type or stage layout; Marivo does not cast lossy values back.",
                    stage="stage_output",
                    help_target="analysis_frame.compute",
                )
        frame = frame.loc[frame[finish.frame_top.presence].astype("boolean").fillna(False)]
    if finish.order:
        frame = frame.sort_values(list(finish.order), kind="stable", na_position="last")
    return frame.loc[:, [column.slot_id for column in finish.columns]].reset_index(drop=True)
