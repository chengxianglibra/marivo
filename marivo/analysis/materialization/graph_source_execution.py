"""R4.3 private source execution through issued R1 reads and Arrow exchange."""

from __future__ import annotations

import hashlib

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredLocal,
    LoweredPlan,
    LoweredRelation,
    SemanticCheck,
    TemporalCheck,
)
from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.rules import PartsTransport
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
from marivo.analysis.methods.registry import REGISTRY
from marivo.datasource.adapters import CompiledRead, SourceSession


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
    rewritten = expression.op().replace(replacements).to_expr() if replacements else expression
    if not isinstance(rewritten, ir.Table):
        raise _invalid("rewritten stage is not an Ibis table")
    schema = expression.schema().to_pyarrow()
    if not rewritten.schema().to_pyarrow().equals(schema, check_metadata=False):
        raise _invalid("staged relation changes the emitted schema")
    return source.compile(qualified, rewritten, purpose=purpose, expected_schema=schema)


def _read(
    source: SourceSession,
    lowered: LoweredPlan,
    expression: ir.Table,
    *,
    purpose: str,
    replacements: dict[ops.Node, ops.Node],
    keys: tuple[str, ...] = (),
    validate_cells: bool = True,
) -> pa.Table:
    issued = _issue(source, lowered, expression, purpose=purpose, replacements=replacements)
    schema = expression.schema().to_pyarrow()
    stream = CheckedStream(
        source.batches(issued, chunk_size=1024), schema, keys, validate_cells=validate_cells
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

        for row in table.to_pylist():
            raw, actual = row["raw_time"], row["normalized_time"]
            if raw is None and actual is None:
                continue
            if isinstance(raw, datetime):
                expected_time: date = instant(raw, check.axis.timezone or "UTC").replace(
                    tzinfo=None
                )
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
        raise MaterializationError(
            expected=expected,
            received=f"{table.num_rows} violating rows",
            repair="Correct the exact source rows or choose a qualified input.",
            stage="graph_check",
        )
    if isinstance(check, SemanticCheck):
        digest = hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest()
        return CompletedCheck(check.requirement, digest)
    return None


def _ordered_checks(
    completed: list[CompletedCheck], pending: tuple[CheckRequirement, ...]
) -> tuple[CompletedCheck, ...]:
    """Freeze all origin-group checks in the plan's order, independent of deadline order."""
    if any(proof.requirement not in pending for proof in completed):
        raise _invalid("completed source check has no admitted origin")
    ordered: list[CompletedCheck] = []
    for requirement in pending:
        proofs = tuple(proof for proof in completed if proof.requirement == requirement)
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
    primary = table.select(primary_names)
    part_contracts: list[PartContract] = []
    parts: list[ExchangePart] = []
    from marivo.analysis.core.model import part_role

    for part in stage.layout.parts:
        role = part_role(part.part)
        names = (*key_names, *(component.column for component in part.columns))
        selected = table.select(names)
        part_contracts.append(PartContract(role, selected.schema, key_names))
        parts.append(ExchangePart(role, selected))
    source_ids = ",".join(stage.source_ids)
    state_kind = REGISTRY.lookup(stage.node.method).semantics.persistent_state_kind
    if state_kind is None:
        raise _invalid(f"method {stage.node.method} has no durable state qualification")
    state = None
    if state_kind != "none":
        statuses = (
            primary.column("status") if state_kind == "spearman" else primary.column("cell_tag")
        )
        state = pa.Table.from_arrays(
            [*(primary.column(name) for name in key_names), statuses],
            names=[*key_names, "status"],
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
        isinstance(stage.node.parameters, PartsTransport)
        and stage.node.parameters.mode == "where"
        and not key_names,
    )
    return from_arrow(
        primary,
        contract,
        parts=tuple(parts),
        completed_checks=completed,
        method_state=state,
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
    local = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if local and (
        len(local) != 1
        or local[0].stage.output != lowered.primary_output
        or local[0].stage.node.method.name != "association.spearman"
        or len(local[0].stage.inputs) != 1
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
    completed: list[CompletedCheck] = []
    replacements: dict[ops.Node, ops.Node] = {}
    tables: dict[str, pa.Table] = {}
    owned: list[ir.Table] = []
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
                left = paired.select((*keys, "va", "taga", "reasona")).rename_columns(
                    [*keys, "value", "cell_tag", "cell_reason"]
                )
                right = paired.select((*keys, "vb", "tagb", "reasonb")).rename_columns(
                    [*keys, "value", "cell_tag", "cell_reason"]
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
            issued = _issue(
                source,
                lowered,
                stage.expression,
                purpose="analysis.graph.stage",
                replacements=replacements,
            )
            staged, table = source.stage_derived(issued)
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
        assert primary is not None
        return _result(
            primary,
            tables[primary.output],
            _ordered_checks(completed, lowered.admitted.checks),
            lowered.admitted.checks,
        )
    finally:
        source.release_staged(owned)
