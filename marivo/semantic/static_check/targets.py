"""Derive source-independent target, key and version facts for a completed registry."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Literal, NoReturn

from marivo._kernel.refs import RefPayloadV1, SemanticKind, _create_ref
from marivo._kernel.timezones import normalize_timezone
from marivo.datasource.spi import (
    EntitySourceIR,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    DeclaredTimezoneContradiction,
    ErrorKind,
    SemanticLoadError,
    repair,
)
from marivo.semantic.ir import (
    EntityIR,
    SnapshotVersioningIR,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
    ValidityVersioningIR,
)
from marivo.semantic.static_check.dimensions import (
    _target_columns,
    _target_error,
    normalize_target_dimension,
)
from marivo.semantic.static_check.physical_identity import entity_dependency_fingerprint
from marivo.semantic.static_check.registry import (
    Registry,
)
from marivo.semantic.static_check.source_timezone import require_source_timezone

if TYPE_CHECKING:
    from marivo.semantic.expressions.ir import (
        CompiledExpressionSidecar as CompiledExpressionSidecar,
    )


def snapshot_target_source(source: EntitySourceIR) -> EntitySourceIR:
    """Detach nested authoring sequences from the captured source declaration."""
    if isinstance(source, JsonSourceIR):
        query: list[tuple[str, JsonQueryParamValue]] = []
        for name, value in source.query_params:
            frozen_value = (
                tuple(value)
                if isinstance(value, Sequence) and not isinstance(value, str)
                else value
            )
            query.append((name, frozen_value))
        return replace(
            source,
            columns=tuple((name, path) for name, path in source.columns),
            query_params=tuple(query),
            body_params=tuple((tuple(path), parameter) for path, parameter in source.body_params),
        )
    if isinstance(source, ParquetSourceIR):
        return replace(
            source, columns=tuple(source.columns) if source.columns is not None else None
        )
    return replace(source, columns=tuple(source.columns))


def _target_time_axis(registry: Registry, entity: EntityIR, path: str) -> TargetDimensionContract:
    axis = normalize_target_dimension(registry, path)
    if axis.entity_ref.path != entity.semantic_id or not axis.is_time_dimension:
        _target_error(
            ref=entity.semantic_id,
            expected="a temporal Dimension on the same Entity",
            received="a foreign or non-temporal version axis",
            action="Reference a declared temporal Dimension owned by this Entity.",
        )
    return axis


def _snapshot_contract(
    registry: Registry, entity: EntityIR, authored: SnapshotVersioningIR, key: tuple[str, ...]
) -> tuple[TargetSnapshotVersion, tuple[str, ...]]:
    entity_id = entity.semantic_id
    axis = _target_time_axis(registry, entity, authored.partition_field)
    if axis.source_column in key:
        _target_error(
            ref=entity_id,
            expected="stable identity K separate from snapshot coordinate",
            received=(
                f"snapshot coordinate {axis.source_column!r} included in primary_key={key!r}"
            ),
            action=f"Remove {axis.source_column!r} from primary_key; keep it in versioning.",
            kind=ErrorKind.IDENTITY_VERSION_OVERLAP,
            constraint_id=ConstraintId.ENTITY_VERSION_KEY_SEPARATE,
            location=entity.location,
        )
    if axis.logical_type == "date":
        zone = require_source_timezone(registry, axis, physical_type="date")
        if zone.timezone != authored.timezone:
            raise DeclaredTimezoneContradiction(
                ref=entity_id,
                declarations=(
                    (axis.ref.path, zone.timezone, zone.origin),
                    (entity_id, authored.timezone, "snapshot"),
                ),
            )
    version = TargetSnapshotVersion(
        axis.ref,
        axis.source_column,
        axis.logical_type,
        authored.timezone,
    )
    return version, (*key, axis.source_column) if key else ()


def _validity_contract(
    registry: Registry, entity: EntityIR, authored: ValidityVersioningIR, key: tuple[str, ...]
) -> tuple[TargetValidityVersion, tuple[str, ...]]:
    entity_id = entity.semantic_id
    start = _target_time_axis(registry, entity, authored.valid_from)
    end = _target_time_axis(registry, entity, authored.valid_to)
    if start.source_column == end.source_column:
        _target_error(
            ref=entity_id,
            expected="distinct validity bounds separate from K",
            received=f"validity bounds share source column {start.source_column!r}",
            action="Declare two distinct validity axes.",
        )
    overlapping = tuple(
        column for column in (start.source_column, end.source_column) if column in key
    )
    if overlapping:
        _target_error(
            ref=entity_id,
            expected="stable identity K separate from validity coordinates",
            received=f"validity coordinates {overlapping!r} included in primary_key={key!r}",
            action=f"Remove {overlapping!r} from primary_key; keep them in versioning.",
            kind=ErrorKind.IDENTITY_VERSION_OVERLAP,
            constraint_id=ConstraintId.ENTITY_VERSION_KEY_SEPARATE,
            location=entity.location,
        )
    if (
        start.logical_type != "unknown"
        and end.logical_type != "unknown"
        and start.logical_type != end.logical_type
    ):
        _target_error(
            ref=entity_id,
            expected="matching DATE or timestamp validity bounds",
            received=f"{start.logical_type}/{end.logical_type}",
            action="Declare validity endpoints with the same logical temporal kind.",
        )
    timezone = None
    if "date" in (start.logical_type, end.logical_type):
        start_zone = require_source_timezone(registry, start, physical_type="date")
        end_zone = require_source_timezone(registry, end, physical_type="date")
        if start_zone.timezone != end_zone.timezone:
            raise DeclaredTimezoneContradiction(
                ref=entity_id,
                declarations=(
                    (start.ref.path, start_zone.timezone, start_zone.origin),
                    (end.ref.path, end_zone.timezone, end_zone.origin),
                ),
            )
        timezone = start_zone.timezone
    version = TargetValidityVersion(
        start.ref,
        end.ref,
        start.source_column,
        end.source_column,
        authored.interval,
        authored.open_end,
        timezone,
    )
    return version, (*key, start.source_column) if key else ()


def normalize_target_entity(registry: Registry, entity_id: str) -> TargetEntityContract:
    """Derive stable identity K and independent version-row facts for authoring and analysis."""
    entity = registry.entities.get(entity_id)
    if entity is None:
        _target_error(
            ref=entity_id,
            expected="a loaded Entity",
            received="not loaded",
            action="Use an Entity from the current Registry.",
        )
    columns = _target_columns(entity)
    key = entity.primary_key
    seen_keys: set[str] = set()
    duplicate_keys: list[str] = []
    for name in key:
        if name in seen_keys and name not in duplicate_keys:
            duplicate_keys.append(name)
        seen_keys.add(name)
    if duplicate_keys:
        _target_error(
            ref=entity_id,
            expected="each identity key listed once in primary_key",
            received=f"duplicate identity keys {tuple(duplicate_keys)!r}",
            action=f"Remove {tuple(duplicate_keys)!r} from repeated positions in primary_key.",
            kind=ErrorKind.DUPLICATE_IDENTITY_KEY,
            constraint_id=ConstraintId.ENTITY_IDENTITY_KEY_UNIQUE,
            location=entity.location,
        )
    projected_names = set(dict(columns))
    if isinstance(entity.source, ParquetSourceIR) and entity.source.columns is not None:
        projected_names = set(entity.source.columns)
    missing_columns = tuple(name for name in key if projected_names and name not in projected_names)
    if missing_columns:
        _target_error(
            ref=entity_id,
            expected="every identity key to be exposed by the source projection",
            received=f"identity columns absent from projection {missing_columns!r}",
            action="Add these identity columns to columns=... or omit the projection.",
            kind=ErrorKind.MISSING_IDENTITY_KEY_COLUMN,
            constraint_id=ConstraintId.ENTITY_IDENTITY_KEY_PROJECTED,
            location=entity.location,
        )
    signature = tuple((name, "unknown") for name in key)
    version: TargetSnapshotVersion | TargetValidityVersion | None = None
    row_key = key
    authored = entity.versioning
    if isinstance(authored, SnapshotVersioningIR):
        version, row_key = _snapshot_contract(registry, entity, authored, key)
    elif isinstance(authored, ValidityVersioningIR):
        version, row_key = _validity_contract(registry, entity, authored, key)
    datasource = registry.datasources.get(entity.datasource)
    if datasource is None:
        _target_error(
            ref=entity_id,
            expected="a loaded declared datasource",
            received="datasource not loaded",
            action="Load the Entity's declared datasource before constructing its contract.",
        )

    dependency_fingerprint = entity_dependency_fingerprint(registry, entity_id)
    credential_slots = tuple(sorted(datasource.env_refs))
    return TargetEntityContract(
        ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.ENTITY, entity_id)),
        datasource_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.DATASOURCE, entity.datasource)
        ),
        dependency_fingerprint=dependency_fingerprint,
        source=snapshot_target_source(entity.source),
        primary_key=key,
        identity_signature=signature,
        version_row_key=row_key,
        columns=columns,
        version=version,
        credential_slots=credential_slots,
    )


def normalize_target_relationship(
    registry: Registry, relationship_id: str
) -> TargetRelationshipContract:
    """Resolve directed keys and structural multiplicity without source I/O."""
    relationship = registry.relationships[relationship_id]
    left_entity = registry.entities.get(relationship.from_entity)
    right_entity = registry.entities.get(relationship.to_entity)

    def invalid(expected: str, received: str, action: str) -> NoReturn:
        raise SemanticLoadError(
            kind=ErrorKind.INVALID_RELATIONSHIP_MAPPING,
            message=f"Relationship {relationship_id!r} has an invalid directed mapping.",
            refs=(relationship_id,),
            location=relationship.location,
            expected=expected,
            received=received,
            hint=action,
            constraint_id=ConstraintId.RELATIONSHIP_MAPPING,
            repair=repair(kind="reauthor", canonical_id="relationship", action=action),
        )

    if left_entity is None or right_entity is None:
        invalid(
            "two loaded Entity endpoints",
            f"{relationship.from_entity!r} -> {relationship.to_entity!r}",
            "Declare both endpoint Entities before loading the Relationship.",
        )
    if not relationship.keys:
        invalid("one or more join key pairs", "empty keys", "Add ms.join_on(...) pairs.")
    columns: list[tuple[str, str]] = []
    for pair in relationship.keys:
        left = registry.dimensions.get(pair.from_key)
        right = registry.dimensions.get(pair.to_key)
        if (
            left is None
            or right is None
            or left.entity != relationship.from_entity
            or right.entity != relationship.to_entity
            or left.source_column is None
            or right.source_column is None
        ):
            invalid(
                "direct-column Dimensions on the exact from/to Entity endpoints",
                f"{pair.from_key!r} -> {pair.to_key!r}",
                "Bind each join key to a direct-column Dimension on its declared endpoint.",
            )
        columns.append((left.source_column, right.source_column))
    from_columns = tuple(left for left, _right in columns)
    to_columns = tuple(right for _left, right in columns)
    if len(set(from_columns)) != len(columns) or len(set(to_columns)) != len(columns):
        invalid(
            "each endpoint key column used once",
            repr(columns),
            "Remove repeated source or target key columns from the relationship keys.",
        )
    from_covers_key = bool(left_entity.primary_key) and set(left_entity.primary_key).issubset(
        from_columns
    )
    to_covers_key = bool(right_entity.primary_key) and set(right_entity.primary_key).issubset(
        to_columns
    )
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
    if from_covers_key and to_covers_key:
        cardinality = "one_to_one"
    elif from_covers_key:
        cardinality = "one_to_many"
    elif to_covers_key:
        cardinality = "many_to_one"
    else:
        cardinality = "many_to_many"
    return TargetRelationshipContract(
        ref=RefPayloadV1.from_ref(_create_ref(SemanticKind.RELATIONSHIP, relationship_id)),
        from_entity_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.ENTITY, relationship.from_entity)
        ),
        to_entity_ref=RefPayloadV1.from_ref(
            _create_ref(SemanticKind.ENTITY, relationship.to_entity)
        ),
        role=relationship.name,
        keys=tuple(columns),
        cardinality=cardinality,
        from_version_resolution_required=left_entity.versioning is not None,
        to_version_resolution_required=right_entity.versioning is not None,
    )


def normalize_target_version_selection(
    entity: TargetEntityContract,
    *,
    boundary: datetime,
    interpretation: Literal["instant", "before_endpoint"],
) -> TargetSnapshotSelection | TargetValiditySelection:
    """Resolve exact period/comparison facts without observing available versions."""
    version = entity.version
    if version is None or interpretation not in {"instant", "before_endpoint"}:
        _target_error(
            ref=entity.ref.path,
            expected="versioning and an exact boundary interpretation",
            received="unversioned Entity or unsupported interpretation",
            action="Supply a versioned Entity and an instant or before-endpoint boundary.",
        )

    zone = normalize_timezone(version.timezone or "UTC").tz
    localized = (
        boundary.replace(tzinfo=zone) if boundary.tzinfo is None else boundary.astimezone(zone)
    )
    if isinstance(version, TargetSnapshotVersion):
        period = localized.date()
        if (
            interpretation == "before_endpoint"
            and localized.timetz().replace(tzinfo=None) == time()
        ):
            period -= timedelta(days=1)
        return TargetSnapshotSelection(version.coordinate_ref, period.isoformat(), interpretation)
    return TargetValiditySelection(
        version.valid_from_ref,
        version.valid_to_ref,
        localized.isoformat(),
        "lt" if interpretation == "before_endpoint" else "le",
        "ge"
        if interpretation == "before_endpoint" or version.interval == "closed_closed"
        else "gt",
        version.open_end,
        interpretation,
    )
