"""Inspect source metadata and assemble validation evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import cast

import ibis.expr.types as ir

from marivo._authoring.model import AuthoringRepair
from marivo._kernel.refs import (
    DatasourceKind,
    FieldKind,
    Ref,
    SemanticKindTag,
)
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
)
from marivo.datasource.spi import (
    CsvSourceIR,
    DatasourceConnectionService,
    JsonSourceIR,
    SourceInspection,
    backend_errors,
    inspect_in_project,
    probe_backend,
)
from marivo.semantic.catalog_model.objects import lookup_registry, lookup_state
from marivo.semantic.errors import SemanticError, repair
from marivo.semantic.ir import EntityIR
from marivo.semantic.runtime_check.context import (
    ValidationLookup,
    _reverse_affected,
)
from marivo.semantic.runtime_check.evidence import (
    _authoring_failure_observed,
    _result,
    _unavailable_observed,
    _unavailable_repair,
)
from marivo.semantic.runtime_check.physical import (
    _field_contracts,
    _input_column_names,
    _inspected_entity_output,
    _schema_missing_fields,
    _schema_repair,
)
from marivo.semantic.runtime_check.results import (
    ValidationCheckResult,
)
from marivo.semantic.runtime_check.source_checks import (
    ValidationStatus,
)


def _schema_observation(
    inspection: SourceInspection,
    *,
    field_types: Mapping[str, object],
    contract_errors: Sequence[SemanticError],
    missing_fields: tuple[Ref[SemanticKindTag], ...],
    missing_keys: tuple[str, ...],
    unavailable_columns: Mapping[str, str],
    unavailable_fields: tuple[Ref[FieldKind], ...],
    declared_only: bool,
    output_columns: tuple[str, ...] | None,
    output_reason: str | None,
    has_expression_body: bool,
) -> dict[str, object]:
    observed: dict[str, object] = {
        "column_count": len(inspection.schema),
        "missing_field_refs": [ref.key for ref in missing_fields],
        "metadata_authority": "declared" if declared_only else "authoritative",
        "metadata_warnings": list(inspection.warnings),
        "execution_capabilities": asdict(inspection.execution_capabilities),
        "field_types": field_types,
        "contract_violations": [
            {
                "refs": list(error.semantic_refs),
                "expected": error.expected,
                "received": error.received,
                "location": (
                    {"file": error.location.file, "line": error.location.line}
                    if error.location is not None
                    else None
                ),
                "message": str(error),
            }
            for error in contract_errors
        ],
    }
    if unavailable_columns:
        observed["unavailable_columns"] = dict(sorted(unavailable_columns.items()))
        observed["unavailable_field_refs"] = [ref.key for ref in unavailable_fields]
    if missing_keys:
        observed["missing_output_primary_key"] = list(missing_keys)
    if output_reason is not None:
        observed["reason"] = output_reason
    if has_expression_body:
        observed["entity_output_established"] = output_reason is None
        if output_columns is not None:
            observed["entity_output_columns"] = list(output_columns)
        else:
            observed["reason"] = output_reason
    return observed


def _inspected_schema_repair(
    schema_status: ValidationStatus,
    *,
    contract_errors: Sequence[SemanticError],
    unavailable_columns: Mapping[str, str],
    has_expression_body: bool,
    output_columns: tuple[str, ...] | None,
    output_reason: str | None,
    missing_fields: tuple[Ref[SemanticKindTag], ...],
    missing_keys: tuple[str, ...],
) -> AuthoringRepair | None:
    return (
        (contract_errors[0].repair if contract_errors else None)
        or (
            repair(
                kind="reauthor",
                canonical_id="validate",
                action=(
                    "Physical types are unavailable for "
                    + ", ".join(
                        f"{name!r} ({kind})" for name, kind in sorted(unavailable_columns.items())
                    )
                    + ". Expose supported physical types or explicitly project the needed "
                    "supported columns with md.table(columns=...), then rerun catalog.validate()."
                ),
            )
            if unavailable_columns
            else None
        )
        or _schema_repair(
            schema_status=schema_status,
            has_expression_body=has_expression_body,
            output_columns=output_columns,
            output_reason=output_reason,
            missing_fields=missing_fields,
            missing_keys=missing_keys,
        )
    )


def _fields_with_unavailable_types(
    catalog: ValidationLookup,
    *,
    entity_id: str,
    output: ir.Table | None,
    unavailable_columns: Mapping[str, str],
) -> tuple[Ref[FieldKind], ...]:
    unavailable_roots = tuple(
        field_ref
        for field_ref, owner in lookup_state(catalog).sidecar.field_owners.items()
        if owner.path == entity_id
        and unavailable_columns
        and (
            output is None
            or any(
                column in unavailable_columns
                for column in lookup_state(catalog).sidecar.bodies[field_ref].source_columns
            )
        )
    )
    unavailable_dependents = (
        set(_reverse_affected(catalog, unavailable_roots)) if unavailable_roots else set()
    )
    return tuple(
        field_ref
        for field_ref, owner in sorted(
            lookup_state(catalog).sidecar.field_owners.items(), key=lambda item: item[0].key
        )
        if owner.path == entity_id and field_ref in unavailable_dependents
    )


def _inspected_schema_result(
    catalog: ValidationLookup,
    *,
    entity: EntityIR,
    entity_ref: Ref[SemanticKindTag],
    affected: tuple[Ref[SemanticKindTag], ...],
    exact_datasource_ref: Ref[DatasourceKind],
    inspection: SourceInspection,
    checked_at: str,
) -> ValidationCheckResult:
    entity_id = entity.semantic_id
    has_expression_body = lookup_state(catalog).sidecar.bodies.get(entity_ref) is not None
    output, output_reason, unavailable_columns = _inspected_entity_output(
        catalog, entity_id=entity_id, inspection=inspection
    )
    output_columns = tuple(output.columns) if output is not None else None
    if not has_expression_body:
        # Direct entities: the Source schema is the output schema, so
        # field bindings keep comparing against physical input.
        missing_fields = _schema_missing_fields(
            catalog,
            entity_id=entity_id,
            available_columns=_input_column_names(inspection),
        )
    elif output_columns is not None:
        missing_fields = _schema_missing_fields(
            catalog,
            entity_id=entity_id,
            available_columns=set(output_columns),
        )
    else:
        # The expression Entity output schema is not established;
        # neither input columns nor an empty relation can decide
        # downstream output-field presence, so no field is reported
        # missing.
        missing_fields = ()
    if output_columns is not None:
        output_set = (
            _input_column_names(inspection) if not has_expression_body else set(output_columns)
        )
        missing_keys = tuple(key for key in entity.primary_key if key not in output_set)
    else:
        missing_keys = ()
    unavailable_fields = _fields_with_unavailable_types(
        catalog,
        entity_id=entity_id,
        output=output,
        unavailable_columns=unavailable_columns,
    )
    field_types, contract_errors = (
        _field_contracts(
            catalog,
            entity_id=entity_id,
            table=output,
            unavailable_fields=unavailable_fields,
        )
        if output is not None
        else ({}, ())
    )
    declared_only = isinstance(entity.source, CsvSourceIR | JsonSourceIR) or any(
        "declared" in warning.lower() or "metadata_unavailable" in warning.lower()
        for warning in inspection.warnings
    )
    schema_status: ValidationStatus = (
        "failed"
        if missing_fields or output_reason is not None or missing_keys or contract_errors
        else "unknown"
        if declared_only
        else "current"
    )
    schema_affected = (
        _reverse_affected(
            catalog,
            (*missing_fields, entity_ref),
        )
        if missing_fields or missing_keys
        else affected
    )
    observed = _schema_observation(
        inspection,
        field_types=field_types,
        contract_errors=contract_errors,
        missing_fields=missing_fields,
        missing_keys=missing_keys,
        unavailable_columns=unavailable_columns,
        unavailable_fields=unavailable_fields,
        declared_only=declared_only,
        output_columns=output_columns,
        output_reason=output_reason,
        has_expression_body=has_expression_body,
    )
    return _result(
        kind="schema",
        status=schema_status,
        datasource=exact_datasource_ref,
        source=entity.source,
        target_refs=(entity_ref,),
        affected_refs=schema_affected,
        checked_at=checked_at,
        inspection=inspection,
        observed=observed,
        repair_value=_inspected_schema_repair(
            schema_status,
            contract_errors=contract_errors,
            unavailable_columns=unavailable_columns,
            has_expression_body=has_expression_body,
            output_columns=output_columns,
            output_reason=output_reason,
            missing_fields=missing_fields,
            missing_keys=missing_keys,
        ),
        user_data_queried=False,
    )


def _metadata_checks(
    catalog: ValidationLookup,
    *,
    selected_entities: tuple[str, ...],
    connections: DatasourceConnectionService,
    checked_at: str,
) -> tuple[list[ValidationCheckResult], dict[str, SourceInspection | None]]:
    registry = lookup_registry(catalog)
    results: list[ValidationCheckResult] = []
    inspections: dict[str, SourceInspection | None] = {}
    connectivity: dict[str, tuple[ValidationStatus, Mapping[str, object]]] = {}
    for entity_id in selected_entities:
        entity = registry.entities[entity_id]
        exact_datasource_ref = ref_factory.datasource(entity.datasource)
        if entity.datasource not in connectivity:
            try:
                backend = connections.session_backend(entity.datasource)
                with backend_errors(backend):
                    probe_backend(registry.datasources[entity.datasource].backend_type, backend)
            except (DatasourceCredentialError, DatasourceCredentialScopeError):
                raise
            except Exception as exc:
                connectivity[entity.datasource] = (
                    "unavailable",
                    _unavailable_observed(exc),
                )
            else:
                connectivity[entity.datasource] = ("current", {"roundtrip": "ibis_literal"})
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
                    else _unavailable_repair(connection_observed)
                ),
                user_data_queried=False,
            )
        )
        try:
            with connections.resolution_context():
                inspection = inspect_in_project(
                    exact_datasource_ref,
                    entity.source,
                    project_root=catalog.workspace_dir,
                    connections=connections,
                )
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except DatasourceAuthoringError as exc:
            inspections[entity_id] = None
            authoring_status: ValidationStatus = (
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
            failure_observed = _unavailable_observed(exc)
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
                    observed=failure_observed,
                    repair_value=_unavailable_repair(failure_observed),
                    user_data_queried=False,
                )
            )
            continue
        inspections[entity_id] = inspection
        results.append(
            _inspected_schema_result(
                catalog,
                entity=entity,
                entity_ref=entity_ref,
                affected=affected,
                exact_datasource_ref=exact_datasource_ref,
                inspection=inspection,
                checked_at=checked_at,
            )
        )
    return results, inspections
