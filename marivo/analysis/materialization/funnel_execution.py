"""Registered funnel consumers and exact retained component validation."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Literal

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    Defined,
    DerivedQuantity,
    DomainSignature,
    EntryAxesPart,
    FindingPolicyPart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    part_role,
)
from marivo.analysis.core.rules import (
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelField,
    FunnelRead,
    FunnelReduce,
    OccurrencePrepare,
    PartsTransport,
)
from marivo.analysis.materialization.cell_arrow import project as cell_project
from marivo.analysis.materialization.cell_arrow import required
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.journey_execution import ASSIGNMENT
from marivo.analysis.methods import funnel as f
from marivo.analysis.methods.domain_coverage import FACTS
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import MethodKey

State = f.EntryAxisState | f.FunnelState | f.ComparisonState | f.AllocationState
ADAPTER: TypeAdapter[State] = TypeAdapter(State)
AXIS: TypeAdapter[f.Axis] = TypeAdapter(f.Axis)


def _retained(role: str, state: State) -> ExchangePart:
    return ExchangePart(role, pa.table({role + "__retained": [ADAPTER.dump_json(state).decode()]}))


def _state(
    parts: tuple[ExchangePart, ...],
    role: str,
    kind: type[f.EntryAxisState]
    | type[f.FunnelState]
    | type[f.ComparisonState]
    | type[f.AllocationState],
) -> State:
    table = next(p.table for p in parts if p.role == role)
    payload = table[role + "__retained"][0].as_py()
    try:
        value = TypeAdapter(kind).validate_json(payload, strict=True)
    except (ValidationError, ValueError):
        fail("funnel_state", "invalid retained state encoding", stage="recovery")
    if TypeAdapter(kind).dump_json(value).decode() != payload:
        fail("funnel_state", "noncanonical or extra retained state fields", stage="recovery")
    return value


def _result(
    node: MethodNode, primary: pa.Table, parts: tuple[ExchangePart, ...], binding: str
) -> ExchangeResult:
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    state_kind = REGISTRY.lookup(node.method).semantics.persistent_state_kind
    assert state_kind is not None
    statuses = (
        None
        if state_kind == "none"
        else pa.Table.from_arrays(
            [
                *(primary[key] for key in keys),
                pa.array(["accepted"] * primary.num_rows, type=pa.string()),
            ],
            names=[*keys, "status"],
        )
    )
    return from_arrow(
        primary,
        ExchangeContract(
            node.signature,
            node.method,
            binding,
            primary.schema,
            keys,
            tuple(PartContract(p.role, p.table.schema, ()) for p in parts),
            (("undefined", ("initial_step", "zero_denominator")),),
            state_kind,
            None if statuses is None else statuses.schema,
        ),
        parts=parts,
        method_state=statuses,
        validate=False,
    )


def assemble_axes(base: pa.Table, mappings: tuple[pa.Table, ...]) -> pa.Table:
    """Attach independent exact mappings by the complete captured occurrence key."""
    keys = tuple(base.column_names)
    positions: dict[tuple[object, ...], int] = {}
    for index, row in enumerate(base.to_pylist()):
        check()
        key = tuple(row[name] for name in keys)
        if None in key or key in positions:
            fail("entry_axes", "nonunique or null occurrence identity", stage="prepare")
        positions[key] = index
    columns: dict[str, pa.Array] = {}
    for mapping in mappings:
        check()
        indices: list[int | None] = [None] * base.num_rows
        for index, row in enumerate(cell_project(mapping, keys).to_pylist()):
            check()
            key = tuple(row[name] for name in keys)
            if key not in positions or indices[positions[key]] is not None:
                fail("entry_axes", "duplicate or foreign path mapping", stage="prepare")
            indices[positions[key]] = index
        if any(index is None for index in indices):
            fail("entry_axes", "missing path mapping", stage="prepare")
        for name in mapping.column_names:
            if name not in keys:
                if name in columns:
                    fail("entry_axes", "duplicate captured axis", stage="prepare")
                columns[name] = (
                    mapping[name].take(pa.array(indices, type=pa.int64())).combine_chunks()
                )
    table = base
    for name in sorted(columns, key=lambda name: int(name.removeprefix("axis_"))):
        table = table.append_column(name, columns[name])
    return table


def axes_result(node: MethodNode, table: pa.Table, binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, FunnelAxesPrepare)
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    rows = []
    for row in table.to_pylist():
        check()
        coordinate = tuple(
            AXIS.validate_python(row[f"axis_{i}"], strict=True) for i in range(len(params.axes))
        )
        rows.append(f.EntryAxisRow(str(row[keys[0]]), tuple(row[k] for k in keys[1:]), coordinate))
    state = f.EntryAxisState(
        tuple(rows), (table.schema.metadata or {})[b"r7.capture_authority"].decode()
    )
    return _result(node, cell_project(table, keys), (_retained("entry_axes", state),), binding)


def _coverage(state: f.FunnelState, declaration: FunnelPart) -> None:
    try:
        facts = FACTS.validate_json(state.coverage, strict=True)
    except ValidationError:
        fail("funnel_coverage", "invalid retained coverage", stage="recovery")
    from marivo.analysis.materialization.journey_execution import validate_assignments
    from marivo.analysis.methods.domain_coverage import coverage

    validate_assignments(state.assignments, declaration.journey)
    captured = declaration.journey.preparation
    params = OccurrencePrepare(
        DomainSignature(captured.binding, "singleton", (), (), declaration.capture_scope),
        captured.events,
        captured.start,
        captured.end,
        captured.order,
        captured.model,
        captured.completeness,
        captured.order_use,
        captured.terminal_state,
    )
    expected = params.events
    recomputed = coverage(
        params, tuple(fact.observed for fact in facts if fact.observed is not None)
    )
    if tuple(facts) != recomputed:
        fail(
            "funnel_coverage",
            "retained coverage differs from frozen declarations/receipts",
            stage="recovery",
        )
    if (
        len(facts) != len(expected)
        or any(
            (
                fact.event,
                fact.fingerprint,
                fact.source_id,
                fact.source_fingerprint,
                fact.subject,
                fact.participant,
            )
            != (
                event.ref.path,
                event.fingerprint,
                event.source_id,
                event.source.dependency_fingerprint,
                event.subject.ref.path,
                event.participant,
            )
            for fact, event in zip(facts, expected, strict=True)
        )
        or any(fact.scope != params.output.definition_id for fact in facts)
        or state.complete != all(fact.complete for fact in facts)
    ):
        fail("funnel_coverage", "coverage differs from captured Event bindings", stage="recovery")


def _key(step: int, coordinates: f.Coordinates) -> dict[str, object]:
    return {
        "key_0": step,
        **{f"key_{i + 1}": AXIS.dump_json(v).decode() for i, v in enumerate(coordinates)},
    }


def _cell(row: dict[str, object], prefix: str, cell: object) -> None:
    row[prefix] = cell.value if isinstance(cell, Defined) else None
    row[prefix + "__cell_tag"] = "defined" if isinstance(cell, Defined) else "undefined"
    row[prefix + "__cell_reason"] = (
        None if isinstance(cell, Defined) else getattr(cell, "reason", None)
    )


def _rows(
    declaration: FunnelPart | FunnelComparisonPart | FunnelAllocationPart, state: State
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    if isinstance(declaration, FunnelPart):
        assert isinstance(state, f.FunnelState)
        _coverage(state, declaration)
        for item in f.components(state, len(declaration.journey.steps), len(declaration.axes)):
            row = {**_key(item.step, item.coordinates), **asdict(item.counts)}
            for rate in f.RATES:
                _cell(row, rate, f.rate(item, rate))
            row.update(value=0, cell_tag="defined", cell_reason=None)
            output.append(row)
    elif isinstance(declaration, FunnelComparisonPart):
        assert isinstance(state, f.ComparisonState)
        _coverage(state.current, declaration.current)
        _coverage(state.baseline, declaration.baseline)
        for pair in f.compare(
            state, len(declaration.current.journey.steps), len(declaration.current.axes)
        ):
            row = {**_key(pair.step, pair.coordinates), "presence": pair.presence}
            for side in ("current", "baseline"):
                endpoint = getattr(pair, side)
                row.update({side + "_" + k: v for k, v in asdict(endpoint.counts).items()})
                _cell(
                    row,
                    side + "_loss_rate_from_previous",
                    f.rate(endpoint, "loss_rate_from_previous"),
                )
            _cell(row, "loss_rate_delta", pair.delta)
            row.update(
                value=row["loss_rate_delta"],
                cell_tag=row["loss_rate_delta__cell_tag"],
                cell_reason=row["loss_rate_delta__cell_reason"],
            )
            output.append(row)
    else:
        assert isinstance(state, f.AllocationState)
        for endpoint, part in (
            (state.original.current, declaration.original.current),
            (state.original.baseline, declaration.original.baseline),
            (state.expanded.current, declaration.comparison.current),
            (state.expanded.baseline, declaration.comparison.baseline),
        ):
            _coverage(endpoint, part)
        for allocated in f.allocate(
            state,
            steps=len(declaration.comparison.current.journey.steps),
            axes=len(declaration.axes),
            target_step=declaration.target_step,
            mode=declaration.mode,
            top_k=declaration.top_k,
            original_axes=len(declaration.original.current.axes),
        ):
            row = _key(allocated.resolution, allocated.coordinates)
            row[f"key_{len(declaration.axes) + 1}"] = sum(
                1 << i for i, value in enumerate(allocated.other_mask) if value
            )
            row[f"key_{len(declaration.axes) + 2}"] = allocated.kind
            row.update(
                {
                    k: v
                    for k, v in asdict(allocated).items()
                    if k not in ("coordinates", "other_mask")
                }
            )
            row["active_axis_mask"] = json.dumps(
                [i < allocated.resolution for i in range(len(declaration.axes))]
            )
            row["other_mask"] = json.dumps(allocated.other_mask)
            row["status"] = "zero_total_delta" if allocated.target == 0 else "ok"
            for name, reason in (
                ("share_total", "zero_total_delta"),
                ("share_positive", "empty_positive_pool"),
                ("share_negative", "empty_negative_pool"),
            ):
                row[name + "__cell_tag"] = "undefined" if row[name] is None else "defined"
                row[name + "__cell_reason"] = reason if row[name] is None else None
            row.update(
                value=getattr(allocated, declaration.view), cell_tag="defined", cell_reason=None
            )
            output.append(row)
    return output


def _table(
    rows: list[dict[str, object]],
    declaration: FunnelPart | FunnelComparisonPart | FunnelAllocationPart,
) -> pa.Table:
    # Establish the same closed schema for empty and nonempty grouped results.
    axes = (
        len(declaration.axes)
        if isinstance(declaration, (FunnelPart, FunnelAllocationPart))
        else len(declaration.current.axes)
    )
    fields = [
        pa.field("key_0", pa.int64()),
        *(pa.field(f"key_{i + 1}", pa.string()) for i in range(axes)),
    ]
    if isinstance(declaration, FunnelPart):
        fields.extend(pa.field(k, pa.int64()) for k in f.COUNTS)
        fields.extend(
            pa.field(k + suffix, pa.float64() if not suffix else pa.string())
            for k in f.RATES
            for suffix in ("", "__cell_tag", "__cell_reason")
        )
        value_type = pa.int64()
    elif isinstance(declaration, FunnelComparisonPart):
        fields.append(pa.field("presence", pa.string()))
        fields.extend(
            pa.field(side + "_" + k, pa.int64())
            for side in ("current", "baseline")
            for k in f.COUNTS
        )
        fields.extend(
            pa.field(k + suffix, pa.float64() if not suffix else pa.string())
            for k in (
                "current_loss_rate_from_previous",
                "baseline_loss_rate_from_previous",
                "loss_rate_delta",
            )
            for suffix in ("", "__cell_tag", "__cell_reason")
        )
        value_type = pa.float64()
    else:
        fields.extend(
            (pa.field(f"key_{axes + 1}", pa.int64()), pa.field(f"key_{axes + 2}", pa.string()))
        )
        fields.extend(
            pa.field(
                k,
                pa.int64()
                if k in ("resolution", "rank")
                else pa.string()
                if k in ("kind", "active_axis_mask", "other_mask", "status")
                else pa.float64(),
            )
            for k in (
                "resolution",
                "kind",
                "current",
                "baseline",
                "contribution",
                "target",
                "current_error",
                "baseline_error",
                "error",
                "share_total",
                "share_positive",
                "share_negative",
                "rank",
                "active_axis_mask",
                "other_mask",
                "status",
            )
        )
        fields.extend(
            pa.field(k + suffix, pa.string())
            for k in ("share_total", "share_positive", "share_negative")
            for suffix in ("__cell_tag", "__cell_reason")
        )
        value_type = pa.float64()
    fields.extend(
        (
            pa.field("value", value_type),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    return pa.Table.from_pylist(rows, schema=pa.schema(fields))


def execute(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    declaration = next(
        p
        for p in node.signature.parts
        if isinstance(p, (FunnelPart, FunnelComparisonPart, FunnelAllocationPart))
    )
    if isinstance(params, FunnelReduce):
        assignments = tuple(
            ASSIGNMENT.validate_json(row["journey__assignment"], strict=True)
            for row in next(p.table for p in inputs[0].parts if p.role == "journey").to_pylist()
        )
        axis_state = (
            _state(inputs[1].parts, "entry_axes", f.EntryAxisState) if params.axes else None
        )
        assert axis_state is None or isinstance(axis_state, f.EntryAxisState)
        metadata = inputs[0].primary.schema.metadata or {}
        authority = metadata[b"r7.capture_authority"].decode()
        if axis_state is not None and authority != axis_state.authority:
            fail(
                "funnel_axes",
                "entry axes and assignment came from different captures",
                stage="consume",
            )
        coverage = metadata[b"r7.coverage"].decode()
        state: State = f.FunnelState(
            assignments,
            () if axis_state is None else axis_state.rows,
            all(fact.complete for fact in FACTS.validate_json(coverage)),
            authority,
            coverage,
        )
    elif isinstance(params, FunnelCompare):
        endpoints = tuple(_state(item.parts, "funnel_state", f.FunnelState) for item in inputs)
        assert isinstance(endpoints[0], f.FunnelState) and isinstance(endpoints[1], f.FunnelState)
        state = f.ComparisonState(endpoints[0], endpoints[1])
    elif isinstance(params, FunnelAttribute):
        original, expanded = (
            _state(item.parts, "funnel_state", f.ComparisonState) for item in inputs
        )
        assert isinstance(original, f.ComparisonState) and isinstance(expanded, f.ComparisonState)
        if (
            original.current.assignments != expanded.current.assignments
            or original.baseline.assignments != expanded.baseline.assignments
            or original.current.authority != expanded.current.authority
            or original.baseline.authority != expanded.baseline.authority
        ):
            fail(
                "funnel_axes",
                "axis expansion changed the original assignments or capture",
                stage="consume",
            )
        state = f.AllocationState(original, expanded)
    else:
        assert isinstance(params, FunnelRead)
        state = _state(
            inputs[0].parts,
            "funnel_state",
            f.FunnelState if isinstance(declaration, FunnelPart) else f.ComparisonState,
        )
    rows = _rows(declaration, state)
    primary = _table(rows, declaration)
    if isinstance(params, FunnelRead):
        primary = _read_table(primary, params)
    parts = [_retained("funnel_state", state)]
    policy = next((p for p in node.signature.parts if isinstance(p, FindingPolicyPart)), None)
    if policy is not None:
        from marivo.analysis.materialization.graph_findings import policy_part

        parts.append(policy_part(node, primary, state))
    return _result(node, primary, tuple(parts), binding)


def _read_table(table: pa.Table, params: FunnelRead) -> pa.Table:
    if params.step is not None:
        table = table.filter(pa.compute.equal(table["key_0"], params.step))
    fields = (params.field, params.field + "__cell_tag", params.field + "__cell_reason")
    for target, field in zip(("value", "cell_tag", "cell_reason"), fields, strict=True):
        column = (
            table[field]
            if field in table.column_names
            else pa.array(
                ["defined" if target == "cell_tag" else None] * table.num_rows, type=pa.string()
            )
        )
        table = table.set_column(table.schema.get_field_index(target), target, column)
    return table


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declaration = next(
        p
        for p in contract.signature.parts
        if isinstance(p, (EntryAxesPart, FunnelPart, FunnelComparisonPart, FunnelAllocationPart))
    )
    if isinstance(declaration, EntryAxesPart):
        state = _state(parts, "entry_axes", f.EntryAxisState)
        assert isinstance(state, f.EntryAxisState)
        keys = {(r.event, *r.key) for r in state.rows}
        if (
            len(keys) != len(state.rows)
            or keys != {tuple(r[k] for k in contract.key_fields) for r in primary.to_pylist()}
            or any(
                r.event != declaration.first_event or len(r.coordinates) != len(declaration.axes)
                for r in state.rows
            )
        ):
            fail(
                "funnel_axes", "retained axis scope differs from captured entries", stage="recovery"
            )
        return
    state = _state(
        parts,
        "funnel_state",
        f.FunnelState
        if isinstance(declaration, FunnelPart)
        else f.ComparisonState
        if isinstance(declaration, FunnelComparisonPart)
        else f.AllocationState,
    )
    expected = _table(_rows(declaration, state), declaration)
    # Read projections preserve every original component and only replace the Cell.
    field = contract.signature.quantity
    if isinstance(field, DerivedQuantity) and field.method_version == "funnel.read@v1":
        params = FunnelRead(
            TypeAdapter(FunnelField).validate_python(field.input_ids[1], strict=True),
            None if field.input_ids[2] == "all" else int(field.input_ids[2]),
        )
        expected = _read_table(expected, params)
    by_key = {tuple(row[k] for k in contract.key_fields): row for row in expected.to_pylist()}
    actual_keys = set()
    for row in primary.to_pylist():
        check()
        key = tuple(row[k] for k in contract.key_fields)
        actual_keys.add(key)
        original = by_key.get(key)
        if original is None or any(
            name in original and original[name] != value for name, value in row.items()
        ):
            fail(
                "funnel_state",
                "primary differs from retained original components",
                stage="recovery",
            )
    if (
        declaration.complete
        and contract.method.name
        in ("funnel.reduce", "funnel.compare", "funnel.read", "funnel_ratio_mix")
        and actual_keys != set(by_key)
    ):
        fail("funnel_state", "complete result omitted retained component rows", stage="recovery")
    if any(p.role == "finding_policy" for p in parts):
        from marivo.analysis.materialization.graph_findings import validate_policy

        validate_policy(contract, primary, parts, state)


def transport(
    method: object, source: ExchangeResult, binding: str, dependencies: tuple[ExchangeResult, ...]
) -> ExchangeResult:
    from marivo.analysis.compiler.graph_lowering import LoweredLocal
    from marivo.analysis.core.predicates import compose, leaves
    from marivo.analysis.methods.predicates import evaluate_leaf

    assert isinstance(method, LoweredLocal)
    node = method.stage.node
    params = node.parameters
    assert isinstance(params, PartsTransport)
    keys = source.contract.key_fields
    rows = [
        {tuple(row[k] for k in keys): row for row in cell_rows(item.primary)}
        for item in (source, *dependencies)
    ]
    if any(set(r) != set(rows[0]) for r in rows[1:]):
        fail("funnel_selection", "predicate lacks corresponding complete keys", stage="consume")
    selected = set()
    for key in rows[0]:
        if all(
            compose(
                tree,
                tuple(
                    evaluate_leaf(
                        leaf,
                        rows[leaf.input_index][key],
                        None if leaf.right_index is None else rows[leaf.right_index][key],
                    )
                    for leaf in leaves(tree)
                ),
            )
            is True
            for tree in params.predicates
        ):
            selected.add(key)
    if params.mode == "limit":
        ordering = next(p.table for p in source.parts if p.role == "ordering")
        positions = {
            tuple(r[k] for k in keys): r["ordering__position"] for r in cell_rows(ordering)
        }
        selected = set(
            sorted(selected, key=lambda k: required(positions[k], int))[: params.limit_count]
        )
    primary = source.primary.filter(
        pa.array(
            [tuple(r[k] for k in keys) in selected for r in cell_rows(source.primary)],
            type=pa.bool_(),
        )
    )
    if params.attribution_view is not None:
        projected = project(source, params.attribution_view)
        primary = projected.primary.filter(
            pa.array(
                [tuple(r[k] for k in keys) in selected for r in cell_rows(projected.primary)],
                type=pa.bool_(),
            )
        )
    if params.display_view == "ranks":
        ranks = next(p.table for p in source.parts if p.role == "ranks")
        by_key = {tuple(r[k] for k in keys): r for r in cell_rows(ranks)}
        for name in ("value", "cell_tag", "cell_reason"):
            primary = primary.set_column(
                primary.schema.get_field_index(name),
                name,
                pa.array(
                    [
                        by_key[tuple(r[k] for k in keys)]["ranks__" + name]
                        for r in cell_rows(primary)
                    ],
                    type=ranks.schema.field("ranks__" + name).type,
                ),
            )
    parts = tuple(
        next(p for p in source.parts if p.role == part_role(declaration))
        for declaration in node.signature.parts
    )
    contracts = tuple(
        next(p for p in source.contract.parts if p.role == part.role) for part in parts
    )
    return from_arrow(
        primary,
        replace(
            source.contract,
            signature=node.signature,
            method=node.method,
            input_binding=binding,
            schema=primary.schema,
            parts=contracts,
            state_kind="none",
            state_schema=None,
            pending_checks=(),
        ),
        parts=parts,
        validate=False,
    )


def project(
    source: ExchangeResult, name: Literal["contribution", "current", "baseline"]
) -> ExchangeResult:
    params = PartsTransport(
        "view",
        source.contract.signature.domain,
        tuple(part_role(p) for p in source.contract.signature.parts),
        True,
        attribution_view=name,
    )
    signature = REGISTRY.derive((source.contract.signature,), params).output
    declaration = next(p for p in signature.parts if isinstance(p, FunnelAllocationPart))
    state = _state(source.parts, "funnel_state", f.AllocationState)
    expected = _table(_rows(declaration, state), declaration)
    values = {
        tuple(r[k] for k in source.contract.key_fields): r["value"] for r in expected.to_pylist()
    }
    primary = source.primary.set_column(
        source.primary.schema.get_field_index("value"),
        "value",
        pa.array(
            [
                values[tuple(r[k] for k in source.contract.key_fields)]
                for r in source.primary.to_pylist()
            ]
        ),
    )
    parts = tuple(p for p in source.parts if p.role != "finding_policy")
    return from_arrow(
        primary,
        replace(
            source.contract,
            signature=signature,
            method=MethodKey("parts_transport"),
            parts=tuple(p for p in source.contract.parts if p.role != "finding_policy"),
            state_kind="none",
            state_schema=None,
            pending_checks=(),
            _frozen=None,
        ),
        parts=parts,
        validate=False,
    )
