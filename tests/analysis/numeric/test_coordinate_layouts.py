"""Independent flat-state witnesses for presence, tuple grain and damage."""

from dataclasses import replace
from typing import Literal

import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoordinateStatePart,
    DomainSignature,
    EndpointPart,
    ObservedQuantity,
    OriginalStatePart,
    Signature,
)
from marivo.analysis.methods.coordinate_state import (
    KeyedCoordinateLayout,
    PartitionCoordinateLayout,
    entries,
    layout,
    retain,
    validate,
)
from marivo.analysis.methods.numeric_state import merge_original
from marivo.analysis.methods.physical import ScalarType


def _signature(*, keyed: bool, joint: bool = False) -> Signature:
    owner = ms.ref.entity("sales.events")
    binding = Binding("session", "owner", "events", "window")
    category = ms.ref.dimension("sales.events.channel")
    coordinate = Coordinate(owner, category.path, "group")
    keys = (
        (coordinate,)
        if keyed
        else (Coordinate(owner, "tenant", "identity"), Coordinate(owner, "id", "identity"))
    )
    part = CoordinateStatePart(
        binding,
        "q",
        category,
        owner,
        ("sum", "non_null_count"),
        "int64",
        "v1",
        extra_coordinates=(Coordinate(owner, "sales.events.status", "group"),) if joint else (),
    )
    return Signature(
        DomainSignature(binding, "group" if keyed else "entity", keys, keys, "targets"),
        ObservedQuantity(
            "q",
            ms.ref.metric("sales.average"),
            "definition",
            None,
            "window",
            "events",
            "strict",
            "mean@v1",
        ),
        (OriginalStatePart(binding, "q", "mean@v1", "events", part.components, "v1"), part),
    )


def test_keyed_presence_is_independent_of_null_support_and_row_order() -> None:
    signature = _signature(keyed=True)
    original = pa.table(
        {
            "key_0": ["empty", "null", "real"],
            "original_state__sum": [0, 0, 9],
            "original_state__non_null_count": [0, 0, 1],
        }
    )
    full = pa.table(
        {
            "key_0": ["real", "null"],
            "coordinate": ["real", "null"],
            "sum": [9, 0],
            "non_null_count": [1, 0],
        }
    )
    retained = retain(signature, "coordinate_state", full, original)
    assert isinstance(layout(signature, "coordinate_state"), KeyedCoordinateLayout)
    assert retained.column_names == ["key_0", "coordinate_state__present"]
    assert retained["coordinate_state__present"].to_pylist() == [False, True, True]
    tables = {"original_state": original, "coordinate_state": retained.take(pa.array([2, 0, 1]))}
    validate(signature, tables)
    assert entries(signature, tables, "coordinate_state").to_pylist() == [
        {"key_0": "null", "coordinate": "null", "sum": 0, "non_null_count": 0},
        {"key_0": "real", "coordinate": "real", "sum": 9, "non_null_count": 1},
    ]


def test_partition_preserves_correlated_tuples_and_unequal_mean_support() -> None:
    signature = _signature(keyed=False, joint=True)
    original = pa.table(
        {
            "key_0": ["a"],
            "key_1": [2**53 + 1],
            "original_state__sum": [100],
            "original_state__non_null_count": [10],
        }
    )
    full = pa.table(
        {
            "key_0": ["a", "a"],
            "key_1": [2**53 + 1] * 2,
            "coordinate": ["web", "app"],
            "coordinate_1": ["paid", "refunded"],
            "sum": [100, 0],
            "non_null_count": [1, 9],
        }
    )
    retained = retain(signature, "coordinate_state", full, original)
    assert isinstance(layout(signature, "coordinate_state"), PartitionCoordinateLayout)
    tables = {"original_state": original, "coordinate_state": retained}
    validate(signature, tables)
    rows = entries(signature, tables, "coordinate_state").to_pylist()
    assert {(r["coordinate"], r["coordinate_1"]) for r in rows} == {
        ("web", "paid"),
        ("app", "refunded"),
    }
    _, value, tag, reason = merge_original(
        rows, original.schema, ("sum", "non_null_count"), "mean", ScalarType("float64"), ()
    )
    assert (value, tag, reason) == (10.0, "defined", None)


