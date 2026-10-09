"""R4.3 private source execution through issued R1 reads and Arrow exchange."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.compiler.cell_lowering import lower_cells
from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredLocal,
    LoweredPlan,
    LoweredRelation,
    SemanticCheck,
    TemporalCheck,
)
from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.cell_encoding import CellBook, CellEncoding, CellFields, EncodedCell
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import Defined, DerivedQuantity, ReferenceStatePart
from marivo.analysis.core.rules import (
    AssociationFit,
    AssociationRead,
    AttributionDerive,
    BindProject,
    CellDerive,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    OccurrencePrepare,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.cell_arrow import column as cell_column
from marivo.analysis.materialization.cell_arrow import (
    from_rows,
    project,
    rename,
    required,
    rows,
    schema_binding,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CheckedStream,
    CompletedCheck,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_spearman_execution import finish_spearman
from marivo.analysis.methods.comparison import evaluate as evaluate_comparison
from marivo.analysis.methods.comparison import propagated_error
from marivo.analysis.methods.registry import REGISTRY
from marivo.datasource.adapters import CompiledRead, SourceSession


def _split_transport(
    stage: LoweredRelation, table: pa.Table
) -> tuple[pa.Table, tuple[ExchangePart, ...]]:
    from marivo.analysis.compiler.coordinate_state import physical

    if "__marivo_coordinate_row" in table.column_names:
        from marivo.analysis.core.model import CoordinateStatePart, part_role
        from marivo.analysis.methods.coordinate_state import retain

        keys = tuple(key.column for key in stage.layout.keys)
        positions: dict[tuple[object, ...], int] = {}
        for index, row in enumerate(
            table.select(keys).to_pylist() if keys else [{} for _ in range(len(table))]
        ):
            positions.setdefault(tuple(row[key] for key in keys), index)
        primary = project(
            table.take(pa.array(list(positions.values()), type=pa.int64())), stage.layout.columns
        )
        part = next(p for p in stage.node.signature.parts if isinstance(p, CoordinateStatePart))
        actual = table.filter(table["__marivo_coordinate_row"])
        full = pa.Table.from_arrays(
            [
                *(actual[k] for k in keys),
                *(actual["__coordinate__" + name] for name in (*part.columns, *part.components)),
            ],
            names=[*keys, *part.columns, *part.components],
        )
        return primary, (
            ExchangePart(
                part_role(part), retain(stage.node.signature, part_role(part), full, primary)
            ),
        )

    children = physical(stage)
    kinds = table["__marivo_row_kind"]
    if kinds.null_count or any(
        value not in range(len(children) + 1) for value in kinds.to_pylist()
    ):
        raise _invalid("terminal transport contains an absent or unknown row kind")
    result: list[pa.Table] = []
    for index, expression in enumerate((stage.expression, *(value for _, value in children))):
        selected = table.filter(pc.equal(table["__marivo_row_kind"], index))
        names = tuple(expression.columns)
        result.append(
            rename(project(selected, tuple(f"__part_{index}__{name}" for name in names)), names)
        )
    return result[0], tuple(
        ExchangePart(role, value) for (role, _), value in zip(children, result[1:], strict=True)
    )


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one admitted and qualified source graph with completed checks",
        received=received,
        repair="Use the exact lowered plan and selected R1 source session.",
        stage="graph_source",
    )


def _issue(
    source: SourceSession,
    lowered: LoweredPlan,
    expression: ir.Table,
    *,
    purpose: str,
    replacements: dict[ops.Node, ops.Node],
) -> CompiledRead:
    bindings = lowered.sources_for(expression)
    if not bindings:
        raise _invalid("source expression has no exact R1 binding")
    qualified = tuple(source.qualify(item.source, lowered.source_requirement) for item in bindings)
    stage_policies = {
        item.expression.op(): item.cell_reasons
        for item in lowered.stages
        if isinstance(item, LoweredRelation)
        and any(
            getattr(stage, "operation", None) == "prepare" and stage.output == item.output
            for stage in lowered.admitted.stages
        )
    }
    raw_cells: dict[ops.Relation, tuple[CellEncoding, ...]] = {}
    for item in lowered.bindings:
        cell = item.layout.cell
        if cell is not None:
            fields = CellFields(cell.value, cell.tag, cell.reason)
            encoded = EncodedCell(
                fields, CellBook.from_reasons(item.cell_reasons), cell.tag + "__state"
            )
            relation = item.source.relation.op()
            previous = raw_cells.get(relation, ())
            if not any(old.fields == fields for old in previous):
                raw_cells[relation] = (*previous, encoded)
    compact = lower_cells(expression, raw_cells=raw_cells, stage_policies=stage_policies)
    physical_replacements: dict[ops.Node, ops.Node] = {}
    for original, replacement in replacements.items():
        if not isinstance(original, ops.Relation) or not isinstance(replacement, ops.Relation):
            raise _invalid("staged replacement lacks an exact relation")
        emitted = lower_cells(
            original.to_expr(), raw_cells=raw_cells, stage_policies=stage_policies
        ).expression
        staged = replacement.to_expr()
        # Staged backends may widen integers or forget SQL nullability.
        staged = staged.mutate(
            **{
                name: staged[name].cast(dtype)
                for name, dtype in emitted.schema().items()
                if staged[name].type() != dtype
            }
        )
        physical_replacements[emitted.op()] = staged.op()
    rewritten = (
        compact.expression.op().replace(physical_replacements).to_expr()
        if physical_replacements
        else compact.expression
    )
    if source.provider.name == "sqlite":
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        rewritten = lower_temporal(rewritten, "sqlite")
    if not isinstance(rewritten, ir.Table):
        raise _invalid("rewritten stage is not an Ibis table")
    schema = schema_binding(
        compact.expression.schema().to_pyarrow(), compact.cells, compact.logical_columns
    )
    if not rewritten.schema().to_pyarrow().equals(schema, check_metadata=False):
        differences = tuple(
            (name, str(dtype), str(rewritten.schema().get(name)))
            for name, dtype in compact.expression.schema().items()
            if rewritten.schema().get(name) != dtype
        )
        raise _invalid(f"staged relation changes emitted field types: {differences}")
    return source.compile(qualified, rewritten, purpose=purpose, expected_schema=schema)


def _read(
    source: SourceSession,
    lowered: LoweredPlan,
    expression: ir.Table,
    *,
    purpose: str,
    replacements: dict[ops.Node, ops.Node],
    keys: tuple[str, ...] = (),
    validate_cells: bool = False,
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> pa.Table:
    issued = _issue(source, lowered, expression, purpose=purpose, replacements=replacements)
    schema = issued.schema
    stream = CheckedStream(
        source.batches(issued, chunk_size=1024),
        schema,
        keys,
        cell_reasons,
        validate_cells=validate_cells,
    )
    try:
        table = pa.Table.from_batches(tuple(stream), schema=schema)
    finally:
        stream.close()
    if not stream.completed:
        raise _invalid("source stream was not exhausted")
    return table


def _check(
    source: SourceSession,
    lowered: LoweredPlan,
    check: IntegrityCheck | SemanticCheck | TemporalCheck,
    replacements: dict[ops.Node, ops.Node],
) -> CompletedCheck | None:
    table = _read(
        source,
        lowered,
        check.violations,
        purpose="analysis.graph.check",
        replacements=replacements,
        validate_cells=False,
    )
    if isinstance(check, TemporalCheck):
        from datetime import date, datetime, timezone

        from marivo.analysis.core.time_grid import instant
        from marivo.analysis.materialization.execute_deadline import check as check_deadline

        for row in table.to_pylist():
            check_deadline()
            raw, actual = row["raw_time"], row["normalized_time"]
            if raw is None and actual is None:
                continue
            from marivo.semantic.ir import StrptimeParse

            if isinstance(raw, str) and isinstance(check.axis.parse, StrptimeParse):
                try:
                    raw = datetime.strptime(raw, check.axis.parse.format)
                except ValueError as error:
                    raise _invalid(
                        "source value does not match the declared temporal format"
                    ) from error
            if isinstance(raw, datetime):
                read_zone = check.axis.timezone or "UTC"
                try:
                    expected_time: date = instant(raw, read_zone).replace(tzinfo=None)
                except DatasetConstructionError as error:
                    raise MaterializationError(
                        expected=f"one unique native instant for {check.axis.ref.path} in {read_zone}",
                        received=f"invalid or ambiguous wall timestamp {raw.isoformat()}",
                        repair=f"Correct the stored {check.axis.ref.path} wall timestamps using {read_zone}, or store timestamp values with explicit UTC offsets.",
                        stage="source_time_validation",
                    ) from error
                if isinstance(actual, datetime) and actual.tzinfo is not None:
                    actual = actual.astimezone(timezone.utc).replace(tzinfo=None)
            elif isinstance(raw, date):
                expected_time = raw
            else:
                raise _invalid("unqualified temporal source representation")
            if actual != expected_time:
                raise _invalid("source engine timezone rules differ from frozen temporal authority")
        return None
    if table.num_rows:
        expected = (
            check.expected
            if isinstance(check, IntegrityCheck)
            else str(check.requirement.obligation.fact.kind)
        )
        repair = "Correct the exact source rows or choose a qualified input."
        if isinstance(check, SemanticCheck):
            fact = check.requirement.obligation.fact
            owner = next(
                (
                    item.node if isinstance(item, LoweredRelation) else item.stage.node
                    for item in lowered.stages
                    if isinstance(item, (LoweredRelation, LoweredLocal))
                    and (
                        item.node if isinstance(item, LoweredRelation) else item.stage.node
                    ).identity
                    == check.requirement.node_id
                ),
                None,
            )
            if fact.subject_id.startswith("buckets:"):
                expected += ": complete original period buckets without renumbering"
                repair = (
                    "Use the complete captured period grid; retain its original bucket coordinates."
                )
            elif fact.subject_id.startswith("coverage:"):
                expected += ": complete original observation state coverage"
                repair = "Use original observations with complete retained state before applying Metric empty finish."
            elif fact.subject_id.startswith("fold_alignment:"):
                expected += ": aligned pre-fold sampling coordinates"
                repair = (
                    "Keep separate spatial groups unless their captured sampling coordinates align."
                )
            elif (
                fact.kind == "key_set_equal"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, CellDerive)
            ):
                repair = "Use corresponding complete inputs, or explicitly assume equality with mv.ExactKeys(verification='assume') when this exact call guarantees it."
            elif (
                fact.kind == "mapping_total"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, BindProject)
            ):
                repair = "Provide a matching owner row for every consumed key; use match_verification='assume' only when this exact read guarantees matching."
            elif (
                fact.kind == "cell_policy"
                and isinstance(owner, MethodNode)
                and isinstance(owner.parameters, BindProject)
            ):
                expected += f": a valid scalar Cell for {owner.parameters.ref.path} on each consumed complete key"
                repair = f"Correct missing or invalid source values for {owner.parameters.ref.path}; classification coordinates require Defined scalar values for every consumed complete key."
        if expected.startswith("r7."):
            from marivo.analysis.core.domain_captures import DomainPreparationError

            raise DomainPreparationError(
                expected.split(":", 1)[0],
                "prepare",
                expected,
                f"{table.num_rows} violating captured rows",
                "Correct the named occurrence keys, participant or version before retrying.",
            )
        raise MaterializationError(
            expected=expected,
            received=f"{table.num_rows} violating rows",
            repair=repair,
            stage="graph_check",
        )
    if isinstance(check, SemanticCheck):
        digest = hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest()
        return CompletedCheck(check.requirement, digest, check.consumers)
    return None


def _ordered_checks(
    completed: list[CompletedCheck], pending: tuple[CheckRequirement, ...]
) -> tuple[CompletedCheck, ...]:
    """Freeze all origin-group checks in the plan's order, independent of deadline order."""
    if any(proof.requirement not in pending for proof in completed):
        raise _invalid("completed source check has no admitted origin")
    ordered: list[CompletedCheck] = []
    for requirement in pending:
        proofs = tuple(
            proof
            for proof in completed
            if proof.requirement == requirement or requirement in proof.consumers
        )
        if not proofs:
            raise _invalid("source check has no completed evidence")
        combined = hashlib.sha256(
            "".join(proof.result_digest for proof in proofs).encode()
        ).hexdigest()
        ordered.append(CompletedCheck(requirement, combined))
    return tuple(ordered)


