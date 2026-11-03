"""Physical field contracts shared by online validation, preview and analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair
from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    SemanticParse,
    SourceLocation,
    StrptimeParse,
    TimestampParse,
)

if TYPE_CHECKING:
    from marivo.semantic.validator import Registry


@dataclass(frozen=True, slots=True)
class PhysicalTypeIssue:
    expected: str
    received: str
    action: str


def time_type_issue(
    physical: dt.DataType,
    *,
    path: str,
    parse: SemanticParse | None,
    engine: str,
) -> PhysicalTypeIssue | None:
    """Check the raw source carrier, before parsing or timezone conversion."""
    declared = (
        parse.timezone if isinstance(parse, DateParse | TimestampParse | StrptimeParse) else None
    )
    aware = isinstance(physical, dt.Timestamp) and (
        physical.timezone is not None or engine == "clickhouse"
    )
    received = f"{engine} physical_type={str(physical)!r}, parser_timezone={declared!r}"
    if aware and declared is not None:
        return PhysicalTypeIssue(
            "an aware source without parser timezone",
            received + "; already-aware physical source",
            f"Replace the parser on {path!r} with parse=ms.timestamp() "
            "and reload the project; the physical source already owns its clock.",
        )
    if isinstance(parse, DateParse):
        valid, expected = physical.is_date(), "a native civil date for the date parser"
    elif isinstance(parse, TimestampParse):
        valid, expected = physical.is_timestamp(), "a native timestamp for ms.timestamp()"
    elif isinstance(parse, StrptimeParse | HourPrefixParse):
        # These parsers construct native string/integer casts. Keep their
        # existing carrier admission and let the backend own value parsing.
        return None
    else:
        valid = physical.is_date() or physical.is_timestamp()
        expected = "a native date or timestamp, or an explicit source parser"
    if valid:
        return None
    return PhysicalTypeIssue(
        expected,
        received,
        f"Update the parser or Entity output for {path!r} to match its physical type, "
        "then reload the project and rerun catalog.validate().",
    )


def validate_field_type(
    value: ir.Value,
    *,
    ref: Ref[SemanticKindTag],
    engine: str,
    parse: SemanticParse | None = None,
    location: SourceLocation | None = None,
) -> None:
    """Reject incompatible physical declarations without reading source rows."""
    physical = value.type()
    issue: PhysicalTypeIssue | None = None
    if ref.kind is SemanticKind.TIME_DIMENSION:
        issue = time_type_issue(physical, path=ref.path, parse=parse, engine=engine)
    elif ref.kind is SemanticKind.MEASURE and not (physical.is_numeric() or physical.is_interval()):
        issue = PhysicalTypeIssue(
            f"a numeric or duration physical type for {ref.path}",
            f"{engine} physical_type={str(physical)!r}",
            f"Expose a numeric or duration Entity output for {ref.path!r}, or correct its Measure "
            "binding. If the goal is to count distinct identifiers or categories, declare the column "
            "with ms.dimension_column(...) and use dimension.count_distinct(). Then reload the "
            "project and rerun catalog.validate().",
        )
    if issue is not None:
        _raise(
            ErrorKind.BINDING_RESULT_INVALID,
            f"Physical type conflicts with the declaration of {ref.key!r}.",
            cls=SemanticRuntimeError,
            refs=(ref.key,),
            location=location,
            expected=issue.expected,
            received=issue.received,
            details={"backend": engine, "physical_type": str(physical), "query_executed": False},
            repair_value=repair(kind="reauthor", canonical_id="validate", action=issue.action),
        )


def validate_bound_field(value: ir.Value, *, ref: Ref[SemanticKindTag], registry: Registry) -> None:
    """Apply a captured physical field's contract to its actual Entity output."""
    if ref.kind is SemanticKind.MEASURE:
        measure = registry.measures[ref.path]
        owner, parse, location = measure.entity, None, measure.location
    elif ref.kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        dimension = registry.dimensions[ref.path]
        owner, parse, location = dimension.entity, dimension.parse, dimension.location
    else:
        return
    datasource = registry.datasources[registry.entities[owner].datasource]
    validate_field_type(
        value, ref=ref, engine=datasource.backend_type, parse=parse, location=location
    )

    if ref.kind is SemanticKind.TIME_DIMENSION:
        from marivo.semantic._source_timezone import require_source_timezone, source_timezone_axis

        physical = value.type()
        aware = isinstance(physical, dt.Timestamp) and (
            physical.timezone is not None or datasource.backend_type == "clickhouse"
        )
        if not aware:
            require_source_timezone(
                registry,
                source_timezone_axis(registry, ref.path),
                physical_type=str(physical),
            )

        if physical.is_date():
            from marivo.semantic.errors import DeclaredTimezoneContradiction
            from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR

            axis = source_timezone_axis(registry, ref.path)
            source = require_source_timezone(registry, axis, physical_type="date")
            declarations: list[tuple[str, str, str]] = [(ref.path, source.timezone, source.origin)]
            version = registry.entities[owner].versioning
            if isinstance(version, SnapshotVersioningIR) and version.partition_field == ref.path:
                declarations.append((owner, version.timezone, "snapshot"))
            elif isinstance(version, ValidityVersioningIR) and ref.path in (
                version.valid_from,
                version.valid_to,
            ):
                other = version.valid_to if ref.path == version.valid_from else version.valid_from
                other_zone = require_source_timezone(
                    registry, source_timezone_axis(registry, other), physical_type="date"
                )
                declarations.append((other, other_zone.timezone, other_zone.origin))
            for temporal_set in registry.temporal_sets.values():
                if ref.path in (temporal_set.start, temporal_set.end):
                    declarations.append(
                        (temporal_set.semantic_id, temporal_set.boundary_timezone, "temporal_set")
                    )
            if len({zone for _path, zone, _origin in declarations}) > 1:
                raise DeclaredTimezoneContradiction(ref=ref.path, declarations=tuple(declarations))
