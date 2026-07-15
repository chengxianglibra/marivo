"""Constraint catalog for ``marivo.semantic`` authoring and validation.

The catalog is the single source for agent-facing rule text, default hints,
and example/doc references.  Validators still own graph algorithms and other
imperative checks, but they report errors through these constraint ids.
"""

from __future__ import annotations

from enum import StrEnum

from marivo.introspection.constraints import ASTSpec, Constraint, Phase

__all__ = [
    "CONSTRAINTS",
    "ASTSpec",
    "Constraint",
    "ConstraintId",
    "constraints_for_error_kind",
    "constraints_for_symbol",
    "default_constraint_for_error_kind",
    "default_hint_for_error_kind",
    "get_constraint",
    "iter_constraints",
]


class ConstraintId(StrEnum):
    """Stable identifiers for semantic constraints."""

    ACTIVE_LOADER_CONTEXT = "active_loader_context"
    ACTIVE_DOMAIN_REQUIRED = "active_domain_required"
    UNIQUE_SEMANTIC_NAME = "unique_semantic_name"
    REF_SHAPE = "ref_shape"
    COMPOSITION_SHAPE = "composition_shape"
    CUMULATIVE_ANCHOR = "cumulative_anchor"
    METRIC_ENTITIES_REQUIRED = "metric_entities_required"
    METRIC_COMPONENT_SCOPE = "metric_component_scope"
    AI_CONTEXT_SCHEMA = "ai_context_schema"
    DOMAIN_OWNER_REQUIRED = "domain_owner_required"
    AST_SINGLE_RETURN = "ast_single_return"
    AST_FORBIDDEN_STATEMENT = "ast_forbidden_statement"
    AST_SQL_ESCAPE_HATCH = "ast_sql_escape_hatch"
    AST_IBIS_ATTR_SHADOW = "ast_ibis_attr_shadow"
    DOMAIN_FILE_PRESENT = "domain_file_present"
    DOMAIN_FILE_MATCHES_DIRECTORY = "domain_file_matches_directory"
    ENTITY_REF_EXISTS = "entity_ref_exists"
    DIMENSION_REF_EXISTS = "dimension_ref_exists"
    METRIC_REF_EXISTS = "metric_ref_exists"
    METRIC_GRAPH_ACYCLIC = "metric_graph_acyclic"
    TIME_DIMENSION_PARTITION_PUSHDOWN = "time_dimension_partition_pushdown"
    TIME_DIMENSION_DTYPE_COMPAT = "time_dimension_dtype_compat"
    TIME_DIMENSION_DEFAULT_UNIQUE = "time_dimension_default_unique"
    RELATIONSHIP_ENDPOINTS = "relationship_endpoints"
    PROJECT_ORGANIZATION = "project_organization"
    PROJECT_ROOT_VALID = "project_root_valid"
    METRIC_EXISTS = "metric_exists"
    ENTITY_EXISTS = "entity_exists"
    DIMENSION_EXISTS = "dimension_exists"
    SYMBOL_EXISTS = "symbol_exists"
    METRIC_ADDITIVITY_REQUIRED = "metric_additivity_required"
    MEASURE_ADDITIVITY_REQUIRED = "measure_additivity_required"
    MEASURE_AGGREGATION_VALID = "measure_aggregation_valid"
    LINEAR_UNIT_COMMENSURABLE = "linear_unit_commensurable"
    METRIC_ROOT_ENTITY_REQUIRED = "metric_root_entity_required"
    METRIC_ROOT_ENTITY_VALID = "metric_root_entity_valid"
    METRIC_VERIFICATION_MODE_VALID = "metric_verification_mode_valid"
    METRIC_ROOT_ONLY_AGGREGATE = "metric_root_only_aggregate"
    METRIC_FANOUT_POLICY_VALID = "metric_fanout_policy_valid"
    METRIC_FANOUT_POLICY_DERIVED = "metric_fanout_policy_derived"
    ENTITY_VERSIONING_VALID = "entity_versioning_valid"
    MATERIALIZE_EXECUTION = "materialize_execution"
    BACKEND_DIALECT_MATCH = "backend_dialect_match"
    COMPILE_EXPRESSION = "compile_expression"
    SINGLE_DATASOURCE_METRIC = "single_datasource_metric"
    PROVENANCE_DIALECT_REQUIRED = "provenance_dialect_required"
    PROVENANCE_VERIFIED = "provenance_verified"
    PARITY_VALUE_MATCH = "parity_value_match"
    PARITY_SCALAR_RESULT = "parity_scalar_result"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    BACKEND_FACTORY_AVAILABLE = "backend_factory_available"
    INSPECT_SOURCE_AVAILABLE = "inspect_source_available"
    PROJECT_LOADED_REQUIRED = "project_loaded_required"
    SAMPLE_INTERVAL_VALID = "sample_interval_valid"
    TIME_FOLD_VALID = "time_fold_valid"
    TIME_FOLD_SEMI_ADDITIVE = "time_fold_requires_semi_additive"
    TIME_FOLD_SAMPLED_TIME_FIELD = "time_fold_requires_sampled_time_field"
    TIME_FOLD_MISSING = "missing_time_fold"
    STATUS_TIME_DIMENSION_REQUIRED = "status_time_dimension_required"
    STATUS_TIME_DIMENSION_INVALID = "invalid_status_time_dimension"
    TIME_GRANULARITY_PARSE_COMPATIBLE = "time_granularity_parse_compatible"


