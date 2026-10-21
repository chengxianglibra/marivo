"""Lower exact member version predicates through Ibis."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from marivo.datasource.timezone import DatasourceEngineTimezone
    from marivo.semantic._compiled_state import CompiledSemanticState

import ibis
import ibis.expr.types as ir

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.semantic.ir import (
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)


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
    validity_checks: list[ir.BooleanValue] | None,
    originals: list[ir.Value] | None = None,
) -> tuple[ir.Value, ...]:
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
    values: list[ir.Value] = []
    kinds: list[str] = []
    zone = version.timezone or "UTC"

    def prepare(path: str) -> ir.Value:
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
        result, _authority = source_time(
            parsed_input,
            axis,
            boundary_timezone=zone,
            read_timezone=resolved_reader.engine_timezone_name,
            read_source=resolved_reader.read_tz_resolution,
            engine=entity_engine(
                state.registry, normalize_target_entity(state.registry, axis.entity_ref.path)
            ),
            prefix=prepare(axis.parse.prefix) if isinstance(axis.parse, HourPrefixParse) else None,
            validity_checks=validity_checks,
        )
        if originals is not None:
            originals.append(raw)
        return result

    for path in paths:
        value = prepare(path)
        values.append(value)
        kinds.append("date" if value.type().is_date() else "timestamp")
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
    validity_checks: list[ir.BooleanValue] | None = None,
) -> ir.BooleanValue:
    """Build exact snapshot or symbolic validity predicates through Ibis."""
    if isinstance(version, TargetSnapshotVersion) and isinstance(selected, TargetSnapshotSelection):
        column = _version_values(table, version, state, reader, dependencies, validity_checks)[0]
        period = date.fromisoformat(selected.period)
        if column.type().is_date():
            return column == ibis.literal(period)
        if column.type().is_timestamp():
            from datetime import timedelta

            zone = ZoneInfo(version.timezone)
            start = datetime.combine(period, time(), zone)
            end = datetime.combine(period + timedelta(days=1), time(), zone)
            return (column >= _bound(column, start)) & (column < _bound(column, end))
        raise DatasetConstructionError(
            expected="a native temporal snapshot coordinate",
            received=str(column.type()),
            repair="Use a native date or timestamp snapshot coordinate.",
            location="analysis.members.version",
        )
    if isinstance(version, TargetValidityVersion) and isinstance(selected, TargetValiditySelection):
        original_fields: list[ir.Value] = []
        start, end = _version_values(
            table, version, state, reader, dependencies, validity_checks, original_fields
        )
        raw_end = original_fields[-1]
        boundary = datetime.fromisoformat(selected.boundary)
        left = (
            start < _bound(start, boundary)
            if selected.start_operator == "lt"
            else start <= _bound(start, boundary)
        )
        right = (
            end > _bound(end, boundary)
            if selected.end_operator == "gt"
            else end >= _bound(end, boundary)
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
) -> tuple[ir.Table, tuple[tuple[str, str, str], ...]]:
    """Retain per-row time errors alongside the exact selected version rows."""
    invalid: list[ir.BooleanValue] = []
    predicate = version_predicate(
        table,
        version,
        selected,
        state=state,
        reader=reader,
        dependencies=dependencies,
        validity_checks=invalid,
    )
    if not invalid:
        return table.filter(predicate), ()
    fields: dict[str, ir.Value] = {}
    checks: list[tuple[str, str, str]] = []
    path = (
        version.coordinate_ref.path
        if isinstance(version, TargetSnapshotVersion)
        else version.valid_from_ref.path
    )
    for index, expression in enumerate(invalid):
        name = f"__version_time_invalid_{index}"
        while name in table.columns or name in fields:
            name += "_"
        fields[name] = expression
        checks.append((name, "an unambiguous existing source civil time", path))
    return table.mutate(**fields).filter(predicate | ibis.or_(*invalid)), tuple(checks)
