"""Source-prefix capture and registered local consumers in the existing Runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

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
    canonical_layout,
)
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.model import (
    ConditionCellsPart,
    CoordinateStatePart,
    CoveragePart,
    FitInputsPart,
    OriginalStatePart,
    PairInputsPart,
    RunCellsPart,
    SubjectPart,
    TrainingInputsPart,
)
from marivo.analysis.core.rules import (
    AssociationFit,
    AssociationRead,
    AttributionDerive,
    CellCompare,
    CellDerive,
    ComparisonRead,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastFit,
    ForecastRead,
    MapCorrespond,
    ObserveCount,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    RowState,
    TimeProduct,
    TimeRunRead,
    TimeRuns,
)
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangePart,
    ExchangeResult,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.methods.consumer_rules import prepared_numeric
from marivo.analysis.methods.physical import arrow_scalar_type
from marivo.analysis.methods.prepared_observation import Restriction, restrict, state
from marivo.datasource.adapters import SourceSession


def _observation(
    stage: LoweredLocal, candidates: pa.Table, selected: ExchangeResult, original: pa.Table
) -> tuple[pa.Table, tuple[ExchangePart, ...]]:
    params = stage.stage.node.parameters
    assert isinstance(params, PreparedObservation)
    observation = params.observation
    assert observation.start is not None and observation.end is not None
    keys = selected.contract.key_fields
    subject = next(p for p in selected.contract.signature.parts if isinstance(p, SubjectPart))
    subject_table = next(p.table for p in selected.parts if p.role == "subject")
    subject_rows = {
        tuple(row[name] for name in keys): tuple(
            row[f"subject__key_{i}"] for i in range(len(subject.subject_key))
        )
        for row in subject_table.to_pylist()
    }
    original_keys = tuple(f"key_{i}" for i in range(len(subject.subject_key)))
    envelope = {tuple(row[name] for name in original_keys) for row in original.to_pylist()}
    default_window: tuple[datetime, datetime] | None = None
    anchor_position: int | None = None
    grid_windows: dict[str | int, tuple[datetime, datetime]] = {}
    selections = []
    for row in selected.primary.to_pylist():
        identity: list[str | int] = []
        for name in keys:
            value = row[name]
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                fail("input_binding", "invalid selected full Subject key", stage="consume")
            identity.append(value)
        subject_identity = subject_rows[tuple(identity)]
        if subject_identity not in envelope:
            fail(
                "input_binding",
                "actual Subject image escapes its original member envelope",
                stage="consume",
            )
        if default_window is None:
            default_window = (
                datetime.fromisoformat(observation.start),
                datetime.fromisoformat(observation.end),
            )
            if observation.grid_window:
                grid = selected.contract.signature.domain.time_grid
                if grid is None:
                    fail(
                        "preparation_bounds",
                        "prepared selection is missing its captured grid",
                        stage="consume",
                    )
                anchor_position = next(
                    i
                    for i, coordinate in enumerate(selected.contract.signature.domain.instance_key)
                    if coordinate.role == "anchor"
                )
                grid_windows = {cell.identity: (cell.start, cell.end) for cell in grid.cells}
        window_start, window_end = default_window
        if observation.grid_window:
            assert anchor_position is not None
            window = grid_windows.get(identity[anchor_position])
            if window is None:
                fail(
                    "input_binding",
                    "selected time key is outside its captured grid",
                    stage="consume",
                )
            window_start, window_end = window
        selections.append(
            Restriction(
                tuple(identity),
                subject_identity,
                window_start,
                window_end,
            )
        )
    restricted = restrict(candidates, tuple(selections))
    original_state = next(
        part for part in stage.stage.node.signature.parts if isinstance(part, OriginalStatePart)
    )
    columns: dict[str, list[object]] = {
        name: [selection.key[i] for selection in selections] for i, name in enumerate(keys)
    }
    columns.update({"value": [], "cell_tag": [], "cell_reason": [], "coverage__complete": []})
    columns.update(
        {
            f"subject__key_{i}": [selection.subject[i] for selection in selections]
            for i in range(len(subject.subject_key))
        }
    )
    columns.update({"original_state__" + name: [] for name in original_state.components})
    coordinate = next(
        (
            part
            for part in stage.stage.node.signature.parts
            if isinstance(part, CoordinateStatePart)
        ),
        None,
    )
    if coordinate is not None:
        columns["coordinate_state__present"] = []
    coordinate_rows: list[dict[str, object]] = []
    for position, rows in enumerate(restricted):
        components = state(observation, rows)
        support = components["count" if isinstance(observation, ObserveCount) else "non_null_count"]
        total = components["count" if isinstance(observation, ObserveCount) else "sum"]
        if not isinstance(observation, ObserveCount) and observation.method == "mean" and support:
            assert isinstance(total, (int, float)) and isinstance(support, int)
            value = total / support
        else:
            value = (
                total
                if support
                or observation.metric.empty_rule == "zero"
                or isinstance(observation, ObserveCount)
                else None
            )
        columns["value"].append(value)
        columns["cell_tag"].append("defined" if value is not None else "null")
        columns["cell_reason"].append(None if value is not None else "empty_contribution")
        columns["coverage__complete"].append(True)
        for name in original_state.components:
            columns["original_state__" + name].append(components[name])
        if coordinate is not None:
            grouped: dict[tuple[str, ...], list[Mapping[str, object]]] = {}
            for row in rows:
                labels: list[str] = []
                for name in coordinate.columns:
                    label = row[name]
                    if not isinstance(label, str):
                        fail(
                            "input_binding",
                            "historical coordinate is not a Defined string",
                            stage="consume",
                        )
                    labels.append(label)
                grouped.setdefault(tuple(labels), []).append(row)
            for group_labels, members in grouped.items():
                totals = state(observation, tuple(members))
                coordinate_rows.append(
                    {
                        **dict(zip(keys, selections[position].key, strict=True)),
                        **dict(zip(coordinate.columns, group_labels, strict=True)),
                        **{name: totals[name] for name in coordinate.components},
                    }
                )
            columns["coordinate_state__present"].append(bool(rows))
    layout = stage.output_layout
    fields = []
    from marivo.analysis.methods.deviation_physical import parse_type
    from marivo.analysis.methods.physical import DecimalType

    amount = parse_type(getattr(observation, "amount_type", "int64"))
    amount_type = arrow_scalar_type(
        DecimalType(38, amount.scale) if isinstance(amount, DecimalType) else amount
    )
    for name in layout.columns:
        dtype = (
            selected.primary.schema.field(name).type
            if name in keys
            else arrow_scalar_type(stage.stage.node.value_type)
            if name == "value"
            else pa.string()
            if name in ("cell_tag", "cell_reason")
            else pa.bool_()
            if name in ("coverage__complete", "coordinate_state__present")
            else subject_table.schema.field(name).type
            if name.startswith("subject__key_")
            else pa.int64()
            if "count" in name
            else amount_type
        )
        fields.append(pa.field(name, dtype))
    metadata = {
        **(selected.primary.schema.metadata or {}),
        **(candidates.schema.metadata or {}),
        b"r7.restriction": json.dumps(
            {
                "scope": stage.stage.node.signature.domain.binding.scope_id,
                "original_member_rows": original.num_rows,
                "selected_member_rows": selected.primary.num_rows,
                "candidate_rows": candidates.num_rows,
                "candidate_arrow_bytes": candidates.nbytes,
                "window": [observation.start, observation.end],
                "components": original_state.components,
            },
            sort_keys=True,
        ).encode(),
    }
    table = pa.Table.from_arrays(
        [pa.array(columns[field.name], type=field.type) for field in fields],
        schema=pa.schema(fields, metadata=metadata),
    )
    retained: tuple[ExchangePart, ...] = ()
    if coordinate is not None:
        from marivo.analysis.methods.coordinate_state import component_type, retain

        full_schema = pa.schema(
            [
                *(table.schema.field(k) for k in keys),
                *((name, pa.string()) for name in coordinate.columns),
                *(
                    (name, component_type(coordinate, name).to_pyarrow())
                    for name in coordinate.components
                ),
            ]
        )
        full = pa.Table.from_pylist(coordinate_rows, schema=full_schema)
        retained = (
            ExchangePart(
                "coordinate_state",
                retain(stage.stage.node.signature, "coordinate_state", full, table),
            ),
        )
    return table, retained


def execute(prepared: PreparedGraph, lowered: LoweredPlan, source: SourceSession) -> ExchangeResult:
    owned: list[ir.Table] = []
    try:
        return _execute(prepared, lowered, source, owned)
    finally:
        source.release_staged(owned)


def _execute(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    source: SourceSession,
    lookup_relations: list[ir.Table],
) -> ExchangeResult:
    from marivo.analysis.materialization.graph_local_execution import (
        _row_result,
        _subject_image,
        _transport_stage,
    )
    from marivo.analysis.materialization.graph_source_execution import (
        _check,
        _consume_field,
        _field_lookup,
        _issue,
        _ordered_checks,
        _read,
        _result,
    )

    locals_ = tuple(item for item in lowered.stages if isinstance(item, LoweredLocal))
    if any(
        not (
            isinstance(
                item.stage.node.parameters,
                (
                    CellCompare,
                    ComparisonRead,
                    AttributionDerive,
                    AssociationFit,
                    AssociationRead,
                    ForecastFit,
                    ForecastRead,
                    TimeRuns,
                    TimeRunRead,
                    DeviationFit,
                    DeviationRead,
                    TimeProduct,
                    PreparedObservation,
                ),
            )
            or (
                isinstance(
                    item.stage.node.parameters,
                    (
                        PartsTransport,
                        RowState,
                        MapCorrespond,
                        OriginalReduce,
                        CellDerive,
                        DisplayRank,
                        DisplayTable,
                    ),
                )
                and any(
                    isinstance(ancestor, MethodNode)
                    and isinstance(ancestor.parameters, ComparisonRead)
                    for ancestor in topology(item.stage.node)
                )
            )
            or (
                isinstance(
                    item.stage.node.parameters, (OriginalReduce, CellDerive, AttributionDerive)
                )
                and prepared_numeric(item.stage.implementation)
            )
            or (
                isinstance(item.stage.node.parameters, RowState)
                and isinstance(item.stage.node.inputs[0].node, MethodNode)
                and isinstance(item.stage.node.inputs[0].node.parameters, PreparedObservation)
            )
            or any(
                isinstance(
                    p,
                    (
                        ConditionCellsPart,
                        RunCellsPart,
                        FitInputsPart,
                        PairInputsPart,
                        TrainingInputsPart,
                    ),
                )
                for e in item.stage.node.inputs
                for p in e.node.signature.parts
            )
            or (
                isinstance(item.stage.node.parameters, PartsTransport)
                and (
                    item.stage.node.parameters.mode == "business_coverage"
                    or any(
                        isinstance(p, CoveragePart) and p.business_windows is not None
                        for p in item.stage.node.inputs[0].node.signature.parts
                    )
                )
            )
            or (
                item.stage.node.inputs[0].node.signature.domain.kind in ("interval",)
                and isinstance(item.stage.node.parameters, (PartsTransport, RowState))
            )
            or (
                isinstance(item.stage.node.parameters, MapCorrespond)
                and item.stage.node.parameters.mode == "subjects"
                and item.stage.node.inputs[0].node.signature.domain.kind
                in (
                    "occurrence",
                    "interval",
                )
            )
        )
        for item in locals_
    ):
        fail("physical_qualification", "unregistered R7 local consumer", stage="admission")
    completed: list[CompletedCheck] = []
    for requirement in lowered.checks:
        if isinstance(requirement, (IntegrityCheck, SemanticCheck, TemporalCheck)):
            proof = _check(source, lowered, requirement, {})
            if proof is not None:
                completed.append(proof)
    relations = {item.output: item for item in lowered.stages if isinstance(item, LoweredRelation)}
    tables: dict[str, pa.Table] = {}
    replacements: dict[ops.Node, ops.Node] = {}
    results: dict[str, ExchangeResult] = {}
    originals = {
        item.stage.node.inputs[1].node.identity
        for item in locals_
        if isinstance(item.stage.node.parameters, PreparedObservation)
    }
    local_source_inputs = {key for item in locals_ for key in item.stage.inputs if key in relations}
    # Complete all source reads before the first local consumer.
    for stage in relations.values():
        params = stage.node.parameters if isinstance(stage.node, MethodNode) else None
        if (
            not isinstance(
                params,
                (PreparedObservation,),
            )
            and stage.node.identity not in originals
            and stage.output not in local_source_inputs
            and not _field_lookup(stage)
        ):
            continue
        table = _read(
            source,
            lowered,
            stage.transport if stage.transport is not None else stage.expression,
            purpose="analysis.domain.prepare",
            replacements=replacements,
            keys=tuple(key.column for key in stage.layout.keys),
            validate_cells=False,
        )
        retained_parts: tuple[ExchangePart, ...] = ()
        if stage.transport is not None:
            from marivo.analysis.materialization.graph_source_execution import _split_transport

            table, retained_parts = _split_transport(stage, table)
        _consume_field(stage, table)
        if _field_lookup(stage):
            issued = _issue(
                source,
                lowered,
                stage.expression,
                purpose="analysis.domain.prepare",
                replacements=replacements,
            )
            staged = source.stage_calculated(issued, table)
            lookup_relations.append(staged)
            replacements[stage.expression.op()] = staged.op()
        if isinstance(params, PreparedObservation):
            table = table.replace_schema_metadata(
                {
                    b"r7.capture_authority": json.dumps(
                        source.domain_authority, sort_keys=True
                    ).encode()
                }
            )
        if (
            isinstance(stage.node, MethodNode)
            and not isinstance(params, PreparedObservation)
            and stage.output not in results
        ):
            results[stage.output] = _result(stage, table, (), (), retained_parts)
        tables[stage.output] = table
    for item in locals_:
        params = item.stage.node.parameters
        for requirement in prepared.admitted.checks:
            if (
                requirement.node_id != item.stage.node.identity
                or requirement.obligation.fact in item.stage.node.derivation.pre
                or any(
                    proof.requirement == requirement or requirement in proof.consumers
                    for proof in completed
                )
            ):
                continue
            prior = next(
                (
                    proof
                    for proof in completed
                    if proof.requirement.obligation == requirement.obligation
                ),
                None,
            )
            if prior is None:
                fail(
                    "input_binding",
                    "local consumer lacks its completed originating check: "
                    + requirement.obligation.check_id,
                    stage="consume",
                )
            completed.append(CompletedCheck(requirement, prior.result_digest))
        if isinstance(params, (DisplayRank, DisplayTable)):
            from marivo.analysis.materialization.graph_display import fixed

            result = fixed(
                item.stage.node,
                tuple(results[key] for key in item.stage.inputs),
                item.stage.node.identity,
            )
            # fixed() verifies complete key pairing and partition Cells before
            # returning. Bind those checks to this local invocation, not SQL.
            proof_digest = hashlib.sha256(
                result.primary.schema.serialize().to_pybytes()
                + repr(cell_rows(result.primary)).encode()
            ).hexdigest()
            for requirement in prepared.admitted.checks:
                if requirement.node_id != item.stage.node.identity or any(
                    proof.requirement == requirement or requirement in proof.consumers
                    for proof in completed
                ):
                    continue
                if requirement.obligation.fact in item.stage.node.derivation.pre:
                    if requirement.obligation.check_id not in (
                        "source.exact_pairing@v1",
                        "source.group_mapping@v1",
                        "source.cell_policy@v1",
                    ):
                        fail("input_binding", "unqualified local display check", stage="consume")
                    completed.append(CompletedCheck(requirement, proof_digest))
                else:
                    prior = next(
                        (
                            proof
                            for proof in completed
                            if proof.requirement.obligation == requirement.obligation
                        ),
                        None,
                    )
                    if prior is None:
                        fail(
                            "input_binding",
                            "local display lacks its completed predecessor check",
                            stage="consume",
                        )
                    completed.append(CompletedCheck(requirement, prior.result_digest))
            results[item.stage.output] = result
        elif isinstance(params, (AssociationFit, AssociationRead, ForecastFit, ForecastRead)):
            from marivo.analysis.materialization.statistical_execution import execute as statistics

            results[item.stage.output] = statistics(
                item.stage.node,
                tuple(results[key] for key in item.stage.inputs),
                item.stage.node.identity,
            )
        elif isinstance(params, (TimeRuns, TimeRunRead)):
            from marivo.analysis.materialization.runs_execution import execute as runs

            results[item.stage.output] = runs(
                item.stage.node,
                tuple(results[key] for key in item.stage.inputs),
                item.stage.node.identity,
            )
        elif isinstance(params, (DeviationFit, DeviationRead)):
            from marivo.analysis.materialization.deviation_execution import execute as deviation

            results[item.stage.output] = deviation(
                item.stage.node,
                tuple(results[key] for key in item.stage.inputs),
                item.stage.node.identity,
            )
        elif isinstance(params, ComparisonRead):
            from marivo.analysis.materialization.graph_local_execution import comparison_project

            results[item.stage.output] = comparison_project(
                item.stage.node, results[item.stage.inputs[0]], item.stage.node.identity
            )
        elif isinstance(params, TimeProduct):
            from marivo.analysis.materialization.graph_local_execution import time_product

            results[item.stage.output] = time_product(
                item.stage.node, results[item.stage.inputs[0]], item.stage.node.identity
            )
        elif isinstance(params, PartsTransport):
            results[item.stage.output] = _transport_stage(
                item,
                results[item.stage.inputs[0]],
                item.stage.node.identity,
                tuple(results[key] for key in item.stage.inputs[1:]),
            )
        elif isinstance(params, (OriginalReduce, CellDerive, AttributionDerive)):
            from marivo.analysis.materialization.graph_local_execution import (
                _coordinate_rollup_stage,
                _difference_stage,
                _fold_rollup_stage,
                _original_rollup_stage,
            )

            values = tuple(results[key] for key in item.stage.inputs)
            input_digest = hashlib.sha256()
            for value in values:
                input_digest.update(value.primary.schema.serialize().to_pybytes())
                input_digest.update(repr(cell_rows(value.primary)).encode())
                for part in value.parts:
                    input_digest.update(part.role.encode())
                    input_digest.update(part.table.schema.serialize().to_pybytes())
                    input_digest.update(repr(cell_rows(part.table)).encode())
            receipt_hash = input_digest.hexdigest()
            owned = tuple(
                c for c in prepared.admitted.checks if c.node_id == item.stage.node.identity
            )
            if isinstance(params, OriginalReduce):
                if params.method == "fold":
                    result = _fold_rollup_stage(item, values[0], item.stage.node.identity)
                elif params.coordinates:
                    result = _coordinate_rollup_stage(item, values[0], item.stage.node.identity)
                else:
                    result = _original_rollup_stage(item, values[0], item.stage.node.identity)
            elif isinstance(params, CellDerive):
                result = _difference_stage(
                    item,
                    (values[0], values[1]),
                    receipt_hash,
                    item.stage.node.identity,
                    tuple(
                        c
                        for c in owned
                        if c.obligation.check_id
                        in (
                            "source.unique_key@v1",
                            "source.exact_pairing@v1",
                            "source.finite_numeric@v1",
                            "source.complete_coverage@v1",
                        )
                    ),
                )
            else:
                from marivo.analysis.materialization.graph_attribution import fixed

                result = fixed(item.stage.node, values, item.stage.node.identity)
            completed.extend(result.completed_checks)
            for requirement in owned:
                if any(proof.requirement == requirement for proof in completed):
                    continue
                prior = next(
                    (
                        proof
                        for proof in completed
                        if proof.requirement.obligation == requirement.obligation
                    ),
                    None,
                )
                if (
                    prior is None
                    and isinstance(params, AttributionDerive)
                    and requirement.obligation.fact in item.stage.node.derivation.pre
                    and requirement.obligation.check_id == "source.exact_pairing@v1"
                ):
                    # Allocation verifies exact basis and ordered endpoint keys.
                    completed.append(CompletedCheck(requirement, receipt_hash))
                    continue
                if (
                    prior is None
                    and isinstance(params, OriginalReduce)
                    and requirement.obligation.fact in item.stage.node.derivation.pre
                    and requirement.obligation.check_id
                    in ("source.contribution_partition@v1", "source.complete_coverage@v1")
                ):
                    proof_digest = hashlib.sha256(
                        result.primary.schema.serialize().to_pybytes()
                        + repr(
                            [(part.role, cell_rows(part.table)) for part in result.parts]
                        ).encode()
                    ).hexdigest()
                    completed.append(CompletedCheck(requirement, proof_digest))
                    continue
                if prior is None:
                    fail(
                        "input_binding",
                        "C09 local consumer lacks its completed predecessor check: "
                        + requirement.obligation.check_id,
                        stage="consume",
                    )
                completed.append(CompletedCheck(requirement, prior.result_digest))
            results[item.stage.output] = result
        elif isinstance(params, RowState):
            owned_check = (
                None
                if params.method == "count"
                else "source.cell_policy@v1"
                if params.method == "count_defined"
                else "source.finite_numeric@v1"
            )
            for requirement in prepared.admitted.checks:
                if (
                    requirement.node_id != item.stage.node.identity
                    or requirement.obligation.check_id == owned_check
                ):
                    continue
                prior = next(
                    (
                        proof
                        for proof in completed
                        if proof.requirement.obligation == requirement.obligation
                    ),
                    None,
                )
                if prior is None:
                    fail(
                        "input_binding",
                        "row continuation lacks its completed predecessor check",
                        stage="consume",
                    )
                completed.append(CompletedCheck(requirement, prior.result_digest))
            result = _row_result(
                item,
                results[item.stage.inputs[0]],
                item.stage.node.identity,
                item.stage.node.identity,
                tuple(
                    c
                    for c in prepared.admitted.checks
                    if c.node_id == item.stage.node.identity
                    and c.obligation.check_id == owned_check
                ),
            )
            completed.extend(result.completed_checks)
            results[item.stage.output] = result
        elif isinstance(params, MapCorrespond):
            results[item.stage.output] = _subject_image(
                item,
                results[item.stage.inputs[0]],
                item.stage.node.identity,
            )
        else:
            assert isinstance(params, PreparedObservation)
            selected = results[item.stage.inputs[0]]
            candidates = tables[item.stage.inputs[1]]
            original = next(
                tables[key]
                for key, relation in relations.items()
                if relation.node.identity == item.stage.node.inputs[1].node.identity
            )
            table, coordinate_parts = _observation(item, candidates, selected, original)
            state_digest = hashlib.sha256(
                table.schema.serialize().to_pybytes() + repr(cell_rows(table)).encode()
            ).hexdigest()
            completed.extend(
                CompletedCheck(requirement, state_digest)
                for requirement in prepared.admitted.checks
                if requirement.node_id == item.stage.node.identity
                and requirement.obligation.fact in item.stage.node.derivation.pre
            )
            predecessor = relations[item.stage.inputs[1]]
            terminal = replace(
                predecessor,
                output=item.stage.output,
                node=item.stage.node,
                layout=canonical_layout(item.stage.node.signature, has_value=True),
                cell_reasons=(("null", ("empty_contribution",)),),
            )
            results[item.stage.output] = _result(
                terminal,
                table,
                _ordered_checks(
                    completed,
                    tuple(
                        c
                        for c in prepared.admitted.checks
                        if any(proof.requirement == c for proof in completed)
                    ),
                ),
                tuple(
                    c
                    for c in prepared.admitted.checks
                    if any(proof.requirement == c for proof in completed)
                ),
                coordinate_parts,
            )
        for requirement in prepared.admitted.checks:
            if requirement.node_id != item.stage.node.identity or any(
                proof.requirement == requirement or requirement in proof.consumers
                for proof in completed
            ):
                continue
            if (
                isinstance(params, PartsTransport)
                and requirement.obligation.fact in item.stage.node.derivation.pre
                and (
                    requirement.obligation.check_id
                    in ("source.exact_pairing@v1", "source.group_mapping@v1")
                    or (
                        params.mode == "cohort"
                        and requirement.obligation.check_id == "source.complete_coverage@v1"
                    )
                )
            ):
                # Transport indexes and checks the actual complete predicate keys
                # and cohort opportunity coverage before returning selected rows.
                selected = results[item.stage.output]
                proof_digest = hashlib.sha256(
                    selected.primary.schema.serialize().to_pybytes()
                    + repr(cell_rows(selected.primary)).encode()
                ).hexdigest()
            else:
                prior = next(
                    (
                        proof
                        for proof in completed
                        if proof.requirement.obligation == requirement.obligation
                    ),
                    None,
                )
                if prior is None:
                    fail(
                        "input_binding",
                        "local consumer lacks its completed originating check: "
                        + requirement.obligation.check_id,
                        stage="consume",
                    )
                proof_digest = prior.result_digest
            completed.append(CompletedCheck(requirement, proof_digest))
    result = results[lowered.primary_output]
    from marivo.analysis.materialization.graph_exchange import from_arrow

    return from_arrow(
        result.primary,
        replace(result.contract, pending_checks=prepared.admitted.checks),
        parts=result.parts,
        method_state=result.method_state,
        completed_checks=_ordered_checks(completed, prepared.admitted.checks),
        validate=False,
    )
