"""Qualify physical source carriers against their provider authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NoReturn

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo._kernel.validation import is_instance
from marivo.analysis.compiler.query_stages import (
    read_components,
    read_reduction,
    require_components,
    require_reduction,
)

if TYPE_CHECKING:
    from marivo.analysis.compiler.query_stages import QueryPlan

from marivo._definition.expressions import (
    EntityCount,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo.analysis.compiler.attribute_inputs import (
    input_failure,
)
from marivo.analysis.compiler.query_stages import (
    SourceReadStage,
)
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.datasource.spi import (
    BoundSource,
    SourceSession,
    TableSourceIR,
    physical_fields,
)
from marivo.semantic.spi import (
    lower_row,
)


def _original_input_fact(
    prepared: PreparedSources, owner: SourceSession, bound: BoundSource, leaf: ops.Node
) -> Literal["qualified", "other", "unavailable"]:
    key = (owner, leaf)
    if key not in prepared.original_input_qualifications:
        try:
            qualified = owner.string_facts.original_input_qualified(
                bound, leaf, owner.table_join_issue
            )
            prepared.original_input_qualifications[key] = "qualified" if qualified else "other"
        except Exception:
            prepared.original_input_qualifications[key] = "unavailable"
    return prepared.original_input_qualifications[key]


def qualify_original_strings(
    prepared: PreparedSources, table: ir.Table, columns: tuple[str, ...]
) -> str | None:
    """Qualify remote strings, retaining MySQL's native comparison semantics."""
    for column in columns:
        value = table[column]
        if not value.type().is_string():
            continue
        fields = physical_fields(value)
        if not fields:
            return "original string coordinate has no qualified physical collation"
        for physical_field in fields:
            matched = False
            for path, bound in prepared.bound.items():
                if physical_field.rel != bound.relation.op():
                    continue
                matched = True
                owner = prepared.sources.get(path, prepared.source)
                if owner.provider.query_policy.key_fusion not in (
                    "none",
                    "local",
                ) and not isinstance(bound.source, TableSourceIR):
                    return "Remote original strings require a physical table collation"
                key = (owner, bound.relation.op(), physical_field.name)
                if key not in prepared.collation_qualifications:
                    prepared.collation_qualifications[key] = (
                        owner.string_facts.original_string_issue(bound, physical_field.name)
                    )
                issue = prepared.collation_qualifications[key]
                if issue is not None:
                    return issue
            if not matched:
                return "original string coordinate has an unqualified physical source"
    return None


def _qualify_local_original(
    prepared: PreparedSources, owner: SourceSession, raw: ir.Table
) -> str | None:
    physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
    leaves = set(raw.op().find(physical))
    qualified: set[ops.Node] = set()
    for path, bound in prepared.bound.items():
        if prepared.sources.get(path, prepared.source) is not owner:
            continue
        for leaf in bound.relation.op().find(physical):
            if leaf not in leaves:
                continue
            fact = _original_input_fact(prepared, owner, bound, leaf)
            if fact == "unavailable":
                return "original recomputation requires visible input collation metadata"
            if fact != "qualified":
                return "original recomputation retains an explicit, default or unknown inherited collation boundary"
            qualified.add(leaf)
    if not leaves or not leaves <= qualified:
        return "original recomputation requires typed physical inputs from one source owner"
    return None


def _physical_value(
    prepared: PreparedSources,
    owner: SourceSession,
    value: ir.Value,
    *,
    role: Literal["identity", "recipe"],
) -> bool:
    fields = physical_fields(value)
    if len(fields) != 1:
        return False
    field = fields[0]
    for path, bound in prepared.bound.items():
        if (
            prepared.sources.get(path, prepared.source) is not owner
            or field.rel != bound.relation.op()
        ):
            continue
        if not isinstance(bound.source, TableSourceIR):
            return False
        if _original_input_fact(prepared, owner, bound, field.rel) != "qualified":
            return False
        physical = bound.facts.schema.field(field.name)
        if role == "identity":
            # Semantic primary_key owns identity integrity; nullable source
            # metadata does not require an additional source-data preflight.
            return bool(
                pa.types.is_signed_integer(physical.type)
                or pa.types.is_string(physical.type)
                or physical.type == pa.date32()
            )
        # Each recipe field has already passed source type admission. Its
        # numeric/result and control types are owned by the closed lowerer.
        return True
    return False


