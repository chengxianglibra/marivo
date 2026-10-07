"""Attribution binding, sufficient-state transport and common exchange verification."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Literal

import pyarrow as pa

from marivo.analysis.core.graph import Edge, MethodNode, Node, method_node
from marivo.analysis.core.model import (
    AttributionPart,
    Coordinate,
    CoordinateStatePart,
    DomainSignature,
    EndpointPart,
    OriginalStatePart,
    Signature,
)
from marivo.analysis.core.rules import (
    AttributionDerive,
    CellDerive,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
    numeric_primary,
)
from marivo.analysis.materialization.graph_protocol import digest
from marivo.analysis.materialization.graph_relation import FrozenBinding, LiveBinding, Relation
from marivo.analysis.methods.attribution import (
    Basis,
    Errors,
    allocate,
    allocation_errors,
    component_errors,
    components,
    numeric,
    reconciles,
)
from marivo.analysis.methods.attribution import columns as columns
from marivo.analysis.methods.attribution import part_keys as part_keys
from marivo.analysis.methods.comparison import _finish
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    ScalarType,
    ValueType,
    arrow_scalar_type,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.state_validation import coordinate_state_matches, state_matches
from marivo.refs import DimensionKind, Ref, SemanticKind
from marivo.semantic.validator import normalize_target_dimension

ROLES = (
    "current_endpoint",
    "baseline_endpoint",
    "basis",
    "allocation",
    "reconciliation",
    "selection_scope",
)


def invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="an absolute Difference with complete original additive or N/W states and authored axes",
        received=received,
        repair="Observe both endpoints with the required contribution coordinates before materializing; preserve complete coverage and the original method.",
        location="analysis.attribution",
        help_target="dsl.LogicalDifferenceRelation.attribute",
    )


def method(signature: Signature) -> Literal["additive_difference", "component_mix"]:
    states = [p.original_state for p in signature.parts if isinstance(p, EndpointPart)]
    if len(states) != 2 or any(
        s is None or s.temporal_policy in ("repeated", "overlapping") or s.fold_kind is not None
        for s in states
    ):
        raise invalid("missing state, illegal fold or unproved temporal partition")
    methods = {s.method_version for s in states if s is not None}
    if len(methods) != 1:
        raise invalid("endpoint original methods differ")
    name = next(iter(methods))
    if name in ("sum@v1", "sum_zero@v1", "count@v1", "linear@v1"):
        return "additive_difference"
    if name in ("mean@v1", "weighted_mean@v1", "ratio@v1"):
        return "component_mix"
    raise invalid("distinct/distribution/extrema state has no R6 allocation method")


def expand(
    node: MethodNode, axes: tuple[Ref[DimensionKind], ...], relation: Relation
) -> MethodNode:
    params = node.parameters
    if isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
        if not isinstance(relation.binding, LiveBinding):
            raise invalid("fixed endpoint is missing retained axes")
        fields = tuple(
            normalize_target_dimension(relation.binding.graph.registry, a.path) for a in axes
        )
        if any(
            f.entity_ref.path != params.contribution.path
            or f.parse is not None
            or f.is_time_dimension
            for f in fields
        ):
            raise invalid("axes must be direct Dimensions on each contribution root")
        fields = tuple(replace(f, logical_type="string") for f in fields)
        return method_node(
            node.inputs,
            replace(params, coordinates=fields),
            sources=node.sources,
            value_type=node.value_type,
        )
    if isinstance(params, (OriginalRatio, OccurrenceCombine, OriginalReduce)):
        children = tuple(
            Edge(e.role, expand(e.node, axes, relation)) if isinstance(e.node, MethodNode) else e
            for e in node.inputs
        )
        return method_node(children, params, value_type=node.value_type)
    raise invalid("no retained observation definition for explicit axis expansion")


def bind(
    relation: Relation,
    axes: tuple[Ref[DimensionKind], ...],
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
) -> Relation:
    if (
        type(axes) is not tuple
        or not axes
        or any(type(a) is not Ref or a.kind is not SemanticKind.DIMENSION for a in axes)
        or len(set(axes)) != len(axes)
        or mode not in ("joint", "hierarchy")
        or (mode == "hierarchy" and len(axes) < 2)
        or (top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 1000))
    ):
        raise invalid(
            "nonempty unique ordered Dimensions; hierarchy requires two axes; top_k must be None or integer 1..1000 excluding bool"
        )
    definition = relation.definition
    if (
        not isinstance(definition.parameters, CellDerive)
        or definition.parameters.method != "difference"
        or definition.parameters.pairing != "exact"
    ):
        raise invalid(
            "relative, nested, selected or missing-side changes have no complete original allocation authority"
        )
    chosen = method(relation.root.signature)
    physical = relation.root.value_type
    if chosen == "component_mix" and isinstance(physical, DurationType):
        raise invalid("Duration component allocation is unqualified")
    endpoints = [p for p in relation.root.signature.parts if isinstance(p, EndpointPart)]
    retained = all(
        p.coordinate_state is not None
        and tuple(c.field for c in p.coordinate_state.coordinates) == tuple(a.path for a in axes)
        for p in endpoints
    )
    expanded: Node = relation.root
    if not retained:
        if isinstance(relation.binding, FrozenBinding):
            raise invalid(
                "fixed Difference lacks the requested retained axes; lineage cannot supply them"
            )
        children = tuple(
            Edge(e.role, expand(e.node, axes, relation)) if isinstance(e.node, MethodNode) else e
            for e in definition.inputs
        )
        expanded = method_node(children, definition.parameters, value_type=physical)
    source = relation.root.signature.domain
    owner = axes[0].path.rsplit(".", 1)[0]
    from marivo.refs import ref

    entity = ref.entity(owner)
    keys = (
        *source.instance_key,
        Coordinate(entity, "attribution:resolution", "group"),
        *(Coordinate(entity, "attribution:axis:" + a.path, "group") for a in axes),
        Coordinate(entity, "attribution:other_mask", "group"),
    )
    domain = DomainSignature(
        source.binding,
        "group",
        keys,
        keys,
        digest(definition.fingerprint + repr((axes, mode, top_k))),
        time_grid=source.time_grid,
    )
    root = method_node(
        (relation._edge(), Edge("quantity", expanded)),
        AttributionDerive(domain, axes, chosen, physical.name, mode, top_k),
        value_type=physical,
        retained_endpoints=(definition, definition)
        if isinstance(relation.binding, FrozenBinding)
        else (),
    )
    return relation._with(root)


def retain_endpoint_states(
    signature: Signature,
    parts: tuple[ExchangePart, ...],
    values: tuple[ExchangeResult, ...],
    baseline_keys: dict[tuple[object, ...], tuple[object, ...]],
) -> tuple[ExchangePart, ...]:
    output = []
    for p in parts:
        declaration = next(
            s
            for s in signature.parts
            if isinstance(s, EndpointPart) and s.side + "_endpoint" == p.role
        )
        if declaration.original_state is None:
            output.append(p)
            continue
        source = values[0 if declaration.side == "current" else 1]
        keys = source.contract.key_fields
        table = p.table
        originals = {
            tuple(r[k] for k in keys): r
            for r in next(p.table for p in source.parts if p.role == "original_state").to_pylist()
        }
        coordinate = next(
            (p.table for p in source.parts if p.role in ("coordinate_state", "allocation_state")),
            None,
        )
        groups = (
            {tuple(r[k] for k in keys): r for r in coordinate.to_pylist()}
            if coordinate is not None
            else {}
        )
        coverage = {
            tuple(r[k] for k in keys): r
            for r in next(p.table for p in source.parts if p.role == "coverage").to_pylist()
        }
        identities = [tuple(r[k] for k in keys) for r in table.to_pylist()]
        identities = [
            baseline_keys.get(k, k) if declaration.side == "baseline" else k for k in identities
        ]
        for c in declaration.original_state.components:
            original = next(p.table for p in source.parts if p.role == "original_state")
            table = table.append_column(
                p.role + "__state__" + c,
                pa.array(
                    [originals[k]["original_state__" + c] for k in identities],
                    type=original.schema.field("original_state__" + c).type,
                ),
            )
        if coordinate is not None:
            table = table.append_column(
                p.role + "__groups",
                pa.array(
                    [
                        groups[k][
                            "allocation_state__groups"
                            if declaration.coordinate_state
                            and declaration.coordinate_state.attribution_only
                            else "coordinate_state__groups"
                        ]
                        for k in identities
                    ],
                    type=coordinate.schema.field(
                        "allocation_state__groups"
                        if declaration.coordinate_state
                        and declaration.coordinate_state.attribution_only
                        else "coordinate_state__groups"
                    ).type,
                ),
            )
        table = table.append_column(
            p.role + "__complete",
            pa.array([coverage[k]["coverage__complete"] for k in identities], type=pa.bool_()),
        )
        output.append(ExchangePart(p.role, table))
    return tuple(output)


def validate_endpoint(declaration: EndpointPart, table: pa.Table, *, complete: bool = True) -> None:
    state = declaration.original_state
    if state is None:
        return
    prefix = declaration.side + "_endpoint__"
    table = numeric_primary(
        table.rename_columns(["value" if n == prefix + "value" else n for n in table.column_names])
    ).rename_columns([prefix + "value" if n == "value" else n for n in table.column_names])
    for row in table.to_pylist():
        original = {"original_state__" + c: row[prefix + "state__" + c] for c in state.components}
        cell = {c: row[prefix + c] for c in ("value", "cell_tag", "cell_reason")}
        if (complete and row[prefix + "complete"] is not True) or not state_matches(
            "original_" + state.method_version.removesuffix("@v1"),
            cell,
            original,
            empty_rules=state.empty_rules,
        ):
            raise invalid("endpoint Cell, original sufficient state or coverage disagrees")
        coord = declaration.coordinate_state
        if coord is not None and not coordinate_state_matches(
            coord.components,
            coord.value_type,
            row[prefix + "groups"],
            original,
            coord.columns,
            coord.component_types,
        ):
            raise invalid("endpoint coordinate partition does not reproduce its original state")


def validate_endpoints(
    signature: Signature, parts: tuple[ExchangePart, ...], keys: tuple[str, ...]
) -> None:
    for declaration in signature.parts:
        if isinstance(declaration, EndpointPart):
            validate_endpoint(
                declaration,
                next(p.table for p in parts if p.role == declaration.side + "_endpoint"),
                complete=False,
            )


def fixed(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    parts = []
    for p in node.signature.parts:
        assert isinstance(p, AttributionPart)
        if p.role in ("current_endpoint", "baseline_endpoint"):
            parts.append(next(x for x in inputs[1].parts if x.role == p.role))
        elif p.role == "basis":
            table = numeric_primary(inputs[0].primary).select(
                (*inputs[0].contract.key_fields, "value")
            )
            parts.append(
                ExchangePart(
                    "basis", table.rename_columns([*inputs[0].contract.key_fields, "basis__value"])
                )
            )
    return result(node.signature, node.value_type, tuple(parts), binding, node.method)


def result(
    signature: Signature,
    physical: ValueType,
    retained: tuple[ExchangePart, ...],
    binding: str,
    method_key: MethodKey | None = None,
    *,
    verify: bool = True,
) -> ExchangeResult:
    from marivo.analysis.methods.semantics import MethodKey

    declarations: dict[str, AttributionPart] = {
        p.role: p for p in signature.parts if isinstance(p, AttributionPart)
    }
    basis_decl = declarations["basis"]
    mix = basis_decl.method == "component_mix"
    by_role = {p.role: p.table for p in retained}
    scope_keys = part_keys(basis_decl)
    output_keys = part_keys(declarations["allocation"])
    sides = []
    for role in ("current_endpoint", "baseline_endpoint"):
        endpoint = declarations[role].endpoint
        assert endpoint is not None
        validate_endpoint(endpoint, by_role[role])
        sides.append(
            {
                tuple(r[k] for k in scope_keys): r
                for r in numeric_primary(
                    by_role[role].rename_columns(
                        [
                            "value" if c == role + "__value" else c
                            for c in by_role[role].column_names
                        ]
                    )
                )
                .rename_columns(
                    [role + "__value" if c == "value" else c for c in by_role[role].column_names]
                )
                .to_pylist()
            }
        )
    basis_rows = numeric_primary(
        by_role["basis"].rename_columns(
            ["value" if c == "basis__value" else c for c in by_role["basis"].column_names]
        )
    ).to_pylist()
    if any(set(side) != {tuple(r[k] for k in scope_keys) for r in basis_rows} for side in sides):
        raise invalid("basis and ordered endpoint key sets differ")
    output_rows: list[dict[str, object]] = []
    allocation_rows: list[dict[str, object]] = []
    reconciliation_rows: list[dict[str, object]] = []
    for row in basis_rows:
        scope = tuple(row[k] for k in scope_keys)
        bases: list[Basis] = []
        totals: list[Fraction] = []
        numerator_errors: list[Errors] = []
        denominator_errors: list[float] = []
        endpoint_values: list[Fraction] = []
        for index, role in enumerate(("current_endpoint", "baseline_endpoint")):
            endpoint = declarations[role].endpoint
            assert (
                endpoint is not None
                and endpoint.original_state is not None
                and endpoint.coordinate_state is not None
            )
            data = sides[index][scope]
            prefix = role + "__"
            if data[prefix + "cell_tag"] != "defined":
                raise invalid("both overall original endpoints must be Defined")
            state = {c: data[prefix + "state__" + c] for c in endpoint.original_state.components}
            n, w = components(state, endpoint.original_state.method_version)
            denominator_errors.append(
                component_errors(state, endpoint.original_state.method_version)[1]
                if physical == ScalarType("float64")
                else 0.0
            )
            if mix and w == 0:
                raise invalid("zero total component denominator")
            exact = n / w if mix else n
            endpoint_values.append(numeric(_finish(exact, physical)))
            if endpoint_values[-1] != numeric(data[prefix + "value"]):
                raise invalid("endpoint is not reproduced by its complete original components")
            totals.append(w)
            groups = data[prefix + "groups"]
            if not isinstance(groups, list):
                raise invalid("missing typed coordinate component partition")
            basis: Basis = {}
            errors: Errors = {}
            for group in groups:
                coordinate = tuple(group[c] for c in endpoint.coordinate_state.columns)
                if any(v is not None and not isinstance(v, str) for v in coordinate):
                    raise invalid("only qualified string contribution coordinates are accepted")
                if coordinate in basis:
                    raise invalid("duplicate complete axis tuples")
                basis[coordinate] = components(group, endpoint.original_state.method_version)
                errors[coordinate] = (
                    component_errors(group, endpoint.original_state.method_version)[0]
                    if physical == ScalarType("float64")
                    else 0.0
                )
            bases.append(basis)
            numerator_errors.append(errors)
        target = numeric(row["value"])
        if target != numeric(_finish(endpoint_values[0] - endpoint_values[1], physical)):
            raise invalid("expanded endpoints do not reproduce the original input target")
        try:
            allocations = allocate(
                bases[0],
                bases[1],
                size=len(basis_decl.axes),
                mode=basis_decl.mode,
                top_k=basis_decl.top_k,
                component_mix=mix,
                physical=physical,
                current_total=totals[0],
                baseline_total=totals[1],
            )
            bounds = allocation_errors(
                bases[0],
                bases[1],
                numerator_errors[0],
                numerator_errors[1],
                allocations,
                size=len(basis_decl.axes),
                top_k=basis_decl.top_k,
                component_mix=mix,
                current_total=totals[0],
                baseline_total=totals[1],
                current_denominator_error=denominator_errors[0],
                baseline_denominator_error=denominator_errors[1],
            )
        except (ValueError, OverflowError) as error:
            raise invalid(str(error)) from error
        totals_by_resolution = {
            resolution: sum((numeric(a[4]) for a in allocations if a[0] == resolution), Fraction())
            for resolution in {a[0] for a in allocations}
        }
        for resolution in totals_by_resolution:
            values = [a[4] for a in allocations if a[0] == resolution]
            if not reconciles(target, values, not mix and physical != ScalarType("float64")):
                raise invalid(
                    "each complete scope/resolution must reconcile; no balancing residual is allowed"
                )
        for resolution, coordinate, current, baseline, contribution in allocations:
            identity = dict(
                zip(
                    output_keys,
                    (
                        *scope,
                        resolution,
                        *(c[0] for c in coordinate),
                        sum(1 << i for i, c in enumerate(coordinate) if c[1]),
                    ),
                    strict=True,
                )
            )
            output_rows.append(
                {**identity, "value": contribution, "cell_tag": "defined", "cell_reason": None}
            )
            allocation_rows.append(
                {
                    **identity,
                    "allocation__contribution": contribution,
                    "allocation__current": current,
                    "allocation__baseline": baseline,
                    **dict(
                        zip(
                            (
                                "allocation__contribution_error_bound",
                                "allocation__current_error_bound",
                                "allocation__baseline_error_bound",
                            ),
                            bounds[resolution, coordinate],
                            strict=True,
                        )
                    ),
                }
            )
            total = totals_by_resolution[resolution]
            reconciliation_rows.append(
                {
                    **identity,
                    "reconciliation__target": _finish(target, physical),
                    "reconciliation__total": _finish(total, physical),
                    "reconciliation__complete": True,
                }
            )
    scope_schema = [by_role["basis"].schema.field(k) for k in scope_keys]
    key_schema = pa.schema(
        [
            *scope_schema,
            pa.field(output_keys[len(scope_keys)], pa.int64()),
            *(pa.field(k, pa.string()) for k in output_keys[len(scope_keys) + 1 : -1]),
            pa.field(output_keys[-1], pa.int64()),
        ]
    )
    dtype = arrow_scalar_type(physical)
    primary = pa.Table.from_pylist(
        output_rows,
        schema=pa.schema(
            [
                *key_schema,
                pa.field("value", dtype),
                pa.field("cell_tag", pa.string()),
                pa.field("cell_reason", pa.string()),
            ]
        ),
    )
    allocation = pa.Table.from_pylist(
        allocation_rows,
        schema=pa.schema(
            [
                *key_schema,
                *(
                    pa.field("allocation__" + c, dtype)
                    for c in ("contribution", "current", "baseline")
                ),
                *(
                    pa.field("allocation__" + c + "_error_bound", pa.float64())
                    for c in ("contribution", "current", "baseline")
                ),
            ]
        ),
    )
    reconciliation = pa.Table.from_pylist(
        reconciliation_rows,
        schema=pa.schema(
            [
                *key_schema,
                pa.field("reconciliation__target", dtype),
                pa.field("reconciliation__total", dtype),
                pa.field("reconciliation__complete", pa.bool_()),
            ]
        ),
    )
    selected = primary.select(output_keys).append_column(
        "selection_scope__selected", pa.array([True] * len(primary), type=pa.bool_())
    )
    parts = (
        *retained,
        ExchangePart("allocation", allocation),
        ExchangePart("reconciliation", reconciliation),
        ExchangePart("selection_scope", selected),
    )
    state = primary.select(output_keys).append_column("status", primary["cell_tag"])
    key = method_key or MethodKey(
        "attribution.component_mix" if mix else "attribution.additive_difference"
    )
    contract = ExchangeContract(
        signature,
        key,
        binding,
        primary.schema,
        output_keys,
        tuple(PartContract(p.role, p.table.schema, part_keys(declarations[p.role])) for p in parts),
        (),
        "attribution_component_mix" if mix else "attribution_additive",
        state.schema,
    )
    # The same validator runs on publication and cold receipt recovery.
    return (
        from_arrow(primary, contract, parts=parts, method_state=state)
        if verify
        else ExchangeResult(contract, primary, parts, (), state)
    )


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declarations: dict[str, AttributionPart] = {
        p.role: p for p in contract.signature.parts if isinstance(p, AttributionPart)
    }
    if tuple(declarations) != ROLES:
        raise invalid("missing or reordered allocation evidence")
    basis = declarations["basis"]
    allocation = declarations["allocation"]
    if allocation.domain != contract.signature.domain or any(
        (p.axes, p.method, p.mode, p.top_k, p.complete, p.view)
        != (basis.axes, basis.method, basis.mode, basis.top_k, basis.complete, basis.view)
        or p.domain != (basis.domain if p.role in ROLES[:3] else allocation.domain)
        for p in declarations.values()
    ):
        raise invalid("allocation declarations disagree on scope, mapping, rule or selected view")
    by_role = {p.role: p.table for p in parts}
    for role in ("current_endpoint", "baseline_endpoint"):
        endpoint = declarations[role].endpoint
        assert endpoint is not None
        validate_endpoint(endpoint, by_role[role])
    # Reproduce complete allocation using the arithmetic owner, without entering exchange validation recursively.
    full = result(
        replace(contract.signature, parts=tuple(declarations.values())),
        physical_type(primary.schema.field("value").type),
        tuple(p for p in parts if p.role in ROLES[:3]),
        contract.input_binding,
        verify=False,
    )
    for role in ("allocation", "reconciliation"):
        wanted = next(p.table for p in full.parts if p.role == role)
        keys = part_keys(declarations[role])
        actual_rows = {tuple(r[k] for k in keys): r for r in by_role[role].to_pylist()}
        wanted_rows = {tuple(r[k] for k in keys): r for r in wanted.to_pylist()}
        if actual_rows != wanted_rows or not by_role[role].schema.equals(
            wanted.schema, check_metadata=False
        ):
            raise invalid("retained allocation or reconciliation differs from original basis")
    keys = contract.key_fields
    originals = {
        tuple(r[k] for k in keys): r
        for r in numeric_primary(
            by_role["allocation"].rename_columns(
                [
                    "value" if c == "allocation__contribution" else c
                    for c in by_role["allocation"].column_names
                ]
            )
        ).to_pylist()
    }
    selected = {
        tuple(r[k] for k in keys)
        for r in by_role["selection_scope"].to_pylist()
        if r["selection_scope__selected"] is True
    }
    if (
        len(selected) != len(by_role["selection_scope"])
        or len(selected) != len(primary)
        or selected != {tuple(r[k] for k in keys) for r in primary.to_pylist()}
    ):
        raise invalid("selection map differs from exact primary keys")
    if declarations["selection_scope"].complete and selected != set(originals):
        raise invalid("complete partition promise differs from selected keys")
    view = declarations["selection_scope"].view
    view_rows = numeric_primary(
        by_role["allocation"].rename_columns(
            [
                "value" if c == "allocation__" + view else c
                for c in by_role["allocation"].column_names
            ]
        )
    ).to_pylist()
    view_values = {tuple(r[k] for k in keys): r["value"] for r in view_rows}
    for row in numeric_primary(primary).to_pylist():
        identity = tuple(row[k] for k in keys)
        if (
            identity not in originals
            or row["value"] != view_values[identity]
            or row["cell_tag"] != "defined"
            or row["cell_reason"] is not None
        ):
            raise invalid("selected numeric view differs from original allocation")


def physical_type(dtype: pa.DataType) -> ValueType:
    if pa.types.is_decimal(dtype):
        return DecimalType(dtype.precision, dtype.scale)
    if pa.types.is_duration(dtype):
        return DurationType(dtype.unit)
    return ScalarType("float64" if pa.types.is_floating(dtype) else "int64")


def pack(result: ExchangeResult, schema: pa.Schema) -> pa.Table:
    """Pack a calculated exchange into its Ibis-issued staging schema."""
    declarations: dict[str, AttributionPart] = {
        p.role: p for p in result.contract.signature.parts if isinstance(p, AttributionPart)
    }
    keyed = {
        p.role: {
            tuple(r[k] for k in part_keys(declarations[p.role])): r for r in p.table.to_pylist()
        }
        for p in result.parts
    }
    rows = []
    for primary in result.primary.to_pylist():
        row = dict(primary)
        for role, declaration in declarations.items():
            identity = tuple(primary[k] for k in part_keys(declaration))
            row.update(
                {
                    k: v
                    for k, v in keyed[role][identity].items()
                    if k not in result.contract.key_fields
                }
            )
        rows.append(row)
    arrays = []
    for field in schema:
        actual = pa.array(
            [r[field.name] for r in rows],
            type=result.primary.schema.field(field.name).type
            if field.name in result.primary.schema.names
            else next(
                p.table.schema.field(field.name).type
                for p in result.parts
                if field.name in p.table.column_names
            ),
        )
        arrays.append(actual.cast(field.type, safe=True))
    return pa.Table.from_arrays(arrays, schema=schema)


def view(relation: Relation, name: Literal["contribution", "current", "baseline"]) -> Relation:
    from marivo.analysis.core.model import part_role

    return relation._with(
        method_node(
            (relation._edge(),),
            PartsTransport(
                "view",
                relation.root.signature.domain,
                tuple(part_role(p) for p in relation.root.signature.parts),
                True,
                attribution_view=name,
            ),
            value_type=relation.root.value_type,
        )
    )


def project(
    source: ExchangeResult, name: Literal["contribution", "current", "baseline"]
) -> ExchangeResult:
    if any(p.role == "funnel_state" for p in source.parts):
        from marivo.analysis.materialization.funnel_execution import project as funnel_project

        return funnel_project(source, name)
    from marivo.analysis.core.model import part_role
    from marivo.analysis.methods.registry import REGISTRY

    signature = REGISTRY.derive(
        (source.contract.signature,),
        PartsTransport(
            "view",
            source.contract.signature.domain,
            tuple(part_role(p) for p in source.contract.signature.parts),
            True,
            attribution_view=name,
        ),
    ).output
    keys = source.contract.key_fields
    values = {
        tuple(r[k] for k in keys): r["allocation__" + name]
        for r in next(p.table for p in source.parts if p.role == "allocation").to_pylist()
    }
    primary = source.primary.set_column(
        source.primary.schema.get_field_index("value"),
        "value",
        pa.array(
            [values[tuple(r[k] for k in keys)] for r in source.primary.to_pylist()],
            type=source.primary.schema.field("value").type,
        ),
    )
    return from_arrow(
        primary,
        replace(
            source.contract,
            signature=signature,
            method=MethodKey("parts_transport"),
            state_kind="none",
            state_schema=None,
            pending_checks=(),
            _frozen=None,
        ),
        parts=source.parts,
        validate=False,
    )


def retain_partition(
    node: MethodNode, source: ExchangeResult, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> tuple[ExchangePart, ...]:
    """Transport sufficient coordinate partitions, without restoring removed axes."""
    coordinate = next((p for p in node.signature.parts if isinstance(p, CoordinateStatePart)), None)
    if coordinate is None:
        return parts
    from marivo.analysis.core.model import part_role
    from marivo.analysis.methods.numeric_state import merge_original

    params = node.parameters
    assert isinstance(params, OriginalReduce)
    original = next(
        p for p in source.contract.signature.parts if isinstance(p, CoordinateStatePart)
    )
    original_state = next(
        p for p in source.contract.signature.parts if isinstance(p, OriginalStatePart)
    )
    state = next(p.table for p in source.parts if p.role == part_role(original))
    source_keys = source.contract.signature.domain.instance_key
    if params.time_mapping:
        grid = params.output_domain.time_grid
        assert grid is not None
        index = next(i for i, c in enumerate(source_keys) if c.role == "anchor")
        field = f"key_{index}"
        mapping = dict(params.time_mapping)
        state = state.set_column(
            state.schema.get_field_index(field),
            field,
            pa.array([mapping[v] for v in state[field].to_pylist()], type=pa.string()),
        )
        source_keys = tuple(
            replace(c, field="time:" + grid.identity) if c.role == "anchor" else c
            for c in source_keys
        )
    grouped: dict[tuple[object, ...], dict[tuple[object, ...], list[dict[str, object]]]] = {}
    for row in state.to_pylist():
        for group in row[part_role(original) + "__groups"]:
            target = tuple(
                row[f"key_{source_keys.index(c)}"]
                if c in source_keys
                else group[original.columns[original.coordinates.index(c)]]
                for c in params.coordinates
            )
            axis = tuple(group[c] for c in original.columns)
            grouped.setdefault(target, {}).setdefault(axis, []).append(group)
    merged = {}
    schema = next(p.table.schema for p in source.parts if p.role == "original_state")
    for identity, coordinates in grouped.items():
        values = []
        for axis, rows in sorted(
            coordinates.items(),
            key=lambda item: tuple((0, "") if v is None else (1, str(v)) for v in item[0]),
        ):
            totals, _, _, _ = merge_original(
                rows,
                schema,
                original.components,
                params.method,
                node.value_type,
                original_state.empty_rules,
            )
            values.append({**dict(zip(original.columns, axis, strict=True)), **totals})
        merged[identity] = values
    keys = tuple(f"key_{i}" for i in range(len(params.coordinates)))
    table = primary.select(keys).append_column(
        "allocation_state__groups",
        pa.array(
            [merged.get(tuple(r[k] for k in keys), []) for r in primary.to_pylist()],
            type=state.schema.field(part_role(original) + "__groups").type,
        ),
    )
    return (*parts, ExchangePart("allocation_state", table))
