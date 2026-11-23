"""Resolve captured field facts without assembling Entity contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from marivo._kernel.refs import RefPayloadV1, SemanticKind, _create_ref
from marivo.datasource.spi import (
    CsvSourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticLoadError,
    repair,
)
from marivo.semantic.ir import (
    DateParse,
    EntityIR,
    HourPrefixParse,
    SourceLocation,
    StrptimeParse,
    TargetDimensionContract,
    TimestampParse,
    is_time_bearing_format,
)
from marivo.semantic.static_check.registry import (
    Registry,
)

if TYPE_CHECKING:
    from marivo.semantic.expressions.ir import (
        CompiledExpressionSidecar as CompiledExpressionSidecar,
    )


def _target_columns(entity: EntityIR) -> tuple[tuple[str, str], ...]:
    source = entity.source
    if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
        names = tuple(name for name, _source in source.columns)
    elif isinstance(source, ParquetSourceIR):
        names = tuple(source.columns or ())
    else:
        names = ()
    return tuple((name, "unknown") for name in names)


def _target_error(
    *,
    ref: str,
    expected: str,
    received: str,
    action: str,
    kind: ErrorKind | str = ErrorKind.INVALID_TARGET_SEMANTICS,
    constraint_id: ConstraintId | None = None,
    location: SourceLocation | None = None,
) -> NoReturn:
    raise SemanticLoadError(
        kind=kind,
        message="The declaration cannot supply the typed analysis semantic contract.",
        refs=(ref,),
        location=location,
        expected=expected,
        received=received,
        hint=action,
        constraint_id=constraint_id,
        repair=repair(kind="reauthor", canonical_id="entity", action=action),
    )


def normalize_target_dimension(
    registry: Registry, dimension_id: str, *, prepared_column: str | None = None
) -> TargetDimensionContract:
    """Normalize a declared column Dimension without compiling or reading its source."""
    dimension = registry.dimensions.get(dimension_id)
    if dimension is None:
        _target_error(
            ref=dimension_id,
            expected="a loaded Dimension",
            received="not loaded",
            action="Use a Dimension from the current Registry.",
        )
    entity = registry.entities.get(dimension.entity)
    source_column = prepared_column or dimension.source_column
    if entity is None or source_column is None:
        _target_error(
            ref=dimension_id,
            expected="a declared direct source column on a loaded Entity",
            received="missing declared source-column facts",
            action="Declare a direct-column Dimension on a source field, and include it in columns=... when the source is projected.",
        )
    columns = dict(_target_columns(entity))
    projected_names = set(columns)
    if prepared_column is None and projected_names and source_column not in projected_names:
        _target_error(
            ref=dimension_id,
            expected="a logical source column exposed by the projection",
            received=f"{dimension.source_column!r} is not projected",
            action="Add the Dimension source column to columns=... or omit the projection.",
        )
    dimension_ref = (
        _create_ref(SemanticKind.TIME_DIMENSION, dimension_id)
        if dimension.is_time_dimension
        else _create_ref(SemanticKind.DIMENSION, dimension_id)
    )
    parse = dimension.parse
    if isinstance(parse, DateParse) or (
        isinstance(parse, StrptimeParse) and not is_time_bearing_format(parse.format)
    ):
        logical_type = "date"
    elif isinstance(parse, (TimestampParse, HourPrefixParse)) or (
        isinstance(parse, StrptimeParse) and is_time_bearing_format(parse.format)
    ):
        logical_type = "timestamp"
    else:
        logical_type = "unknown"
    return TargetDimensionContract(
        ref=RefPayloadV1.from_ref(dimension_ref),
        entity_ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.ENTITY, entity.semantic_id)),
        source_column=source_column,
        logical_type=logical_type,
        nullable=True,
        is_time_dimension=dimension.is_time_dimension,
        sample_interval=dimension.sample_interval,
        parse=parse,
        timezone=(
            parse.timezone
            if isinstance(parse, DateParse | TimestampParse | StrptimeParse)
            else None
        ),
    )
