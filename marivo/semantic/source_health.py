"""Explicit, bounded source-health checks for a loaded semantic catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeAlias, cast
from zoneinfo import ZoneInfo

import ibis.expr.types as ir
import pandas as pd

from marivo._authoring.model import AuthoringRepair
from marivo._compat import UTC
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import DatasourceAuthoringError, _backend_failure_summary
from marivo.datasource.inspection import SourceInspection, _inspect_in_project
from marivo.datasource.ir import CsvSourceIR, EntitySourceIR, JsonSourceIR
from marivo.datasource.source import AuthoringScope
from marivo.refs import (
    DatasourceKind,
    DimensionKind,
    EntityKind,
    FieldKind,
    MeasureKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.refs import ref as ref_factory
from marivo.render import Card, RenderableResult
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair
from marivo.semantic.preview_scope import PreviewScope, _normalize_scopes, dependency_entities

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.resolver import SemanticResolver
    from marivo.semantic.validator import Registry

SourceHealthStatus = Literal["current", "failed", "unavailable", "unknown"]
SourceHealthCheckKind = Literal[
    "connectivity",
    "schema",
    "not_null",
    "allowed_values",
    "unique",
    "freshness",
    "relationship_matches",
    "relationship_cardinality",
]
SourceScalar: TypeAlias = str | int | float | bool
RelationshipSide = Literal["from", "both"]
RelationshipCardinality = Literal[
    "one_to_one",
    "many_to_one",
    "one_to_many",
    "many_to_many",
]

_FIELD_KINDS = frozenset(
    {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
)


def _require_ref(
    value: object,
    *,
    parameter: str,
    allowed: frozenset[SemanticKind],
) -> Ref[SemanticKindTag]:
    if type(value) is not Ref or value.kind not in allowed:
        expected = " | ".join(sorted(kind.value for kind in allowed))
        raise TypeError(f"{parameter} must be exact Ref[{expected}].")
    return cast("Ref[SemanticKindTag]", value)


@dataclass(frozen=True, slots=True)
class NotNullSourceCheck:
    field: Ref[FieldKind]
    kind: Literal["not_null"] = "not_null"

    def __post_init__(self) -> None:
        _require_ref(self.field, parameter="field", allowed=_FIELD_KINDS)


@dataclass(frozen=True, slots=True)
class AllowedValuesSourceCheck:
    field: Ref[DimensionKind | TimeDimensionKind]
    values: tuple[SourceScalar, ...]
    kind: Literal["allowed_values"] = "allowed_values"

    def __post_init__(self) -> None:
        _require_ref(
            self.field,
            parameter="field",
            allowed=frozenset({SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}),
        )
        if not self.values:
            raise ValueError("allowed_values requires at least one value.")
        if len(set(self.values)) != len(self.values):
            raise ValueError("allowed_values values must be unique.")
        if not all(isinstance(value, str | int | float | bool) for value in self.values):
            raise TypeError("allowed_values values must be str, int, float, or bool.")


@dataclass(frozen=True, slots=True)
class UniqueSourceCheck:
    fields: tuple[Ref[FieldKind], ...]
    kind: Literal["unique"] = "unique"

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("unique requires at least one field.")
        for field in self.fields:
            _require_ref(field, parameter="fields", allowed=_FIELD_KINDS)
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("unique fields must not repeat.")


@dataclass(frozen=True, slots=True)
class FreshnessSourceCheck:
    field: Ref[TimeDimensionKind]
    max_age: timedelta
    kind: Literal["freshness"] = "freshness"

    def __post_init__(self) -> None:
        _require_ref(
            self.field,
            parameter="field",
            allowed=frozenset({SemanticKind.TIME_DIMENSION}),
        )
        if not isinstance(self.max_age, timedelta) or self.max_age <= timedelta(0):
            raise ValueError("freshness max_age must be a positive timedelta.")


@dataclass(frozen=True, slots=True)
class RelationshipMatchesSourceCheck:
    relationship: Ref[RelationshipKind]
    side: RelationshipSide
    kind: Literal["relationship_matches"] = "relationship_matches"

    def __post_init__(self) -> None:
        _require_ref(
            self.relationship,
            parameter="relationship",
            allowed=frozenset({SemanticKind.RELATIONSHIP}),
        )
        if self.side not in {"from", "both"}:
            raise ValueError("relationship_matches side must be 'from' or 'both'.")


@dataclass(frozen=True, slots=True)
class RelationshipCardinalitySourceCheck:
    relationship: Ref[RelationshipKind]
    expected: RelationshipCardinality
    kind: Literal["relationship_cardinality"] = "relationship_cardinality"

    def __post_init__(self) -> None:
        _require_ref(
            self.relationship,
            parameter="relationship",
            allowed=frozenset({SemanticKind.RELATIONSHIP}),
        )
        if self.expected not in {
            "one_to_one",
            "many_to_one",
            "one_to_many",
            "many_to_many",
        }:
            raise ValueError("relationship_cardinality expected value is invalid.")


SourceCheck: TypeAlias = (
    NotNullSourceCheck
    | AllowedValuesSourceCheck
    | UniqueSourceCheck
    | FreshnessSourceCheck
    | RelationshipMatchesSourceCheck
    | RelationshipCardinalitySourceCheck
)
FieldSourceCheck: TypeAlias = (
    NotNullSourceCheck | AllowedValuesSourceCheck | UniqueSourceCheck | FreshnessSourceCheck
)


class SourceCheckNamespace:
    """Closed constructors for explicit source-health expectations."""

    def not_null(self, field: Ref[FieldKind], /) -> NotNullSourceCheck:
        return NotNullSourceCheck(field=field)

    def allowed_values(
        self,
        field: Ref[DimensionKind | TimeDimensionKind],
        /,
        *,
        values: Sequence[SourceScalar],
    ) -> AllowedValuesSourceCheck:
        return AllowedValuesSourceCheck(field=field, values=tuple(values))

    def unique(self, *, fields: Sequence[Ref[FieldKind]]) -> UniqueSourceCheck:
        return UniqueSourceCheck(fields=tuple(fields))

    def freshness(
        self,
        field: Ref[TimeDimensionKind],
        /,
        *,
        max_age: timedelta,
    ) -> FreshnessSourceCheck:
        return FreshnessSourceCheck(field=field, max_age=max_age)

    def relationship_matches(
        self,
        relationship: Ref[RelationshipKind],
        /,
        *,
        side: RelationshipSide,
    ) -> RelationshipMatchesSourceCheck:
        return RelationshipMatchesSourceCheck(relationship=relationship, side=side)

    def relationship_cardinality(
        self,
        relationship: Ref[RelationshipKind],
        /,
        *,
        expected: RelationshipCardinality,
    ) -> RelationshipCardinalitySourceCheck:
        return RelationshipCardinalitySourceCheck(
            relationship=relationship,
            expected=expected,
        )


source_check = SourceCheckNamespace()


def _scope_dict(scope: AuthoringScope) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "unpruned",
        "max_rows": scope.max_rows,
        "timeout_seconds": scope.timeout_seconds,
    }
    time_range = getattr(scope, "_time_range", None)
    if time_range is not None:
        payload.update(
            {
                "kind": "time_range",
                "column": time_range.column,
                "start": time_range.start.isoformat(),
                "end": time_range.end.isoformat(),
            }
        )
    elif hasattr(scope, "values"):
        payload.update({"kind": "partition", "values": dict(scope.values)})
    return payload


def _repair_dict(value: AuthoringRepair | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "kind": value.kind,
        "help_target": f"{value.help_target.surface}.{value.help_target.canonical_id}",
        "action": value.action,
        "snippet": value.snippet,
        "candidates": list(value.candidates),
    }


@dataclass(frozen=True, repr=False, slots=True)
class SourceHealthCheckResult(RenderableResult):
    kind: SourceHealthCheckKind
    status: SourceHealthStatus
    datasource: Ref[DatasourceKind]
    source: EntitySourceIR
    target_refs: tuple[Ref[SemanticKindTag], ...]
    affected_refs: tuple[Ref[SemanticKindTag], ...]
    checked_at: str
    observed_schema_fingerprint: str | None
    observed_capability_fingerprint: str | None
    observed: Mapping[str, object]
    repair: AuthoringRepair | None
    user_data_queried: bool
    scopes: tuple[tuple[Ref[EntityKind], AuthoringScope], ...] = ()

    def _repr_identity(self) -> str:
        return (
            f"SourceHealthCheckResult kind={self.kind} status={self.status} "
            f"targets={len(self.target_refs)}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".observed", ".affected_refs", ".repair", ".show()", ".to_dict()"),
        ).status(
            f"user_data_queried={self.user_data_queried} "
            f"datasource={self.datasource.key} source={self.source.kind}"
        )
        if self.affected_refs:
            card = card.listing("affected refs", (ref.key for ref in self.affected_refs))
        if self.repair is not None:
            card = card.field("repair", self.repair.action)
        return card

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "datasource": self.datasource.key,
            "source": self.source.to_dict(),
            "target_refs": [ref.key for ref in self.target_refs],
            "affected_refs": [ref.key for ref in self.affected_refs],
            "checked_at": self.checked_at,
            "observed_schema_fingerprint": self.observed_schema_fingerprint,
            "observed_capability_fingerprint": self.observed_capability_fingerprint,
            "observed": dict(self.observed),
            "repair": _repair_dict(self.repair),
            "user_data_queried": self.user_data_queried,
            "scopes": [
                {"entity": entity_ref.key, "scope": _scope_dict(scope)}
                for entity_ref, scope in self.scopes
            ],
        }


@dataclass(frozen=True, repr=False, slots=True)
class SourceHealthReport(RenderableResult):
    status: SourceHealthStatus
    checks: tuple[SourceHealthCheckResult, ...]
    checked_at: str
    catalog_definition_fingerprint: str

    @property
    def affected_refs(self) -> tuple[Ref[SemanticKindTag], ...]:
        return tuple(dict.fromkeys(ref for check in self.checks for ref in check.affected_refs))

    def _repr_identity(self) -> str:
        return f"SourceHealthReport status={self.status} checks={len(self.checks)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".checks", ".affected_refs", ".show()", ".to_dict()"),
        ).table(
            columns=("check", "status", "data query", "affected refs"),
            rows=(
                (
                    check.kind,
                    check.status,
                    "yes" if check.user_data_queried else "no",
                    str(len(check.affected_refs)),
                )
                for check in self.checks
            ),
            row_count=len(self.checks),
            label="source health",
            show_omission_counts=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "affected_refs": [ref.key for ref in self.affected_refs],
            "checked_at": self.checked_at,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
        }


def _overall_status(checks: Sequence[SourceHealthCheckResult]) -> SourceHealthStatus:
    statuses = {check.status for check in checks}
    for status in ("failed", "unavailable", "unknown"):
        if status in statuses:
            return cast("SourceHealthStatus", status)
    return "current"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _reverse_affected(
    catalog: SemanticCatalog,
    roots: Sequence[Ref[SemanticKindTag]],
) -> tuple[Ref[SemanticKindTag], ...]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for ref, dependencies in catalog._state.dependencies.items():
            if ref not in affected and any(dependency in affected for dependency in dependencies):
                affected.add(ref)
                changed = True
    return tuple(sorted(affected, key=lambda ref: ref.key))


def _field_owner(registry: Registry, ref: Ref[SemanticKindTag]) -> str:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return registry.dimensions[ref.path].entity
    if ref.kind is SemanticKind.MEASURE:
        return registry.measures[ref.path].entity
    raise AssertionError(f"unsupported source-health field kind: {ref.kind}")


def _check_entities(check: SourceCheck, registry: Registry) -> tuple[str, ...]:
    if isinstance(check, UniqueSourceCheck):
        return tuple(dict.fromkeys(_field_owner(registry, field) for field in check.fields))
    if isinstance(check, NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck):
        return (_field_owner(registry, check.field),)
    relationship = registry.relationships[check.relationship.path]
    return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))


def _normalize_checks(
    checks: Sequence[SourceCheck],
    *,
    registry: Registry,
    selected_entities: frozenset[str],
) -> tuple[SourceCheck, ...]:
    normalized = tuple(checks)
    for check in normalized:
        if not isinstance(
            check,
            NotNullSourceCheck
            | AllowedValuesSourceCheck
            | UniqueSourceCheck
            | FreshnessSourceCheck
            | RelationshipMatchesSourceCheck
            | RelationshipCardinalitySourceCheck,
        ):
            _raise(
                ErrorKind.INVALID_REF,
                "catalog.source_health(checks=...) requires values from ms.source_check.",
                cls=SemanticRuntimeError,
                details={"query_executed": False, "received_type": type(check).__name__},
            )
        target_refs = (
            check.fields
            if isinstance(check, UniqueSourceCheck)
            else (
                (check.field,)
                if isinstance(
                    check,
                    NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck,
                )
                else (check.relationship,)
            )
        )
        for target in target_refs:
            if target not in registry_ref_set(registry):
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Source-health target {target.key!r} is absent from the loaded catalog.",
                    cls=SemanticRuntimeError,
                    refs=(target.key,),
                    details={"query_executed": False},
                )
        entities = _check_entities(check, registry)
        if isinstance(check, UniqueSourceCheck) and len(entities) != 1:
            _raise(
                ErrorKind.INVALID_REF,
                "ms.source_check.unique(fields=...) requires fields from one Entity.",
                cls=SemanticRuntimeError,
                refs=tuple(field.key for field in check.fields),
                details={"query_executed": False},
            )
        if not set(entities).issubset(selected_entities):
            _raise(
                ErrorKind.INVALID_REF,
                "Source-health checks must be grounded in the selected refs' Entity sources.",
                cls=SemanticRuntimeError,
                details={"query_executed": False, "unselected_entities": entities},
            )
    return normalized


def registry_ref_set(registry: Registry) -> frozenset[Ref[SemanticKindTag]]:
    refs: set[Ref[SemanticKindTag]] = set()
    refs.update(ref_factory.entity(value) for value in registry.entities)
    refs.update(
        (
            ref_factory.time_dimension(value)
            if field.is_time_dimension
            else ref_factory.dimension(value)
        )
        for value, field in registry.dimensions.items()
    )
    refs.update(ref_factory.measure(value) for value in registry.measures)
    refs.update(ref_factory.relationship(value) for value in registry.relationships)
    return frozenset(refs)


def _result(
    *,
    kind: SourceHealthCheckKind,
    status: SourceHealthStatus,
    datasource: Ref[DatasourceKind],
    source: EntitySourceIR,
    target_refs: Sequence[Ref[SemanticKindTag]],
    affected_refs: Sequence[Ref[SemanticKindTag]],
    checked_at: str,
    inspection: SourceInspection | None,
    observed: Mapping[str, object],
    repair_value: AuthoringRepair | None,
    user_data_queried: bool,
    scopes: Sequence[tuple[Ref[EntityKind], AuthoringScope]] = (),
) -> SourceHealthCheckResult:
    schema_fingerprint = None
    capability_fingerprint = None
    if inspection is not None:
        schema_fingerprint = _fingerprint(
            [(column.name, column.type, column.nullable) for column in inspection.schema]
        )
        capability_fingerprint = _fingerprint(asdict(inspection.execution_capabilities))
    return SourceHealthCheckResult(
        kind=kind,
        status=status,
        datasource=datasource,
        source=source,
        target_refs=tuple(target_refs),
        affected_refs=tuple(affected_refs),
        checked_at=checked_at,
        observed_schema_fingerprint=schema_fingerprint,
        observed_capability_fingerprint=capability_fingerprint,
        observed=MappingProxyType(dict(observed)),
        repair=repair_value,
        user_data_queried=user_data_queried,
        scopes=tuple(scopes),
    )


def _unavailable_observed(exc: Exception) -> dict[str, object]:
    failure = _backend_failure_summary(exc)
    message = f"{failure.identity} {failure.message}".lower()
    if "permission" in message or "denied" in message or "not authorized" in message:
        code = "permission_denied"
    elif any(
        marker in message
        for marker in (
            "not found",
            "missing",
            "does not exist",
            "tablenotfound",
            "failed to resolve datasource table",
        )
    ):
        code = "source_unavailable"
    else:
        code = "connection_unavailable"
    return {
        "code": code,
        "failure_identity": failure.identity,
        "message": failure.message,
    }


def _authoring_failure_observed(exc: DatasourceAuthoringError) -> dict[str, object]:
    failure = _backend_failure_summary(exc)
    return {
        "code": exc.code,
        "stage": exc.stage,
        "failure_identity": failure.identity,
        "message": failure.message,
    }


def _materialized_field(
    resolver: SemanticResolver,
    ref: Ref[FieldKind],
    table: ir.Table,
) -> ir.Value:
    if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return resolver.dimension_on(ref, table)
    return resolver.measure_on(cast("Ref[MeasureKind]", ref), table)


def _schema_missing_fields(
    catalog: SemanticCatalog,
    *,
    entity_id: str,
    inspection: SourceInspection,
) -> tuple[Ref[SemanticKindTag], ...]:
    available_columns = {column.name for column in inspection.schema}
    missing: list[Ref[SemanticKindTag]] = []
    for field_ref, owner_ref in catalog._state.sidecar.field_owners.items():
        if owner_ref.path != entity_id:
            continue
        body = catalog._state.sidecar.bodies.get(field_ref)
        if body is not None and any(
            column not in available_columns for column in body.source_columns
        ):
            missing.append(cast("Ref[SemanticKindTag]", field_ref))
    return tuple(missing)


def _field_frame(
    resolver: SemanticResolver,
    registry: Registry,
    fields: Sequence[Ref[FieldKind]],
    scope: AuthoringScope,
) -> pd.DataFrame:
    entity_id = _field_owner(registry, fields[0])
    table = resolver.table(ref_factory.entity(entity_id)).limit(scope.max_rows)
    values = [
        _materialized_field(resolver, field, table).name(f"value_{index}")
        for index, field in enumerate(fields)
    ]
    return cast("pd.DataFrame", table.select(*values).execute())


def _freshness_observation(
    value: object,
    *,
    declared_timezone: str | None,
) -> tuple[datetime | None, str | None]:
    if value is None or value is pd.NaT or value is pd.NA:
        return None, "no_non_null_values"
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if type(value) is date:
        value = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    if not isinstance(value, datetime):
        return None, "unsupported_observed_type"
    if value.tzinfo is None or value.utcoffset() is None:
        if declared_timezone is None:
            return None, "naive_observed_time"
        value = value.replace(tzinfo=ZoneInfo(declared_timezone))
    return value.astimezone(UTC), None


def _execute_field_check(
    *,
    catalog: SemanticCatalog,
    check: FieldSourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> SourceHealthCheckResult:
    registry = catalog._reg
    fields = check.fields if isinstance(check, UniqueSourceCheck) else (check.field,)
    entity_id = _field_owner(registry, fields[0])
    entity = registry.entities[entity_id]
    datasource = ref_factory.datasource(entity.datasource)
    source = entity.source
    scope = scopes[entity_id]
    target_refs = tuple(fields)
    affected = _reverse_affected(catalog, target_refs)
    query_attempted = False
    try:
        profile = require_profile_for_backend_type(
            registry.datasources[entity.datasource].backend_type
        )
        timeout = profile.authoring_timeout
        if timeout is None:
            raise RuntimeError(
                f"backend {profile.name!r} has no adapter-enforced authoring timeout"
            )
        backend = resolver.connections.session_backend(entity.datasource)
        with timeout(backend, scope.timeout_seconds):
            query_attempted = True
            frame = _field_frame(resolver, registry, fields, scope)
    except Exception as exc:
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(entity_id),
            observed=_unavailable_observed(exc),
            repair_value=repair(
                kind="reconnect",
                canonical_id="source_health",
                action="Restore source access or repair the affected semantic field, then rerun source health.",
            ),
            user_data_queried=query_attempted,
            scopes=((ref_factory.entity(entity_id), scope),),
        )

    status: SourceHealthStatus
    observed: dict[str, object]
    if isinstance(check, NotNullSourceCheck):
        null_count = int(frame.iloc[:, 0].isna().sum())
        status = "current" if null_count == 0 else "failed"
        observed = {"rows_observed": len(frame), "null_count": null_count}
    elif isinstance(check, AllowedValuesSourceCheck):
        values = frame.iloc[:, 0]
        invalid = values[values.notna() & ~values.isin(check.values)]
        status = "current" if invalid.empty else "failed"
        observed = {
            "rows_observed": len(frame),
            "invalid_count": len(invalid),
            "allowed_values": list(check.values),
        }
    elif isinstance(check, UniqueSourceCheck):
        duplicate_count = int(frame.duplicated(keep=False).sum())
        status = "current" if duplicate_count == 0 else "failed"
        observed = {
            "rows_observed": len(frame),
            "duplicate_row_count": duplicate_count,
        }
    elif isinstance(check, FreshnessSourceCheck):
        maximum = frame.iloc[:, 0].max() if not frame.empty else None
        parse = registry.dimensions[check.field.path].parse
        normalized, issue = _freshness_observation(
            maximum,
            declared_timezone=getattr(parse, "timezone", None),
        )
        if issue is not None:
            status = "unknown"
            observed = {"rows_observed": len(frame), "reason": issue}
        else:
            assert normalized is not None
            age = datetime.fromisoformat(checked_at) - normalized
            status = "current" if age <= check.max_age else "failed"
            observed = {
                "rows_observed": len(frame),
                "maximum": normalized.isoformat(),
                "age_seconds": age.total_seconds(),
                "max_age_seconds": check.max_age.total_seconds(),
            }
    else:
        raise AssertionError(f"unexpected field source check: {type(check).__name__}")
    return _result(
        kind=check.kind,
        status=status,
        datasource=datasource,
        source=source,
        target_refs=target_refs,
        affected_refs=affected,
        checked_at=checked_at,
        inspection=inspections.get(entity_id),
        observed=observed,
        repair_value=(
            None
            if status == "current"
            else repair(
                kind="reauthor",
                canonical_id="source_health",
                action="Repair the scoped source data or revise the explicit expectation, then rerun source health.",
            )
        ),
        user_data_queried=True,
        scopes=((ref_factory.entity(entity_id), scope),),
    )


def _relationship_frames(
    resolver: SemanticResolver,
    registry: Registry,
    relationship_ref: Ref[SemanticKindTag],
    scopes: Mapping[str, AuthoringScope],
) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    relationship = registry.relationships[relationship_ref.path]
    left_id = relationship.from_entity
    right_id = relationship.to_entity
    left = resolver.table(ref_factory.entity(left_id)).limit(scopes[left_id].max_rows)
    right = resolver.table(ref_factory.entity(right_id)).limit(scopes[right_id].max_rows)
    left_values = []
    right_values = []
    for index, key in enumerate(relationship.keys):
        from_key, to_key = key.to_tuple()
        from_ir = registry.dimensions[from_key]
        to_ir = registry.dimensions[to_key]
        from_ref = (
            ref_factory.time_dimension(from_key)
            if from_ir.is_time_dimension
            else ref_factory.dimension(from_key)
        )
        to_ref = (
            ref_factory.time_dimension(to_key)
            if to_ir.is_time_dimension
            else ref_factory.dimension(to_key)
        )
        left_values.append(
            resolver.dimension_on(cast("Ref[FieldKind]", from_ref), left).name(f"key_{index}")
        )
        right_values.append(
            resolver.dimension_on(cast("Ref[FieldKind]", to_ref), right).name(f"key_{index}")
        )
    return (
        left.select(*left_values).execute(),
        right.select(*right_values).execute(),
        left_id,
        right_id,
    )


def _execute_relationship_check(
    *,
    catalog: SemanticCatalog,
    check: RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> SourceHealthCheckResult:
    registry = catalog._reg
    relationship = registry.relationships[check.relationship.path]
    left_entity = registry.entities[relationship.from_entity]
    right_entity = registry.entities[relationship.to_entity]
    datasource = ref_factory.datasource(left_entity.datasource)
    target_refs = (check.relationship,)
    affected = _reverse_affected(catalog, target_refs)
    bound_scopes = (
        (ref_factory.entity(relationship.from_entity), scopes[relationship.from_entity]),
        (ref_factory.entity(relationship.to_entity), scopes[relationship.to_entity]),
    )
    if left_entity.datasource != right_entity.datasource:
        return _result(
            kind=check.kind,
            status="unknown",
            datasource=datasource,
            source=left_entity.source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(relationship.from_entity),
            observed={"reason": "cross_datasource_relationship"},
            repair_value=repair(
                kind="reauthor",
                canonical_id="source_health",
                action="Run the relationship expectation in an accountable external system or colocate the source contract.",
            ),
            user_data_queried=False,
            scopes=bound_scopes,
        )
    query_attempted = False
    try:
        profile = require_profile_for_backend_type(
            registry.datasources[left_entity.datasource].backend_type
        )
        timeout = profile.authoring_timeout
        if timeout is None:
            raise RuntimeError(
                f"backend {profile.name!r} has no adapter-enforced authoring timeout"
            )
        backend = resolver.connections.session_backend(left_entity.datasource)
        timeout_seconds = min(scope.timeout_seconds for _ref, scope in bound_scopes)
        with timeout(backend, timeout_seconds):
            query_attempted = True
            left, right, _left_id, _right_id = _relationship_frames(
                resolver,
                registry,
                check.relationship,
                scopes,
            )
    except Exception as exc:
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=left_entity.source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(relationship.from_entity),
            observed=_unavailable_observed(exc),
            repair_value=repair(
                kind="reconnect",
                canonical_id="source_health",
                action="Restore both relationship sources and permissions, then rerun source health.",
            ),
            user_data_queried=query_attempted,
            scopes=bound_scopes,
        )
    key_columns = list(left.columns)
    observed: dict[str, object]
    if isinstance(check, RelationshipMatchesSourceCheck):
        non_null_left = left.dropna(subset=key_columns)
        non_null_right = right.dropna(subset=key_columns)
        left_unmatched = len(left) - len(non_null_left)
        right_unmatched = len(right) - len(non_null_right)
        right_keys = set(map(tuple, non_null_right[key_columns].itertuples(index=False, name=None)))
        left_keys = set(map(tuple, non_null_left[key_columns].itertuples(index=False, name=None)))
        left_unmatched += sum(
            tuple(row) not in right_keys
            for row in non_null_left[key_columns].itertuples(index=False, name=None)
        )
        right_unmatched += sum(
            tuple(row) not in left_keys
            for row in non_null_right[key_columns].itertuples(index=False, name=None)
        )
        failed = left_unmatched > 0 or (check.side == "both" and right_unmatched > 0)
        status: SourceHealthStatus = "failed" if failed else "current"
        observed = {
            "from_rows_observed": len(left),
            "to_rows_observed": len(right),
            "from_unmatched_count": left_unmatched,
            "to_unmatched_count": right_unmatched,
            "required_side": check.side,
        }
    else:
        left_non_null = left.dropna(subset=key_columns)
        right_non_null = right.dropna(subset=key_columns)
        if left_non_null.empty or right_non_null.empty:
            status = "unknown"
            observed = {
                "from_rows_observed": len(left),
                "to_rows_observed": len(right),
                "reason": "insufficient_non_null_keys",
                "expected_cardinality": check.expected,
            }
        else:
            left_unique = not left_non_null.duplicated(subset=key_columns, keep=False).any()
            right_unique = not right_non_null.duplicated(subset=key_columns, keep=False).any()
            if left_unique and right_unique:
                observed_cardinality: RelationshipCardinality = "one_to_one"
            elif left_unique:
                observed_cardinality = "one_to_many"
            elif right_unique:
                observed_cardinality = "many_to_one"
            else:
                observed_cardinality = "many_to_many"
            status = "current" if observed_cardinality == check.expected else "failed"
            observed = {
                "from_rows_observed": len(left),
                "to_rows_observed": len(right),
                "observed_cardinality": observed_cardinality,
                "expected_cardinality": check.expected,
            }
    return _result(
        kind=check.kind,
        status=status,
        datasource=datasource,
        source=left_entity.source,
        target_refs=target_refs,
        affected_refs=affected,
        checked_at=checked_at,
        inspection=inspections.get(relationship.from_entity),
        observed=observed,
        repair_value=(
            None
            if status == "current"
            else (
                repair(
                    kind="rescope",
                    canonical_id="source_health",
                    action="Choose a scope with non-null relationship keys, then rerun source health.",
                )
                if status == "unknown"
                else repair(
                    kind="reauthor",
                    canonical_id="source_health",
                    action="Repair the scoped relationship data or revise the explicit expectation, then rerun source health.",
                )
            )
        ),
        user_data_queried=True,
        scopes=bound_scopes,
    )


def run_source_health(
    catalog: SemanticCatalog,
    *,
    refs: Sequence[Ref[SemanticKindTag]],
    checks: Sequence[SourceCheck],
    scope: PreviewScope | None,
) -> SourceHealthReport:
    """Run metadata checks and explicitly requested bounded data checks."""
    registry = catalog._reg
    selected_entities = tuple(
        dict.fromkeys(
            entity_id
            for ref in refs
            for entity_id in dependency_entities(ref.path, ref.kind, registry)
        )
    )
    if not selected_entities:
        _raise(
            ErrorKind.INVALID_REF,
            "catalog.source_health(refs=...) requires refs grounded in at least one Entity.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    normalized_checks = _normalize_checks(
        checks,
        registry=registry,
        selected_entities=frozenset(selected_entities),
    )
    checked_entities = tuple(
        dict.fromkeys(
            entity_id
            for check in normalized_checks
            for entity_id in _check_entities(check, registry)
        )
    )
    if normalized_checks and scope is None:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.source_health(..., checks=...) requires one explicit AuthoringScope or exact Entity mapping.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    if not normalized_checks and scope is not None:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.source_health(..., scope=...) requires at least one explicit data check.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    normalized_scopes = (
        dict(
            _normalize_scopes(
                checked_entities,
                scope,
                preview_ref=refs[0].path,
                operation="catalog.source_health",
            )
        )
        if scope is not None
        else {}
    )
    checked_at = datetime.now(tz=UTC).isoformat()
    results: list[SourceHealthCheckResult] = []
    inspections: dict[str, SourceInspection | None] = {}
    connections = catalog._project._connection_service()
    connectivity: dict[str, tuple[SourceHealthStatus, Mapping[str, object]]] = {}
    for entity_id in selected_entities:
        entity = registry.entities[entity_id]
        exact_datasource_ref = ref_factory.datasource(entity.datasource)
        if entity.datasource not in connectivity:
            try:
                backend = connections.session_backend(entity.datasource)
                backend.raw_sql("SELECT 1")
            except Exception as exc:
                connectivity[entity.datasource] = (
                    "unavailable",
                    _unavailable_observed(exc),
                )
            else:
                connectivity[entity.datasource] = ("current", {"roundtrip": "SELECT 1"})
        connection_status, connection_observed = connectivity[entity.datasource]
        entity_ref = cast("Ref[SemanticKindTag]", ref_factory.entity(entity_id))
        affected = _reverse_affected(catalog, (entity_ref,))
        results.append(
            _result(
                kind="connectivity",
                status=connection_status,
                datasource=exact_datasource_ref,
                source=entity.source,
                target_refs=(entity_ref,),
                affected_refs=affected,
                checked_at=checked_at,
                inspection=None,
                observed=connection_observed,
                repair_value=(
                    None
                    if connection_status == "current"
                    else repair(
                        kind="reconnect",
                        canonical_id="source_health",
                        action="Restore datasource connectivity or permissions, then rerun source health.",
                    )
                ),
                user_data_queried=False,
            )
        )
        try:
            inspection = _inspect_in_project(
                exact_datasource_ref,
                entity.source,
                project_root=catalog._project.workspace_dir,
            )
        except DatasourceAuthoringError as exc:
            inspections[entity_id] = None
            authoring_status: SourceHealthStatus = (
                "unavailable" if exc.code == "datasource_missing" else "failed"
            )
            results.append(
                _result(
                    kind="schema",
                    status=authoring_status,
                    datasource=exact_datasource_ref,
                    source=entity.source,
                    target_refs=(entity_ref,),
                    affected_refs=affected,
                    checked_at=checked_at,
                    inspection=None,
                    observed=_authoring_failure_observed(exc),
                    repair_value=exc.repair,
                    user_data_queried=False,
                )
            )
            continue
        except Exception as exc:
            inspections[entity_id] = None
            results.append(
                _result(
                    kind="schema",
                    status="unavailable",
                    datasource=exact_datasource_ref,
                    source=entity.source,
                    target_refs=(entity_ref,),
                    affected_refs=affected,
                    checked_at=checked_at,
                    inspection=None,
                    observed=_unavailable_observed(exc),
                    repair_value=repair(
                        kind="inspect",
                        canonical_id="source_health",
                        action="Restore metadata access or the physical source, then rerun source health.",
                    ),
                    user_data_queried=False,
                )
            )
            continue
        inspections[entity_id] = inspection
        missing_fields = _schema_missing_fields(
            catalog,
            entity_id=entity_id,
            inspection=inspection,
        )
        declared_only = isinstance(entity.source, CsvSourceIR | JsonSourceIR) or any(
            "declared" in warning.lower() or "metadata_unavailable" in warning.lower()
            for warning in inspection.warnings
        )
        schema_status: SourceHealthStatus = (
            "failed" if missing_fields else "unknown" if declared_only else "current"
        )
        schema_affected = _reverse_affected(catalog, missing_fields) if missing_fields else affected
        results.append(
            _result(
                kind="schema",
                status=schema_status,
                datasource=exact_datasource_ref,
                source=entity.source,
                target_refs=(entity_ref,),
                affected_refs=schema_affected,
                checked_at=checked_at,
                inspection=inspection,
                observed={
                    "column_count": len(inspection.schema),
                    "missing_field_refs": [ref.key for ref in missing_fields],
                    "metadata_authority": "declared" if declared_only else "authoritative",
                    "metadata_warnings": list(inspection.warnings),
                    "execution_capabilities": asdict(inspection.execution_capabilities),
                },
                repair_value=(
                    None
                    if schema_status == "current"
                    else (
                        repair(
                            kind="reauthor",
                            canonical_id="source_health",
                            action="Restore the missing physical columns or update the affected semantic field bindings.",
                        )
                        if schema_status == "failed"
                        else repair(
                            kind="rescope",
                            canonical_id="source_health",
                            action="Run only the explicit scoped data checks needed to prove the declared-only source contract.",
                        )
                    )
                ),
                user_data_queried=False,
            )
        )
    if normalized_checks:
        resolver = catalog._semantic_resolver(
            connections=connections,
            entity_scopes=normalized_scopes,
        )
        for check in normalized_checks:
            if isinstance(
                check, RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck
            ):
                results.append(
                    _execute_relationship_check(
                        catalog=catalog,
                        check=check,
                        scopes=normalized_scopes,
                        inspections=inspections,
                        resolver=resolver,
                        checked_at=checked_at,
                    )
                )
            else:
                results.append(
                    _execute_field_check(
                        catalog=catalog,
                        check=check,
                        scopes=normalized_scopes,
                        inspections=inspections,
                        resolver=resolver,
                        checked_at=checked_at,
                    )
                )
    return SourceHealthReport(
        status=_overall_status(results),
        checks=tuple(results),
        checked_at=checked_at,
        catalog_definition_fingerprint=catalog.definition_fingerprint,
    )
