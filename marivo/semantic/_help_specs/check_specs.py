"""Semantic check capability specifications."""

from __future__ import annotations

from marivo._authoring.model import AuthoringCapability, AuthoringInputRequirement
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target
from marivo.semantic._help_specs.builders import (
    _LOCAL,
    _NONE,
    _PREVIEW,
    _VALIDATE,
    _capability,
    _parameter_input,
    _target,
)


def _source_check_factory_capabilities() -> tuple[AuthoringCapability, ...]:
    """Build exact leaves for every public ``ms.source_check`` method."""

    return (
        _capability(
            "source_check.not_null",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.not_null",
            "Require one field to contain no null values.",
            kind="method",
            output="NotNullSourceCheck",
            inputs=(
                _parameter_input(
                    "subject",
                    "Dimension | TimeDimension | Measure | DimensionSymbol | TimeDimensionSymbol | MeasureSymbol",
                    "field",
                ),
            ),
            effects=_NONE,
            example="ms.source_check.not_null(ms.ref.dimension('sales.orders.region'))",
            public_entrypoint="ms.source_check.not_null",
        ),
        _capability(
            "source_check.allowed_values",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.allowed_values",
            "Require one categorical or temporal field to use only the declared values.",
            kind="method",
            output="AllowedValuesSourceCheck",
            inputs=(
                _parameter_input(
                    "subject",
                    "Dimension | TimeDimension | DimensionSymbol | TimeDimensionSymbol",
                    "field",
                ),
                _parameter_input("dependency", "SourceScalarSequence", "values"),
            ),
            effects=_NONE,
            example=(
                "ms.source_check.allowed_values("
                "ms.ref.dimension('sales.orders.region'), values=('US', 'EU'))"
            ),
            public_entrypoint="ms.source_check.allowed_values",
        ),
        _capability(
            "source_check.unique",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.unique",
            "Require a non-empty same-Entity field tuple to be unique.",
            kind="method",
            output="UniqueSourceCheck",
            inputs=(
                _parameter_input(
                    "subject",
                    "Dimension | TimeDimension | Measure | DimensionSymbol | TimeDimensionSymbol | MeasureSymbol",
                    "fields",
                ),
            ),
            effects=_NONE,
            example=("ms.source_check.unique(fields=(ms.ref.dimension('sales.orders.order_id'),))"),
            public_entrypoint="ms.source_check.unique",
        ),
        _capability(
            "source_check.freshness",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.freshness",
            "Require one time dimension's maximum value to be within a declared age.",
            kind="method",
            output="FreshnessSourceCheck",
            inputs=(
                _parameter_input("subject", "TimeDimension | TimeDimensionSymbol", "field"),
                _parameter_input("dependency", "Duration", "max_age"),
            ),
            effects=_NONE,
            example=(
                "from datetime import timedelta\n"
                "ms.source_check.freshness("
                "ms.ref.time_dimension('sales.orders.created_at'), max_age=timedelta(days=1))"
            ),
            public_entrypoint="ms.source_check.freshness",
        ),
        _capability(
            "source_check.relationship_matches",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.relationship_matches",
            "Check unmatched relationship keys only within the selected bounded source scopes.",
            kind="method",
            output="RelationshipMatchesSourceCheck",
            inputs=(
                _parameter_input("subject", "Relationship | RelationshipSymbol", "relationship"),
                _parameter_input("dependency", "RelationshipSide", "side"),
            ),
            effects=_NONE,
            example=(
                "ms.source_check.relationship_matches("
                "ms.ref.relationship('sales.orders_to_customers'), side='both')"
            ),
            public_entrypoint="ms.source_check.relationship_matches",
        ),
        _capability(
            "source_check.relationship_cardinality",
            "marivo.semantic.runtime_check.source_checks.SourceCheckNamespace.relationship_cardinality",
            "Require current relationship multiplicity to match one exact expectation.",
            kind="method",
            output="RelationshipCardinalitySourceCheck",
            inputs=(
                _parameter_input("subject", "Relationship | RelationshipSymbol", "relationship"),
                _parameter_input("dependency", "RelationshipCardinality", "expected"),
            ),
            effects=_NONE,
            example=(
                "ms.source_check.relationship_cardinality("
                "ms.ref.relationship('sales.orders_to_customers'), "
                "expected='many_to_one')"
            ),
            public_entrypoint="ms.source_check.relationship_cardinality",
        ),
    )


SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "preview",
        "marivo.semantic.catalog.SemanticCatalog.preview",
        "Run one scoped data preview for a current catalog entry or exact ref. Ordinary Metric previews support DuckDB, PostgreSQL, MySQL, Trino and ClickHouse, reusing the nullable Row/Metric kernel; Population/sample/window preparation rejects. Each Entity contributes at most min(scope.max_rows, 10,000) output rows and reports an approximate result. Build scopes with md.unpruned (no pruning predicate), md.time_range (physical time range), or md.partition (physical partition values), all with positive row/time guards. Physical scopes reuse datasource partition preflight; no partition is selected automatically. For multiple dependency Entities, supply exact (Entity, scope) pairs. limit must be an integer from 1 to 100 inclusive and bounds retained preview rows; scope.max_rows independently guards source acquisition. coverage reports Entity input counts and caps; exhaustion is unknown when a cap is reached. execution_limits reports native timeout enforcement; no scan row/byte cap is requested and scan statistics are unknown. Unordered samples are not full-source estimates.",
        kind="method",
        output="PreviewResult",
        inputs=(
            AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
            _parameter_input("subject", "SemanticObject", "ref"),
            _parameter_input(
                "scope", "AuthoringScope | Sequence[tuple[Entity, AuthoringScope]]", "scope"
            ),
            _parameter_input(
                "dependency",
                "Sequence[tuple[Entity, JSON source parameter mapping]]",
                "source_parameters",
                optional=True,
            ),
        ),
        effects=_PREVIEW,
        constraints=("backend_factory_available",),
        example="catalog.preview(revenue, scope=md.unpruned(max_rows=1000, timeout_seconds=30))",
        preconditions=("a current loaded SemanticCatalog",),
        repair_kinds=("reconnect", "reauthor", "user_choice"),
        public_entrypoint="catalog.preview",
        see_also=(
            *(
                LiveHelpTarget(surface="datasource", canonical_id=canonical_target(target))
                for target in ("unpruned", "time_range", "partition")
            ),
            LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
        ),
    ),
    _capability(
        "preview_many",
        "marivo.semantic.catalog.SemanticCatalog.preview_many",
        "Run scoped data previews for a non-empty entry/ref sequence. Ordinary Metric previews support all five SQL backends and reuse datasource partition preflight for every input before any business read. Each Entity contributes at most min(scope.max_rows, 10,000) output rows and reports an approximate result. limit must be an integer from 1 to 100 inclusive and bounds retained preview rows per result; scope.max_rows independently guards Entity output acquisition. coverage reports actual Entity input counts and caps. execution_limits reports native timeout enforcement; no scan row/byte cap is requested. Physical scan statistics remain unknown.",
        kind="method",
        output="PreviewBatchResult",
        inputs=(
            AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
            AuthoringInputRequirement(
                role="subject",
                family="SemanticObject",
                parameter_names=("refs",),
                max_count=None,
            ),
            _parameter_input(
                "scope", "AuthoringScope | Sequence[tuple[Entity, AuthoringScope]]", "scope"
            ),
            _parameter_input(
                "dependency",
                "Sequence[tuple[Entity, JSON source parameter mapping]]",
                "source_parameters",
                optional=True,
            ),
        ),
        effects=_PREVIEW,
        constraints=("backend_factory_available",),
        example="catalog.preview_many([revenue], scope=md.unpruned(max_rows=1000, timeout_seconds=30))",
        preconditions=("a current loaded SemanticCatalog",),
        repair_kinds=("reconnect", "reauthor", "user_choice"),
        public_entrypoint="catalog.preview_many",
        see_also=(LiveHelpTarget(surface="analysis", canonical_id="session.observe"),),
    ),
    _capability(
        "source_check",
        None,
        "Build explicit null, enum, uniqueness, freshness, relationship, or cardinality expectations; no expectation is inferred from samples.",
        kind="boundary",
        output="SourceCheck",
        effects=_NONE,
        see_also=tuple(
            _target(f"source_check.{method}")
            for method in (
                "not_null",
                "allowed_values",
                "unique",
                "freshness",
                "relationship_matches",
                "relationship_cardinality",
            )
        ),
    ),
    *_source_check_factory_capabilities(),
    _capability(
        "validate",
        "marivo.semantic.catalog.SemanticCatalog.validate",
        'Connect to the selected dependency closure and validate Entity outputs, field bindings, numeric/duration types and temporal parser/timezone contracts against real physical types. Unavailable types disclose column names and raw types in observed["unavailable_columns"]; direct Entities still check independent fields. Preview and analysis reuse these field checks. Explicit data expectations require a bounded scope. Does not change readiness or qualify arbitrary analysis methods.',
        kind="method",
        output="ValidationReport",
        inputs=(
            AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
            AuthoringInputRequirement(
                role="subject",
                family="SemanticObject",
                parameter_names=("refs",),
                max_count=None,
            ),
            _parameter_input("dependency", "SourceCheck", "checks", optional=True),
            _parameter_input(
                "scope",
                "AuthoringScope | Sequence[tuple[Entity, AuthoringScope]]",
                "scope",
                optional=True,
            ),
        ),
        effects=_VALIDATE,
        example="catalog.validate([revenue])",
        preconditions=("a current loaded SemanticCatalog",),
        repair_kinds=("inspect", "reconnect", "rescope", "reauthor"),
        public_entrypoint="catalog.validate",
    ),
    _capability(
        "readiness",
        "marivo.semantic.catalog.SemanticCatalog.readiness",
        "Check selected definitions' physical dependency closures without connecting or querying. Declared Measures and Metrics are accepted and expand to their loaded physical dependencies, including declared contribution time fields. requested_ids and checked_ids retain the distinction. analysis_ready_inputs contains only directly requested ready physical objects. Unrelated load warnings are excluded. Analysis execution qualifies physical types and operation support.",
        kind="method",
        output="ReadinessReport",
        inputs=(
            AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
            _parameter_input("subject", "SemanticObject", "refs", optional=True),
        ),
        effects=_LOCAL,
        example="catalog.readiness([catalog.get('measure:sales.orders.amount')])",
        preconditions=("a current loaded SemanticCatalog",),
        public_entrypoint="catalog.readiness",
        see_also=(
            LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.compute"),
            LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.persist"),
        ),
    ),
)
