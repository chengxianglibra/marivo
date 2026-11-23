"""Run qualified reads and complete checked source outputs."""

from __future__ import annotations

from dataclasses import replace
from decimal import DecimalException
from typing import TYPE_CHECKING

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
from ibis.common.exceptions import IbisError

from marivo._kernel.errors import MarivoError
from marivo.analysis.compiler.query_stages import (
    AttributeFrequencyRead,
    ReductionRead,
    read_attribute,
    read_members,
    read_reduction,
    require_attribute,
    require_reduction,
)

if TYPE_CHECKING:
    from marivo.analysis.compiler.query_stages import AttributeRootLayout

from marivo._definition.expressions import (
    SourceAggregate,
)
from marivo.analysis.compiler.attribute_inputs import (
    input_failure,
    original_input_slot,
)
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_stages import (
    SourceReadStage,
    read_native_reductions,
)
from marivo.analysis.materialization import (
    query_parameters,
    source_completion,
    temporal_sql,
)
from marivo.analysis.materialization.graph_preflight import (
    relation_operations,
)
from marivo.analysis.materialization.native_query_checks import (
    finish_population_checks,
)
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_components import (
    complete_attribute_inputs,
)
from marivo.analysis.materialization.source_qualification import _route_gap
from marivo.analysis.methods.carriers.stages import StageOutput, source_columns
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import CheckReceipt
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.datasource.spi import (
    PhysicalRequirement,
    QualifiedSource,
    SourceSession,
    TableSourceIR,
)
from marivo.semantic.spi import (
    NumericCheck,
)


def _qualify_query_read(
    prepared: PreparedSources, stage: SourceReadStage, lowered: LoweredRead
) -> tuple[QualifiedSource, ...]:
    expression = lowered.expression
    source = prepared.sources.get(stage.source_id, prepared.source)
    relations = set(expression.op().find(ops.Relation))
    return tuple(
        source.qualify(
            bound, PhysicalRequirement("query.source_read", 1, relation_operations(expression))
        )
        for path, bound in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is source
        and bound.relation.op() in relations
    )


def qualify_query_sources(prepared: PreparedSources, lowered: LoweredQuery) -> None:
    """Qualify every schema-bound read template before a Run is admitted."""
    for read in lowered.reads:
        stage = lowered.plan.source_read_stage(read.stage_id)
        source = prepared.sources.get(stage.source_id, prepared.source)
        stage, read = finish_population_checks(stage, read, source.provider.name)
        expression = read.expression
        for parameter in stage.parameters:
            empty = StageOutput.from_frame(
                pd.DataFrame(columns=[column.slot_id for column in parameter.columns]),
                parameter.columns,
                tuple(column.slot_id for column in parameter.columns),
            )
            expression = query_parameters.instantiate(
                expression,
                parameter.placeholder,
                parameter.columns,
                empty,
                provider=prepared.sources.get(stage.source_id, prepared.source).provider.name,
                source=prepared.sources.get(stage.source_id, prepared.source),
            )
        read = replace(read, expression=expression)
        _admit_numeric_method(prepared.sources.get(stage.source_id, prepared.source), stage, read)
        read = replace(read, expression=_native_query_expression(prepared, stage, read))
        expression = read.expression
        qualified = _qualify_query_read(prepared, stage, read)
        source = prepared.sources.get(stage.source_id, prepared.source)
        _qualify_join_topology(source, qualified)
        try:
            issued = source.compile(
                qualified,
                expression,
                purpose="query.prepare",
                expected_schema=expression.schema().to_pyarrow(),
            )
        except (IbisError, NotImplementedError, TypeError, ValueError) as error:
            _route_gap(
                "a natively compiled Query expression for the selected backend and types",
                f"{source.provider.name}: {type(error).__name__}: {error}",
                "Use a supported native method/type or export an admitted Query for pandas processing.",
            )
        # Parameterized templates qualify with empty inputs; their executed SQL differs.
        if not stage.parameters:
            prepared.compiled_reads[(source, expression.op())] = issued