def _result(
    stage: LoweredRelation,
    table: pa.Table,
    completed: tuple[CompletedCheck, ...],
    pending: tuple[CheckRequirement, ...],
    retained_parts: tuple[ExchangePart, ...] = (),
) -> ExchangeResult:
    if not isinstance(stage.node, MethodNode):
        raise _invalid("root must be a registered method node")
    if "original_state__samples" in table.column_names:
        from marivo.analysis.methods.temporal_fold import decode_samples, encode_samples

        try:
            canonical_samples = [
                encode_samples(decode_samples(value))
                for value in table["original_state__samples"].to_pylist()
            ]
        except (ValueError, TypeError, OverflowError) as error:
            raise _invalid("invalid source pre-fold samples") from error
        table = table.set_column(
            table.schema.get_field_index("original_state__samples"),
            "original_state__samples",
            pa.array(canonical_samples, type=pa.string()),
        )
    from marivo.analysis.methods.physical import DurationType

    if isinstance(stage.node.value_type, DurationType) and "value" in table.column_names:
        table = table.set_column(
            table.schema.get_field_index("value"),
            "value",
            table["value"].cast(pa.duration(stage.node.value_type.unit), safe=True),
        )
    from marivo.analysis.core.model import DisplayPart

    for display_part in stage.node.signature.parts:
        if isinstance(display_part, DisplayPart):
            for component, dtype in zip(display_part.components, display_part.types, strict=True):
                name = f"{display_part.role}__{component}"
                if dtype.startswith("interval(") and name in table.column_names:
                    import ibis.expr.datatypes as dt

                    table = table.set_column(
                        table.schema.get_field_index(name),
                        name,
                        table[name].cast(dt.dtype(dtype).to_pyarrow()),
                    )
                    if display_part.role == "columns":
                        table = table.set_column(
                            table.schema.get_field_index(component),
                            component,
                            table[component].cast(dt.dtype(dtype).to_pyarrow()),
                        )
    key_names = tuple(item.column for item in stage.layout.keys)
    cell_names = (
        ()
        if stage.layout.cell is None
        else (
            stage.layout.cell.value,
            stage.layout.cell.tag,
            stage.layout.cell.reason,
        )
    )
    primary_names = (*key_names, *stage.layout.extras, *cell_names)
    primary = project(table, primary_names)
    part_contracts: list[PartContract] = []
    parts: list[ExchangePart] = []
    from marivo.analysis.core.model import AttributionPart, part_role

    for part in stage.layout.parts:
        role = part_role(part.part)
        names = (*key_names, *(component.column for component in part.columns))
        selected = next(
            (item.table for item in retained_parts if item.role == role), project(table, names)
        )
        if isinstance(stage.node.value_type, DurationType) and isinstance(
            part.part, AttributionPart
        ):
            for name in (
                ("contribution", "current", "baseline")
                if role == "allocation"
                else ("target", "total")
                if role == "reconciliation"
                else ()
            ):
                column = role + "__" + name
                selected = selected.set_column(
                    selected.schema.get_field_index(column),
                    column,
                    selected[column].cast(pa.duration(stage.node.value_type.unit), safe=True),
                )
        from marivo.analysis.materialization.graph_reference import part_keys
        from marivo.analysis.methods.coordinate_state import (
            PartitionCoordinateLayout,
        )
        from marivo.analysis.methods.coordinate_state import (
            layout as coordinate_layout,
        )

        spec = coordinate_layout(stage.node.signature, role)
        retained_keys = (
            tuple(dict.fromkeys((*spec.keys, *spec.coordinates)))
            if isinstance(spec, PartitionCoordinateLayout)
            else part_keys(stage.node.signature, role)
        )
        part_contracts.append(PartContract(role, selected.schema, retained_keys))
        parts.append(ExchangePart(role, selected))
    from marivo.analysis.methods.coordinate_state import (
        KeyedCoordinateLayout,
        PartitionCoordinateLayout,
    )
    from marivo.analysis.methods.coordinate_state import (
        layout as coordinate_layout,
    )

    for retained in retained_parts:
        if any(part.role == retained.role for part in parts):
            continue
        spec = coordinate_layout(stage.node.signature, retained.role)
        if not isinstance(spec, (KeyedCoordinateLayout, PartitionCoordinateLayout)):
            raise _invalid("undeclared contribution receipt")
        keys = (
            tuple(dict.fromkeys((*spec.keys, *spec.coordinates)))
            if isinstance(spec, PartitionCoordinateLayout)
            else spec.keys
        )
        parts.append(retained)
        part_contracts.append(PartContract(retained.role, retained.table.schema, keys))
    source_ids = ",".join(stage.source_ids)
    state_kind = REGISTRY.lookup(stage.node.method).semantics.persistent_state_kind
    if state_kind is None:
        raise _invalid(f"method {stage.node.method} has no durable state qualification")
    if state_kind == "spearman":
        for status, value in zip(
            primary["status"].to_pylist(), primary["value"].to_pylist(), strict=True
        ):
            if status == "valid" and (
                type(value) is not float or not math.isfinite(value) or abs(value) > 1 + 1e-12
            ):
                raise _invalid(
                    "finite valid Spearman coefficient in [-1, 1] required at conversion"
                )
    state = None
    if state_kind != "none":
        statuses = (
            pa.array(["accepted"] * len(primary), type=pa.string())
            if state_kind in ("cohort", "table", "occurrence_inputs")
            else primary.column("status")
            if state_kind == "spearman"
            else cell_column(primary, "cell_tag")
        )
        state = pa.Table.from_arrays(
            [*(primary.column(name) for name in key_names), statuses],
            names=[*key_names, "status"],
        )
        if (
            state_kind in ("share", "penetration", "standardized")
            and isinstance(stage.node.signature.quantity, DerivedQuantity)
            and stage.node.signature.quantity.method_version.startswith("reference.")
        ):
            from marivo.analysis.materialization.graph_exchange import reference_parameters
            from marivo.analysis.materialization.graph_reference import error_bounds

            state = state.append_column(
                "error_bound",
                pa.array(
                    error_bounds(reference_parameters(stage.node.signature), tuple(parts), primary),
                    type=pa.float64(),
                ),
            )
    contract = ExchangeContract(
        stage.node.signature,
        stage.node.method,
        source_ids,
        primary.schema,
        key_names,
        tuple(part_contracts),
        stage.cell_reasons,
        state_kind,
        None if state is None else state.schema,
        pending,
        (
            isinstance(stage.node.parameters, (CellDerive, DisplayRank, DisplayTable))
            or (
                isinstance(stage.node.parameters, PartsTransport)
                and (
                    stage.node.parameters.mode in ("where", "limit")
                    or stage.node.parameters.display_view is not None
                )
            )
        )
        and not key_names,
        stage.column_reasons,
    )
    return from_arrow(
        primary,
        contract,
        parts=tuple(parts),
        completed_checks=completed,
        method_state=state,
        validate=False,
    )


