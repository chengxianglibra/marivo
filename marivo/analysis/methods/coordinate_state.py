"""Closed physical coordinate layouts shared by source and retained execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

import ibis.expr.datatypes as dt
import pyarrow as pa

from marivo.analysis.core.model import (
    AttributionPart,
    Coordinate,
    CoordinateStatePart,
    EndpointPart,
    Signature,
    part_role,
)


@dataclass(frozen=True, slots=True)
class KeyedCoordinateLayout:
    owner: str
    keys: tuple[str, ...]
    coordinates: tuple[str, ...]
    components: tuple[str, ...]
    kind: Literal["keyed"] = "keyed"


@dataclass(frozen=True, slots=True)
class PartitionCoordinateLayout:
    owner: str
    keys: tuple[str, ...]
    coordinates: tuple[str, ...]
    components: tuple[str, ...]
    kind: Literal["partition"] = "partition"


@dataclass(frozen=True, slots=True)
class TablePartLayout:
    kind: Literal["table"] = "table"


CoordinateLayout: TypeAlias = KeyedCoordinateLayout | PartitionCoordinateLayout
PartLayout: TypeAlias = TablePartLayout | CoordinateLayout


def declarations(
    signature: Signature,
) -> tuple[tuple[str, CoordinateStatePart, tuple[Coordinate, ...], str], ...]:
    result: list[tuple[str, CoordinateStatePart, tuple[Coordinate, ...], str]] = []
    for part in signature.parts:
        if isinstance(part, CoordinateStatePart):
            result.append((part_role(part), part, signature.domain.instance_key, "original_state"))
        endpoint = (
            part
            if isinstance(part, EndpointPart)
            else part.endpoint
            if isinstance(part, AttributionPart)
            else None
        )
        if endpoint is not None and endpoint.coordinate_state is not None:
            owner = endpoint.side + "_endpoint"
            keys = (
                part.domain.instance_key
                if isinstance(part, AttributionPart)
                else signature.domain.instance_key
            )
            result.append((owner + "_coordinates", endpoint.coordinate_state, keys, owner))
    return tuple(result)


def layout(signature: Signature, role: str) -> PartLayout:
    for name, part, keys, owner in declarations(signature):
        if name != role:
            continue
        columns = tuple(
            f"key_{keys.index(coordinate)}" if coordinate in keys else column
            for coordinate, column in zip(part.coordinates, part.columns, strict=True)
        )
        parent = tuple(f"key_{i}" for i in range(len(keys)))
        components = tuple(
            ("original_state__" if owner == "original_state" else owner + "__state__") + name
            for name in part.components
        )
        if set(part.coordinates) <= set(keys):
            return KeyedCoordinateLayout(owner, parent, columns, components)
        return PartitionCoordinateLayout(owner, parent, columns, part.components)
    return TablePartLayout()


def component_type(part: CoordinateStatePart, name: str) -> dt.DataType:
    explicit = dict(part.component_types).get(name)
    if explicit is not None:
        return dt.dtype("int64" if explicit.startswith("interval(") else explicit)
    if "count" in name:
        return dt.int64
    value = dt.dtype("int64" if part.value_type.startswith("interval(") else part.value_type)
    if name == "weighted_numerator" and isinstance(value, dt.Decimal):
        return dt.Decimal(38, (value.scale or 0) * 2)
    return value


def entries(signature: Signature, tables: Mapping[str, pa.Table], role: str) -> pa.Table:
    """Return flat complete coordinates/components without nested Python objects."""
    spec = layout(signature, role)
    if isinstance(spec, TablePartLayout):
        raise ValueError("coordinate role has no frozen layout")
    part = next(part for name, part, _, _ in declarations(signature) if name == role)
    retained = tables[role]
    if isinstance(spec, PartitionCoordinateLayout):
        values = retained
        component_columns = part.components
    else:
        owner = tables[spec.owner]
        # The marker shares the exact complete parent domain, never row order.
        positions = {
            tuple(row[key] for key in spec.keys): index
            for index, row in enumerate(retained.to_pylist())
        }
        order = pa.array(
            [
                positions[tuple(row[key] for key in spec.keys)]
                for row in owner.select(spec.keys).to_pylist()
            ],
            type=pa.int64(),
        )
        marker = retained.take(order)[role + "__present"]
        values = owner.filter(marker)
        component_columns = spec.components
    return pa.Table.from_arrays(
        [
            *(values[key] for key in spec.keys),
            *(values[column] for column in spec.coordinates),
            *(values[column] for column in component_columns),
        ],
        names=[*spec.keys, *part.columns, *part.components],
    )


def retain(signature: Signature, role: str, full: pa.Table, primary: pa.Table) -> pa.Table:
    """Encode a flat partition using its statically selected current layout."""
    spec = layout(signature, role)
    if isinstance(spec, TablePartLayout):
        raise ValueError("coordinate role has no frozen layout")
    part = next(part for name, part, _, _ in declarations(signature) if name == role)
    if isinstance(spec, PartitionCoordinateLayout):
        columns = [*spec.keys]
        arrays = [full[key] for key in spec.keys]
        for coordinate, column in zip(part.columns, spec.coordinates, strict=True):
            if column not in columns:
                columns.append(column)
                arrays.append(full[coordinate])
        return pa.Table.from_arrays(
            [*arrays, *(full[name] for name in part.components)], names=[*columns, *part.components]
        )
    present = {tuple(row[key] for key in spec.keys) for row in full.select(spec.keys).to_pylist()}
    return primary.select(spec.keys).append_column(
        role + "__present",
        pa.array(
            [
                tuple(row[key] for key in spec.keys) in present
                for row in primary.select(spec.keys).to_pylist()
            ],
            type=pa.bool_(),
        ),
    )


def key_fields(signature: Signature, role: str) -> tuple[str, ...] | None:
    spec = layout(signature, role)
    if isinstance(spec, TablePartLayout):
        return None
    return (
        tuple(dict.fromkeys((*spec.keys, *spec.coordinates)))
        if isinstance(spec, PartitionCoordinateLayout)
        else spec.keys
    )


def validate(signature: Signature, tables: Mapping[str, pa.Table]) -> None:
    """Reject damaged layouts, identities, markers and sufficient components."""
    from marivo.analysis.methods.state_validation import coordinate_state_matches

    for role, part, _, owner_role in declarations(signature):
        spec = layout(signature, role)
        assert not isinstance(spec, TablePartLayout)
        retained, owner = tables[role], tables[owner_role]
        keys = key_fields(signature, role)
        assert keys is not None
        expected_names = (
            (*spec.keys, role + "__present")
            if isinstance(spec, KeyedCoordinateLayout)
            else (*keys, *part.components)
        )
        if tuple(retained.column_names) != expected_names:
            raise ValueError("contribution layout fields differ from their frozen declaration")
        for key in spec.keys:
            if retained.schema.field(key).type != owner.schema.field(key).type:
                raise ValueError("contribution parent key type differs")
        prefix = "original_state__" if owner_role == "original_state" else owner_role + "__state__"
        for name in part.components:
            if owner.schema.field(prefix + name).type != component_type(part, name).to_pyarrow():
                raise ValueError("owning contribution component carrier differs")
        identities = [tuple(row[k] for k in keys) for row in retained.select(keys).to_pylist()]
        if len(identities) != len(set(identities)) or any(None in key for key in identities):
            raise ValueError("contribution complete keys are missing or duplicated")
        parents = {tuple(row[k] for k in spec.keys) for row in owner.select(spec.keys).to_pylist()}
        actual = {
            tuple(row[k] for k in spec.keys) for row in retained.select(spec.keys).to_pylist()
        }
        if owner_role != "original_state":
            marker = owner[owner_role + "__contribution_present"]
            if marker.type != pa.bool_() or marker.null_count:
                raise ValueError("endpoint contribution presence must be a non-null boolean")
        if isinstance(spec, KeyedCoordinateLayout):
            marker = retained[role + "__present"]
            if marker.type != pa.bool_() or marker.null_count or actual != parents:
                raise ValueError("contribution presence must be a complete non-null boolean domain")
            if owner_role != "original_state":
                expected = {
                    tuple(row[k] for k in spec.keys): row[owner_role + "__contribution_present"]
                    for row in owner.to_pylist()
                }
                if any(
                    row[role + "__present"] != expected[tuple(row[k] for k in spec.keys)]
                    for row in retained.to_pylist()
                ):
                    raise ValueError(
                        "endpoint contribution presence differs from its owning receipt"
                    )
        else:
            if not actual <= parents:
                raise ValueError("contribution row has no owning parent")
            if owner_role != "original_state" and any(
                row[owner_role + "__contribution_present"]
                != (tuple(row[k] for k in spec.keys) in actual)
                for row in owner.to_pylist()
            ):
                raise ValueError("endpoint contribution presence differs from its actual partition")
            for name in part.components:
                if retained.schema.field(name).type != component_type(part, name).to_pyarrow():
                    raise ValueError("contribution component carrier differs")
        full = entries(signature, tables, role)
        grouped: dict[tuple[object, ...], list[dict[str, object]]] = {}
        for row in full.to_pylist():
            grouped.setdefault(tuple(row[k] for k in spec.keys), []).append(
                {name: row[name] for name in (*part.columns, *part.components)}
            )
        prefix = "original_state__" if owner_role == "original_state" else owner_role + "__state__"
        for row in owner.to_pylist():
            identity = tuple(row[k] for k in spec.keys)
            groups = sorted(
                grouped.get(identity, []), key=lambda row: tuple(str(row[c]) for c in part.columns)
            )
            original = {"original_state__" + name: row[prefix + name] for name in part.components}
            if owner_role != "original_state" and all(value is None for value in original.values()):
                if groups or row[owner_role + "__contribution_present"] is not False:
                    raise ValueError("absent endpoint cannot retain contributions")
                continue
            if not coordinate_state_matches(
                part.components,
                part.value_type,
                groups,
                original,
                part.columns,
                part.component_types,
            ):
                raise ValueError("contribution partition differs from its owning original state")