def _qualify_join_topology(source: SourceSession, qualified: tuple[QualifiedSource, ...]) -> None:
    """Qualify physical ClickHouse join topology without reading business rows."""
    if source.provider.table_join_issue is None or not any(
        "join" in item.requirement.operations for item in qualified
    ):
        return
    for item in qualified:
        physical = item.binding.source
        if not isinstance(physical, TableSourceIR):
            _route_gap(
                "a known ClickHouse physical source profile for this join",
                type(physical).__name__,
                "Use a source with an explicitly qualified physical join topology.",
            )
        try:
            engine = source.table_engine(item.binding)
        except Exception as error:
            _route_gap(
                "visible ClickHouse physical metadata for this join",
                f"{item.binding.facts.source_identity}: {type(error).__name__}",
                "Grant the reader access to the source's system.tables metadata or use a qualified join source.",
            )
        if engine is None:
            _route_gap(
                "a known ClickHouse physical table profile for this join",
                item.binding.facts.source_identity,
                "Use a source whose table profile is visible to the reader.",
            )
        try:
            issue = source.table_join_issue(item.binding)
        except Exception as error:
            _route_gap(
                "visible ClickHouse physical topology metadata for this join",
                f"{physical.table}: {type(error).__name__}",
                "Grant access to system.tables metadata for the declared source.",
            )
        if issue is not None:
            _route_gap(
                "a qualified ClickHouse Distributed join route"
                if engine == "Distributed"
                else "a qualified ClickHouse physical join topology",
                issue,
                "Use a MergeTree or Distributed source. Marivo compiles GLOBAL IN/JOIN without changing distributed_product_mode or substituting local tables.",
            )


def _admit_numeric_method(source: SourceSession, stage: SourceReadStage, read: LoweredRead) -> None:
    if not source.provider.query_policy.requires_reducer_admission:
        return
    reductions = (
        *(
            require_reduction(stage)
            for stage in read_members(stage) or (stage,)
            if read_reduction(stage) is not None
        ),
        *read_native_reductions(stage),
    )
    for reduction in reductions:
        node = reduction.node
        if isinstance(node, SourceAggregate) and node.reducer not in (
            "count",
            "count_distinct",
            "sum",
            "min",
            "max",
            "mean",
        ):
            if source.provider.dialect.aggregates.select(node.reducer) is not None:
                continue
            _route_gap(
                "a qualified native algorithm for this source reduction",
                f"{source.provider.name}: {node.reducer}",
                "Use an admitted native reduction, or export its input for pandas. Exact and approximate algorithms are not interchangeable.",
            )


def _native_query_expression(
    prepared: PreparedSources, stage: SourceReadStage, read: LoweredRead
) -> ir.Table:
    source = prepared.sources.get(stage.source_id, prepared.source)
    # Qualification and execution consume the same immutable tree. Stage IDs
    # are insufficient: bound parameters can change the expression within a stage.
    key = (source.provider.name, read.expression.op())
    cached = prepared.native_expressions.get(key)
    if cached is not None:
        return cached
    expression = temporal_sql.lower_temporal(read.expression, source.provider.name)
    assert isinstance(expression, ir.Table)
    expression = source.provider.dialect.lower_query_numeric(expression)
    prepared.native_expressions[key] = expression
    return expression


def _actual_attribute_layout(
    layout: AttributeRootLayout, frame: pd.DataFrame
) -> AttributeRootLayout:
    """Capture returned native physical columns before Attribute normalization."""
    return replace(
        layout,
        columns=source_columns(
            frame.loc[frame[layout.kind] == (1 if layout.scope == "parent" else 0)],
            layout.columns,
            layout.keys,
        ),
    )


