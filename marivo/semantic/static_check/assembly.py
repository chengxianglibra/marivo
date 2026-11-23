"""Validation layers for marivo.semantic v1.1.

Three layers:
  1. decorator-time (inline in authoring)
  2. AST whitelist (base metric body scanning)
  3. assembly-time (cross-object reference validation)"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo._kernel.refs import SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo.introspection.fuzzy import did_you_mean
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    WarningKind,
    repair,
)
from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    RelationshipIR,
    StrptimeParse,
    TimestampParse,
    is_time_bearing_format,
)
from marivo.semantic.policies import AdditiveAllV1, AdditiveOverV1
from marivo.semantic.static_check.ast_rules import (
    _CAST_TARGET_TO_DECLARED,
    _time_dimension_dtype_advisory,
    _time_dimension_pushdown_advisory,
)
from marivo.semantic.static_check.dimensions import (
    _target_columns,
)
from marivo.semantic.static_check.projected_schema import (
    _validate_projected_source_aliases,
)
from marivo.semantic.static_check.registry import (
    Registry,
)
from marivo.semantic.static_check.targets import (
    normalize_target_entity,
    normalize_target_relationship,
)

if TYPE_CHECKING:
    from marivo.semantic.expressions.ir import (
        CompiledExpressionSidecar as CompiledExpressionSidecar,
    )


__all__ = [
    "assembly_validate",
]

_TEMPORAL_DATA_TYPES = {"date", "datetime", "timestamp"}

_AGGREGATE_METHODS = {"sum", "mean", "avg", "count", "nunique", "max", "min"}


def _validate_entity_versioning(
    errors: list[SemanticError], entity_id: str, registry: Registry
) -> None:
    """Validate stable identity and separate version coordinates with the sole normalizer."""
    try:
        normalize_target_entity(registry, entity_id)
    except SemanticError as error:
        errors.append(error)


def _validate_measure_refs(registry: Registry) -> list[SemanticError]:
    """Validate that every measure's entity resolves to a known entity."""
    errors: list[SemanticError] = []
    for measure in registry.measures.values():
        if measure.entity not in registry.entities:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.ENTITY_NOT_FOUND,
                    message=f"Measure {measure.semantic_id!r} references unknown entity {measure.entity!r}.",
                    refs=(measure.semantic_id, measure.entity),
                    details={"measure": measure.semantic_id, "entity": measure.entity},
                )
            )
    return errors


def _validate_dsl_declarations(registry: Registry) -> list[SemanticError]:
    """Validate physical Measure additivity coordinates and status-time ownership."""

    errors: list[SemanticError] = []
    for measure in registry.measures.values():
        policy = measure.dsl_additivity
        coordinates = (
            policy.exceptions
            if isinstance(policy, AdditiveAllV1)
            else policy.coordinates
            if isinstance(policy, AdditiveOverV1)
            else ()
        )
        for coordinate in coordinates:
            dimension = registry.dimensions.get(coordinate)
            if dimension is None or dimension.entity != measure.entity:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=f"{measure.semantic_id!r} names a non-native additivity coordinate {coordinate!r}.",
                        refs=(measure.semantic_id, coordinate),
                        expected=f"Dimension on {measure.entity}",
                        received=coordinate,
                        hint="Declare a coordinate on the computation root or revise the additivity policy.",
                    )
                )
        axis = measure.status_time_dimension
        if axis is not None:
            dimension = registry.dimensions.get(axis)
            if (
                dimension is None
                or not dimension.is_time_dimension
                or dimension.entity != measure.entity
            ):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=f"{measure.semantic_id!r} has an invalid status time dimension {axis!r}.",
                        refs=(measure.semantic_id, axis),
                        expected=f"TimeDimension on {measure.entity}",
                        received=axis,
                        hint="Bind the Measure's native status-time axis.",
                    )
                )
    return errors