def qualify_original_source(
    prepared: PreparedSources,
    reads: tuple[SourceReadStage, ...],
    raw: ir.Table,
    *,
    sampled: bool = False,
    attribute: bool = False,
) -> str | None:
    """Qualify typed numeric recipes independently of complete identity carriers."""
    if not reads:
        return "original fiber has no source reads"
    owner = prepared.sources.get(reads[0].source_id, prepared.source)
    if owner.provider.query_policy.key_fusion == "local":
        return _qualify_local_original(prepared, owner, raw)
    if (
        owner.provider.query_policy.key_fusion == "none"
        or not owner.uses_native_provider()
        or owner.datasource.backend_type != owner.provider.name
    ):
        return "original fiber has no qualified physical backend route"

    if owner.provider.table_join_issue is not None:
        for bound in prepared.bound.values():
            if bound.relation.op() not in raw.op().find(ops.DatabaseTable):
                continue
            try:
                issue = owner.table_join_issue(bound)
            except Exception:
                return "ClickHouse original inputs require visible system.tables topology metadata"
            if issue is not None:
                return issue

    coordinate_columns: list[str] = []
    for read in reads:
        if read_components(read) is not None:
            coordinate_columns.extend(name for _, name in require_components(read).identity)
        if read_reduction(read) is not None:
            coordinate_columns.extend(
                dict(read.field_columns)[path] for _, path in require_reduction(read).axes
            )
        coordinate_columns.extend(
            name for _, name in read.field_columns if raw[name].type().is_string()
        )
    issue = qualify_original_strings(
        prepared,
        raw,
        tuple(dict.fromkeys(name for name in coordinate_columns if raw[name].type().is_string())),
    )
    if issue is not None:
        return issue
    for read in reads:
        issue = _qualify_original_read(
            prepared, owner, read, raw, sampled=sampled, attribute=attribute
        )
        if issue is not None:
            return issue
    return None


def _qualify_original_read(
    prepared: PreparedSources,
    owner: SourceSession,
    read: SourceReadStage,
    raw: ir.Table,
    *,
    sampled: bool,
    attribute: bool,
) -> str | None:
    reduction = read_reduction(read)
    if (
        reduction is None
        or read_components(read) is None
        or (not read.binding_id and not attribute)
        or (reduction.duration is not None and not attribute)
        or (
            not sampled
            and not attribute
            and (
                reduction.calendar is not None
                or reduction.calendars
                or reduction.sampled
                or reduction.preserve_sample_presence
                or reduction.window_key is not None
            )
        )
        or prepared.sources.get(read.source_id, prepared.source) is not owner
    ):
        return "Remote original fiber lacks an exact prepared physical binding"
    fields = dict(read.field_columns)
    if not attribute and any(
        not raw[fields[path]].type().is_string() for _, path in reduction.axes
    ):
        return "Remote original numeric coordinates retain their pandas key boundary"
    if any(
        not _physical_value(prepared, owner, raw[name], role="identity")
        for _, name in require_components(read).identity
    ):
        return "Remote original identity requires direct physical integer, string or date32 keys"
    node = reduction.node
    if isinstance(node, EntityCount):
        return None
    operands: tuple[RowNode, ...]
    if isinstance(node, SourceWeightedMean):
        operands = (node.value, node.weight)
    elif is_instance(node, SourceAggregate) and (
        node.reducer
        in (
            "sum",
            "count",
            "count_distinct",
            "min",
            "max",
            "median",
            "quantile",
        )
        or node.reducer == "mean"
    ):
        operands = (node.operand,)
    else:
        return "Remote original method retains its established host numeric finisher"
    for operand in operands:
        value = lower_row(operand, {path: raw[name] for path, name in read.field_columns}).value
        if not value.type().is_numeric():
            return "Remote original recipes require a numeric result"
        origins = physical_fields(value)
        if not origins or any(
            not _physical_value(prepared, owner, field.to_expr(), role="recipe")
            for field in origins
        ):
            return "Remote original recipes require typed physical inputs from one source owner"
    return None


def qualify_attribute_game_sources(prepared: PreparedSources, plan: QueryPlan) -> None:
    """Reuse native physical-input and collation qualification before capture."""
    for path, bound in prepared.bound.items():
        owner = prepared.sources.get(path, prepared.source)
        if not isinstance(bound.source, TableSourceIR):
            input_failure(
                "unqualified_contribution_route", "replacement states require native tables"
            )
        if owner.provider.table_join_issue is not None:
            try:
                issue = owner.table_join_issue(bound)
            except Exception:
                input_failure(
                    "unqualified_contribution_route",
                    "ClickHouse replacement capture requires visible system.tables topology metadata",
                )
            if issue is not None:
                input_failure("unqualified_contribution_route", issue)
        physical = tuple(bound.relation.op().find(ops.DatabaseTable))
        if not physical or any(
            _original_input_fact(prepared, owner, bound, leaf) != "qualified" for leaf in physical
        ):
            input_failure(
                "unqualified_contribution_route",
                f"{owner.provider.name}: replacement capture lacks qualified physical input/collation metadata",
            )
    for stage in plan.stages:
        if not isinstance(stage, SourceReadStage):
            continue
        relation = prepared.relations.get(stage.stage_id)
        if relation is None:
            relation = prepared.relations[stage.source_id]
        string_columns = tuple(
            name for _, name in stage.field_columns if relation[name].type().is_string()
        )
        issue = qualify_original_strings(prepared, relation, string_columns)
        if issue is not None:
            input_failure("unqualified_contribution_route", issue)


def _route_gap(expected: str, received: str, repair: str) -> NoReturn:
    raise AnalysisStageUnavailableError(
        stage="query_execution",
        expected=expected,
        received=received,
        repair=f"{repair} No result was completed or published.",
        target="analysis_frame.compute",
    )
