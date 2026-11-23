"""Check declared Entity output and projection dependencies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from marivo._kernel.refs import SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.spi import (
    CsvSourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
)
from marivo.semantic.ir import (
    EntityIR,
    SourceLocation,
)
from marivo.semantic.static_check.registry import (
    Registry,
)

if TYPE_CHECKING:
    from marivo.semantic.expressions.ir import (
        CompiledExpressionSidecar as CompiledExpressionSidecar,
    )


_PROJECTED_ALIAS_DISPLAY_LIMIT = 8
_OUTPUT_COLUMN_DISPLAY_LIMIT = 8


def _expression_entity_ids(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
) -> frozenset[str]:
    """Return entity ids that own a compiled Table expression body."""
    if sidecar is None:
        return frozenset()
    return frozenset(
        ref.path
        for ref in sidecar.bodies
        if ref.kind is SemanticKind.ENTITY and ref.path in registry.entities
    )


def _validate_projected_source_aliases(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
) -> list[SemanticError]:
    """Validate direct semantic column references against projected aliases.

    Only direct Entity declarations carry the projected-alias contract: their
    output schema is the Source schema. Expression Entities own a transformed
    output schema, so their downstream references are validated against the
    body output when actual physical metadata is bound instead.
    """
    expression_entities = _expression_entity_ids(registry, sidecar)
    missing_by_entity: dict[str, list[dict[str, object]]] = {}

    def record(
        *, object_id: str, entity: EntityIR, field: str, column: str, location: SourceLocation
    ) -> None:
        source = entity.source
        if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
            aliases = {output_name for output_name, _source in source.columns}
        elif isinstance(source, ParquetSourceIR) and source.columns is not None:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            aliases = set(source.columns)
        else:
            return
        if not aliases:
            return
        if entity.semantic_id in expression_entities:
            return
        if column in aliases:
            return
        missing_by_entity.setdefault(entity.semantic_id, []).append(
            {
                "object": object_id,
                "field": field,
                "received_column": column,
                "location": {"file": location.file, "line": location.line},
            }
        )

    for primary_entity in registry.entities.values():
        for column in primary_entity.primary_key:
            record(
                object_id=primary_entity.semantic_id,
                entity=primary_entity,
                field="primary_key",
                column=column,
                location=primary_entity.location,
            )
    for dimension in registry.dimensions.values():
        entity = registry.entities.get(dimension.entity)
        if entity is None or dimension.source_column is None:
            continue
        record(
            object_id=dimension.semantic_id,
            entity=entity,
            field="column",
            column=dimension.source_column,
            location=dimension.location,
        )
    if sidecar is not None:
        for measure in registry.measures.values():
            entity = registry.entities.get(measure.entity)
            body = sidecar.bodies.get(ref_factory.measure(measure.semantic_id))
            if entity is None or body is None or body.source_column is None:
                continue
            record(
                object_id=measure.semantic_id,
                entity=entity,
                field="column",
                column=body.source_column,
                location=measure.location,
            )

    errors: list[SemanticError] = []
    for entity_id, missing in missing_by_entity.items():
        entity = registry.entities[entity_id]
        source = entity.source
        if isinstance(source, TableSourceIR | CsvSourceIR | JsonSourceIR):
            aliases = tuple(output_name for output_name, _source in source.columns)
        elif isinstance(source, ParquetSourceIR) and source.columns is not None:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            aliases = source.columns
        else:
            continue
        visible_missing = missing[:_PROJECTED_ALIAS_DISPLAY_LIMIT]
        rendered_missing = ", ".join(
            f"{item['object']}.{item['field']}={item['received_column']!r}"
            for item in visible_missing
        )
        omitted_missing = len(missing) - len(visible_missing)
        if omitted_missing:
            rendered_missing += f", ... (+{omitted_missing} more)"
        refs = tuple(dict.fromkeys((entity_id, *(str(item["object"]) for item in missing))))
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Projected entity {entity_id!r} is missing output aliases required by "
                    f"semantic references: {rendered_missing}."
                ),
                refs=refs,
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                expected="stable output aliases declared by md.table(columns=...)",
                received=", ".join(str(item["received_column"]) for item in visible_missing),
                hint=(
                    "Add every missing logical output to the entity's columns= projection, "
                    "or change each semantic column= to an exposed alias."
                ),
                details={
                    "entity": entity_id,
                    "missing_references": missing,
                    "omitted_missing_reference_count": omitted_missing,
                    "available_output_aliases": list(aliases),
                },
            )
        )
    return errors


def _render_column_list(columns: Sequence[str]) -> str:
    """Render up to eight columns with an omitted-count suffix."""
    visible = columns[:_OUTPUT_COLUMN_DISPLAY_LIMIT]
    rendered = ", ".join(repr(column) for column in visible)
    omitted = len(columns) - len(visible)
    if omitted > 0:
        rendered += f", ... (+{omitted} more)"
    return rendered


def _expression_entity_output_schema_errors(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
    entity: EntityIR,
    output_columns: tuple[str, ...],
) -> list[SemanticError]:
    """Check declared keys and downstream column refs against the output schema.

    Missing input columns are Entity-definition failures; missing output
    columns referenced by a downstream field are downstream-definition
    failures. Repairs name the bounded actual output columns.
    """
    errors: list[SemanticError] = []
    entity_id = entity.semantic_id
    output_set = set(output_columns)
    missing_keys = tuple(key for key in entity.primary_key if key not in output_set)
    if missing_keys:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Entity {entity_id!r} output is missing declared primary_key "
                    f"component(s) {_render_column_list(missing_keys)}. The output schema "
                    "is authoritative for downstream keys and fields."
                ),
                refs=(entity_id,),
                expected=f"primary_key {_render_column_list(missing_keys)} on the Entity output",
                received=f"output columns: {_render_column_list(output_columns)}",
                hint=(
                    "Expose the declared key column(s) in the Entity body output, or "
                    "update primary_key to columns that identify the transformed grain."
                ),
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                details={
                    "entity": entity_id,
                    "missing_references": [
                        {
                            "object": entity_id,
                            "field": "primary_key",
                            "received_column": key,
                        }
                        for key in missing_keys
                    ],
                    "omitted_missing_reference_count": 0,
                    "available_output_columns": list(output_columns),
                },
            )
        )
    downstream_missing: list[dict[str, object]] = []
    for dimension in registry.dimensions.values():
        if dimension.entity != entity_id or dimension.source_column is None:
            continue
        if dimension.source_column not in output_set:
            downstream_missing.append(
                {
                    "object": dimension.semantic_id,
                    "field": "column",
                    "received_column": dimension.source_column,
                    "location": {"file": dimension.location.file, "line": dimension.location.line},
                }
            )
    if sidecar is not None:
        for measure in registry.measures.values():
            body = sidecar.bodies.get(ref_factory.measure(measure.semantic_id))
            if measure.entity != entity_id or body is None or body.source_column is None:
                continue
            if body.source_column not in output_set:
                downstream_missing.append(
                    {
                        "object": measure.semantic_id,
                        "field": "column",
                        "received_column": body.source_column,
                        "location": {
                            "file": measure.location.file,
                            "line": measure.location.line,
                        },
                    }
                )
    if downstream_missing:
        visible = downstream_missing[:_OUTPUT_COLUMN_DISPLAY_LIMIT]
        rendered = ", ".join(
            f"{item['object']}.{item['field']}={item['received_column']!r}" for item in visible
        )
        omitted = len(downstream_missing) - len(visible)
        if omitted > 0:
            rendered += f", ... (+{omitted} more)"
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.INVALID_REF,
                message=(
                    f"Entity {entity_id!r} output is missing column(s) required by "
                    f"semantic references: {rendered}."
                ),
                refs=(entity_id, *(str(item["object"]) for item in downstream_missing)),
                expected="columns exposed by the Entity body output",
                received=", ".join(str(item["received_column"]) for item in visible),
                hint=(
                    "Expose every missing column in the Entity body output, or change each "
                    "semantic column= to a column the transformed output provides."
                ),
                location=entity.location,
                constraint_id=ConstraintId.REF_SHAPE,
                details={
                    "entity": entity_id,
                    "missing_references": downstream_missing,
                    "omitted_missing_reference_count": omitted,
                    "available_output_columns": list(output_columns),
                },
            )
        )
    return errors
