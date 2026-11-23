"""Explicit bounded checks of scoped field and relationship values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import date, datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo._kernel.compat import UTC
from marivo._kernel.refs import (
    FieldKind,
    Ref,
    SemanticKindTag,
)
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.spi import (
    AuthoringScope,
    SourceInspection,
    require_profile_for_backend_type,
)
from marivo.semantic.catalog_model.objects import lookup_registry
from marivo.semantic.errors import repair
from marivo.semantic.runtime_check.context import (
    ValidationLookup,
    _field_owner,
    _materialized_field,
    _reverse_affected,
)
from marivo.semantic.runtime_check.evidence import (
    _result,
    _unavailable_observed,
    _unavailable_repair,
)
from marivo.semantic.runtime_check.results import (
    ValidationCheckResult,
)
from marivo.semantic.runtime_check.source_checks import (
    AllowedValuesSourceCheck,
    FieldSourceCheck,
    FreshnessSourceCheck,
    NotNullSourceCheck,
    RelationshipCardinality,
    RelationshipCardinalitySourceCheck,
    RelationshipMatchesSourceCheck,
    UniqueSourceCheck,
    ValidationStatus,
    source_check_field,
    source_check_relationship,
)

if TYPE_CHECKING:
    from marivo.semantic.runtime_check.resolver import SemanticResolver
    from marivo.semantic.static_check.registry import Registry


def _field_frame(
    resolver: SemanticResolver,
    registry: Registry,
    fields: Sequence[Ref[FieldKind]],
    scope: AuthoringScope,
) -> pd.DataFrame:
    entity_id = _field_owner(registry, fields[0])
    table = resolver.entity(ref_factory.entity(entity_id)).limit(scope.max_rows)
    values = [
        _materialized_field(resolver, field, table).name(f"value_{index}")
        for index, field in enumerate(fields)
    ]
    return cast(
        "pd.DataFrame",
        resolver.connections.collect_source(
            registry.entities[entity_id].datasource,
            table.select(*values),
            purpose="semantic.validate_field",
            max_rows=scope.max_rows,
        ),
    )


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
    catalog: ValidationLookup,
    check: FieldSourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> ValidationCheckResult:
    registry = lookup_registry(catalog)
    fields = check._fields if isinstance(check, UniqueSourceCheck) else (source_check_field(check),)
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
        timeout = profile.native_query_timeout
        with (
            resolver.connections.terminal_scope(scope.timeout_seconds)
            if timeout is not None
            else nullcontext()
        ):
            backend = resolver.connections.session_backend(entity.datasource)
            with timeout(backend, scope.timeout_seconds) if timeout is not None else nullcontext():
                query_attempted = True
                frame = _field_frame(resolver, registry, fields, scope)
    except Exception as exc:
        failure_observed = _unavailable_observed(exc)
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(entity_id),
            observed=failure_observed,
            repair_value=_unavailable_repair(failure_observed),
            user_data_queried=query_attempted,
            scopes=((ref_factory.entity(entity_id), scope),),
        )

    status: ValidationStatus
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
        parse = registry.dimensions[source_check_field(check).path].parse
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
                canonical_id="validate",
                action="Repair the scoped source data or revise the explicit expectation, then rerun online validation.",
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
    left = resolver.entity(ref_factory.entity(left_id)).limit(scopes[left_id].max_rows)
    right = resolver.entity(ref_factory.entity(right_id)).limit(scopes[right_id].max_rows)
    left_values = []
    right_values = []
    for index, key in enumerate(relationship.keys):
        from_key, to_key = key.from_key, key.to_key
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
        resolver.connections.collect_source(
            registry.entities[left_id].datasource,
            left.select(*left_values),
            purpose="semantic.validate_relationship",
            max_rows=scopes[left_id].max_rows,
        ),
        resolver.connections.collect_source(
            registry.entities[right_id].datasource,
            right.select(*right_values),
            purpose="semantic.validate_relationship",
            max_rows=scopes[right_id].max_rows,
        ),
        left_id,
        right_id,
    )


def _relationship_observation(
    check: RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck,
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> tuple[ValidationStatus, dict[str, object]]:
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
        status: ValidationStatus = "failed" if failed else "current"
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
    return status, observed


def _execute_relationship_check(
    *,
    catalog: ValidationLookup,
    check: RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck,
    scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    resolver: SemanticResolver,
    checked_at: str,
) -> ValidationCheckResult:
    registry = lookup_registry(catalog)
    relationship = registry.relationships[source_check_relationship(check).path]
    left_entity = registry.entities[relationship.from_entity]
    right_entity = registry.entities[relationship.to_entity]
    datasource = ref_factory.datasource(left_entity.datasource)
    target_refs = (source_check_relationship(check),)
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
                canonical_id="validate",
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
        timeout = profile.native_query_timeout
        timeout_seconds = min(scope.timeout_seconds for _ref, scope in bound_scopes)
        with (
            resolver.connections.terminal_scope(timeout_seconds)
            if timeout is not None
            else nullcontext()
        ):
            backend = resolver.connections.session_backend(left_entity.datasource)
            with timeout(backend, timeout_seconds) if timeout is not None else nullcontext():
                query_attempted = True
                left, right, _left_id, _right_id = _relationship_frames(
                    resolver,
                    registry,
                    source_check_relationship(check),
                    scopes,
                )
    except Exception as exc:
        failure_observed = _unavailable_observed(exc)
        return _result(
            kind=check.kind,
            status="unavailable",
            datasource=datasource,
            source=left_entity.source,
            target_refs=target_refs,
            affected_refs=affected,
            checked_at=checked_at,
            inspection=inspections.get(relationship.from_entity),
            observed=failure_observed,
            repair_value=_unavailable_repair(failure_observed),
            user_data_queried=query_attempted,
            scopes=bound_scopes,
        )
    status, observed = _relationship_observation(check, left, right)
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
                    canonical_id="validate",
                    action="Choose a scope with non-null relationship keys, then rerun online validation.",
                )
                if status == "unknown"
                else repair(
                    kind="reauthor",
                    canonical_id="validate",
                    action="Repair the scoped relationship data or revise the explicit expectation, then rerun online validation.",
                )
            )
        ),
        user_data_queried=True,
        scopes=bound_scopes,
    )
