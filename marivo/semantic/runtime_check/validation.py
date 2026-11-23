"""Online physical contract validation and explicit bounded data checks."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, cast

from marivo._kernel.compat import UTC
from marivo._kernel.input_context import captured_context
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.validation import input_sequence
from marivo.semantic.catalog_model.inputs import catalog_entry_dependencies
from marivo.semantic.catalog_model.objects import lookup_registry
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair
from marivo.semantic.runtime_check.context import (
    ValidationLookup,
    _field_owner,
    validation_connections,
    validation_resolver,
)
from marivo.semantic.runtime_check.data_checks import (
    _execute_field_check,
    _execute_relationship_check,
)
from marivo.semantic.runtime_check.inspection import (
    _metadata_checks,
)
from marivo.semantic.runtime_check.preview_scope import PreviewScope, _normalize_scopes
from marivo.semantic.runtime_check.results import (
    ValidationCheckResult,
    ValidationReport,
)
from marivo.semantic.runtime_check.source_checks import (
    AllowedValuesSourceCheck,
    FreshnessSourceCheck,
    NotNullSourceCheck,
    RelationshipCardinalitySourceCheck,
    RelationshipMatchesSourceCheck,
    SourceCheck,
    UniqueSourceCheck,
    ValidationStatus,
    source_check_field,
    source_check_relationship,
)

if TYPE_CHECKING:
    from marivo.datasource.spi import AuthoringScope, DatasourceConnectionService, SourceInspection
    from marivo.semantic.static_check.registry import Registry


def _overall_status(checks: Sequence[ValidationCheckResult]) -> ValidationStatus:
    statuses = {check.status for check in checks}
    for status in ("failed", "unavailable", "unknown"):
        if status in statuses:
            return cast("ValidationStatus", status)
    return "current"


def _check_entities(check: SourceCheck, registry: Registry) -> tuple[str, ...]:
    if isinstance(check, UniqueSourceCheck):
        return tuple(dict.fromkeys(_field_owner(registry, field) for field in check._fields))
    if isinstance(check, NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck):
        return (_field_owner(registry, source_check_field(check)),)
    relationship = registry.relationships[source_check_relationship(check).path]
    return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))


def _normalize_checks(
    checks: Sequence[SourceCheck],
    *,
    registry: Registry,
    selected_entities: frozenset[str],
    catalog: ValidationLookup,
) -> tuple[SourceCheck, ...]:
    if not input_sequence(checks):
        _raise(
            ErrorKind.INVALID_PARAMETER,
            "checks must be a sequence of source checks.",
            cls=SemanticRuntimeError,
            details={"parameter": "checks", "query_executed": False},
            expected="a sequence of checks created with ms.source_check",
            received=type(checks).__name__,
            hint="Pass a sequence of checks created with ms.source_check.",
            repair_value=repair(
                kind="retry",
                canonical_id="validate",
                action="Pass a sequence of checks created with ms.source_check, or omit checks for metadata validation.",
            ),
        )
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
                ErrorKind.INVALID_PARAMETER,
                "catalog.validate(checks=...) requires values from ms.source_check.",
                cls=SemanticRuntimeError,
                details={"query_executed": False, "received_type": type(check).__name__},
                expected="a check created with ms.source_check",
                received=type(check).__name__,
                hint="Create checks with ms.source_check before passing them to catalog.validate.",
                repair_value=repair(
                    kind="retry",
                    canonical_id="validate",
                    action="Replace the invalid value with a check created using ms.source_check.",
                ),
            )
        if (
            captured_context(check).project_root is not None
            and captured_context(check).project_root != catalog.workspace_dir.resolve()
        ):
            _raise(
                ErrorKind.INVALID_REF,
                "The source check belongs to another project.",
                cls=SemanticRuntimeError,
                expected=f"a source check from {catalog.workspace_dir}",
                received=str(captured_context(check).project_root),
                details={"parameter": "checks", "query_executed": False},
                repair_value=repair(
                    kind="inspect",
                    canonical_id="validate",
                    action="Rebuild the complete check with named objects from the receiving project's Catalog.",
                ),
            )
        target_refs = (
            check._fields
            if isinstance(check, UniqueSourceCheck)
            else (
                (source_check_field(check),)
                if isinstance(
                    check,
                    NotNullSourceCheck | AllowedValuesSourceCheck | FreshnessSourceCheck,
                )
                else (source_check_relationship(check),)
            )
        )
        for target in target_refs:
            if target not in registry_ref_set(registry):
                _raise(
                    ErrorKind.NOT_FOUND,
                    f"Online validation target {target.key!r} is absent from the loaded catalog.",
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
                refs=tuple(field.key for field in check._fields),
                details={"query_executed": False},
            )
        if not set(entities).issubset(selected_entities):
            _raise(
                ErrorKind.INVALID_REF,
                "Online validation checks must be grounded in the selected refs' Entity sources.",
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


def _dependency_entities(
    catalog: ValidationLookup, refs: Sequence[Ref[SemanticKindTag]]
) -> tuple[str, ...]:
    pending = list(refs)
    checked: set[Ref[SemanticKindTag]] = set()
    while pending:
        ref = pending.pop()
        if ref in checked:
            continue
        checked.add(ref)
        pending.extend(catalog_entry_dependencies(catalog, ref))
    return tuple(sorted(ref.path for ref in checked if ref.kind is SemanticKind.ENTITY))


def _run_source_checks(
    catalog: ValidationLookup,
    checks: tuple[SourceCheck, ...],
    normalized_scopes: Mapping[str, AuthoringScope],
    inspections: Mapping[str, SourceInspection | None],
    connections: DatasourceConnectionService,
    checked_at: str,
) -> Iterator[ValidationCheckResult]:
    resolver = validation_resolver(
        catalog,
        connections=connections,
        entity_scopes=normalized_scopes,
    )
    for check in checks:
        if isinstance(check, RelationshipMatchesSourceCheck | RelationshipCardinalitySourceCheck):
            yield _execute_relationship_check(
                catalog=catalog,
                check=check,
                scopes=normalized_scopes,
                inspections=inspections,
                resolver=resolver,
                checked_at=checked_at,
            )
        else:
            yield _execute_field_check(
                catalog=catalog,
                check=check,
                scopes=normalized_scopes,
                inspections=inspections,
                resolver=resolver,
                checked_at=checked_at,
            )


def run_validation(
    catalog: ValidationLookup,
    *,
    refs: Sequence[Ref[SemanticKindTag]],
    checks: Sequence[SourceCheck],
    scope: PreviewScope | None,
) -> ValidationReport:
    """Run metadata checks and explicitly requested bounded data checks."""
    registry = lookup_registry(catalog)
    selected_entities = _dependency_entities(catalog, refs)
    if not selected_entities:
        _raise(
            ErrorKind.INVALID_REF,
            "catalog.validate(refs=...) requires refs grounded in at least one Entity.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
        )
    normalized_checks = _normalize_checks(
        checks,
        registry=registry,
        selected_entities=frozenset(selected_entities),
        catalog=catalog,
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
            ErrorKind.INVALID_PARAMETER,
            "catalog.validate(..., checks=...) requires one explicit AuthoringScope or exact Entity mapping.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
            expected="an explicit scope when checks are supplied",
            received="checks without scope",
            hint="Supply a bounded scope for the selected checks, or omit checks for metadata-only validation.",
            repair_value=repair(
                kind="rescope",
                canonical_id="validate",
                action="Supply an explicit bounded scope for the checks, or omit checks for metadata-only validation.",
            ),
        )
    if not normalized_checks and scope is not None:
        _raise(
            ErrorKind.INVALID_PARAMETER,
            "catalog.validate(..., scope=...) requires at least one explicit data check.",
            cls=SemanticRuntimeError,
            refs=tuple(ref.key for ref in refs),
            details={"query_executed": False},
            expected="at least one explicit data check with a scope",
            received="scope without checks",
            hint="Supply checks from ms.source_check, or omit scope for metadata-only validation.",
            repair_value=repair(
                kind="retry",
                canonical_id="validate",
                action="Supply checks created with ms.source_check, or omit scope for metadata-only validation.",
            ),
        )
    normalized_scopes = (
        dict(
            _normalize_scopes(
                checked_entities,
                scope,
                preview_ref=refs[0].path,
                operation="catalog.validate",
                catalog=catalog,
            )
        )
        if scope is not None
        else {}
    )
    with validation_connections(catalog) as connections:
        checked_at = datetime.now(tz=UTC).isoformat()
        results, inspections = _metadata_checks(
            catalog,
            selected_entities=selected_entities,
            connections=connections,
            checked_at=checked_at,
        )
        if normalized_checks:
            results.extend(
                _run_source_checks(
                    catalog,
                    normalized_checks,
                    normalized_scopes,
                    inspections,
                    connections,
                    checked_at,
                )
            )
        return ValidationReport(
            status=_overall_status(results),
            checks=tuple(results),
            checked_at=checked_at,
            catalog_definition_fingerprint=catalog.definition_fingerprint,
        )
