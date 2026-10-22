"""Lower exact member version predicates through Ibis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from marivo.datasource.timezone import DatasourceEngineTimezone
    from marivo.semantic._compiled_state import CompiledSemanticState

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.source_time import source_time_comparison, source_time_predicate
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.semantic.ir import (
    TargetDimensionContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)


@dataclass(frozen=True, slots=True)
class _VersionValue:
    value: ir.Value
    raw: ir.Value
    axis: TargetDimensionContract
    authority: SourceTimeAuthority
    prefix: ir.Value | None = None
    prefix_axis: TargetDimensionContract | None = None

    def comparison(
        self, boundary: datetime, operator: Literal["<", "<=", ">", ">="]
    ) -> ir.BooleanValue:
        result = source_time_comparison(
            self.raw,
            self.axis,
            self.authority,
            bound=boundary,
            comparison=operator,
            prefix=self.prefix,
            prefix_axis=self.prefix_axis,
        )
        if result is not None:
            return result
        bound = _bound(self.value, boundary)
        if operator == "<":
            return self.value < bound
        if operator == "<=":
            return self.value <= bound
        if operator == ">":
            return self.value > bound
        return self.value >= bound


def _bound(column: ir.Value, boundary: datetime) -> ir.Value:
    dtype = column.type()
    if dtype.is_date():
        if boundary.timetz().replace(tzinfo=None) != time():
            return ibis.literal(boundary.replace(tzinfo=None))
        return ibis.literal(boundary.date())
    if dtype.is_timestamp():
        if dtype.timezone is None:
            boundary = boundary.replace(tzinfo=None)
        return ibis.literal(boundary).cast(dtype)
    raise DatasetConstructionError(
        expected="a native date or timestamp version axis",
        received=str(dtype),
        repair="Declare a native temporal version axis with explicit timezone authority.",
        location="analysis.members.version",
    )


def _version_values(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion,
    state: CompiledSemanticState,
    reader: DatasourceEngineTimezone | None,
    dependencies: list[ir.Value] | None,
) -> tuple[_VersionValue, ...]:
    """Prepare every version consumer with the same physical parser and authority."""
    from marivo.analysis.compiler.source_time import entity_engine, source_time
    from marivo.datasource.timezone import probe_engine_timezone
    from marivo.refs import ref
    from marivo.semantic._expression_binding import evaluate_expression_body
    from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity

    resolved_reader = reader if reader is not None else probe_engine_timezone(table._find_backend())
    paths = (
        (version.coordinate_ref.path,)
        if isinstance(version, TargetSnapshotVersion)
        else (version.valid_from_ref.path, version.valid_to_ref.path)
    )
    values: list[_VersionValue] = []
    kinds: list[str] = []
    zone = version.timezone or "UTC"

    def prepare(path: str) -> _VersionValue:
        from marivo.semantic.ir import HourPrefixParse

        axis = normalize_target_dimension(state.registry, path, prepared_column=path)
        body = state.sidecar.bodies.get(ref.time_dimension(axis.ref.path))
        if body is None:
            raise DatasetConstructionError(
                expected="a captured temporal field body",
                received=path,
                repair="Load the owning semantic model.",
                location="analysis.members.version",
            )
        raw = evaluate_expression_body(
            catalog_definition_fingerprint=state.definition_fingerprint,
            expression_sidecar=state.sidecar,
            owning_ref=ref.time_dimension(axis.ref.path),
            body=body,
            entity_refs=(ref.entity(axis.entity_ref.path),),
            aliases=(table,),
        )
        if dependencies is not None:
            dependencies.append(raw)
        parsed_input = raw
        if isinstance(version, TargetValidityVersion) and path == version.valid_to_ref.path:
            sentinels = tuple(value for value in version.open_end if value is not None)
            if sentinels:
                is_open = ibis.or_(
                    *(raw == ibis.literal(value).cast(raw.type()) for value in sentinels)
                )
                parsed_input = is_open.ifelse(ibis.null().cast(raw.type()), raw)
        prefix = prepare(axis.parse.prefix) if isinstance(axis.parse, HourPrefixParse) else None
        result, authority = source_time(
            parsed_input,
            axis,
            boundary_timezone=zone,
            read_timezone=resolved_reader.engine_timezone_name,
            read_source=resolved_reader.read_tz_resolution,
            engine=entity_engine(
                state.registry, normalize_target_entity(state.registry, axis.entity_ref.path)
            ),
            prefix=prefix.value if prefix is not None else None,
            prefix_timezone=resolved_reader.engine_timezone_name if prefix is not None else None,
        )
        return _VersionValue(
            result,
            raw,
            axis,
            authority,
            prefix.raw if prefix is not None else None,
            prefix.axis if prefix is not None else None,
        )

    for path in paths:
        value = prepare(path)
        values.append(value)
        kinds.append("date" if value.value.type().is_date() else "timestamp")
    if isinstance(version, TargetValidityVersion):
        if len(set(kinds)) != 1:
            raise DatasetConstructionError(
                expected="matching DATE/DATE or timestamp/timestamp validity fields",
                received="/".join(kinds),
                repair="Declare validity endpoints with the same temporal kind.",
                location="analysis.members.version",
            )
        if kinds[0] == "date" and version.timezone is None:
            raise DatasetConstructionError(
                expected="an explicit business timezone for DATE validity",
                received="timezone=None",
                repair="Set timezone on ms.validity to the business day timezone.",
                location="analysis.members.version",
            )
        if kinds[0] == "timestamp" and version.timezone is not None:
            raise DatasetConstructionError(
                expected="timestamp validity without a business timezone",
                received=version.timezone,
                repair="Remove timezone from ms.validity; declare timezone on the parser for naive sources, and omit it for aware sources.",
                location="analysis.members.version",
            )
    return tuple(values)


def version_predicate(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion | None,
    selected: TargetSnapshotSelection | TargetValiditySelection | None,
    *,
    state: CompiledSemanticState,
    reader: DatasourceEngineTimezone | None = None,
    dependencies: list[ir.Value] | None = None,
) -> ir.BooleanValue:
    """Build exact snapshot or symbolic validity predicates through Ibis."""
    if isinstance(version, TargetSnapshotVersion) and isinstance(selected, TargetSnapshotSelection):
        prepared = _version_values(table, version, state, reader, dependencies)[0]
        column = prepared.value
        period = date.fromisoformat(selected.period)
        if column.type().is_date():
            raw = source_time_comparison(
                prepared.raw,
                prepared.axis,
                prepared.authority,
                bound=period,
                comparison="==",
                prefix=prepared.prefix,
                prefix_axis=prepared.prefix_axis,
            )
            if raw is not None:
                return raw
            return column == ibis.literal(period)
        if column.type().is_timestamp():
            from datetime import timedelta

            zone = ZoneInfo(version.timezone)
            snapshot_start = datetime.combine(period, time(), zone)
            snapshot_end = datetime.combine(period + timedelta(days=1), time(), zone)
            raw = source_time_predicate(
                prepared.raw,
                prepared.axis,
                prepared.authority,
                start=snapshot_start,
                end=snapshot_end,
                prefix=prepared.prefix,
                prefix_axis=prepared.prefix_axis,
            )
            if raw is not None:
                return raw
            return (column >= _bound(column, snapshot_start)) & (
                column < _bound(column, snapshot_end)
            )
        raise DatasetConstructionError(
            expected="a native temporal snapshot coordinate",
            received=str(column.type()),
            repair="Use a native date or timestamp snapshot coordinate.",
            location="analysis.members.version",
        )
    if isinstance(version, TargetValidityVersion) and isinstance(selected, TargetValiditySelection):
        start, end = _version_values(table, version, state, reader, dependencies)
        raw_end = end.raw
        boundary = datetime.fromisoformat(selected.boundary)
        left = (
            start.comparison(boundary, "<")
            if selected.start_operator == "lt"
            else start.comparison(boundary, "<=")
        )
        right = (
            end.comparison(boundary, ">")
            if selected.end_operator == "gt"
            else end.comparison(boundary, ">=")
        )
        for sentinel in selected.open_end:
            right = right | (
                raw_end.isnull()
                if sentinel is None
                else raw_end == ibis.literal(sentinel).cast(raw_end.type())
            )
        return left & right
    if version is None and selected is None:
        return ibis.literal(True)
    raise DatasetConstructionError(
        expected="matching declared version and exact selection",
        received=f"{type(version).__name__}/{type(selected).__name__}",
        repair="Rebuild the member graph with the declared version's explicit anchor.",
        location="analysis.members.version",
    )


def prepare_version_filter(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion,
    selected: TargetSnapshotSelection | TargetValiditySelection | None,
    *,
    state: CompiledSemanticState,
    reader: DatasourceEngineTimezone | None = None,
    dependencies: list[ir.Value] | None = None,
) -> ir.Table:
    """Select versions with each endpoint's captured source-time authority."""
    return table.filter(
        version_predicate(
            table, version, selected, state=state, reader=reader, dependencies=dependencies
        )
    )