_EXPR_BODY_AST_SPEC = ASTSpec(
    name="single_return_ibis_expression",
    single_return=True,
    forbidden_statements=(
        "Assign",
        "AugAssign",
        "AnnAssign",
        "Import",
        "ImportFrom",
        "For",
        "AsyncFor",
        "While",
        "If",
        "With",
        "AsyncWith",
        "Try",
        "TryStar",
        "FunctionDef",
        "AsyncFunctionDef",
        "ClassDef",
        "Delete",
        "Global",
        "Nonlocal",
        "Raise",
        "Assert",
        "Pass",
        "Break",
        "Continue",
        "Expr",
    ),
    forbidden_attributes=("sql", "raw_sql"),
    forbidden_calls=("ms.component",),
)


def _constraint(
    id: ConstraintId,
    error_kind: str,
    phase: Phase,
    applies_to: tuple[str, ...],
    title: str,
    why: str,
    hint: str,
    *,
    example: str | None = None,
    docs_ref: str | None = None,
    ast_spec: ASTSpec | None = None,
) -> Constraint:
    return Constraint(
        id=id.value,
        error_kind=error_kind,
        phase=phase,
        applies_to=applies_to,
        title=title,
        why=why,
        hint=hint,
        example=example,
        docs_ref=docs_ref,
        ast_spec=ast_spec,
    )


CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.ACTIVE_LOADER_CONTEXT: _constraint(
        ConstraintId.ACTIVE_LOADER_CONTEXT,
        "outside_loader_context",
        "decorator",
        (
            "domain",
            "entity",
            "dimension",
            "time_dimension",
            "metric",
            "derived_metric",
            "relationship",
        ),
        "Decorators require an active semantic loader context.",
        "Semantic declarations register into the project loader registry, not global process state.",
        "Put declarations under models/semantic/<model>/ and load them with ms.load().",
    ),
    ConstraintId.ACTIVE_DOMAIN_REQUIRED: _constraint(
        ConstraintId.ACTIVE_DOMAIN_REQUIRED,
        "missing_domain",
        "decorator",
        ("entity", "dimension", "time_dimension", "metric", "derived_metric", "relationship"),
        "Declarations need a domain namespace.",
        "Every semantic object is stored as <domain>.<name>.",
        "Call ms.domain(name=..., owner=...) in _domain.py or pass domain=... explicitly.",
    ),
    ConstraintId.UNIQUE_SEMANTIC_NAME: _constraint(
        ConstraintId.UNIQUE_SEMANTIC_NAME,
        "duplicate_name",
        "decorator",
        (
            "domain",
            "entity",
            "dimension",
            "time_dimension",
            "metric",
            "derived_metric",
            "relationship",
        ),
        "Names must be unique within their kind scope. Dimensions and time dimensions are scoped to their entity.",
        "Duplicate semantic ids within the same kind make registry lookups ambiguous. Dimensions are entity-scoped; entities and metrics are domain-scoped within their own kind.",
        "Rename one object, move it to a different entity (for dimensions), or use a different domain namespace.",
    ),
    ConstraintId.REF_SHAPE: _constraint(
        ConstraintId.REF_SHAPE,
        "invalid_ref",
        "decorator",
        ("entity", "dimension", "time_dimension", "metric", "relationship", "ref"),
        "References must be typed refs returned by Marivo authoring helpers.",
        "The loader persists semantic ids, not arbitrary Python objects.",
        'Use md.ref("datasource.warehouse") for datasource parameters and EntityRef/DimensionRef/MetricRef values returned by decorators.',
    ),
    ConstraintId.COMPOSITION_SHAPE: _constraint(
        ConstraintId.COMPOSITION_SHAPE,
        "invalid_composition",
        "decorator",
        ("metric", "derived_metric", "sum", "ratio", "weighted_average"),
        "Metrics need a supported composition builder.",
        "Composition declares how metric values compose during drilldown and derived calculations.",
        "Run ms.help('composition') to inspect supported builders; SQL aggregation belongs in the metric body.",
    ),
    ConstraintId.CUMULATIVE_ANCHOR: _constraint(
        ConstraintId.CUMULATIVE_ANCHOR,
        "invalid_ref",
        "decorator",
        ("derived_metric", "cumulative"),
        "Cumulative anchors must be valid reset grains or fixed-size trailing windows.",
        "The anchor selects the accumulation shape: all_history (default), grain_to_date (MTD/QTD/YTD resets), or trailing (rolling N).",
        "Pass anchor=ms.grain_to_date(grain='month'|'quarter'|'year'|'week'), "
        "anchor=ms.trailing(count=N, unit='day'|'hour'|...), or omit anchor for all-history.",
    ),
    ConstraintId.METRIC_ENTITIES_REQUIRED: _constraint(
        ConstraintId.METRIC_ENTITIES_REQUIRED,
        "missing_entities",
        "decorator",
        ("metric",),
        "Base metrics must declare at least one entity.",
        "Entity-backed metrics read source rows from their declared entity arguments.",
        "Simple metrics need entities=[...]; use ms.ratio/ms.weighted_average/ms.linear "
        "for metrics composed from other metrics.",
    ),
    ConstraintId.METRIC_COMPONENT_SCOPE: _constraint(
        ConstraintId.METRIC_COMPONENT_SCOPE,
        "invalid_component_body",
        "ast",
        ("metric",),
        "ms.component() is no longer supported in metric bodies.",
        "Derived metrics are body-free and declare composition through ms.ratio/ms.weighted_average/ms.linear.",
        "Remove ms.component() calls; use ms.ratio/ms.weighted_average/ms.linear with composition metadata instead.",
    ),
    ConstraintId.AI_CONTEXT_SCHEMA: _constraint(
        ConstraintId.AI_CONTEXT_SCHEMA,
        "invalid_ai_context",
        "decorator",
        ("domain", "entity", "dimension", "time_dimension", "metric", "relationship"),
        "ai_context must use the supported schema.",
        "Agent-facing metadata is persisted in a stable IR shape.",
        "Use business_definition, guardrails, synonyms, examples, instructions, and owner_notes.",
    ),
    ConstraintId.DOMAIN_OWNER_REQUIRED: _constraint(
        ConstraintId.DOMAIN_OWNER_REQUIRED,
        "invalid_domain_owner",
        "decorator",
        ("domain",),
        "Domains require a named human owner.",
        "Domain owners are accountable for semantic correctness and quality.",
        'Pass owner="Mina Zhang" to ms.domain(...).',
    ),
    ConstraintId.AST_SINGLE_RETURN: _constraint(
        ConstraintId.AST_SINGLE_RETURN,
        "metric_body_not_single_return",
        "ast",
        ("entity", "dimension", "time_dimension", "metric"),
        "Decorator function bodies must be a single return expression.",
        "The body is captured as a restricted expression DSL, not arbitrary Python.",
        "Inline the expression directly as return <ibis expression>.",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.AST_FORBIDDEN_STATEMENT: _constraint(
        ConstraintId.AST_FORBIDDEN_STATEMENT,
        "invalid_component_body",
        "ast",
        ("entity", "dimension", "time_dimension", "metric"),
        "Decorator bodies cannot contain statements, imports, assignments, lambdas, or nested definitions.",
        "Only deterministic expression bodies can be stored and recompiled safely.",
        "Keep the body to a single return expression. For a metric composed from "
        "other metrics, use the body-free constructors instead: "
        "ms.ratio(numerator=, denominator=), ms.linear(add=, subtract=), or "
        "ms.weighted_average(value=, weight=). For conditionals, use ibis "
        ".ifelse() / ibis.cases() inside the one return expression.",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.AST_SQL_ESCAPE_HATCH: _constraint(
        ConstraintId.AST_SQL_ESCAPE_HATCH,
        "sql_escape_hatch",
        "ast",
        ("entity", "dimension", "time_dimension", "metric"),
        "Raw SQL calls are not allowed in Python-track expression bodies.",
        "The Python semantic track stores ibis expressions; SQL text is provenance only.",
        "Use ibis expressions in the body and put the original SQL in provenance=ms.from_sql(...) on metrics.",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.AST_IBIS_ATTR_SHADOW: _constraint(
        ConstraintId.AST_IBIS_ATTR_SHADOW,
        "ibis_attr_shadow",
        "ast",
        ("entity", "dimension", "time_dimension", "metric"),
        "Attribute accesses on entity table parameters must not shadow ibis Table methods/properties.",
        "Dot notation (e.g. table.schema) resolves to the ibis Table method instead of the column when the name conflicts. The planner then fails with an unhelpful AttributeError.",
        'Use bracket notation for column names that conflict with ibis Table attributes: table["schema"] instead of table.schema.',
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.DOMAIN_FILE_PRESENT: _constraint(
        ConstraintId.DOMAIN_FILE_PRESENT,
        "domain_file_missing",
        "assembly",
        ("ms.load()",),
        "Each domain directory needs a _domain.py file that calls ms.domain().",
        "The loader uses _domain.py to establish the domain namespace.",
        'Create models/semantic/<domain>/_domain.py with ms.domain(name="<domain>", owner="Mina Zhang").',
    ),
    ConstraintId.DOMAIN_FILE_MATCHES_DIRECTORY: _constraint(
        ConstraintId.DOMAIN_FILE_MATCHES_DIRECTORY,
        "domain_file_mismatch",
        "assembly",
        ("domain",),
        "The domain name must match its directory.",
        "Directory names define stable domain namespaces on disk.",
        "Rename the directory or update ms.domain(name=...) so they match.",
    ),
    ConstraintId.ENTITY_REF_EXISTS: _constraint(
        ConstraintId.ENTITY_REF_EXISTS,
        "missing_entity_ref",
        "assembly",
        ("entity", "dimension", "time_dimension", "metric"),
        "Entity and datasource references must resolve.",
        "Semantic objects compile through registered datasource and entity ids.",
        "Reference a declared datasource name or EntityRef/qualified entity id.",
    ),
    ConstraintId.DIMENSION_REF_EXISTS: _constraint(
        ConstraintId.DIMENSION_REF_EXISTS,
        "missing_dimension_ref",
        "assembly",
        ("dimension", "time_dimension", "relationship"),
        "Dimension references must resolve.",
        "Relationships and time prefixes need registered dimension ids.",
        "Reference a declared DimensionRef/TimeDimensionRef or qualified dimension id.",
    ),
    ConstraintId.METRIC_REF_EXISTS: _constraint(
        ConstraintId.METRIC_REF_EXISTS,
        "missing_metric_ref",
        "assembly",
        ("metric",),
        "Metric component references must resolve.",
        "Derived metrics compose existing metrics.",
        "Reference a declared MetricRef or qualified metric id in decomposition components.",
    ),
    ConstraintId.METRIC_GRAPH_ACYCLIC: _constraint(
        ConstraintId.METRIC_GRAPH_ACYCLIC,
        "cross_model_cycle",
        "assembly",
        ("metric",),
        "Metric component graphs must be acyclic.",
        "Cycles cannot be compiled into a finite metric expression.",
        "Remove the circular component reference chain.",
    ),
    ConstraintId.TIME_DIMENSION_PARTITION_PUSHDOWN: _constraint(
        ConstraintId.TIME_DIMENSION_PARTITION_PUSHDOWN,
        "time_dimension_pushdown_advisory",
        "assembly",
        ("time_dimension",),
        "Partition time dimensions should preserve raw sortable encodings when possible.",
        "Raw day/hour partition comparisons are easier for SQL engines to push down than parsed or cast expressions.",
        "For day/hour partition columns such as dt, log_date, event_date, hh, or log_hour, prefer string or integer format with date_format and a bare column body; keep cast/parse expressions only when business time semantics require them.",
    ),
    ConstraintId.TIME_DIMENSION_DTYPE_COMPAT: _constraint(
        ConstraintId.TIME_DIMENSION_DTYPE_COMPAT,
        "time_dimension_dtype_advisory",
        "assembly",
        ("time_dimension",),
        "Time dimension data_type declarations must be compatible with the body expression's ibis dtype.",
        "A mismatch between declared data_type and the actual ibis expression dtype causes TypeError at execution.",
        "Ensure the .cast() target in the body matches the declared data_type: .cast('date') → data_type='date'; .cast('timestamp') or raw timestamp column → data_type='datetime' or 'timestamp'.",
    ),
    ConstraintId.TIME_DIMENSION_DEFAULT_UNIQUE: _constraint(
        ConstraintId.TIME_DIMENSION_DEFAULT_UNIQUE,
        "duplicate_default_time_dimension",
        "assembly",
        ("time_dimension",),
        "At most one time dimension per entity may carry is_default=True.",
        "Multiple default time dimensions create ambiguity at observe() time.",
        "Remove is_default=True from all but one time dimension on this entity.",
    ),
    ConstraintId.RELATIONSHIP_ENDPOINTS: _constraint(
        ConstraintId.RELATIONSHIP_ENDPOINTS,
        "invalid_relationship_endpoint",
        "assembly",
        ("relationship",),
        "Relationship endpoints must be registered entities.",
        "The compiler uses relationships to plan joins between known entities.",
        "Pass EntityRef values or qualified entity ids to from_entity and to_entity.",
    ),
    ConstraintId.PROJECT_ORGANIZATION: _constraint(
        ConstraintId.PROJECT_ORGANIZATION,
        "organization_error",
        "assembly",
        ("project",),
        "Project files must follow the semantic project layout.",
        "The loader imports known files and accumulates structured semantic declarations.",
        "Check models/semantic/<model>/ files for syntax, import, or organization issues.",
    ),
    ConstraintId.PROJECT_ROOT_VALID: _constraint(
        ConstraintId.PROJECT_ROOT_VALID,
        "invalid_project",
        "assembly",
        ("project",),
        "The project root must contain models/semantic/.",
        "ms.load() needs a concrete semantic root to load declarations.",
        "Point --project at the project root, not the semantic directory itself.",
    ),
    ConstraintId.METRIC_ADDITIVITY_REQUIRED: _constraint(
        ConstraintId.METRIC_ADDITIVITY_REQUIRED,
        "missing_metric_additivity",
        "assembly",
        ("metric",),
        "Base metrics must declare additivity.",
        "Additivity determines how metric values aggregate across dataset rows.",
        "Set additivity to 'additive', 'semi_additive', or 'non_additive' on @ms.metric().",
    ),
    ConstraintId.MEASURE_ADDITIVITY_REQUIRED: _constraint(
        ConstraintId.MEASURE_ADDITIVITY_REQUIRED,
        "missing_measure_additivity",
        "assembly",
        ("metric",),
        "A measure used by a tier-1 metric must declare additivity.",
        "Add additivity= to the @ms.measure(...) declaration.",
        "Set additivity to 'additive', 'semi_additive', or 'non_additive'.",
    ),
    ConstraintId.MEASURE_AGGREGATION_VALID: _constraint(
        ConstraintId.MEASURE_AGGREGATION_VALID,
        "invalid_measure_aggregation",
        "assembly",
        ("metric",),
        "sum/count aggregations are invalid on non-additive measures.",
        "Use mean/min/max, or model the metric as a ratio/derived composition.",
        "Change the aggregation or the measure dimension additivity.",
    ),
    ConstraintId.LINEAR_UNIT_COMMENSURABLE: _constraint(
        ConstraintId.LINEAR_UNIT_COMMENSURABLE,
        "incommensurable_linear_units",
        "assembly",
        ("metric",),
        "Linear metric terms must share one unit; differing units cannot be added.",
        "Addition is only defined on commensurable quantities (CNY + {order} is undefined).",
        "Align the component units, or remodel as a ratio/derived composition.",
    ),
    ConstraintId.METRIC_ROOT_ENTITY_REQUIRED: _constraint(
        ConstraintId.METRIC_ROOT_ENTITY_REQUIRED,
        "missing_metric_root_entity",
        "decorator",
        ("metric",),
        "@ms.metric(...) with more than one entity requires root_entity=...",
        "The root entity determines join order and grain for cross-entity metrics.",
        "Pass root_entity=<EntityRef> when a metric references more than one entity.",
    ),
    ConstraintId.METRIC_ROOT_ENTITY_VALID: _constraint(
        ConstraintId.METRIC_ROOT_ENTITY_VALID,
        "invalid_metric_root_entity",
        "assembly",
        ("metric",),
        "root_entity must be one of the metric's entities.",
        "The root entity anchors the metric's aggregation grain.",
        "Use an EntityRef from the metric's entities list as root_entity.",
    ),
    ConstraintId.METRIC_VERIFICATION_MODE_VALID: _constraint(
        ConstraintId.METRIC_VERIFICATION_MODE_VALID,
        "invalid_verification_mode",
        "assembly",
        ("metric",),
        "Metric provenance must be consistent.",
        "provenance enables SQL parity verification; derived metrics must omit provenance.",
        "Base metrics: use provenance=ms.from_sql(sql=..., dialect=...). Derived metrics: remove provenance.",
    ),
    ConstraintId.METRIC_ROOT_ONLY_AGGREGATE: _constraint(
        ConstraintId.METRIC_ROOT_ONLY_AGGREGATE,
        "non_root_metric_aggregate",
        "assembly",
        ("metric",),
        "Base metrics must aggregate only on the root dataset.",
        "Aggregating a non-root dataset changes the grain and may produce incorrect results.",
        "Ensure aggregate calls (.sum(), .mean(), etc.) only chain from the root dataset parameter.",
    ),
    ConstraintId.METRIC_FANOUT_POLICY_VALID: _constraint(
        ConstraintId.METRIC_FANOUT_POLICY_VALID,
        "invalid_metric_fanout_policy",
        "assembly",
        ("metric",),
        "fanout_policy must be 'block' or 'aggregate_then_join', authored on base metrics only.",
        "Fan-out is a metric-level decision, gated by measure additivity on the merge grain.",
        "Set fanout_policy='aggregate_then_join' only on additive/semi_additive base metrics; derived metrics must keep the default.",
    ),
    ConstraintId.METRIC_FANOUT_POLICY_DERIVED: _constraint(
        ConstraintId.METRIC_FANOUT_POLICY_DERIVED,
        "derived_metric_fanout_policy",
        "assembly",
        ("metric",),
        "Derived metrics must keep fanout_policy='block'.",
        "Derived metrics inherit fan-out behavior from their component metrics, which each declare their own policy.",
        "Derived metrics (ms.ratio/ms.weighted_average/ms.linear) must not declare fanout_policy; "
        "set fanout_policy on the relevant base components instead.",
    ),
    ConstraintId.ENTITY_VERSIONING_VALID: _constraint(
        ConstraintId.ENTITY_VERSIONING_VALID,
        "invalid_entity_versioning",
        "assembly",
        ("entity",),
        "Snapshot versioning partition field must be part of primary_key.",
        "The partition field determines which rows are used for latest snapshot joins.",
        "Add the partition column to the entity's primary_key list.",
    ),
    ConstraintId.METRIC_EXISTS: _constraint(
        ConstraintId.METRIC_EXISTS,
        "metric_not_found",
        "runtime",
        ("metric", "SemanticCatalog"),
        "Requested metrics must exist in the loaded project.",
        "Runtime operations compile registered metric ids.",
        "catalog = ms.load(); catalog.metrics.show() and use catalog.get('metric.<semantic_id>').",
    ),
    ConstraintId.ENTITY_EXISTS: _constraint(
        ConstraintId.ENTITY_EXISTS,
        "entity_not_found",
        "runtime",
        ("entity", "SemanticCatalog"),
        "Requested entities must exist in the loaded project.",
        "Runtime operations look up registered entity ids.",
        "catalog.entities.show() and use catalog.get('entity.<semantic_id>').",
    ),
    ConstraintId.DIMENSION_EXISTS: _constraint(
        ConstraintId.DIMENSION_EXISTS,
        "dimension_not_found",
        "runtime",
        ("dimension", "SemanticCatalog"),
        "Requested dimensions must exist in the loaded project.",
        "Runtime operations look up registered dimension ids.",
        "catalog.dimensions.show() and use catalog.get('dimension.<semantic_id>').",
    ),
    ConstraintId.SYMBOL_EXISTS: _constraint(
        ConstraintId.SYMBOL_EXISTS,
        "not_found",
        "runtime",
        ("SemanticCatalog",),
        "Requested semantic objects must exist in the loaded project.",
        "Lookup methods search across all registered symbol kinds.",
        "catalog.domains.show() and catalog.datasources.show() for available names.",
    ),
    ConstraintId.MATERIALIZE_EXECUTION: _constraint(
        ConstraintId.MATERIALIZE_EXECUTION,
        "materialize_failed",
        "runtime",
        ("metric", "SemanticProject"),
        "Metric materialization must compile and execute successfully.",
        "Materialization evaluates the stored ibis expression against a backend.",
        "Check metric bodies, dataset references, and backend_factory wiring.",
    ),
    ConstraintId.BACKEND_DIALECT_MATCH: _constraint(
        ConstraintId.BACKEND_DIALECT_MATCH,
        "backend_mismatch",
        "runtime",
        ("datasource", "metric"),
        "Backend dialects must match datasource declarations.",
        "The compiler relies on datasource backend_type for compatible execution.",
        "Use a backend_factory that returns the declared datasource backend.",
    ),
    ConstraintId.COMPILE_EXPRESSION: _constraint(
        ConstraintId.COMPILE_EXPRESSION,
        "compile_error",
        "runtime",
        ("metric", "SemanticProject"),
        "Metric expressions must compile to backend SQL.",
        "Unsupported ibis expressions cannot be materialized.",
        "Simplify the metric expression or use supported ibis operations.",
    ),
    ConstraintId.SINGLE_DATASOURCE_METRIC: _constraint(
        ConstraintId.SINGLE_DATASOURCE_METRIC,
        "cross_datasource_not_supported",
        "runtime",
        ("metric",),
        "A metric can only span one datasource.",
        "Cross-datasource metric execution has no single backend to compile against.",
        "Keep component datasets on one datasource or model the integration upstream.",
    ),
    ConstraintId.PROVENANCE_DIALECT_REQUIRED: _constraint(
        ConstraintId.PROVENANCE_DIALECT_REQUIRED,
        "provenance_dialect_missing",
        "parity",
        ("metric",),
        "Metric provenance SQL requires a dialect.",
        "The parity engine compares Python metric output with the original SQL.",
        "Add provenance=ms.from_sql(sql=..., dialect=...) to the metric decorator.",
    ),
    ConstraintId.PROVENANCE_VERIFIED: _constraint(
        ConstraintId.PROVENANCE_VERIFIED,
        "unverified_provenance",
        "parity",
        ("metric",),
        "Source SQL provenance should be parity checked.",
        "Agents need to know whether Python semantics match the original SQL definition.",
        "Run project.parity_check(...) or semantic check --parity.",
    ),
    ConstraintId.PARITY_VALUE_MATCH: _constraint(
        ConstraintId.PARITY_VALUE_MATCH,
        "parity_value_mismatch",
        "parity",
        ("metric",),
        "Parity expected and actual values must match.",
        "A mismatch means the Python metric has drifted from source SQL semantics.",
        "Compare the compiled metric expression with provenance SQL and update the metric body.",
    ),
    ConstraintId.PARITY_SCALAR_RESULT: _constraint(
        ConstraintId.PARITY_SCALAR_RESULT,
        "parity_not_scalar",
        "parity",
        ("metric",),
        "Parity SQL must return exactly one scalar result.",
        "Scalar parity compares one metric value to one source SQL value.",
        "Adjust provenance SQL so it returns one row and one column.",
    ),
    ConstraintId.AMBIGUOUS_REFERENCE: _constraint(
        ConstraintId.AMBIGUOUS_REFERENCE,
        "ambiguous_reference",
        "runtime",
        ("entity", "dimension", "time_dimension", "metric", "relationship"),
        "Unqualified name lookups must resolve to a single object kind.",
        "Cross-kind name matches make registry lookups ambiguous.",
        'Use catalog.<collection>.get("<typed_id>") to retrieve a specific object, or browse via catalog.domains, catalog.metrics, etc.',
    ),
    ConstraintId.BACKEND_FACTORY_AVAILABLE: _constraint(
        ConstraintId.BACKEND_FACTORY_AVAILABLE,
        "backend_factory_required",
        "runtime",
        ("SemanticProject",),
        "Runtime preview requires a configured datasource.",
        "Datasource backends are resolved internally via DatasourceConnectionService.",
        "Ensure datasources are configured under models/datasources/ before calling catalog.preview(...).",
    ),
    ConstraintId.INSPECT_SOURCE_AVAILABLE: _constraint(
        ConstraintId.INSPECT_SOURCE_AVAILABLE,
        "inspect_source_required",
        "runtime",
        ("SemanticProject",),
        "Source inspection methods require a configured datasource.",
        "inspect_source maps datasource+source to TableMetadata for schema discovery.",
        "Ensure datasources are configured under models/datasources/ before calling source inspection methods.",
    ),
    ConstraintId.PROJECT_LOADED_REQUIRED: _constraint(
        ConstraintId.PROJECT_LOADED_REQUIRED,
        "project_not_loaded",
        "runtime",
        ("SemanticProject",),
        "Project must be loaded before accessing semantic objects.",
        "Listing and lookup methods require a loaded registry.",
        "Call ms.load() to load the semantic project, then access metrics, entities, or dimensions.",
    ),
    ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE: _constraint(
        ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        "invalid_ref",
        "decorator",
        ("time_dimension",),
        "Time granularity must match its parse variant.",
        "Parse variants make most time combinations unconstructable; the remaining rule is granularity compatibility.",
        "Use ms.datetime(...) or ms.timestamp(...) for minute/second grains, and use granularity='hour' with ms.hour_prefix(...).",
    ),
    ConstraintId.SAMPLE_INTERVAL_VALID: _constraint(
        ConstraintId.SAMPLE_INTERVAL_VALID,
        "invalid_sample_interval",
        "decorator",
        ("time_dimension",),
        "sample_interval must be a positive minute or hour interval that divides one day evenly.",
        "Sampled time dimensions represent periodic measurements within a day; the interval must evenly divide 24 hours.",
        "Use sample_interval=(5, 'minute') or another minute/hour interval that divides a day.",
    ),
    ConstraintId.TIME_FOLD_VALID: _constraint(
        ConstraintId.TIME_FOLD_VALID,
        "invalid_time_fold",
        "decorator",
        ("metric",),
        "time_fold must be a supported fold kind with valid parameters.",
        "Fold kinds define how sampled time series are compressed into a single representative value.",
        "Use time_fold='mean', 'min', 'max', 'first', 'last', or ('percentile', q) with 0 < q < 1.",
    ),
    ConstraintId.TIME_FOLD_SEMI_ADDITIVE: _constraint(
        ConstraintId.TIME_FOLD_SEMI_ADDITIVE,
        "time_fold_requires_semi_additive",
        "assembly",
        ("metric",),
        "time_fold is only valid on semi_additive metrics.",
        "Additive metrics sum unconditionally; non-additive metrics cannot be folded.",
        "Set additivity='semi_additive' when using time_fold, or remove time_fold from additive/non_additive metrics.",
    ),
    ConstraintId.TIME_FOLD_SAMPLED_TIME_FIELD: _constraint(
        ConstraintId.TIME_FOLD_SAMPLED_TIME_FIELD,
        "time_fold_requires_sampled_time_field",
        "assembly",
        ("metric",),
        "time_fold requires status_time_dimension to reference a sampled time dimension.",
        "The fold operation compresses sampled status points, so the bound status axis must declare sample_interval.",
        "Set status_time_dimension to a root entity time dimension with sample_interval, or remove time_fold for non-sampled status metrics.",
    ),
    ConstraintId.TIME_FOLD_MISSING: _constraint(
        ConstraintId.TIME_FOLD_MISSING,
        "missing_time_fold",
        "assembly",
        ("metric",),
        "Semi-additive metrics on sampled entities must declare a time_fold.",
        "Without a fold, sampled semi-additive metrics would double-count intra-day observations.",
        "Add time_fold='mean' (or another fold kind) to the metric declaration.",
    ),
    ConstraintId.STATUS_TIME_DIMENSION_REQUIRED: _constraint(
        ConstraintId.STATUS_TIME_DIMENSION_REQUIRED,
        "missing_status_time_dimension",
        "assembly",
        ("metric",),
        "Semi-additive metrics must declare status_time_dimension.",
        "The status time dimension is the business as-of axis that the metric cannot be summed across directly. sampled metrics additionally declare time_fold.",
        "Set status_time_dimension to the root entity time dimension that represents the metric's business status/as-of time.",
    ),
    ConstraintId.STATUS_TIME_DIMENSION_INVALID: _constraint(
        ConstraintId.STATUS_TIME_DIMENSION_INVALID,
        "invalid_status_time_dimension",
        "assembly",
        ("metric",),
        "status_time_dimension must reference a time dimension on the metric root entity.",
        "A semi-additive metric's status axis must be a declared root entity time dimension; time_fold additionally requires that axis to be sampled.",
        "Use a root entity @ms.time_dimension(...) ref as status_time_dimension.",
    ),
}

_DEFAULT_BY_ERROR_KIND: dict[str, str] = {}
for _constraint_obj in CONSTRAINTS.values():
    _DEFAULT_BY_ERROR_KIND.setdefault(_constraint_obj.error_kind, _constraint_obj.id)


def get_constraint(id: ConstraintId | str) -> Constraint | None:
    """Return a constraint by id."""

    try:
        constraint_id = id if isinstance(id, ConstraintId) else ConstraintId(id)
    except ValueError:
        return None
    return CONSTRAINTS.get(constraint_id)


def iter_constraints() -> tuple[Constraint, ...]:
    """Return all constraints in declaration order."""

    return tuple(CONSTRAINTS.values())


def constraints_for_symbol(symbol: str) -> tuple[Constraint, ...]:
    """Return constraints whose applies_to includes *symbol*."""

    return tuple(c for c in CONSTRAINTS.values() if symbol in c.applies_to)


def constraints_for_error_kind(error_kind: str) -> tuple[Constraint, ...]:
    """Return constraints that map to an ErrorKind value."""

    return tuple(c for c in CONSTRAINTS.values() if c.error_kind == error_kind)


def default_constraint_for_error_kind(error_kind: str) -> Constraint | None:
    """Return the default constraint for an ErrorKind value."""

    constraint_id = _DEFAULT_BY_ERROR_KIND.get(error_kind)
    if constraint_id is None:
        return None
    return get_constraint(constraint_id)


def default_hint_for_error_kind(error_kind: str) -> str | None:
    """Return the catalog-backed default hint for an ErrorKind value."""

    constraint = default_constraint_for_error_kind(error_kind)
    return constraint.hint if constraint is not None else None