def execute_source_graph(
    prepared: PreparedGraph, lowered: LoweredPlan, source: SourceSession
) -> ExchangeResult:
    """Evaluate a private source graph without Run, Store or Artifact publication."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "source"
    ):
        raise _invalid("prepared and lowered graph identity or input class differs")
    if any(
        isinstance(stage.node, MethodNode)
        and isinstance(stage.node.parameters, (OccurrencePrepare, PreparedObservation))
        for stage in lowered.stages
        if isinstance(stage, LoweredRelation)
    ) or any(
        isinstance(stage, LoweredLocal)
        and (
            (
                isinstance(stage.stage.node.parameters, PartsTransport)
                and stage.stage.node.parameters.mode == "business_coverage"
            )
            or isinstance(
                stage.stage.node.parameters,
                (
                    AssociationFit,
                    AssociationRead,
                    ForecastFit,
                    ForecastRead,
                    TimeRuns,
                    TimeRunRead,
                    DeviationFit,
                    DeviationRead,
                ),
            )
        )
        for stage in lowered.stages
    ):
        from marivo.analysis.materialization.graph_preparation import execute as execute_preparation

        return execute_preparation(prepared, lowered, source)
    local = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if local and (
        any(
            len(item.stage.inputs) != 1
            or item.stage.node.method.name
            not in (
                "association.spearman",
                "cell.difference",
                "cell.relative_change",
                "cell.ratio",
                "reference.share",
                "reference.penetration",
                "reference.standardize",
                "attribution.additive_difference",
                "attribution.component_mix",
                "display.rank",
                "display.table",
            )
            or (
                item.stage.node.method.name == "association.spearman"
                and item.stage.output != lowered.primary_output
            )
            for item in local
        )
    ):
        raise _invalid("unqualified source-local successor")
    primary = next(
        (
            stage
            for stage in lowered.stages
            if isinstance(stage, LoweredRelation) and stage.output == lowered.primary_output
        ),
        None,
    )
    if primary is None and not local:
        raise _invalid("missing lowered primary relation")
    # Native checks and terminal output are independent live reads.
    direct_native = not local and all(
        isinstance(stage, LoweredRelation) for stage in lowered.stages
    )
    completed: list[CompletedCheck] = []
    replacements: dict[ops.Node, ops.Node] = {}
    tables: dict[str, pa.Table] = {}
    owned: list[ir.Table] = []
    issued_reads: dict[str, CompiledRead] = {}
    final_local: LoweredRelation | None = None
    coordinate_parts: dict[str, tuple[ExchangePart, ...]] = {}
    try:
        for stage in lowered.stages:
            if isinstance(stage, LoweredLocal):
                predecessor = next(
                    (
                        item
                        for item in lowered.stages
                        if isinstance(item, LoweredRelation)
                        and item.output == stage.stage.inputs[0]
                    ),
                    None,
                )
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, AttributionDerive
                ):
                    from marivo.analysis.materialization.graph_attribution import pack, result

                    retained = (
                        *coordinate_parts.get(predecessor.output, ()),
                        *(
                            ExchangePart(
                                role, project(tables[predecessor.output], expression.columns)
                            )
                            for role, expression in predecessor.part_expressions
                            if role in ("current_endpoint", "baseline_endpoint", "basis")
                        ),
                    )
                    finished = result(
                        stage.stage.node.signature,
                        stage.stage.node.value_type,
                        retained,
                        ",".join(predecessor.source_ids),
                    )
                    table = pack(finished, tables[predecessor.output].schema)
                    coordinate_parts[stage.stage.output] = finished.parts
                    staged = source.stage_calculated(issued_reads[predecessor.output], table)
                    owned.append(staged)
                    tables[stage.stage.output] = table
                    replacements[predecessor.expression.op()] = staged.op()
                    final_local = replace(predecessor, output=stage.stage.output)
                    for check in lowered.checks:
                        if (
                            isinstance(check, SemanticCheck)
                            and check.requirement.stage_output == stage.stage.output
                        ):
                            proof = _check(source, lowered, check, replacements)
                            if proof is not None:
                                completed.append(proof)
                    continue
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, (DisplayRank, DisplayTable)
                ):
                    from marivo.analysis.materialization.graph_display import finish

                    table = finish(stage.stage.node, tables[predecessor.output])
                    final_local = replace(predecessor, output=stage.stage.output)
                    coordinate_parts[stage.stage.output] = coordinate_parts.get(
                        predecessor.output, ()
                    )
                    staged = source.stage_calculated(issued_reads[predecessor.output], table)
                    owned.append(staged)
                    tables[stage.stage.output] = table
                    replacements[predecessor.expression.op()] = staged.op()
                    for check in lowered.checks:
                        if (
                            isinstance(check, SemanticCheck)
                            and check.requirement.stage_output == stage.stage.output
                        ):
                            proof = _check(source, lowered, check, replacements)
                            if proof is not None:
                                completed.append(proof)
                    continue
                if predecessor is not None and isinstance(
                    stage.stage.node.parameters, ReferenceDerive
                ):
                    from marivo.analysis.materialization.graph_reference import (
                        result as reference_result,
                    )

                    parts = tuple(
                        ExchangePart(
                            role,
                            _read(
                                source,
                                lowered,
                                expression,
                                purpose="analysis.graph.reference",
                                replacements=replacements,
                                cell_reasons=next(
                                    part.cell_reasons
                                    for part in predecessor.node.signature.parts
                                    if isinstance(part, ReferenceStatePart) and part.role == role
                                ),
                            ),
                        )
                        for role, expression in predecessor.part_expressions
                    )
                    finished = reference_result(
                        stage.stage.node, parts, ",".join(predecessor.source_ids)
                    )
                    table = tables[predecessor.output]
                    keys = tuple(key.column for key in predecessor.layout.keys)
                    finished_rows = {
                        tuple(row[name] for name in keys): row for row in rows(finished.primary)
                    }
                    assert finished.method_state is not None
                    bounds = {
                        tuple(row[name] for name in keys): row["error_bound"]
                        for row in finished.method_state.to_pylist()
                    }
                    output_rows = []
                    for row in rows(table):
                        key = tuple(row[name] for name in keys)
                        row.update(
                            {
                                name: finished_rows[key][name]
                                for name in ("value", "cell_tag", "cell_reason")
                            }
                        )
                        row["reference_proof__retained"] = bounds[key]
                        output_rows.append(row)
                    table = from_rows(output_rows, issued_reads[predecessor.output].schema)
                    staged = source.stage_calculated(issued_reads[predecessor.output], table)
                    owned.append(staged)
                    tables[stage.stage.output] = table
                    replacements[predecessor.expression.op()] = staged.op()
                    final_local = replace(predecessor, output=stage.stage.output)
                    for check in lowered.checks:
                        if (
                            isinstance(check, SemanticCheck)
                            and check.requirement.stage_output == stage.stage.output
                        ):
                            proof = _check(source, lowered, check, replacements)
                            if proof is not None:
                                completed.append(proof)
                    continue
                if predecessor is not None and isinstance(stage.stage.node.parameters, CellDerive):
                    node = stage.stage.node
                    params = node.parameters
                    assert isinstance(params, CellDerive)
                    table = tables[predecessor.output]
                    output_rows = []
                    for row in rows(table):
                        if row["cell_tag"] != "defined":
                            row["correspondence__result_error_bound"] = 0.0
                            output_rows.append(row)
                            continue
                        try:
                            cell = evaluate_comparison(
                                params.method,
                                row["current_endpoint__value"],
                                row["baseline_endpoint__value"],
                                node.inputs[0].node.value_type,
                                node.inputs[1].node.value_type,
                            )
                        except (ValueError, OverflowError) as error:
                            raise _invalid(f"comparison finish failed: {error}") from error
                        try:
                            row["correspondence__result_error_bound"] = propagated_error(
                                params.method,
                                row["current_endpoint__value"],
                                row["baseline_endpoint__value"],
                                cell.value if isinstance(cell, Defined) else None,
                                required(row["correspondence__current_error_bound"], float),
                                required(row["correspondence__baseline_error_bound"], float),
                            )
                        except (ValueError, OverflowError) as error:
                            raise _invalid(str(error)) from error
                        row["value"] = cell.value if isinstance(cell, Defined) else None
                        row["cell_tag"] = "defined" if isinstance(cell, Defined) else "undefined"
                        row["cell_reason"] = None if isinstance(cell, Defined) else cell.reason
                        output_rows.append(row)
                    table = from_rows(output_rows, issued_reads[predecessor.output].schema)
                    final_local = replace(predecessor, output=stage.stage.output)
                    # Validate the finished Cell, endpoint and correspondence exchange before use.
                    coordinate_parts[stage.stage.output] = coordinate_parts.get(
                        predecessor.output, ()
                    )
                    _result(final_local, table, (), (), coordinate_parts[stage.stage.output])
                    staged = source.stage_calculated(issued_reads[predecessor.output], table)
                    owned.append(staged)
                    tables[stage.stage.output] = table
                    replacements[predecessor.expression.op()] = staged.op()
                    for check in lowered.checks:
                        if (
                            isinstance(check, SemanticCheck)
                            and check.requirement.stage_output == stage.stage.output
                        ):
                            proof = _check(source, lowered, check, replacements)
                            if proof is not None:
                                completed.append(proof)
                    continue
                if predecessor is None or predecessor.layout.extras != (
                    "va",
                    "taga",
                    "reasona",
                    "vb",
                    "tagb",
                    "reasonb",
                ):
                    raise _invalid("missing exact paired source preparation")
                paired = tables[predecessor.output]
                keys = tuple(item.column for item in predecessor.layout.keys)
                left = rename(
                    project(paired, (*keys, "va", "taga", "reasona")),
                    [*keys, "value", "cell_tag", "cell_reason"],
                )
                right = rename(
                    project(paired, (*keys, "vb", "tagb", "reasonb")),
                    [*keys, "value", "cell_tag", "cell_reason"],
                )
                return finish_spearman(
                    stage,
                    left,
                    right,
                    keys,
                    ",".join(predecessor.source_ids),
                    _ordered_checks(completed, lowered.admitted.checks),
                    lowered.admitted.checks,
                )
            if not isinstance(stage, LoweredRelation):
                raise _invalid("unqualified source stage")
            for check in lowered.checks:
                if (
                    isinstance(check, SemanticCheck)
                    and check.requirement.stage_output == stage.output
                    and check.requirement.obligation.before == "consume"
                ):
                    proof = _check(source, lowered, check, replacements)
                    if proof is not None:
                        completed.append(proof)
            if direct_native:
                if stage.output == lowered.primary_output:
                    received = _read(
                        source,
                        lowered,
                        stage.transport if stage.transport is not None else stage.expression,
                        purpose="analysis.graph.stage",
                        replacements={},
                        cell_reasons=stage.cell_reasons,
                    )
                    if stage.transport is not None:
                        received, retained = _split_transport(stage, received)
                        coordinate_parts[stage.output] = retained
                    tables[stage.output] = received
            else:
                issued = _issue(
                    source,
                    lowered,
                    stage.expression,
                    purpose="analysis.graph.stage",
                    replacements=replacements,
                )
                issued_reads[stage.output] = issued
                if stage.transport is None:
                    staged, table = source.stage_derived(issued)
                else:
                    received = _read(
                        source,
                        lowered,
                        stage.transport,
                        purpose="analysis.graph.stage",
                        replacements=replacements,
                        cell_reasons=stage.cell_reasons,
                    )
                    table, retained = _split_transport(stage, received)
                    coordinate_parts[stage.output] = retained
                    if table.schema != issued.schema:
                        table = from_rows(rows(table), issued.schema)
                    staged = source.stage_calculated(issued, table)
                owned.append(staged)
                tables[stage.output] = table
                replacements[stage.expression.op()] = staged.op()
            for check in lowered.checks:
                if (
                    isinstance(check, (IntegrityCheck, TemporalCheck))
                    and check.stage_output == stage.output
                ) or (
                    isinstance(check, SemanticCheck)
                    and check.requirement.stage_output == stage.output
                    and check.requirement.obligation.before == "publish"
                ):
                    proof = _check(source, lowered, check, replacements)
                    if proof is not None:
                        completed.append(proof)
        if final_local is not None and final_local.output == lowered.primary_output:
            primary = final_local
        assert primary is not None
        retained_parts = (
            *coordinate_parts.get(primary.output, ()),
            *tuple(
                ExchangePart(
                    role,
                    _read(
                        source,
                        lowered,
                        expression,
                        purpose="analysis.graph.part",
                        replacements=replacements,
                        cell_reasons=next(
                            (
                                part.cell_reasons
                                for part in primary.node.signature.parts
                                if isinstance(part, ReferenceStatePart) and part.role == role
                            ),
                            (),
                        ),
                    ),
                )
                for role, expression in primary.part_expressions
                if role not in {p.role for p in coordinate_parts.get(primary.output, ())}
            ),
        )
        return _result(
            primary,
            tables[primary.output],
            _ordered_checks(completed, lowered.admitted.checks),
            lowered.admitted.checks,
            retained_parts,
        )
    finally:
        source.release_staged(owned)
