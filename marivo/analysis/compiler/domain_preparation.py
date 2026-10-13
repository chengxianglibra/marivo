"""Ibis occurrence preparation; no packet SQL or identity-derived business order."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import reduce
from operator import or_

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_lowering import (
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.compiler.source_time import encoded_time_predicate
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.rules import (
    ObserveCount,
    ObserveMetric,
    PreparedObservation,
)
from marivo.semantic.ir import TargetSnapshotVersion, TargetValidityVersion


def capture_time(value: ir.Value) -> ir.TimestampValue:
    """Adopt the qualified driver's native microsecond carrier before all checks."""
    if not isinstance(value, ir.TimestampValue):
        fail(
            "physical_qualification",
            f"occurrence time requires timestamp, received {value.type()}",
            stage="lowering",
        )
    kind = value.type()
    if kind.scale is not None and kind.scale > 6:
        value = value.cast("timestamp('UTC')" if kind.timezone is not None else "timestamp")
    assert isinstance(value, ir.TimestampValue)
    return value


def lower_candidates(
    stage: SourceMethodStage,
    members: LoweredRelation,
    bindings: tuple[SourceBinding, ...],
    relations: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
    *,
    captured_columns: tuple[tuple[str, str], ...] = (),
    observation: ObserveMetric | ObserveCount | None = None,
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    from marivo.analysis.compiler.graph_lowering import CoordinateColumn, _contribution_rows
    from marivo.analysis.core.model import Coordinate

    params = stage.node.parameters
    if observation is None:
        assert isinstance(params, PreparedObservation)
        observation = params.observation
    rows, ids, authority = _contribution_rows(
        stage,
        observation,
        bindings,
        relations,
        checks,
        prepared=True,
        captured_columns=captured_columns,
    )
    rows = rows.mutate(event_time=rows.event_time.cast(dt.Timestamp(timezone="UTC", scale=6)))
    assert observation.start is not None and observation.end is not None
    start, end = (datetime.fromisoformat(value) for value in (observation.start, observation.end))
    if start.utcoffset() is None or end.utcoffset() is None:
        fail(
            "input_binding",
            "prepared observation bounds require explicit timezone",
            stage="lowering",
        )
    start, end = (value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start, end))
    encoded = encoded_time_predicate(
        rows.__raw_event_time, observation.event, authority, start=start, end=end
    )
    rows = rows.filter(
        encoded
        if encoded is not None
        else (rows.event_time >= ibis.literal(start, type=rows.event_time.type()))
        & (rows.event_time < ibis.literal(end, type=rows.event_time.type()))
    )
    mapping = members.expression
    selected = rows.semi_join(
        mapping,
        [rows[f"member_{i}"] == mapping[key.column] for i, key in enumerate(members.layout.keys)],
    )
    ids = tuple(dict.fromkeys((*ids, *members.source_ids)))
    selected = selected.drop("__raw_event_time")
    keys = tuple(
        CoordinateColumn(Coordinate(observation.contribution, name, "identity"), name)
        for name in rows.columns
        if name.startswith("candidate__key_")
    )
    ids = tuple(dict.fromkeys((*ids, *members.source_ids)))
    layout = RelationLayout(
        keys,
        None,
        (),
        tuple(name for name in selected.columns if name not in {key.column for key in keys}),
    )
    return selected, layout, ids


def _version(table: ir.Table, binding: SourceBinding, instant: ir.Value) -> ir.BooleanValue:
    from marivo.analysis.compiler.source_time import render, timestamp

    version = binding.leaf.definition.version
    if isinstance(version, TargetSnapshotVersion):
        column = table[version.source_column]
        point = render(version.timezone or "UTC", timestamp(instant)).cast("date")
        actual = (
            render(version.timezone or "UTC", timestamp(column)).cast("date")
            if column.type().is_timestamp() and column.type().timezone is not None
            else column.cast("date")
        )
        return actual == point
    if isinstance(version, TargetValidityVersion):
        start, end = table[version.valid_from_column], table[version.valid_to_column]
        open_end = reduce(
            or_,
            (
                end.isnull() if value is None else end == ibis.literal(value).cast(end.type())
                for value in version.open_end
            ),
            ibis.literal(False),
        )
        point = (
            instant
            if start.type().is_timestamp() and start.type().timezone is not None
            else render(version.timezone or "UTC", timestamp(instant))
        )
        return (start <= point) & (
            open_end | (end > point if version.interval == "closed_open" else end >= point)
        )
    return ibis.literal(True)