def read_stage(
    prepared: PreparedSources,
    stage: SourceReadStage,
    lowered: LoweredRead,
    *,
    value_keys: tuple[str, ...] = (),
) -> StageOutput:
    """Submit one lowered SourceReadStage and normalize its pandas result."""
    if read_attribute(stage) is not None and require_attribute(stage).parent_context is None:
        input_failure(
            "missing_component_proof",
            "the source read lacks its checked original-parent association",
        )
    source = prepared.sources.get(stage.source_id, prepared.source)
    stage, lowered = finish_population_checks(stage, lowered, source.provider.name)
    expression = _native_query_expression(prepared, stage, lowered)
    lowered = replace(lowered, expression=expression)
    purpose = f"query.{stage.stage_id}"
    qualified_read = prepared.compiled_reads.get((source, expression.op()))
    if qualified_read is not None:
        # The qualified SQL is final; only the execution purpose differs.
        issued = source.reissue(qualified_read, purpose=purpose)
    else:
        issued = source.compile(
            _qualify_query_read(prepared, stage, lowered),
            expression,
            purpose=purpose,
            expected_schema=expression.schema().to_pyarrow(),
        )
    source.verify_query_timezones(issued.sql)

    try:
        frame = source.execute(issued)
    except (TypeError, ValueError, OverflowError, DecimalException) as error:
        raise MaterializationError(
            expected="native Ibis pandas values compatible with the compiled expression types",
            received=f"{source.provider.name}: {type(error).__name__}: {error}",
            repair="Inspect the native Ibis conversion error and result dtype. If a wider numeric carrier is intended, author .cast(ibis.dtype('decimal(38,0)')) in the Entity expression before reduction, or rescale inputs to the intended unit. Retry frame.compute(); Marivo does not widen or switch kernels automatically.",
            stage="source_conversion",
            help_target="analysis_frame.compute",
        ) from error
    except Exception as error:
        if isinstance(error, MarivoError):
            raise
        raise MaterializationError(
            expected="successful native source execution for the compiled expression",
            received=f"{source.provider.name}: {type(error).__name__}: {error}",
            repair="Inspect the native backend failure and correct the input range or expression. If a wider carrier is intended, author .cast(ibis.dtype('decimal(38,0)')) in the Entity expression before reduction, or rescale inputs to the intended unit. Decimal transport supports precision <=38. Retry frame.compute(); source failures do not switch execution paths.",
            stage="source_execution",
            help_target="analysis_frame.compute",
        ) from error
    if isinstance(stage.program, AttributeFrequencyRead):
        return source_completion._capture_attribute_state(
            stage,
            frame,
            source_completion.complete_attribute_frequencies(stage, frame, lowered.numeric_checks),
            source.provider.name,
        )
    if read_attribute(stage) is not None:
        return _complete_attribute_read(stage, frame, lowered.numeric_checks)
    if read_members(stage):
        output = source_completion._complete_common_read(stage, frame, lowered.numeric_checks)
    else:
        output = source_completion._complete_source_frame(
            stage, frame, lowered.numeric_checks, value_keys=value_keys
        )
    return source_completion._capture_attribute_state(stage, frame, output, source.provider.name)


def _complete_attribute_read(
    stage: SourceReadStage,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> StageOutput:
    source_completion._validate_source_checks(stage, frame, numeric_checks)
    layout = require_attribute(stage)
    originals: dict[tuple[int, str], StageOutput] = {}
    for kind, keys in (
        ((1, layout.parent_keys),) if layout.scope == "parent" else ((0, layout.keys),)
    ):
        selected = frame.loc[frame[layout.kind] == kind].reset_index(drop=True)
        for leaf in layout.leaves:
            selected = frame.loc[
                (frame[layout.kind] == kind) & (frame[layout.fiber] == leaf.fiber)
            ].reset_index(drop=True)
            reduction = read_reduction(leaf.read)
            assert reduction is not None
            prefix = original_input_slot(kind, leaf.read.occurrence, "")
            names = {
                name: name.removeprefix(prefix)
                for name in selected.columns
                if name.startswith(prefix)
            }
            names.update({name: name for name in (*keys, reduction.value_slot)})
            raw = selected.loc[:, list(names)].rename(columns=names)
            original_columns = tuple(
                column
                for column in leaf.read.columns
                if column.slot_id not in leaf.read.keys or column.slot_id in keys
            )
            original_stage = replace(
                leaf.read,
                keys=keys,
                columns=original_columns,
                program=ReductionRead(require_reduction(leaf.read)),
                # The original value is the actual aggregate for this
                # selected kind and fiber, grouped at these exact keys.
                unique_keys=UniqueKeys(
                    tuple(column.slot_id for column in original_columns), (keys,)
                ),
            )
            checks = tuple(
                (name.removeprefix(prefix), check)
                for name, check in numeric_checks
                if name.startswith(prefix)
            )
            completed = source_completion._complete_source_frame(original_stage, raw, checks)
            originals[(kind, reduction.value_slot)] = completed
    # Formula originals are actual native outputs of this same statement.
    # Capture their physical carriers before the root normalizer consumes
    # any conservative compiler metadata.
    layout = _actual_attribute_layout(layout, frame)
    fine, inputs = complete_attribute_inputs(layout, stage.stage_id, frame, originals)
    inputs = replace(
        inputs,
        receipts=(
            *(CheckReceipt(name, stage.stage_id) for name, _ in numeric_checks),
            *inputs.receipts,
        ),
    )
    return StageOutput.from_frame(
        fine,
        source_columns(fine, stage.columns, stage.keys),
        stage.keys,
        receipts=inputs.receipts,
        attribute_inputs=(inputs,),
        source_rows=len(frame),
        unique_keys=(
            inputs.parent_unique_keys if layout.scope == "parent" else inputs.fine_unique_keys
        ),
    )
