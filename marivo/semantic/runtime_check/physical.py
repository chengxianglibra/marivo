"""Validate declared schema and compiled field contracts against physical metadata."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import ibis
import ibis.expr.types as ir

from marivo._authoring.model import AuthoringRepair
from marivo._kernel.refs import (
    Ref,
    SemanticKindTag,
)
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.spi import (
    SourceInspection,
    ibis_table_type,
)
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic.catalog_model.objects import lookup_registry, lookup_state
from marivo.semantic.errors import SemanticError, repair
from marivo.semantic.runtime_check.context import ValidationLookup
from marivo.semantic.runtime_check.physical_contracts import validate_bound_field
from marivo.semantic.runtime_check.source_checks import (
    ValidationStatus,
)


def _input_column_names(inspection: SourceInspection) -> set[str]:
    """Return the physical input column names from one inspection."""
    return {column.name for column in inspection.schema}


def _schema_repair(
    *,
    schema_status: ValidationStatus,
    has_expression_body: bool,
    output_columns: tuple[str, ...] | None,
    output_reason: str | None,
    missing_fields: tuple[Ref[SemanticKindTag], ...],
    missing_keys: tuple[str, ...],
) -> AuthoringRepair | None:
    """Return the schema-check repair for one entity, or None when current.

    Repair text follows the check classification: direct entities repair
    against physical input columns; expression entities repair against the
    established output, the failed expression build, or the undetermined
    output schema.
    """
    if schema_status == "current":
        return None
    if schema_status == "unknown":
        return repair(
            kind="rescope",
            canonical_id="validate",
            action="Run only the explicit scoped data checks needed to prove the declared-only source contract.",
        )
    if missing_keys:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "Expose the missing primary_key column(s) "
                f"{', '.join(repr(key) for key in missing_keys)} in the Entity body "
                "output, or update primary_key to columns that identify the "
                "transformed grain."
            ),
        )
    if not has_expression_body:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action="Restore the missing physical columns or update the affected semantic field bindings.",
        )
    if missing_fields:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "Expose the missing column(s) in the Entity body output, or update the "
                "affected semantic field bindings to columns the Entity output provides."
            ),
        )
    if output_reason is not None:
        return repair(
            kind="reauthor",
            canonical_id="validate",
            action=(
                "The Entity output schema could not be established from the inspected "
                "input, so output-field presence is undetermined. Correct the Entity "
                "expression or the Source schema, then rerun online validation."
            ),
        )
    return repair(
        kind="reauthor",
        canonical_id="validate",
        action="Restore the missing physical columns or update the affected semantic field bindings.",
    )


def _schema_missing_fields(
    catalog: ValidationLookup,
    *,
    entity_id: str,
    available_columns: set[str],
) -> tuple[Ref[SemanticKindTag], ...]:
    missing: list[Ref[SemanticKindTag]] = []
    for field_ref, owner_ref in lookup_state(catalog).sidecar.field_owners.items():
        if owner_ref.path != entity_id:
            continue
        body = lookup_state(catalog).sidecar.bodies.get(field_ref)
        if body is not None and any(
            column not in available_columns for column in body.source_columns
        ):
            missing.append(cast("Ref[SemanticKindTag]", field_ref))
    return tuple(missing)


def _inspected_entity_output(
    catalog: ValidationLookup,
    *,
    entity_id: str,
    inspection: SourceInspection,
) -> tuple[ir.Table | None, str | None, dict[str, str]]:
    """Bind the Entity output to inspected physical types without reading rows."""

    entity_ref = ref_factory.entity(entity_id)
    body = lookup_state(catalog).sidecar.bodies.get(entity_ref)
    engine = (
        lookup_registry(catalog)
        .datasources[lookup_registry(catalog).entities[entity_id].datasource]
        .backend_type
    )
    input_types: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    for column in inspection.schema:
        normalized = ibis_table_type(
            column.type,
            backend_type=engine,
        )
        if normalized is None:
            unavailable[column.name] = column.type
        else:
            input_types[column.name] = normalized
    reason = "declared_input_types_unusable" if unavailable else None
    if unavailable and body is not None:
        return None, reason, unavailable
    try:
        unbound = ibis.table(input_types, name=f"marivo_unbound_{entity_id}")
    except Exception:
        return None, "declared_input_types_unusable", unavailable
    if body is None:
        return unbound, reason, unavailable
    try:
        result = evaluate_expression_body(
            catalog_definition_fingerprint=catalog.definition_fingerprint,
            expression_sidecar=lookup_state(catalog).sidecar,
            owning_ref=entity_ref,
            body=body,
            entity_refs=(entity_ref,),
            aliases=(unbound,),
            table_result=True,
        )
    except Exception:
        return None, "entity_expression_invalid", unavailable
    if not isinstance(result, ibis.Table):
        return None, "entity_expression_invalid", unavailable
    return result, None, unavailable


def _field_contracts(
    catalog: ValidationLookup,
    *,
    entity_id: str,
    table: ir.Table,
    unavailable_fields: Sequence[Ref[SemanticKindTag]],
) -> tuple[dict[str, str], tuple[SemanticError, ...]]:
    """Validate every field on the selected Entity's real output schema."""
    field_types: dict[str, str] = {}
    errors: list[SemanticError] = []
    for field_ref, owner in sorted(
        lookup_state(catalog).sidecar.field_owners.items(), key=lambda item: item[0].key
    ):
        if owner.path != entity_id:
            continue
        body = lookup_state(catalog).sidecar.bodies[field_ref]
        if field_ref in unavailable_fields:
            continue
        try:
            value = evaluate_expression_body(
                catalog_definition_fingerprint=catalog.definition_fingerprint,
                expression_sidecar=lookup_state(catalog).sidecar,
                owning_ref=field_ref,
                body=body,
                entity_refs=(owner,),
                aliases=(table,),
            )
            field_types[field_ref.key] = str(value.type())
            validate_bound_field(value, ref=field_ref, registry=lookup_registry(catalog))
        except SemanticError as exc:
            errors.append(exc)
    return field_types, tuple(errors)