def _validate_expression_bindings(
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> list[SemanticError]:
    """Validate every compiled field binding against exact positional ownership."""
    errors: list[SemanticError] = []
    for owning_ref, body in sidecar.bodies.items():
        entity_ids: tuple[str, ...]
        if owning_ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            dimension = registry.dimensions.get(owning_ref.path)
            entity_ids = () if dimension is None else (dimension.entity,)
        elif owning_ref.kind is SemanticKind.MEASURE:
            measure = registry.measures.get(owning_ref.path)
            entity_ids = () if measure is None else (measure.entity,)
        else:
            continue
        for binding in body.bindings:
            field_ref = binding.to_ref()
            field_body = sidecar.bodies.get(field_ref)
            field_owner = sidecar.field_owners.get(field_ref)
            if field_ref not in sidecar.catalog_refs or field_body is None or field_owner is None:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_TARGET_MISSING,
                        message=(
                            f"Expression body {owning_ref.key!r} binds missing field "
                            f"{field_ref.key!r}."
                        ),
                        refs=(owning_ref.key, field_ref.key),
                        expected=(
                            "a loaded dimension, time_dimension, or measure with a compiled body"
                        ),
                        received="missing catalog object, owner, or expression body",
                        hint=(
                            "Declare the field in the loaded catalog and call it as "
                            "ms.bind(field_ref, entity_alias)."
                        ),
                    )
                )
                continue
            if binding.entity_position >= len(entity_ids):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_ENTITY_MISMATCH,
                        message=(
                            f"Expression body {owning_ref.key!r} binds field "
                            f"{field_ref.key!r} to unavailable entity position "
                            f"{binding.entity_position}."
                        ),
                        refs=(owning_ref.key, field_ref.key),
                        expected=f"an entity position below {len(entity_ids)}",
                        received=str(binding.entity_position),
                        hint="Call the field with its exact direct entity parameter.",
                    )
                )
                continue
            expected_owner = ref_factory.entity(entity_ids[binding.entity_position])
            if field_owner != expected_owner:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.BINDING_ENTITY_MISMATCH,
                        message=(
                            f"Field {field_ref.key!r} does not belong to entity position "
                            f"{binding.entity_position} in {owning_ref.key!r}."
                        ),
                        refs=(owning_ref.key, field_ref.key, expected_owner.key),
                        expected=expected_owner.key,
                        received=field_owner.key,
                        hint="Call the field with the direct parameter for its owning entity.",
                    )
                )
    return errors


def _is_filtered_domain_ref(ref: str, loaded_models: set[str] | None) -> bool:
    """Return True if ref points to an object in a model that was filtered out."""
    if loaded_models is None or "." not in ref:
        return False
    return ref.split(".", 1)[0] not in loaded_models


def _filtered_domain_ref_warning(
    obj_id: str,
    ref: str,
    ref_kind: str,
) -> StructuredWarning:
    """Build a warning for a cross-object ref to a filtered-out domain."""
    ref_domain = ref.split(".", 1)[0]
    return StructuredWarning(
        kind="filtered_domain_ref",
        message=f"{ref_kind} {obj_id!r} references {ref!r} from filtered-out domain {ref_domain!r}.",
        refs=(obj_id, ref),
        location=None,
    )


def _validate_entities(errors: list[SemanticError], registry: Registry) -> None:
    for ds_id, ds_ir in registry.entities.items():
        default = ds_ir.default_time
        if default is not None:
            field = registry.dimensions.get(default)
            if field is None or not field.is_time_dimension or field.entity != ds_id:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_TIME_PATH,
                        message=f"Entity {ds_id!r} has an invalid default contribution time.",
                        refs=(ds_id, default),
                        expected=f"a native TimeDimension declared on {ds_id}",
                        received=(
                            f"{default} on {field.entity}"
                            if field is not None and field.is_time_dimension
                            else f"{default}, which is not a declared TimeDimension"
                        ),
                        hint="Choose a native TimeDimension on this Entity or omit default_time.",
                        location=ds_ir.location,
                    )
                )
        if ds_ir.datasource not in registry.datasources:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_DATASOURCE_REF,
                    message=f"Entity {ds_id!r} references unknown datasource {ds_ir.datasource!r}.",
                    refs=(ds_id, ds_ir.datasource),
                    expected="a datasource declared in this project",
                    received=ds_ir.datasource,
                    hint="Choose a declared datasource or add the intended datasource declaration.",
                    repair=repair(
                        kind="reauthor",
                        canonical_id="load",
                        action="Choose a declared datasource or add the intended datasource declaration, then reload the project.",
                        candidates=tuple(sorted(registry.datasources)),
                    ),
                    details={
                        "missing_ref": ds_ir.datasource,
                        "did_you_mean": did_you_mean(
                            ds_ir.datasource, sorted(registry.datasources.keys())
                        ),
                    },
                )
            )
        versioning = ds_ir.versioning
        projected_names = {name for name, _physical in _target_columns(ds_ir)}
        missing_projected_key = bool(projected_names) and any(
            name not in projected_names for name in ds_ir.primary_key
        )
        if (
            (versioning is not None or ds_ir.primary_key)
            and ds_ir.datasource in registry.datasources
            and not missing_projected_key
        ):
            _validate_entity_versioning(errors, ds_id, registry)


