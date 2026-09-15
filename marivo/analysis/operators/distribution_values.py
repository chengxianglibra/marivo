"""Exact Shapley weights over complete source-produced coalition values only."""

from __future__ import annotations

import math
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.distribution_attribution import (
    BASELINE,
    COALITION,
    COALITION_VALUE,
    CURRENT,
    PLAYER_COUNT,
    PLAYERS,
)
from marivo.analysis.datasets.descriptors import _bool_tuple_arity
from marivo.analysis.materialization.storage import _matches_type
from marivo.analysis.operators.attribute_values import reconciles
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1
from marivo.analysis.operators.errors import attribution_error
from marivo.analysis.operators.row import ordered
from marivo.analysis.operators.row_values import compare_value


def validate_coalition_schema(schema: pa.Schema, spec: AttributeSpecV1) -> None:
    scopes = tuple(field.name for field in spec.scope_fields)
    times = tuple(
        name
        for name in ("current_time", "baseline_time")
        if name in {field.name for field in spec.input_row.schema.columns}
    )
    if set(schema.names) != {
        *scopes,
        *times,
        PLAYER_COUNT,
        PLAYERS,
        COALITION,
        CURRENT,
        BASELINE,
        COALITION_VALUE,
        "active_axis_mask",
    } or len(schema.names) != len(set(schema.names)):
        raise attribution_error("closed coalition schema", "unexpected coalition fields")
    for field in (
        *spec.scope_fields,
        *(field for field in spec.input_row.schema.columns if field.name in times),
    ):
        if not _matches_type(field.logical_type_id, schema.field(field.name).type):
            raise attribution_error("exact coalition scope types", "invalid scope type")
    for name in (PLAYER_COUNT, COALITION):
        if schema.field(name).type != pa.int64():
            raise attribution_error("int64 coalition identifiers", "invalid coalition type")
    for name in (CURRENT, BASELINE, COALITION_VALUE):
        if schema.field(name).type != pa.float64():
            raise attribution_error("float64 coalition values", "invalid coalition value type")
    inventory = schema.field(PLAYERS).type
    if not pa.types.is_list(inventory) or not pa.types.is_struct(inventory.value_type):
        raise attribution_error("typed player inventory", "invalid player list")
    element = inventory.value_type
    if element.names != [*(field.name for field in spec.axis_fields), "other_mask"]:
        raise attribution_error("exact authored player axes", "invalid player fields")
    for field in spec.axis_fields:
        if not _matches_type(field.logical_type_id, element.field(field.name).type):
            raise attribution_error("exact player coordinate types", "invalid player axis type")
    for kind in (schema.field("active_axis_mask").type, element.field("other_mask").type):
        if (
            not (pa.types.is_list(kind) or pa.types.is_fixed_size_list(kind))
            or kind.value_type != pa.bool_()
        ):
            raise attribution_error("boolean resolution and Other masks", "invalid mask type")


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise attribution_error("finite coalition values and endpoints", "undefined coalition")
    return float(value)


