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
    ACTIVE_MODEL_REQUIRED = "active_model_required"
    UNIQUE_SEMANTIC_NAME = "unique_semantic_name"
    REF_SHAPE = "ref_shape"
    DECOMPOSITION_SHAPE = "decomposition_shape"
    METRIC_DATASETS_REQUIRED = "metric_datasets_required"
    METRIC_COMPONENT_SCOPE = "metric_component_scope"
    AI_CONTEXT_SCHEMA = "ai_context_schema"
    AST_SINGLE_RETURN = "ast_single_return"
    AST_FORBIDDEN_STATEMENT = "ast_forbidden_statement"
    AST_SQL_ESCAPE_HATCH = "ast_sql_escape_hatch"
    MODEL_FILE_PRESENT = "model_file_present"
    MODEL_FILE_MATCHES_DIRECTORY = "model_file_matches_directory"
    DATASET_REF_EXISTS = "dataset_ref_exists"
    FIELD_REF_EXISTS = "field_ref_exists"
    METRIC_REF_EXISTS = "metric_ref_exists"
    METRIC_GRAPH_ACYCLIC = "metric_graph_acyclic"
    HOUR_TIME_FIELD_PREFIX = "hour_time_field_prefix"
    SUBDAY_GRANULARITY_WITHOUT_TIME = "subday_granularity_without_time"
    TIME_FIELD_PARTITION_PUSHDOWN = "time_field_partition_pushdown"
    TIME_FIELD_DTYPE_COMPAT = "time_field_dtype_compat"
    TIME_FIELD_DEFAULT_UNIQUE = "time_field_default_unique"
    RELATIONSHIP_ENDPOINTS = "relationship_endpoints"
    PROJECT_ORGANIZATION = "project_organization"
    PROJECT_ROOT_VALID = "project_root_valid"
    METRIC_EXISTS = "metric_exists"
    METRIC_ADDITIVITY_REQUIRED = "metric_additivity_required"
    METRIC_ROOT_DATASET_REQUIRED = "metric_root_dataset_required"
    METRIC_ROOT_DATASET_VALID = "metric_root_dataset_valid"
    METRIC_VERIFICATION_MODE_VALID = "metric_verification_mode_valid"
    METRIC_ROOT_ONLY_AGGREGATE = "metric_root_only_aggregate"
    METRIC_FANOUT_POLICY_VALID = "metric_fanout_policy_valid"
    METRIC_FANOUT_POLICY_DERIVED = "metric_fanout_policy_derived"
    DATASET_VERSIONING_VALID = "dataset_versioning_valid"
    MATERIALIZE_EXECUTION = "materialize_execution"
    BACKEND_DIALECT_MATCH = "backend_dialect_match"
    COMPILE_EXPRESSION = "compile_expression"
    SINGLE_DATASOURCE_METRIC = "single_datasource_metric"
    SOURCE_SQL_REQUIRED = "source_sql_required"
    PROVENANCE_VERIFIED = "provenance_verified"
    PARITY_VALUE_MATCH = "parity_value_match"
    PARITY_SCALAR_RESULT = "parity_scalar_result"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    BACKEND_FACTORY_AVAILABLE = "backend_factory_available"


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


_EXAMPLE_BASE = "marivo-skills/marivo-semantic/references/examples"

CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.ACTIVE_LOADER_CONTEXT: _constraint(
        ConstraintId.ACTIVE_LOADER_CONTEXT,
        "outside_loader_context",
        "decorator",
        (
            "model",
            "dataset",
            "field",
            "time_field",
            "metric",
            "derived_metric",
            "relationship",
        ),
        "Decorators require an active semantic loader context.",
        "Semantic declarations register into the project loader registry, not global process state.",
        "Put declarations under .marivo/semantic/<model>/ and load them with SemanticProject.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.ACTIVE_MODEL_REQUIRED: _constraint(
        ConstraintId.ACTIVE_MODEL_REQUIRED,
        "missing_model",
        "decorator",
        ("dataset", "field", "time_field", "metric", "derived_metric", "relationship"),
        "Declarations need a model namespace.",
        "Every semantic object is stored as <model>.<name>.",
        "Call ms.model(name=...) in _model.py or pass model_name=... explicitly.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.UNIQUE_SEMANTIC_NAME: _constraint(
        ConstraintId.UNIQUE_SEMANTIC_NAME,
        "duplicate_name",
        "decorator",
        ("model", "dataset", "field", "time_field", "metric", "derived_metric", "relationship"),
        "Names must be unique within their kind scope. Fields and time fields are scoped to their dataset.",
        "Duplicate semantic ids within the same kind make registry lookups ambiguous. Fields are dataset-scoped; datasets and metrics are model-scoped within their own kind.",
        "Rename one object, move it to a different dataset (for fields), or use a different model namespace.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.REF_SHAPE: _constraint(
        ConstraintId.REF_SHAPE,
        "invalid_ref",
        "decorator",
        ("dataset", "field", "time_field", "metric", "relationship", "ref"),
        "References must be strings or decorator-returned refs.",
        "The loader persists semantic ids, not arbitrary Python objects.",
        "Use datasource names as strings and DatasetRef/FieldRef/MetricRef values returned by decorators.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.DECOMPOSITION_SHAPE: _constraint(
        ConstraintId.DECOMPOSITION_SHAPE,
        "invalid_decomposition",
        "decorator",
        ("metric", "derived_metric", "sum", "ratio", "weighted_average"),
        "Metrics need a supported decomposition builder.",
        "Decomposition declares how metric values compose during drilldown and derived calculations.",
        "Run ms.help('decomposition', format='json') to inspect supported builders; SQL aggregation belongs in the metric body.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.METRIC_DATASETS_REQUIRED: _constraint(
        ConstraintId.METRIC_DATASETS_REQUIRED,
        "missing_datasets",
        "decorator",
        ("metric",),
        "Base metrics must declare at least one dataset.",
        "Dataset-backed metrics read source rows from their declared dataset arguments.",
        "Base metrics need datasets=[...]; use ms.derived_metric(...) for metrics composed from other metrics.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.METRIC_COMPONENT_SCOPE: _constraint(
        ConstraintId.METRIC_COMPONENT_SCOPE,
        "invalid_component_body",
        "ast",
        ("metric",),
        "ms.component() is no longer supported in metric bodies.",
        "Derived metrics are body-free and declare composition through ms.derived_metric(...).",
        "Remove ms.component() calls; use ms.derived_metric(...) with decomposition metadata instead.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.AI_CONTEXT_SCHEMA: _constraint(
        ConstraintId.AI_CONTEXT_SCHEMA,
        "invalid_ai_context",
        "decorator",
        ("model", "dataset", "field", "time_field", "metric", "relationship"),
        "ai_context must use the supported schema.",
        "Agent-facing metadata is persisted in a stable IR shape.",
        "Use business_definition, guardrails, synonyms, examples, instructions, and owner_notes.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.AST_SINGLE_RETURN: _constraint(
        ConstraintId.AST_SINGLE_RETURN,
        "metric_body_not_single_return",
        "ast",
        ("dataset", "field", "time_field", "metric"),
        "Decorator function bodies must be a single return expression.",
        "The body is captured as a restricted expression DSL, not arbitrary Python.",
        "Inline the expression directly as return <ibis expression>.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.AST_FORBIDDEN_STATEMENT: _constraint(
        ConstraintId.AST_FORBIDDEN_STATEMENT,
        "invalid_component_body",
        "ast",
        ("dataset", "field", "time_field", "metric"),
        "Decorator bodies cannot contain statements, imports, assignments, lambdas, or nested definitions.",
        "Only deterministic expression bodies can be stored and recompiled safely.",
        "Move setup outside the decorator body and keep the body to one return expression.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.AST_SQL_ESCAPE_HATCH: _constraint(
        ConstraintId.AST_SQL_ESCAPE_HATCH,
        "sql_escape_hatch",
        "ast",
        ("dataset", "field", "time_field", "metric"),
        "Raw SQL calls are not allowed in Python-track expression bodies.",
        "The Python semantic track stores ibis expressions; SQL text is provenance only.",
        "Use ibis expressions in the body and put the original SQL in source_sql= on metrics.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
        ast_spec=_EXPR_BODY_AST_SPEC,
    ),
    ConstraintId.MODEL_FILE_PRESENT: _constraint(
        ConstraintId.MODEL_FILE_PRESENT,
        "model_file_missing",
        "assembly",
        ("model",),
        "Each model directory needs a _model.py file that calls ms.model().",
        "The loader uses _model.py to establish the model namespace.",
        "Create .marivo/semantic/<model>/_model.py with ms.model(name='<model>').",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.MODEL_FILE_MATCHES_DIRECTORY: _constraint(
        ConstraintId.MODEL_FILE_MATCHES_DIRECTORY,
        "model_file_mismatch",
        "assembly",
        ("model",),
        "The model name must match its directory.",
        "Directory names define stable model namespaces on disk.",
        "Rename the directory or update ms.model(name=...) so they match.",
    ),
    ConstraintId.DATASET_REF_EXISTS: _constraint(
        ConstraintId.DATASET_REF_EXISTS,
        "missing_dataset_ref",
        "assembly",
        ("dataset", "field", "time_field", "metric"),
        "Dataset and datasource references must resolve.",
        "Semantic objects compile through registered datasource and dataset ids.",
        "Reference a declared datasource name or DatasetRef/qualified dataset id.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
    ),
    ConstraintId.FIELD_REF_EXISTS: _constraint(
        ConstraintId.FIELD_REF_EXISTS,
        "missing_field_ref",
        "assembly",
        ("field", "time_field", "relationship"),
        "Field references must resolve.",
        "Relationships and time prefixes need registered field ids.",
        "Reference a declared FieldRef/TimeFieldRef or qualified field id.",
    ),
    ConstraintId.METRIC_REF_EXISTS: _constraint(
        ConstraintId.METRIC_REF_EXISTS,
        "missing_metric_ref",
        "assembly",
        ("metric",),
        "Metric component references must resolve.",
        "Derived metrics compose existing metrics.",
        "Reference a declared MetricRef or qualified metric id in decomposition components.",
        example=f"{_EXAMPLE_BASE}/01_single_model_file.py",
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
    ConstraintId.HOUR_TIME_FIELD_PREFIX: _constraint(
        ConstraintId.HOUR_TIME_FIELD_PREFIX,
        "hour_time_field_prefix_missing",
        "assembly",
        ("time_field",),
        "Hour-only string/integer time fields need a day-level required_prefix.",
        "A standalone hour value is not a complete time axis.",
        "Set required_prefix to a registered day-level time field.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.SUBDAY_GRANULARITY_WITHOUT_TIME: _constraint(
        ConstraintId.SUBDAY_GRANULARITY_WITHOUT_TIME,
        "subday_granularity_without_time",
        "assembly",
        ("time_field",),
        "Sub-day granularity requires a time-bearing data_type.",
        "A time field declaring hour/minute/second granularity on data_type='date' cannot carry time-of-day information; string/integer formats that lack hour/minute/second tokens likewise cannot.",
        "Use data_type='datetime'/'timestamp', or a time-bearing string/integer format like yyyymmddhhmm.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.TIME_FIELD_PARTITION_PUSHDOWN: _constraint(
        ConstraintId.TIME_FIELD_PARTITION_PUSHDOWN,
        "time_field_pushdown_advisory",
        "assembly",
        ("time_field",),
        "Partition time fields should preserve raw sortable encodings when possible.",
        "Raw day/hour partition comparisons are easier for SQL engines to push down than parsed or cast expressions.",
        "For day/hour partition columns such as dt, log_date, event_date, hh, or log_hour, prefer data_type='string' or 'integer' with date_format and a bare column body; keep cast/parse expressions only when business time semantics require them.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.TIME_FIELD_DTYPE_COMPAT: _constraint(
        ConstraintId.TIME_FIELD_DTYPE_COMPAT,
        "time_field_dtype_advisory",
        "assembly",
        ("time_field",),
        "Time field data_type declarations must be compatible with the body expression's ibis dtype.",
        "A mismatch between declared data_type and the actual ibis expression dtype causes TypeError at execution.",
        "Ensure the .cast() target in the body matches the declared data_type: .cast('date') → data_type='date'; .cast('timestamp') or raw timestamp column → data_type='datetime' or 'timestamp'.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.TIME_FIELD_DEFAULT_UNIQUE: _constraint(
        ConstraintId.TIME_FIELD_DEFAULT_UNIQUE,
        "duplicate_default_time_field",
        "assembly",
        ("time_field",),
        "At most one time field per dataset may carry is_default=True.",
        "Multiple default time fields create ambiguity at observe() time.",
        "Remove is_default=True from all but one time field on this dataset.",
    ),
    ConstraintId.RELATIONSHIP_ENDPOINTS: _constraint(
        ConstraintId.RELATIONSHIP_ENDPOINTS,
        "invalid_relationship_endpoint",
        "assembly",
        ("relationship",),
        "Relationship endpoints must be registered datasets.",
        "The compiler uses relationships to plan joins between known datasets.",
        "Pass DatasetRef values or qualified dataset ids to from_dataset and to_dataset.",
    ),
    ConstraintId.PROJECT_ORGANIZATION: _constraint(
        ConstraintId.PROJECT_ORGANIZATION,
        "organization_error",
        "assembly",
        ("project",),
        "Project files must follow the semantic project layout.",
        "The loader imports known files and accumulates structured semantic declarations.",
        "Check .marivo/semantic/<model>/ files for syntax, import, or organization issues.",
    ),
    ConstraintId.PROJECT_ROOT_VALID: _constraint(
        ConstraintId.PROJECT_ROOT_VALID,
        "invalid_project",
        "assembly",
        ("project",),
        "The project root must contain .marivo/semantic/.",
        "SemanticProject needs a concrete semantic root to load declarations.",
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
    ConstraintId.METRIC_ROOT_DATASET_REQUIRED: _constraint(
        ConstraintId.METRIC_ROOT_DATASET_REQUIRED,
        "missing_metric_root_dataset",
        "assembly",
        ("metric",),
        "Multi-dataset base metrics must declare root_dataset.",
        "The root dataset determines join order and grain for cross-dataset metrics.",
        "Pass root_dataset=<DatasetRef> when a metric references more than one dataset.",
    ),
    ConstraintId.METRIC_ROOT_DATASET_VALID: _constraint(
        ConstraintId.METRIC_ROOT_DATASET_VALID,
        "invalid_metric_root_dataset",
        "assembly",
        ("metric",),
        "root_dataset must be one of the metric's datasets.",
        "The root dataset anchors the metric's aggregation grain.",
        "Use a DatasetRef from the metric's datasets list as root_dataset.",
    ),
    ConstraintId.METRIC_VERIFICATION_MODE_VALID: _constraint(
        ConstraintId.METRIC_VERIFICATION_MODE_VALID,
        "invalid_verification_mode",
        "assembly",
        ("metric",),
        "Base metrics must declare a valid verification mode.",
        "verification_mode separates author intent from computed verification status.",
        "Use verification_mode='sql_parity' with source_sql/source_dialect, or verification_mode='python_native' without SQL provenance.",
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
        "Use ms.derived_metric(...) without fanout_policy and set fanout_policy on the relevant base components instead.",
    ),
    ConstraintId.DATASET_VERSIONING_VALID: _constraint(
        ConstraintId.DATASET_VERSIONING_VALID,
        "invalid_dataset_versioning",
        "assembly",
        ("dataset",),
        "Snapshot versioning partition field must be part of primary_key.",
        "The partition field determines which rows are used for latest snapshot joins.",
        "Add the partition column to the dataset's primary_key list.",
    ),
    ConstraintId.METRIC_EXISTS: _constraint(
        ConstraintId.METRIC_EXISTS,
        "metric_not_found",
        "runtime",
        ("metric", "SemanticProject"),
        "Requested metrics must exist in the loaded project.",
        "Runtime operations compile registered metric ids.",
        "Check project.list_metrics() and use the metric semantic_id.",
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
    ConstraintId.SOURCE_SQL_REQUIRED: _constraint(
        ConstraintId.SOURCE_SQL_REQUIRED,
        "source_sql_missing",
        "parity",
        ("metric",),
        "Parity checks require metric source_sql provenance.",
        "The parity engine compares Python metric output with the original SQL.",
        "Add source_sql=... and source_dialect=... to the metric decorator.",
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
        "Compare the compiled metric expression with source_sql and update the metric body.",
    ),
    ConstraintId.PARITY_SCALAR_RESULT: _constraint(
        ConstraintId.PARITY_SCALAR_RESULT,
        "parity_not_scalar",
        "parity",
        ("metric",),
        "Parity SQL must return exactly one scalar result.",
        "Scalar parity compares one metric value to one source SQL value.",
        "Adjust source_sql so it returns one row and one column.",
    ),
    ConstraintId.AMBIGUOUS_REFERENCE: _constraint(
        ConstraintId.AMBIGUOUS_REFERENCE,
        "ambiguous_reference",
        "runtime",
        ("dataset", "field", "time_field", "metric", "relationship"),
        "Unqualified name lookups must resolve to a single object kind.",
        "Cross-kind name matches make registry lookups ambiguous.",
        "Pass kind= to describe() or search(kind=...) to disambiguate.",
        docs_ref="marivo-skills/marivo-semantic/references/authoring-patterns.md",
    ),
    ConstraintId.BACKEND_FACTORY_AVAILABLE: _constraint(
        ConstraintId.BACKEND_FACTORY_AVAILABLE,
        "backend_factory_required",
        "runtime",
        ("SemanticProject",),
        "Materialization and preview methods require a backend_factory.",
        "A backend_factory maps datasource names to ibis backends for data access.",
        "Call project.bind_backend_factory(...) after load() or pass backend_factory=... explicitly.",
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
