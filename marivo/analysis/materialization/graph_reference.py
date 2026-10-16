"""Reference graph binding and registered consumption of verified input tables."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.materialization.cell_arrow import (
    compact_schema,
    from_rows,
    logical_schema,
    required,
)
from marivo.analysis.materialization.cell_arrow import number as arrow_number
from marivo.analysis.materialization.cell_arrow import rows as cell_rows

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_relation import Relation
from marivo.analysis.core.graph import Edge, MethodNode, Node, method_node, retained_nodes
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
    ArrowPart,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    _pandas_records,
    from_pandas,
    numeric_primary,
    reference_parameters,
    to_arrow,
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
    from marivo.analysis.materialization.graph_relation import Relation, _shared_root

    if not isinstance(values, Relation) or not isinstance(reference, Relation):
        raise invalid("reference inputs must be typed Relations")
    if (
        values.runtime.session_ref != reference.runtime.session_ref
        or values.runtime.store.store_id != reference.runtime.store.store_id
    ):
        raise invalid("cross-Session reference")
    reference_root = reference.root
    roots: tuple[Node, ...] = (values.root, _shared_root(values.root, reference_root))
    if kind == "share":
        basis = share_basis(values.definition, reference.definition)
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
            (
                Edge(
                    "reference"
                    if index == 1
                    else "subject"
                    if root.signature.quantity is None
                    else "quantity",
                    root,
                )
                for index, root in enumerate(roots)
            )
        ),
        params,
        value_type=value_type,
        retained_endpoints=(),
    )
    return values._with_sources(reference)._with(node)


def part_keys(signature: Signature, role: str) -> tuple[str, ...]:
    from marivo.analysis.methods.coordinate_state import key_fields

    coordinate_keys = key_fields(signature, role)
    if coordinate_keys is not None:
        return coordinate_keys
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


@dataclass(frozen=True, slots=True)
class _ReferenceParts:
    parts: tuple[ExchangePart, ...]
    schemas: Mapping[str, pa.Schema]


def _schema_for(
    frame: pd.DataFrame, schemas: Mapping[str, pa.Schema] | None, role: str
) -> pa.Schema:
    schema = schemas.get(role) if schemas is not None else None
    return schema if schema is not None else pa.Schema.from_pandas(frame, preserve_index=False)


def _rows(part: ExchangePart, schemas: Mapping[str, pa.Schema] | None) -> list[dict[str, object]]:
    schema = _schema_for(part.table, schemas, part.role)
    return cell_rows(numeric_primary(to_arrow(part.table, schema)))


def _frame_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(name): None if pd.isna(value) else value for name, value in row.items()}
        for row in frame.to_dict("records")
    ]


def _missing(value: object) -> bool:
    return bool(pd.isna(pd.Series([value], dtype="object")).iloc[0])


def _same_rows(
    left: list[dict[str, object]], right: list[dict[str, object]], names: tuple[str, ...]
) -> bool:
    left_rows = Counter(tuple(row[name] for name in names) for row in left)
    right_rows = Counter(tuple(row[name] for name in names) for row in right)
    return left_rows == right_rows


def finish(
    params: ReferenceDerive,
    value_type: ValueType,
    parts: tuple[ExchangePart, ...],
    *,
    schemas: Mapping[str, pa.Schema] | None = None,
) -> pd.DataFrame:
    by_role = {part.role: part for part in parts}
    value_part, reference_part = by_role["stratum_values"], by_role["fixed_reference"]
    values_schema = _schema_for(value_part.table, schemas, value_part.role)
    reference_schema = _schema_for(reference_part.table, schemas, reference_part.role)
    values, reference = _rows(value_part, schemas), _rows(reference_part, schemas)
    keys = tuple(f"key_{index}" for index in range(len(params.output_domain.instance_key)))
    result: list[tuple[tuple[object, ...], Cell]]
    if params.kind == "penetration":
        proof_part = by_role["reference_proof"]
        proof_schema = _schema_for(proof_part.table, schemas, proof_part.role)
        proof = _rows(proof_part, schemas)
        names = tuple(values_schema.names)
        if not proof_schema.equals(values_schema, check_metadata=False) or not _same_rows(
            proof, values, names
        ):
            raise invalid("intersection proof differs from retained complete identities")
        names = tuple(reference_schema.names)
        if tuple(values_schema.names) != names or tuple(
            field.type for field in values_schema
        ) != tuple(field.type for field in reference_schema):
            raise invalid("complete identity schemas differ")
        if any(
            _missing(row[name]) for rows in (values, reference) for row in rows for name in names
        ):
            raise invalid("complete identities cannot contain null coordinates")
        first = {tuple(row[name] for name in names) for row in values}
        second = {tuple(row[name] for name in names) for row in reference}
        if len(first) != len(values) or len(second) != len(reference):
            raise invalid("complete identities cannot contain duplicate rows")
        result = [((), penetration(len(first & second), len(second)))]
    else:
        value_physical = physical_type(values_schema.field("value").type)
        weight_physical = physical_type(reference_schema.field("value").type)
        if reference_type(params.kind, value_physical, weight_physical) != value_type:
            raise invalid("numeric schemas disagree with the frozen result type")
        rows, refs = values, reference
        if params.kind == "share":
            if len(refs) != 1 or refs[0]["cell_tag"] != "defined":
                raise invalid("share reference must be one finite Defined denominator")
            denominator = number(refs[0]["value"], weight_physical)
            from marivo.analysis.methods.numeric_state import merge_original

            proof_part = by_role["reference_proof"]
            proof_schema = _schema_for(proof_part.table, schemas, proof_part.role)
            proof = _rows(proof_part, schemas)
            state = params.share_state
            if state is None:
                raise invalid("share lost its original additive support state")
            components = tuple(
                {name: row["original_state__" + name] for name in state.components} for row in proof
            )
            _, reproduced, tag, _ = merge_original(
                components,
                proof_schema,
                state.components,
                state.method_version.removesuffix("@v1"),
                weight_physical,
                state.empty_rules,
            )
            if tag != "defined" or reproduced != denominator:
                raise invalid("reference denominator differs from complete additive support")
            support = {tuple(row[key] for key in keys): row for row in proof}
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
                values_schema.field(name).type != reference_schema.field(name).type
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
            strata_part = by_role["strata"]
            strata = _rows(strata_part, schemas)
            if not _same_rows(strata, reference, names):
                raise invalid("retained strata differ from the complete reference key image")
            proof_part = by_role["reference_proof"]
            proof_schema = _schema_for(proof_part.table, schemas, proof_part.role)
            proof = _rows(proof_part, schemas)
            if not proof_schema.equals(values_schema, check_metadata=False) or not _same_rows(
                proof, values, tuple(values_schema.names)
            ):
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
    return pd.DataFrame.from_records(
        [
            {
                **dict(zip(keys, key, strict=True)),
                "value": item.value if isinstance(item, Defined) else None,
                "cell_tag": "defined" if isinstance(item, Defined) else "undefined",
                "cell_reason": None if isinstance(item, Defined) else item.reason,
            }
            for key, item in result
        ],
        columns=[*keys, "value", "cell_tag", "cell_reason"],
    )


def error_bounds(
    params: ReferenceDerive, parts: tuple[ArrowPart, ...], primary: pa.Table
) -> list[float]:
    from marivo.analysis.methods.comparison import propagated_error, roundoff

    tables = {part.role: part.table for part in parts}
    if params.kind == "penetration":
        return [
            roundoff(row["value"]) if type(row["value"]) is float else 0.0
            for row in primary.to_pylist()
        ]
    values, reference = tables["stratum_values"], tables["fixed_reference"]
    rows, refs = cell_rows(numeric_primary(values)), cell_rows(numeric_primary(reference))
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
                required(by_key[tuple(row[name] for name in names)]["error_bound"], float),
                required(ref["error_bound"], float),
            )
            for row in primary.to_pylist()
        ]
    ordered = tuple(by_key[tuple(row[name] for name in names)] for row in refs)
    return [
        standardized_error(
            tuple(cell(row, physical_type(values.schema.field("value").type)) for row in ordered),
            tuple(row["value"] for row in refs),
            tuple(required(row["error_bound"], float) for row in ordered),
            tuple(required(row["error_bound"], float) for row in refs),
            row["value"],
        )
        for row in primary.to_pylist()
    ]


def _pandas_error_bounds(
    params: ReferenceDerive,
    parts: tuple[ExchangePart, ...],
    primary: pd.DataFrame,
    schemas: Mapping[str, pa.Schema],
    primary_schema: pa.Schema,
) -> list[float]:
    from marivo.analysis.methods.comparison import propagated_error, roundoff

    by_role = {part.role: part for part in parts}
    primary_rows = _pandas_records(primary, primary_schema)
    if params.kind == "penetration":
        return [
            roundoff(row["value"]) if type(row["value"]) is float else 0.0 for row in primary_rows
        ]
    values = _rows(by_role["stratum_values"], schemas)
    refs = _rows(by_role["fixed_reference"], schemas)
    names = tuple(
        f"key_{index}"
        for index in range(
            len(
                params.strata if params.kind == "standardize" else params.output_domain.instance_key
            )
        )
    )
    by_key = {tuple(row[name] for name in names): row for row in values}
    value_physical = physical_type(schemas["stratum_values"].field("value").type)
    if params.kind == "share":
        ref = refs[0]
        return [
            propagated_error(
                "ratio",
                by_key[tuple(row[name] for name in names)]["value"],
                ref["value"],
                row["value"],
                required(by_key[tuple(row[name] for name in names)]["error_bound"], float),
                required(ref["error_bound"], float),
            )
            for row in primary_rows
        ]
    ordered = tuple(by_key[tuple(row[name] for name in names)] for row in refs)
    return [
        standardized_error(
            tuple(cell(row, value_physical) for row in ordered),
            tuple(row["value"] for row in refs),
            tuple(required(row["error_bound"], float) for row in ordered),
            tuple(required(row["error_bound"], float) for row in refs),
            row["value"],
        )
        for row in primary_rows
    ]


def result(node: MethodNode, parts: _ReferenceParts, binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, ReferenceDerive)
    exchange_parts, schemas = parts.parts, parts.schemas
    try:
        primary = finish(params, node.value_type, exchange_parts, schemas=schemas)
    except (ValueError, OverflowError) as error:
        raise invalid(str(error)) from error
    keys = tuple(f"key_{index}" for index in range(len(node.signature.domain.instance_key)))
    value_schema = schemas.get("stratum_values")
    if value_schema is None:
        value_schema = pa.Schema.from_pandas(
            next(part.table for part in exchange_parts if part.role == "stratum_values"),
            preserve_index=False,
        )
    primary_schema = pa.schema(
        [
            *(pa.field(key, value_schema.field(key).type) for key in keys),
            pa.field("value", arrow_scalar_type(node.value_type)),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )
    bounds = _pandas_error_bounds(params, exchange_parts, primary, schemas, primary_schema)
    state = pd.DataFrame(
        {
            **{key: primary[key].tolist() for key in keys},
            "status": primary["cell_tag"].tolist(),
            "error_bound": bounds,
        },
        columns=[*keys, "status", "error_bound"],
    )
    state_schema = pa.schema(
        [
            *(primary_schema.field(key) for key in keys),
            pa.field("status", pa.string()),
            pa.field("error_bound", pa.float64()),
        ]
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary_schema,
        keys,
        tuple(
            PartContract(part.role, schemas[part.role], part_keys(node.signature, part.role))
            for part in exchange_parts
        ),
        (("undefined", ("empty_reference", "zero_denominator")),),
        "standardized" if params.kind == "standardize" else params.kind,
        state_schema,
    )
    return from_pandas(primary, contract, parts=exchange_parts, method_state=state, validate=False)


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
    from marivo.analysis.materialization.graph_exchange import _pandas_records

    schemas = {part.role: part.schema for part in checked.contract.parts}
    reference_rows = _pandas_records(reference, schemas["fixed_reference"])
    value_rows = _pandas_records(values, schemas["stratum_values"])
    facts: list[tuple[str, str]] = [("retained_reference", params.reference_id)]
    if params.kind == "penetration":
        names = tuple(reference.columns)
        members = {tuple(row[name] for name in names) for row in value_rows}
        population = {tuple(row[name] for name in names) for row in reference_rows}
        facts.extend(
            (
                ("reference_members", str(len(population))),
                ("intersection_members", str(len(members & population))),
            )
        )
    elif params.kind == "share":
        denominator = arrow_number(reference_rows[0]["value"])
        names = checked.contract.key_fields
        proof_schema = schemas["reference_proof"]
        support = _pandas_records(tables["reference_proof"], proof_schema)
        complete = {tuple(row[name] for name in names) for row in support}
        current_rows = _pandas_records(checked.primary, checked.contract.schema)
        current = {tuple(row[name] for name in names) for row in current_rows}
        nonnegative = denominator > 0 and all(
            row["cell_tag"] == "defined" and arrow_number(row["value"]) >= 0 for row in support
        )
        facts.extend(
            (
                ("reference_denominator", str(denominator)),
                ("current_partition", "complete" if current == complete else "partial"),
                ("nonnegative_range", "proved [0,1]" if nonnegative else "unproved; signed share"),
            )
        )
    else:
        total = weight_sum(
            tuple(row["value"] for row in reference_rows),
            physical_type(schemas["fixed_reference"].field("value").type),
        )
        facts.extend(
            (
                ("represented_weight_sum_deviation", str(total - 1)),
                ("retained_strata", str(len(reference))),
            )
        )
    if checked.method_state is not None:
        facts.append(
            (
                "arithmetic_error_bound",
                repr(max(checked.method_state["error_bound"].tolist(), default=0.0)),
            )
        )
    return tuple(facts)


def fixed_parts(node: MethodNode, inputs: tuple[ExchangeResult, ...]) -> _ReferenceParts:
    params = node.parameters
    assert isinstance(params, ReferenceDerive)
    parts: list[ExchangePart] = []
    schemas: dict[str, pa.Schema] = {}
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
        source_schema = source.contract.schema
        logical_source_schema = logical_schema(source_schema).remove_metadata()
        source_rows = _pandas_records(source.primary, source_schema)
        if declaration.role == "strata":
            frame = pd.DataFrame.from_records(
                ({key: row[key] for key in source.contract.key_fields} for row in source_rows),
                columns=source.contract.key_fields,
            )
            schema = pa.schema(
                [logical_source_schema.field(key) for key in source.contract.key_fields]
            )
        else:
            frame = pd.DataFrame.from_records(source_rows, columns=logical_source_schema.names)
            fields = list(logical_source_schema)
            if "value" in logical_source_schema.names:
                from marivo.analysis.materialization.graph_local_execution import (
                    _operand_bound,
                    _operand_components,
                )

                components = _operand_components(source)
                frame["error_bound"] = [
                    _operand_bound(
                        source,
                        row,
                        components.get(tuple(row[name] for name in source.contract.key_fields), {}),
                    )
                    for row in source_rows
                ]
                fields.append(pa.field("error_bound", pa.float64()))
            schema = pa.schema(fields)
            if declaration.original_state is not None:
                state_part = next(part for part in source.parts if part.role == "original_state")
                state_schema = next(
                    part.schema for part in source.contract.parts if part.role == "original_state"
                )
                logical_state_schema = logical_schema(state_schema).remove_metadata()
                state_rows = _pandas_records(state_part.table, state_schema)
                indexed_state = {
                    tuple(row[key] for key in source.contract.key_fields): row for row in state_rows
                }
                for name in declaration.original_state.components:
                    column = "original_state__" + name
                    frame[column] = pd.Series(
                        [
                            indexed_state[tuple(row[key] for key in source.contract.key_fields)][
                                column
                            ]
                            for row in source_rows
                        ],
                        dtype="object",
                    )
                    fields.append(logical_state_schema.field(column))
                schema = pa.schema(fields)
            schema = compact_schema(schema, declaration.cell_reasons)
            frame = from_rows(_frame_rows(frame), schema).to_pandas()
        parts.append(ExchangePart(declaration.role, frame))
        schemas[declaration.role] = schema
    return _ReferenceParts(tuple(parts), schemas)