def execute_distribution(frame: pd.DataFrame, spec: AttributeSpecV1) -> pd.DataFrame:
    """Validate the complete game before emitting any contribution rows."""
    scopes = tuple(field.name for field in spec.scope_fields)
    axes = tuple(field.name for field in spec.axis_fields)
    times = tuple(name for name in ("current_time", "baseline_time") if name in frame.columns)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    validate_coalition_schema(table.schema, spec)
    games: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in table.to_pylist():
        mask = row["active_axis_mask"]
        if (
            not isinstance(mask, list)
            or len(mask) != len(axes)
            or any(type(value) is not bool for value in mask)
        ):
            raise attribution_error("complete boolean resolution mask", "invalid active mask")
        key = (*[row[name] for name in scopes], tuple(mask))
        games.setdefault(key, []).append(row)
    records: list[dict[str, object]] = []
    validated_games = []
    scope_resolutions: dict[tuple[object, ...], set[tuple[bool, ...]]] = {}
    expected_masks = {
        tuple(index < length for index in range(len(axes)))
        for length in ((len(axes),) if spec.mode == "joint" else range(1, len(axes) + 1))
    }
    for key, game in games.items():
        first = game[0]
        active_mask = first["active_axis_mask"]
        if not isinstance(active_mask, list):
            raise attribution_error("boolean resolution mask", "invalid mask")
        mask = tuple(active_mask)
        if mask not in expected_masks:
            raise attribution_error("authored resolution prefixes", "unexpected resolution")
        scope = key[:-1]
        scope_resolutions.setdefault(scope, set()).add(mask)
        players, count = first[PLAYERS], first[PLAYER_COUNT]
        if (
            type(count) is not int
            or count < 1
            or not isinstance(players, list)
            or len(players) != count
        ):
            raise attribution_error(
                "one or more complete mapped players",
                "invalid player inventory",
                repair="Lower top_k or choose a coarser attribution axis.",
            )
        coordinates = []
        for player in players:
            if not isinstance(player, dict) or set(player) != {*axes, "other_mask"}:
                raise attribution_error("complete mapped player coordinates", "invalid player")
            other = player["other_mask"]
            if (
                not isinstance(other, list)
                or len(other) != len(axes)
                or any(type(value) is not bool for value in other)
                or any(
                    (not active and (player[axis] is not None or flag))
                    or (flag and player[axis] is not None)
                    for axis, active, flag in zip(axes, mask, other, strict=True)
                )
            ):
                raise attribution_error(
                    "consistent active/Other player masks", "invalid player mask"
                )
            coordinates.append((*[player[axis] for axis in axes], tuple(other)))
        if len(set(coordinates)) != count:
            raise attribution_error("unique complete players", "duplicate player")
        current, baseline = _number(first[CURRENT]), _number(first[BASELINE])
        values: dict[int, float] = {}
        for row in game:
            coalition = row[COALITION]
            if type(coalition) is not int or not 0 <= coalition < 2**count or coalition in values:
                raise attribution_error(
                    "unique complete coalition identifiers", "duplicate or invalid coalition"
                )
            if (
                row[PLAYERS] != players
                or row[PLAYER_COUNT] != count
                or row[CURRENT] != current
                or row[BASELINE] != baseline
                or any(row[name] != first[name] for name in times)
            ):
                raise attribution_error(
                    "one immutable game inventory and endpoints", "inconsistent coalition authority"
                )
            values[coalition] = _number(row[COALITION_VALUE])
        if (
            len(values) != 2**count
            or not reconciles(values.get(0, math.inf), baseline)
            or not reconciles(values.get(2**count - 1, math.inf), current)
        ):
            raise attribution_error(
                "complete coalition coverage and independent endpoints",
                "missing coalition or endpoint mismatch",
            )
        validated_games.append((first, mask, players, values, current, baseline))
    if any(masks != expected_masks for masks in scope_resolutions.values()):
        raise attribution_error(
            "every authored resolution in each scope", "missing complete resolution"
        )
    for first, mask, players, values, current, baseline in validated_games:
        count = len(players)
        delta = current - baseline
        if not math.isfinite(delta):
            raise attribution_error("finite overall Delta", "endpoint difference overflow")
        rows = []
        for player_id, player in enumerate(players):
            bit = 1 << player_id
            low, high = [], []
            for coalition in range(2**count):
                if coalition & bit:
                    continue
                size = coalition.bit_count()
                weight = (
                    math.factorial(size) * math.factorial(count - size - 1) / math.factorial(count)
                )
                low.append(weight * values[coalition])
                high.append(weight * values[coalition | bit])
            before, after = math.fsum(low), math.fsum(high)
            contribution = after - before
            rows.append(
                {
                    **{name: first[name] for name in (*scopes, *times)},
                    **player,
                    "active_axis_mask": mask,
                    "current_value": _number(after),
                    "baseline_value": _number(before),
                    "overall_delta": delta,
                    "contribution": _number(contribution),
                    "status": "zero_total_delta" if delta == 0 else "ok",
                }
            )
        if not reconciles(math.fsum(row["contribution"] for row in rows), delta):
            raise attribution_error(
                "exact per-resolution reconciliation", "Shapley reconciliation failed"
            )
        positive = math.fsum(max(row["contribution"], 0) for row in rows)
        negative = math.fsum(max(-row["contribution"], 0) for row in rows)

        def compare(a: dict[str, object], b: dict[str, object]) -> int:
            magnitude = compare_value(
                abs(_number(a["contribution"])), abs(_number(b["contribution"]))
            )
            return (
                -magnitude
                if magnitude
                else compare_value(
                    tuple(a[name] for name in (*axes, "other_mask")),
                    tuple(b[name] for name in (*axes, "other_mask")),
                )
            )

        for rank, row in enumerate(sorted(rows, key=cmp_to_key(compare)), 1):
            c = row["contribution"]
            row.update(
                contribution_rank=rank,
                share_of_total_delta=None if delta == 0 else c / delta,
                share_of_positive_pool=None if positive == 0 else max(c, 0) / positive,
                share_of_negative_pool=None if negative == 0 else max(-c, 0) / negative,
            )
        records.extend(rows)
    player_type = table.schema.field(PLAYERS).type.value_type
    fields = []
    for field in spec.output_row.schema.columns:
        arity = _bool_tuple_arity(field.logical_type_id)
        if arity is not None:
            dtype = pa.list_(pa.bool_(), arity)
        elif field.name in table.schema.names:
            dtype = table.schema.field(field.name).type
        elif field.name in axes:
            dtype = player_type.field(field.name).type
        else:
            dtype = {"float64": pa.float64(), "int64": pa.int64(), "string": pa.string()}[
                field.logical_type_id
            ]
        fields.append(pa.field(field.name, dtype, field.nullable))
    output = pa.Table.from_pylist(records, schema=pa.schema(fields)).to_pandas(
        types_mapper=pd.ArrowDtype
    )
    return ordered(output, spec.output_row, spec.output_rows)