def _validate_relationship_keys(
    errors: list[SemanticError],
    warnings: list[StructuredWarning],
    registry: Registry,
    loaded_models: set[str] | None,
    r_id: str,
    r_ir: RelationshipIR,
) -> None:
    for join_key in r_ir.keys:
        ff = join_key.from_key
        if ff not in registry.dimensions:
            if _is_filtered_domain_ref(ff, loaded_models):
                warnings.append(_filtered_domain_ref_warning(r_id, ff, "Relationship"))
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_DIMENSION_REF,
                        message=f"Relationship {r_id!r} references unknown from_key {ff!r}.",
                        refs=(r_id, ff),
                        details={
                            "missing_ref": ff,
                            "did_you_mean": did_you_mean(ff, sorted(registry.dimensions.keys())),
                        },
                    )
                )
        tf = join_key.to_key
        if tf not in registry.dimensions:
            if _is_filtered_domain_ref(tf, loaded_models):
                warnings.append(_filtered_domain_ref_warning(r_id, tf, "Relationship"))
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.MISSING_DIMENSION_REF,
                        message=f"Relationship {r_id!r} references unknown to_key {tf!r}.",
                        refs=(r_id, tf),
                        details={
                            "missing_ref": tf,
                            "did_you_mean": did_you_mean(tf, sorted(registry.dimensions.keys())),
                        },
                    )
                )


def _validate_relationships(
    errors: list[SemanticError],
    warnings: list[StructuredWarning],
    registry: Registry,
    loaded_models: set[str] | None,
) -> None:
    for r_id, r_ir in registry.relationships.items():
        error_count = len(errors)
        if r_ir.from_entity not in registry.entities:
            if _is_filtered_domain_ref(r_ir.from_entity, loaded_models):
                warnings.append(
                    _filtered_domain_ref_warning(r_id, r_ir.from_entity, "Relationship")
                )
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                        message=f"Relationship {r_id!r} references unknown "
                        f"from_entity {r_ir.from_entity!r}.",
                        refs=(r_id, r_ir.from_entity),
                    )
                )
        if r_ir.to_entity not in registry.entities:
            if _is_filtered_domain_ref(r_ir.to_entity, loaded_models):
                warnings.append(_filtered_domain_ref_warning(r_id, r_ir.to_entity, "Relationship"))
            else:
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_RELATIONSHIP_ENDPOINT,
                        message=f"Relationship {r_id!r} references unknown "
                        f"to_entity {r_ir.to_entity!r}.",
                        refs=(r_id, r_ir.to_entity),
                    )
                )
        _validate_relationship_keys(errors, warnings, registry, loaded_models, r_id, r_ir)

        if (
            len(errors) == error_count
            and r_ir.from_entity in registry.entities
            and r_ir.to_entity in registry.entities
        ):
            try:
                normalize_target_relationship(registry, r_id)
            except SemanticError as error:
                errors.append(error)


def _validate_hour_prefixes(
    errors: list[SemanticError],
    warnings: list[StructuredWarning],
    registry: Registry,
    loaded_models: set[str] | None,
) -> None:
    for f_id, f_ir in registry.dimensions.items():
        if f_ir.is_time_dimension and isinstance(f_ir.parse, HourPrefixParse):
            prefix_name = f_ir.parse.prefix
            prefix_field = registry.dimensions.get(prefix_name)
            if prefix_field is None:
                candidates = [
                    d
                    for d in registry.dimensions.values()
                    if d.entity == f_ir.entity and d.name == prefix_name
                ]
                prefix_field = candidates[0] if len(candidates) == 1 else None
            if prefix_field is None or not prefix_field.is_time_dimension:
                if _is_filtered_domain_ref(prefix_name, loaded_models):
                    warnings.append(
                        _filtered_domain_ref_warning(f_id, prefix_name, "Time dimension")
                    )
                else:
                    errors.append(
                        SemanticLoadError(
                            kind=ErrorKind.MISSING_DIMENSION_REF,
                            message=f"Time dimension {f_id!r} prefix "
                            f"{prefix_name!r} is not a registered time dimension.",
                            refs=(f_id, prefix_name),
                            details={
                                "missing_ref": prefix_name,
                                "did_you_mean": did_you_mean(
                                    prefix_name, sorted(registry.dimensions.keys())
                                ),
                            },
                        )
                    )
            elif isinstance(prefix_field.parse, (TimestampParse, HourPrefixParse)) or (
                isinstance(prefix_field.parse, StrptimeParse)
                and is_time_bearing_format(prefix_field.parse.format)
            ):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_REF,
                        message=f"Time dimension {f_id!r} hour prefix declares timestamp meaning.",
                        refs=(f_id, prefix_name),
                        expected="civil-date TimeDimension",
                        received=type(prefix_field.parse).__name__,
                        hint="Reference a native DATE or a date-only parser as the hour prefix.",
                        repair=repair(
                            kind="reauthor",
                            canonical_id="hour_prefix",
                            action="Choose a civil-date TimeDimension as the prefix and reload the model.",
                        ),
                    )
                )


