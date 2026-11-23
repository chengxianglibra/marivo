"""Contracts consumed by Analysis, Ontology and the read service."""

from collections.abc import Iterable

from marivo._kernel.refs import ExactRef
from marivo.semantic import Entity, SemanticCatalog, SemanticObject
from marivo.semantic._compiled_state import build_compiled_state
from marivo.semantic._consumption import (
    bind_entity,
    bind_field,
    bind_field_in_state,
    bind_metric,
    bind_metric_in_state,
    bind_predicate,
    bind_predicate_in_state,
)
from marivo.semantic._deferred_composition import (
    _rename_slots as rename_deferred_slots,
)
from marivo.semantic._deferred_semantics import (
    consume_field,
    consume_metric,
    consume_predicate,
)
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic._expression_carrier import expression_role
from marivo.semantic._expression_values import (
    DimensionExpression,
    FieldExpression,
    NumericMetric,
    RowPredicate,
)
from marivo.semantic._expression_values import (
    _ExpressionCarrier as ExpressionCarrier,
)
from marivo.semantic._numeric_duration import (
    recipe_duration,
)
from marivo.semantic._numeric_lowering import (
    finish_nullable_sum,
    lower_metric_formula,
    lower_predicate,
    lower_row,
    lower_source_reduction,
    native_mean,
)
from marivo.semantic.catalog_entries import (
    DependencyContext,
)
from marivo.semantic.catalog_entries import (
    _json_value as serialize_catalog_json_value,
)
from marivo.semantic.catalog_model.inputs import require_catalog_index
from marivo.semantic.catalog_model.objects import entry_value, lookup_state
from marivo.semantic.catalog_model.refs import _make_ref as make_catalog_ref
from marivo.semantic.catalog_previews import require_catalog_ref
from marivo.semantic.check import (
    run_check,
)
from marivo.semantic.compiled_types import (
    CompiledSemanticState,
    PublishedDeclaration,
)
from marivo.semantic.definition_resolution import _Resolver as DefinitionResolver
from marivo.semantic.expressions.inputs import _as_deferred as as_deferred
from marivo.semantic.expressions.inputs import expression_recipe
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.expressions.metric_time import (
    metric_time_occurrences,
)
from marivo.semantic.expressions.objects import (
    _Entity as ResolvedEntity,
)
from marivo.semantic.expressions.objects import (
    entity_exact,
)
from marivo.semantic.expressions.operations import _arith_unit as arith_unit
from marivo.semantic.input_scope import validate_context
from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    SampleIntervalIR,
    SemanticParse,
    SnapshotVersioningIR,
    SourceLocation,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
    TimestampParse,
    ValidityVersioningIR,
    is_time_bearing_format,
)
from marivo.semantic.numeric_operations import (
    NumericCheck,
    NumericValue,
    force_cast,
    numeric_binary,
    numeric_failure,
)
from marivo.semantic.paths import (
    Path,
    RelationshipStep,
    path,
)
from marivo.semantic.runtime_check.materializer import (
    materialize_entity_output,
)
from marivo.semantic.runtime_check.physical_contracts import (
    time_type_issue,
    validate_bound_field,
)
from marivo.semantic.search.documents import exact_identity_matches
from marivo.semantic.search.models import SearchDocument
from marivo.semantic.search.protocol import EmbeddingBackend, rank_similarity, valid_embedding_shape
from marivo.semantic.state_consumption import consume_metric_in_state
from marivo.semantic.static_check.dimensions import normalize_target_dimension
from marivo.semantic.static_check.registry import Registry
from marivo.semantic.static_check.source_timezone import (
    DeclaredSourceTimezone,
    effective_source_timezone,
)
from marivo.semantic.static_check.targets import (
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
    snapshot_target_source,
)
from marivo.semantic.unit_algebra import (
    linear_unit,
    multiply_unit_states,
    ratio_unit,
    render_unit,
    unit_state,
)


def catalog_entity_identity(entry: Entity) -> ExactRef:
    value = entry_value(entry)
    assert isinstance(value, ResolvedEntity)
    return entity_exact(value)


def catalog_state(catalog: SemanticCatalog) -> CompiledSemanticState:
    """Return the compiled state captured when the Catalog was loaded."""
    return lookup_state(catalog)


def iter_catalog_entries(catalog: SemanticCatalog) -> Iterable[SemanticObject]:
    """Visit the entries captured by this Catalog, in index order."""
    return require_catalog_index(catalog).entries()


__all__ = [
    "CompiledExpressionSidecar",
    "CompiledSemanticState",
    "DateParse",
    "DeclaredSourceTimezone",
    "DefinitionResolver",
    "DependencyContext",
    "DimensionExpression",
    "EmbeddingBackend",
    "ExpressionCarrier",
    "FieldExpression",
    "HourPrefixParse",
    "NumericCheck",
    "NumericMetric",
    "NumericValue",
    "Path",
    "PublishedDeclaration",
    "Registry",
    "RelationshipStep",
    "ResolvedEntity",
    "RowPredicate",
    "SampleIntervalIR",
    "SearchDocument",
    "SemanticParse",
    "SnapshotVersioningIR",
    "SourceLocation",
    "StrptimeParse",
    "TargetDimensionContract",
    "TargetEntityContract",
    "TargetRelationshipContract",
    "TargetSnapshotSelection",
    "TargetSnapshotVersion",
    "TargetValiditySelection",
    "TargetValidityVersion",
    "TimestampParse",
    "ValidityVersioningIR",
    "arith_unit",
    "as_deferred",
    "bind_entity",
    "bind_field",
    "bind_field_in_state",
    "bind_metric",
    "bind_metric_in_state",
    "bind_predicate",
    "bind_predicate_in_state",
    "build_compiled_state",
    "catalog_entity_identity",
    "catalog_state",
    "consume_field",
    "consume_metric",
    "consume_metric_in_state",
    "consume_predicate",
    "effective_source_timezone",
    "entity_exact",
    "evaluate_expression_body",
    "exact_identity_matches",
    "expression_recipe",
    "expression_role",
    "finish_nullable_sum",
    "force_cast",
    "is_time_bearing_format",
    "iter_catalog_entries",
    "linear_unit",
    "lower_metric_formula",
    "lower_predicate",
    "lower_row",
    "lower_source_reduction",
    "make_catalog_ref",
    "materialize_entity_output",
    "metric_time_occurrences",
    "multiply_unit_states",
    "native_mean",
    "normalize_target_dimension",
    "normalize_target_entity",
    "normalize_target_relationship",
    "normalize_target_version_selection",
    "numeric_binary",
    "numeric_failure",
    "path",
    "rank_similarity",
    "ratio_unit",
    "recipe_duration",
    "rename_deferred_slots",
    "render_unit",
    "require_catalog_ref",
    "run_check",
    "serialize_catalog_json_value",
    "snapshot_target_source",
    "time_type_issue",
    "unit_state",
    "valid_embedding_shape",
    "validate_bound_field",
    "validate_context",
]