@pytest.mark.parametrize(
    "damage", ("duplicate", "parent", "null_coordinate", "carrier", "component")
)
def test_damaged_flat_partition_rejects(damage: str) -> None:
    signature = _signature(keyed=False)
    original = pa.table(
        {
            "key_0": ["a"],
            "key_1": [1],
            "original_state__sum": [4],
            "original_state__non_null_count": [1],
        }
    )
    full = pa.table(
        {"key_0": ["a"], "key_1": [1], "coordinate": ["web"], "sum": [4], "non_null_count": [1]}
    )
    damaged = retain(signature, "coordinate_state", full, original)
    if damage == "duplicate":
        damaged = pa.concat_tables([damaged, damaged])
    else:
        name = {
            "parent": "key_1",
            "null_coordinate": "coordinate",
            "carrier": "sum",
            "component": "sum",
        }[damage]
        replacement = (
            pa.array([None], type=pa.string())
            if damage == "null_coordinate"
            else pa.array([4.0])
            if damage == "carrier"
            else pa.array([5])
        )
        damaged = damaged.set_column(damaged.schema.get_field_index(name), name, replacement)
    with pytest.raises(ValueError):
        validate(signature, {"original_state": original, "coordinate_state": damaged})


@pytest.mark.parametrize("marker", (pa.array([None], type=pa.bool_()), pa.array([1])))
def test_damaged_presence_rejects(marker: pa.Array) -> None:
    signature = _signature(keyed=True)
    original = pa.table(
        {"key_0": ["web"], "original_state__sum": [4], "original_state__non_null_count": [1]}
    )
    retained = pa.table({"key_0": ["web"], "coordinate_state__present": marker})
    with pytest.raises(ValueError):
        validate(signature, {"original_state": original, "coordinate_state": retained})


def test_attribution_layout_does_not_restore_recomposition_authority() -> None:
    signature = _signature(keyed=False)
    original, coordinate = signature.parts
    assert isinstance(coordinate, CoordinateStatePart)
    saved = replace(signature, parts=(original, replace(coordinate, attribution_only=True)))
    assert layout(saved, "allocation_state") == layout(signature, "coordinate_state")
    assert saved.parts[1] != signature.parts[1]


@pytest.mark.parametrize("side", ("current", "baseline"))
@pytest.mark.parametrize("damage", ("none", "null", "false", "empty_true", "carrier"))
def test_partition_endpoint_presence_matches_actual_parent_groups(
    side: Literal["current", "baseline"], damage: str
) -> None:
    signature = _signature(keyed=False)
    original, coordinate = signature.parts
    assert isinstance(original, OriginalStatePart)
    assert isinstance(coordinate, CoordinateStatePart)
    signature = replace(
        signature,
        parts=(EndpointPart(signature.domain.binding, side, "q", "v1", original, coordinate),),
    )
    role = side + "_endpoint"
    marker = role + "__contribution_present"
    owner = pa.table(
        {
            "key_0": ["empty", "null", "real"],
            "key_1": [2**53 + 1] * 3,
            role + "__state__sum": [0, 0, 9],
            role + "__state__non_null_count": [0, 0, 1],
            marker: [False, True, True],
        }
    )
    full = pa.table(
        {
            "key_0": ["real", "null"],
            "key_1": [2**53 + 1] * 2,
            "coordinate": ["web", "app"],
            "sum": [9, 0],
            "non_null_count": [1, 0],
        }
    )
    retained = retain(signature, role + "_coordinates", full, owner)
    tables = {role: owner, role + "_coordinates": retained}
    validate(signature, tables)
    if damage == "none":
        return
    markers = (
        pa.array([0, 1, 1])
        if damage == "carrier"
        else pa.array(
            [True, True, True]
            if damage == "empty_true"
            else [False, None if damage == "null" else False, True],
            type=pa.bool_(),
        )
    )
    tables[role] = owner.set_column(owner.schema.get_field_index(marker), marker, markers)
    with pytest.raises(ValueError, match="endpoint contribution presence"):
        validate(signature, tables)
