"""Reference graph binding and registered consumption of verified input tables."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import pyarrow as pa

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_relation import Relation

from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    method_node,
    retained_nodes,
    topology,
)
from marivo.analysis.core.model import (
    Cell,
    Defined,
    DerivedQuantity,
    DomainSignature,
    Null,
    OriginalStatePart,
    ReferenceStatePart,
    Signature,
    Undefined,
    Unknown,
)
from marivo.analysis.core.rules import (
    AttachCategory,
    DisplayRank,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    ReferenceDerive,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
    numeric_primary,
    reference_parameters,
)
from marivo.analysis.materialization.graph_protocol import digest
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    ScalarType,
    ValueType,
    arrow_scalar_type,
)
from marivo.analysis.methods.references import (
    number,
    penetration,
    reference_type,
    share,
    standardize,
    standardized_error,
    weight_sum,
)
from marivo.refs import EntityKind, Ref


def invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a proved fixed reference with complete compatible inputs",
        received=received,
        repair="Bind the original quantity, complete strata and the matching statistical Entity; do not normalize missing inputs.",
        location="analysis.reference",
        help_target="dsl.reference_weights",
    )


def original(node: MethodNode) -> MethodNode:
    while isinstance(
        node.parameters, (PartsTransport, OriginalReduce, AttachCategory, DisplayRank)
    ):
        if isinstance(node.parameters, PartsTransport) and node.parameters.display_view == "ranks":
            raise invalid("rank quantities have no original Metric state")
        children: tuple[Node, ...] = node.retained_endpoints or tuple(
            edge.node for edge in node.inputs
        )
        if not children or not isinstance(children[0], MethodNode):
            raise invalid("missing frozen original definition")
        node = children[0]
    return node


def statistical_unit(node: MethodNode) -> Ref[EntityKind]:
    params = original(node).parameters
    if isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
        if isinstance(params, ObserveMetric) and (
            params.method not in ("sum", "mean") or params.fold is not None
        ):
            raise invalid("quantity has no admitted standardization method")
        return params.contribution
    if isinstance(params, OriginalRatio):
        parent = original(node)
        children: tuple[Node, ...] = parent.retained_endpoints or tuple(
            edge.node for edge in parent.inputs
        )
        if len(children) != 2 or not isinstance(children[1], MethodNode):
            raise invalid("ratio has no frozen denominator definition")
        return statistical_unit(children[1])
    if isinstance(params, OccurrenceCombine):
        parent = original(node)
        children = parent.retained_endpoints or tuple(edge.node for edge in parent.inputs)
        units = {statistical_unit(child) for child in children if isinstance(child, MethodNode)}
        if len(units) == 1 and len(children) == len(params.terms):
            return next(iter(units))
    raise invalid("statistical unit cannot be proved from original components")


def share_basis(values: MethodNode, reference: MethodNode) -> MethodNode:
    if not isinstance(reference.parameters, OriginalReduce) or reference.parameters.coordinates:
        raise invalid("share reference must be an explicit original Singleton rollup")
    dependencies: tuple[Node, ...] = reference.retained_endpoints or tuple(
        edge.node for edge in reference.inputs
    )
    child = dependencies[0]
    if not isinstance(child, MethodNode):
        raise invalid("reference lost its additive support definition")
    method = original(child).parameters
    if not (
        isinstance(method, (ObserveCount, OccurrenceCombine))
        or (isinstance(method, ObserveMetric) and method.method == "sum" and method.fold is None)
    ):
        raise invalid("share requires an additive measure, not a mean or ordinary ratio")
    if child.identity not in {item.identity for item in retained_nodes(values)}:
        raise invalid("numerator lacks retained inclusion in the reference support")
    return child


def bind(
    values: Relation,
    reference: Relation,
    kind: Literal["share", "penetration", "standardize"],
    *,
    unit: Ref[EntityKind] | None = None,
    strata_dependencies: tuple[str, ...] = (),
    reference_id: str | None = None,
) -> Relation:
    from marivo.analysis.materialization.graph_relation import (
        FrozenBinding,
        LiveBinding,
        Relation,
        _shared_root,
    )

    if not isinstance(values, Relation) or not isinstance(reference, Relation):
        raise invalid("reference inputs must be typed Relations")
    if (
        values.runtime.session_ref != reference.runtime.session_ref
        or values.runtime.store.store_id != reference.runtime.store.store_id
    ):
        raise invalid("cross-Session reference")
    if isinstance(values.binding, LiveBinding) != isinstance(reference.binding, LiveBinding):
        raise invalid("mixed source/fixed reference")
    roots: tuple[Node, ...] = (values.root, _shared_root(values.root, reference.root))
    if kind == "share":
        basis = share_basis(values.definition, reference.definition)
        if isinstance(values.binding, FrozenBinding):
            candidates = tuple(
                item
                for item in retained_nodes(values.definition)
                if isinstance(item, FixedLeaf) and item.definition_fingerprint == basis.fingerprint
            )
            if (
                isinstance(values.root, FixedLeaf)
                and values.root.definition_fingerprint == basis.fingerprint
            ):
                candidates = (values.root, *candidates)
            dependencies = tuple(
                item for item in topology(reference.definition) if isinstance(item, FixedLeaf)
            )
            if not candidates or not any(
                item.artifact == candidates[0].artifact for item in dependencies
            ):
                raise invalid("fixed reference must roll up this exact retained numerator Artifact")
            roots += (_shared_root(values.root, candidates[0]),)
        else:
            roots += (_shared_root(roots[1], basis),)
    if kind == "standardize" and unit != statistical_unit(values.definition):
        raise invalid("reference statistical Entity differs from the frozen receiver")
    left = values.root.signature
    identity = digest(kind + values.root.fingerprint + reference.root.fingerprint + repr(unit))
    domain = (
        left.domain
        if kind == "share"
        else DomainSignature(left.domain.binding, "singleton", (), (), identity)
    )
    if kind == "share":
        domain = replace(domain, definition_id=identity)
    quantity = left.quantity
    params = ReferenceDerive(
        kind,
        domain,
        reference_id or reference.root.fingerprint,
        quantity.unit if kind == "standardize" and quantity else None,
        quantity.time_scope if quantity else "untimed",
        left.domain.instance_key if kind == "standardize" else (),
        unit,
        next(
            (part for part in roots[2].signature.parts if isinstance(part, OriginalStatePart)), None
        )
        if kind == "share"
        else None,
        strata_dependencies,
    )
    try:
        value_type = reference_type(kind, values.root.value_type, reference.root.value_type)
    except ValueError as error:
        raise invalid(f"{values.root.value_type}/{reference.root.value_type}: {error}") from error
    node = method_node(
        tuple(
            Edge(
                "reference"
                if index == 1
                else "subject"
                if root.signature.quantity is None
                else "quantity",
                root,
            )
            for index, root in enumerate(roots)
        ),
        params,
        value_type=value_type,
        retained_endpoints=(values.definition, reference.definition, basis)
        if kind == "share" and isinstance(values.binding, FrozenBinding)
        else (values.definition, reference.definition)
        if isinstance(values.binding, FrozenBinding)
        else (),
    )
    return values._with_sources(reference)._with(node)


def part_keys(signature: Signature, role: str) -> tuple[str, ...]:
    from marivo.analysis.core.model import AttributionPart
    from marivo.analysis.materialization.graph_attribution import part_keys as attribution_keys

    attribution = next(
        (p for p in signature.parts if isinstance(p, AttributionPart) and p.role == role), None
    )
    if attribution is not None:
        return attribution_keys(attribution)
    declaration = next(
        (
            part
            for part in signature.parts
            if isinstance(part, ReferenceStatePart) and part.role == role
        ),
        None,
    )
    return (
        tuple(f"key_{index}" for index in range(len(declaration.domain.instance_key)))
        if declaration
        else tuple(f"key_{index}" for index in range(len(signature.domain.instance_key)))
    )


def cell(row: dict[str, object], physical: ValueType) -> Cell:
    tag, value, reason = row["cell_tag"], row["value"], row["cell_reason"]
    if tag == "defined":
        return Defined(number(value, physical))
    if tag == "null":
        return Null(str(reason))
    if tag == "undefined":
        return Undefined(str(reason))
    if tag == "unknown":
        return Unknown(str(reason))
    raise invalid("invalid retained Cell tag")


def finish(
    params: ReferenceDerive, value_type: ValueType, parts: tuple[ExchangePart, ...]
) -> pa.Table:
    tables = {part.role: part.table for part in parts}
    values, reference = tables["stratum_values"], tables["fixed_reference"]
    keys = tuple(f"key_{index}" for index in range(len(params.output_domain.instance_key)))
    if params.kind == "penetration":
        if not tables["reference_proof"].equals(values):
            raise invalid("intersection proof differs from retained complete identities")
        names = tuple(reference.column_names)
        if tuple(values.column_names) != names or not values.schema.equals(
            reference.schema, check_metadata=False
        ):
            raise invalid("complete identity schemas differ")
        first = {tuple(row[name] for name in names) for row in values.to_pylist()}
        second = {tuple(row[name] for name in names) for row in reference.to_pylist()}
        result = [((), penetration(len(first & second), len(second)))]
    else:
        value_physical = physical_type(values.schema.field("value").type)
        weight_physical = physical_type(reference.schema.field("value").type)
        if reference_type(params.kind, value_physical, weight_physical) != value_type:
            raise invalid("numeric schemas disagree with the frozen result type")
        rows = numeric_primary(values).to_pylist()
        refs = numeric_primary(reference).to_pylist()
        if params.kind == "share":
            if len(refs) != 1 or refs[0]["cell_tag"] != "defined":
                raise invalid("share reference must be one finite Defined denominator")
            denominator = number(refs[0]["value"], weight_physical)
            from marivo.analysis.methods.numeric_state import merge_original

            proof = tables["reference_proof"]
            state = params.share_state
            if state is None:
                raise invalid("share lost its original additive support state")
            components = tuple(
                {name: row["original_state__" + name] for name in state.components}
                for row in proof.to_pylist()
            )
            _, reproduced, tag, _ = merge_original(
                components,
                proof.schema,
                state.components,
                state.method_version.removesuffix("@v1"),
                weight_physical,
                state.empty_rules,
            )
            if tag != "defined" or reproduced != denominator:
                raise invalid("reference denominator differs from complete additive support")
            support = {
                tuple(row[key] for key in keys): row for row in numeric_primary(proof).to_pylist()
            }
            result = []
            for row in rows:
                key = tuple(row[key] for key in keys)
                if key not in support or any(
                    row[name] != support[key][name] for name in ("value", "cell_tag", "cell_reason")
                ):
                    raise invalid("numerator differs from its reference support")
                operand = cell(row, value_physical)
                if not isinstance(operand, Defined):
                    raise invalid("share numerator must be finite Defined")
                result.append(
                    (
                        tuple(row[key] for key in keys),
                        share(operand.value, denominator, value_physical, weight_physical),
                    )
                )
        else:
            names = tuple(f"key_{index}" for index in range(len(params.strata)))
            if any(
                values.schema.field(name).type != reference.schema.field(name).type
                for name in names
            ):
                raise invalid("stratum coordinate physical types differ")
            by_key = {tuple(row[name] for name in names): row for row in rows}
            reference_keys = [tuple(row[name] for name in names) for row in refs]
            if (
                not refs
                or len(by_key) != len(rows)
                or len(set(reference_keys)) != len(refs)
                or set(reference_keys) != set(by_key)
            ):
                raise invalid(
                    "missing, duplicate or empty reference strata; normalization is forbidden"
                )
            if any(row["cell_tag"] != "defined" for row in refs):
                raise invalid("reference weights must all be finite Defined")
            if tables["strata"].to_pylist() != reference.select(names).to_pylist():
                raise invalid("retained strata differ from the complete reference key image")
            if not tables["reference_proof"].equals(values):
                raise invalid("standardization proof differs from retained stratum values")
            result = [
                (
                    (),
                    standardize(
                        tuple(cell(by_key[key], value_physical) for key in reference_keys),
                        tuple(row["value"] for row in refs),
                        value_physical,
                        weight_physical,
                    ),
                )
            ]
    schema = pa.schema(
        [
            *(pa.field(name, values.schema.field(name).type) for name in keys),
            pa.field("value", arrow_scalar_type(value_type)),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )
    return pa.Table.from_pylist(
        [
            {
                **dict(zip(keys, key, strict=True)),
                "value": item.value if isinstance(item, Defined) else None,
                "cell_tag": "defined" if isinstance(item, Defined) else "undefined",
                "cell_reason": None if isinstance(item, Defined) else item.reason,
            }
            for key, item in result
        ],
        schema=schema,
    )


def error_bounds(
    params: ReferenceDerive, parts: tuple[ExchangePart, ...], primary: pa.Table
) -> list[float]:
    from marivo.analysis.methods.comparison import propagated_error, roundoff

    tables = {part.role: part.table for part in parts}
    if params.kind == "penetration":
        return [
            roundoff(row["value"]) if type(row["value"]) is float else 0.0
            for row in primary.to_pylist()
        ]
    values, reference = tables["stratum_values"], tables["fixed_reference"]
    rows, refs = numeric_primary(values).to_pylist(), numeric_primary(reference).to_pylist()
    names = tuple(
        f"key_{index}"
        for index in range(
            len(
                params.strata if params.kind == "standardize" else params.output_domain.instance_key
            )
        )
    )
    by_key = {tuple(row[name] for name in names): row for row in rows}
    if params.kind == "share":
        ref = refs[0]
        return [
            propagated_error(
                "ratio",
                by_key[tuple(row[name] for name in names)]["value"],
                ref["value"],
                row["value"],
                by_key[tuple(row[name] for name in names)]["error_bound"],
                ref["error_bound"],
            )
            for row in primary.to_pylist()
        ]
    ordered = tuple(by_key[tuple(row[name] for name in names)] for row in refs)
    return [
        standardized_error(
            tuple(cell(row, physical_type(values.schema.field("value").type)) for row in ordered),
            tuple(row["value"] for row in refs),
            tuple(row["error_bound"] for row in ordered),
            tuple(row["error_bound"] for row in refs),
            row["value"],
        )
        for row in primary.to_pylist()
    ]


def result(node: MethodNode, parts: tuple[ExchangePart, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, ReferenceDerive)
    try:
        primary = finish(params, node.value_type, parts)
        bounds = error_bounds(params, parts, primary)
    except (ValueError, OverflowError) as error:
        raise invalid(str(error)) from error
    keys = tuple(f"key_{index}" for index in range(len(node.signature.domain.instance_key)))
    state = pa.Table.from_arrays(
        [
            *(primary[key] for key in keys),
            primary["cell_tag"],
            pa.array(bounds, type=pa.float64()),
        ],
        names=[*keys, "status", "error_bound"],
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(
            PartContract(part.role, part.table.schema, part_keys(node.signature, part.role))
            for part in parts
        ),
        (("undefined", ("empty_reference", "zero_denominator")),),
        "standardized" if params.kind == "standardize" else params.kind,
        state.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=state)


def physical_type(dtype: pa.DataType) -> ValueType:
    if pa.types.is_decimal(dtype):
        return DecimalType(dtype.precision, dtype.scale)
    if pa.types.is_duration(dtype):
        return DurationType(dtype.unit)
    if dtype == pa.int64():
        return ScalarType("int64")
    if dtype == pa.float64():
        return ScalarType("float64")
    raise invalid("reference numeric schema is not qualified")


def disclosure(checked: ExchangeResult) -> tuple[tuple[str, str], ...]:
    """Describe independently verified reference facts without exposing member keys."""
    quantity = checked.contract.signature.quantity
    if not isinstance(quantity, DerivedQuantity) or not quantity.method_version.startswith(
        "reference."
    ):
        return ()
    params = reference_parameters(checked.contract.signature)
    tables = {part.role: part.table for part in checked.parts}
    reference, values = tables["fixed_reference"], tables["stratum_values"]
    facts: list[tuple[str, str]] = [("retained_reference", params.reference_id)]
    if params.kind == "penetration":
        names = reference.column_names
        members = {tuple(row[name] for name in names) for row in values.to_pylist()}
        population = {tuple(row[name] for name in names) for row in reference.to_pylist()}
        facts.extend(
            (
                ("reference_members", str(len(population))),
                ("intersection_members", str(len(members & population))),
            )
        )
    elif params.kind == "share":
        denominator = reference["value"][0].as_py()
        names = checked.contract.key_fields
        support = tables["reference_proof"].to_pylist()
        complete = {tuple(row[name] for name in names) for row in support}
        current = {tuple(row[name] for name in names) for row in checked.primary.to_pylist()}
        nonnegative = denominator > 0 and all(
            row["cell_tag"] == "defined" and row["value"] >= 0 for row in support
        )
        facts.extend(
            (
                ("reference_denominator", str(denominator)[:128]),
                ("current_partition", "complete" if current == complete else "partial"),
                ("nonnegative_range", "proved [0,1]" if nonnegative else "unproved; signed share"),
            )
        )
    else:
        total = weight_sum(
            tuple(reference["value"].to_pylist()),
            physical_type(reference.schema.field("value").type),
        )
        facts.extend(
            (
                ("represented_weight_sum_deviation", str(total - 1)[:160]),
                ("retained_strata", str(reference.num_rows)),
            )
        )
    if checked.method_state is not None:
        facts.append(
            (
                "arithmetic_error_bound",
                repr(max(checked.method_state["error_bound"].to_pylist(), default=0.0)),
            )
        )
    return tuple(facts)


def fixed_parts(node: MethodNode, inputs: tuple[ExchangeResult, ...]) -> tuple[ExchangePart, ...]:
    params = node.parameters
    assert isinstance(params, ReferenceDerive)
    parts: list[ExchangePart] = []
    for declaration in node.signature.parts:
        assert isinstance(declaration, ReferenceStatePart)
        index = (
            1
            if declaration.role in ("fixed_reference", "strata")
            else 2
            if declaration.role == "reference_proof" and params.kind == "share"
            else 0
        )
        source = inputs[index]
        table = source.primary
        if declaration.role != "strata" and "value" in table.column_names:
            from marivo.analysis.materialization.graph_local_execution import (
                _fixed_operand_bound,
                _operand_components,
            )

            components = _operand_components(source)
            table = table.append_column(
                "error_bound",
                pa.array(
                    [
                        _fixed_operand_bound(
                            source,
                            row,
                            components.get(
                                tuple(row[name] for name in source.contract.key_fields), {}
                            ),
                        )
                        for row in numeric_primary(source.primary).to_pylist()
                    ],
                    type=pa.float64(),
                ),
            )
        if declaration.role == "strata":
            table = table.select(source.contract.key_fields)
        if declaration.original_state is not None:
            state = next(part.table for part in source.parts if part.role == "original_state")
            keys = source.contract.key_fields
            rows = {tuple(row[key] for key in keys): row for row in state.to_pylist()}
            for name in declaration.original_state.components:
                column = "original_state__" + name
                table = table.append_column(
                    column,
                    pa.array(
                        [
                            rows[tuple(row[key] for key in keys)][column]
                            for row in table.to_pylist()
                        ],
                        type=state.schema.field(column).type,
                    ),
                )
        parts.append(ExchangePart(declaration.role, table))
    return tuple(parts)