def _validate_time_advisories(
    warnings: list[StructuredWarning], registry: Registry, sidecar: CompiledExpressionSidecar | None
) -> None:
    for f_id, f_ir in registry.dimensions.items():
        field_ref = (
            ref_factory.time_dimension(f_id)
            if f_ir.is_time_dimension
            else ref_factory.dimension(f_id)
        )
        body = None if sidecar is None else sidecar.bodies.get(field_ref)
        if _time_dimension_pushdown_advisory(
            f_ir,
            None if body is None else body.callable,
            None if body is None else body.syntax_tree,
        ):
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} casts or parses a partition-like source column. "
                        "For an encoded time axis, declare its raw string/integer column "
                        "with ms.strptime(format). Parsing remains in time predicates; "
                        "retain independent physical filters where needed. No partition "
                        "enumeration or validation queries are added."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )

    for f_id, f_ir in registry.dimensions.items():
        field_ref = (
            ref_factory.time_dimension(f_id)
            if f_ir.is_time_dimension
            else ref_factory.dimension(f_id)
        )
        body = None if sidecar is None else sidecar.bodies.get(field_ref)
        inferred = _time_dimension_dtype_advisory(
            f_ir,
            None if body is None else body.callable,
            None if body is None else body.syntax_tree,
        )
        if inferred is not None:
            compatible = sorted(_CAST_TARGET_TO_DECLARED.get(inferred, set()))
            parse = f_ir.parse
            declared_kind: str | None = None
            if parse is None:
                continue  # deferred parse — skip dtype advisory
            elif isinstance(parse, DateParse):
                declared_kind = "date"
            elif isinstance(parse, TimestampParse):
                declared_kind = "timestamp"
            elif isinstance(parse, StrptimeParse):
                declared_kind = "strptime"
            elif isinstance(parse, HourPrefixParse):
                declared_kind = "hour_prefix"
            warnings.append(
                StructuredWarning(
                    kind=WarningKind.TIME_DIMENSION_DTYPE_ADVISORY.value,
                    message=(
                        f"Time field {f_id!r} declares parse kind={declared_kind!r} "
                        f"but body .cast({inferred!r}) produces ibis dtype {inferred!r}. "
                        f"Compatible parse kinds: {', '.join(compatible)}. "
                        "This mismatch causes TypeError at execution."
                    ),
                    refs=(f_id,),
                    location=f_ir.location,
                )
            )


def assembly_validate(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None = None,
    *,
    loaded_models: set[str] | None = None,
) -> tuple[list[SemanticError], list[StructuredWarning]]:
    """Layer 3: assembly-time cross-object validation.

    Returns (errors, warnings).  Does not raise.

    When *loaded_models* is provided, references that can remain outside the
    selected scope produce ``filtered_domain_ref`` warnings. Each Dimension
    requires its owning Entity in the loaded registry.
    """
    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []

    errors.extend(_validate_projected_source_aliases(registry, sidecar))

    _validate_entities(errors, registry)

    for f_id, f_ir in registry.dimensions.items():
        if f_ir.entity not in registry.entities:
            action = "Declare the owning Entity, or correct the Entity reference, then reload."
            if _is_filtered_domain_ref(f_ir.entity, loaded_models):
                domain = f_ir.entity.split(".", 1)[0]
                action = (
                    f"Include {domain!r} in domains if it declares this Entity, "
                    "or correct the Entity reference, then reload."
                )
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.MISSING_ENTITY_REF,
                    message=f"Dimension {f_id!r} references unknown entity {f_ir.entity!r}.",
                    refs=(f_id, f_ir.entity),
                    location=f_ir.location,
                    expected="the owning Entity in the loaded domain scope",
                    received=f_ir.entity,
                    repair=repair(kind="reauthor", canonical_id="load", action=action),
                    details={
                        "missing_ref": f_ir.entity,
                        "did_you_mean": did_you_mean(f_ir.entity, sorted(registry.entities)),
                    },
                )
            )

    errors.extend(_validate_measure_refs(registry))
    errors.extend(_validate_dsl_declarations(registry))

    if sidecar is not None:
        errors.extend(_validate_expression_bindings(registry, sidecar))
    _validate_relationships(errors, warnings, registry, loaded_models)

    _validate_hour_prefixes(errors, warnings, registry, loaded_models)

    _validate_time_advisories(warnings, registry, sidecar)

    return errors, warnings
