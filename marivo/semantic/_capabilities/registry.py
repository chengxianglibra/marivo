"""Closed registry and consumed-type catalog for ``marivo.semantic``."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringInputRole,
    ConnectionEffect,
    DataAccessEffect,
    EffectFlag,
    MutationEffect,
    RepairKind,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import (
    CATALOG_COLLECTION_PROPERTIES,
    CATALOG_MEMBER_CONTRACTS,
)
from marivo.semantic._capabilities.model import (
    AuthoringSourceContract,
    SemanticCapabilityRegistry,
    SemanticRepairContract,
    SemanticRootGroup,
    SemanticTypeContract,
)

INPUT_FAMILIES = frozenset(
    {
        "Ref",
        "Ref[domain]",
        "Ref[datasource]",
        "Ref[entity]",
        "Ref[dimension]",
        "Ref[time_dimension]",
        "Ref[measure]",
        "Ref[metric]",
        "Ref[relationship]",
        "Ref[event]",
        "Ref[event] | ParticipantRoleHandle",
        "Ref[state_model]",
        "Ref[period_calendar]",
        "Ref[temporal_set]",
        "Ref[work_schedule]",
        "Ref[dimension | time_dimension]",
        "Ref[dimension | time_dimension | measure]",
        "Ref | RuntimeMetricExpression",
        "CatalogEntry",
        "CatalogEntry | Ref",
        "CatalogEntry | Ref | RuntimeMetricExpression",
        "CatalogCollection",
        "CatalogLookupKey | Ref",
        "SemanticCatalog",
        "SemanticKind",
        "AuthoringScope | Mapping[Ref[entity], AuthoringScope]",
        "SourceCheck",
        "Mapping[Ref[entity], JSON source parameter mapping]",
        "HelpTarget",
        "DomainName",
        "EntityName",
        "DimensionName",
        "TimeDimensionName",
        "MeasureName",
        "MetricName",
        "RelationshipName",
        "EventName",
        "ColumnName",
        "TableName",
        "SqlText",
        "SqlDialect",
        "AggFunc",
        "Additivity",
        "Unit",
        "Granularity",
        "Granularity | Grain",
        "ParseVariant",
        "PositiveInt",
        "TimeFold",
        "JoinKeySpec",
        "RelationshipEndpoint",
        "DemandSignal",
        "RelTol",
        "AbsTol",
        "ForceFlag",
        "AiContextValue",
        "OwnerName",
        "Primary_key",
        "VersioningPartition",
        "FanoutPolicy",
        "RefKind",
        "AnchorSpec",
        "WeightSpec",
        "LinearTerm",
        "ValiditySpec",
        "DateTimeSpec",
        "TimestampSpec",
        "StrptimeSpec",
        "HourPrefixSpec",
        "GrainToDate",
        "TrailingSpec",
        "WhereFilter",
        "FilterConditions",
        "EntityAlias",
        "Participant",
        "ParticipantRoleHandle",
        "LifecycleState",
        "Inception",
        "StateTransition",
        "ModelStateHandle",
        "StateModelName",
        "LifecycleStateName",
        "PeriodCorrespondence",
        "Grain",
        "Text",
    }
)

OUTPUT_FAMILIES = frozenset(
    {
        "SemanticCatalog",
        "CatalogEntry",
        "CatalogCollection",
        "PreviewBatchResult",
        "PreviewResult",
        "ReadinessReport",
        "SourceCheck",
        "SourceHealthReport",
        "RichnessReport",
        "ParityResult",
        "Ref",
        "Ref[domain]",
        "Ref[datasource]",
        "Ref[entity]",
        "Ref[dimension]",
        "Ref[time_dimension]",
        "Ref[measure]",
        "Ref[metric]",
        "Ref[relationship]",
        "Ref[event]",
        "Ref[state_model]",
        "Ref[period_calendar]",
        "Ref[temporal_set]",
        "Ref[work_schedule]",
        "Ref[dimension | time_dimension]",
        "Ref[dimension | time_dimension | measure]",
        "JoinKey",
        "SqlProvenance",
        "AiContextValue",
        "Additivity",
        "ValiditySpec",
        "DateTimeSpec",
        "TimestampSpec",
        "StrptimeSpec",
        "HourPrefixSpec",
        "GrainToDate",
        "TrailingSpec",
        "None",
        "Text",
        "WhereFilter",
        "IbisValue",
        "Participant",
        "ParticipantRoleHandle",
        "LifecycleState",
        "Inception",
        "StateTransition",
        "ModelStateHandle",
        "PeriodCorrespondence",
        "Grain",
        "CalendarPeriodPage",
        "CalendarLevelDetails",
        "PeriodCalendarDetails",
        "PeriodCalendarEntry",
    }
)

ERROR_TYPES: Mapping[str, type] = {}
TYPE_CONTRACTS: Mapping[type, SemanticTypeContract] = {}


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=canonical_id)


def _inputs(
    *families: tuple[AuthoringInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: AuthoringInputRole, family: str) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(role=role, family=family, min_count=0)


def _effects(
    data_access: DataAccessEffect = "none",
    connection: ConnectionEffect = "none",
    mutations: tuple[MutationEffect, ...] = (),
    flags: tuple[EffectFlag, ...] = (),
) -> AuthoringEffects:
    return AuthoringEffects(
        data_access=data_access,
        connection=connection,
        mutations=mutations,
        flags=flags,
    )


_NONE = _effects()
_LOCAL = _effects("local_metadata_read")
_AUTHOR = _effects(mutations=("semantic_source",))
_PREVIEW = _effects(
    "scoped_data_read",
    "opens_connection",
    flags=(
        "requires_explicit_scope",
        "requires_positive_row_guard",
        "requires_positive_timeout_guard",
    ),
)
_SOURCE_HEALTH = _effects(
    "live_metadata_or_scoped_data_read",
    "opens_connection",
    flags=("scope_required_for_declared_data_checks",),
)
_CERTIFYING_PREVIEW = _effects(
    "scoped_data_read",
    "opens_connection",
    flags=(
        "requires_explicit_scope",
        "requires_positive_row_guard",
        "requires_positive_timeout_guard",
        "may_publish_certified_artifact",
    ),
)
_PARITY = _effects("potentially_unbounded_read", "opens_connection")


def _capability(
    canonical_id: str,
    callable_path: str | None,
    summary: str,
    *,
    kind: AuthoringCapabilityKind = "callable",
    output: str | None = None,
    inputs: tuple[AuthoringInputRequirement, ...] = (),
    effects: AuthoringEffects = _NONE,
    constraints: tuple[str, ...] = (),
    example: str | None = None,
    preconditions: tuple[str, ...] = (),
    repair_kinds: tuple[RepairKind, ...] = (),
    see_also: tuple[LiveHelpTarget, ...] = (),
    public_entrypoint: str | None = None,
) -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id=canonical_id,
        kind=kind,
        surface="semantic",
        public_entrypoint=(public_entrypoint if callable_path is not None else None)
        or (f"ms.{canonical_id}" if callable_path is not None else None),
        callable_path=callable_path,
        summary=summary,
        input_requirements=inputs,
        output_family=output,
        preconditions=preconditions,
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
    )


def _authoring_source_contract(
    kind: SemanticKind,
    *,
    prerequisite_targets: tuple[LiveHelpTarget, ...],
) -> AuthoringSourceContract:
    member = next(member for member in CATALOG_MEMBER_CONTRACTS if member.kind is kind)
    placement_kind: Literal["domain_entrypoint", "domain_module"]
    if kind is SemanticKind.DOMAIN:
        placement_kind = "domain_entrypoint"
        path_template = "models/semantic/<domain>/_domain.py"
        identity_template = "<domain>"
    elif kind in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<entity>.<name>"
    elif kind is SemanticKind.ENTITY:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<entity>"
    else:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<name>"
    return AuthoringSourceContract(
        placement_kind=placement_kind,
        path_template=path_template,
        prerequisite_targets=prerequisite_targets,
        catalog_collection=member.property_name,
        canonical_identity_template=identity_template,
    )


def _source_contracts() -> Mapping[str, AuthoringSourceContract]:
    """Build closed placement/handoff facts for every source-authored object."""

    datasource_authoring = LiveHelpTarget(surface="datasource", canonical_id="authoring")
    by_kind = {
        SemanticKind.DOMAIN: _authoring_source_contract(
            SemanticKind.DOMAIN,
            prerequisite_targets=(),
        ),
        SemanticKind.ENTITY: _authoring_source_contract(
            SemanticKind.ENTITY,
            prerequisite_targets=(_target("domain"), datasource_authoring),
        ),
        SemanticKind.DIMENSION: _authoring_source_contract(
            SemanticKind.DIMENSION,
            prerequisite_targets=(_target("entity"),),
        ),
        SemanticKind.TIME_DIMENSION: _authoring_source_contract(
            SemanticKind.TIME_DIMENSION,
            prerequisite_targets=(_target("entity"),),
        ),
        SemanticKind.MEASURE: _authoring_source_contract(
            SemanticKind.MEASURE,
            prerequisite_targets=(_target("entity"),),
        ),
        SemanticKind.METRIC: _authoring_source_contract(
            SemanticKind.METRIC,
            prerequisite_targets=(_target("entity"), _target("measure")),
        ),
        SemanticKind.RELATIONSHIP: _authoring_source_contract(
            SemanticKind.RELATIONSHIP,
            prerequisite_targets=(_target("entity"),),
        ),
        SemanticKind.EVENT: _authoring_source_contract(
            SemanticKind.EVENT,
            prerequisite_targets=(
                _target("dimension"),
                _target("time_dimension"),
                _target("participant"),
            ),
        ),
        SemanticKind.STATE_MODEL: _authoring_source_contract(
            SemanticKind.STATE_MODEL,
            prerequisite_targets=(
                _target("entity"),
                _target("event"),
                _target("lifecycle_state"),
                _target("transition"),
            ),
        ),
        SemanticKind.PERIOD_CALENDAR: _authoring_source_contract(
            SemanticKind.PERIOD_CALENDAR,
            prerequisite_targets=(_target("time_dimension"), _target("dimension")),
        ),
        SemanticKind.TEMPORAL_SET: _authoring_source_contract(
            SemanticKind.TEMPORAL_SET,
            prerequisite_targets=(_target("dimension"), _target("time_dimension")),
        ),
        SemanticKind.WORK_SCHEDULE: _authoring_source_contract(
            SemanticKind.WORK_SCHEDULE,
            prerequisite_targets=(_target("dimension"), _target("time_dimension")),
        ),
    }
    ids_by_kind = {
        SemanticKind.DOMAIN: ("domain",),
        SemanticKind.ENTITY: ("entity",),
        SemanticKind.DIMENSION: ("dimension", "dimension_column"),
        SemanticKind.TIME_DIMENSION: ("time_dimension", "time_dimension_column"),
        SemanticKind.MEASURE: ("measure", "measure_column"),
        SemanticKind.METRIC: (
            "aggregate",
            "count",
            "cumulative",
            "ratio",
            "weighted_mean",
            "linear",
            "metric",
        ),
        SemanticKind.RELATIONSHIP: ("relationship",),
        SemanticKind.EVENT: ("event",),
        SemanticKind.STATE_MODEL: ("state_model",),
        SemanticKind.PERIOD_CALENDAR: ("period_calendar",),
        SemanticKind.TEMPORAL_SET: ("temporal_set",),
        SemanticKind.WORK_SCHEDULE: ("work_schedule",),
    }
    return MappingProxyType(
        {
            canonical_id: by_kind[kind]
            for kind, canonical_ids in ids_by_kind.items()
            for canonical_id in canonical_ids
        }
    )


def _repair_contracts() -> Mapping[str, SemanticRepairContract]:
    """Build exact repair routes for deterministic authoring/layout failures."""

    rows = (
        SemanticRepairContract(
            error_kind="outside_loader_context",
            kind="reauthor",
            help_target=_target("authoring"),
            action=(
                "Move the declaration into the semantic project and load it through ms.load(); "
                "do not call source-mutating constructors directly from a REPL."
            ),
            snippet=(
                "# models/semantic/<domain>/<module>.py\n"
                "import marivo.semantic as ms\n"
                "# declaration fragment evaluated by ms.load()"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="missing_domain",
            kind="reauthor",
            help_target=_target("domain"),
            action=(
                "Declare the domain with an accountable owner in its _domain.py, "
                "or pass an existing typed domain ref."
            ),
            snippet=(
                "# models/semantic/<domain>/_domain.py\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='<domain>', owner=accountable_owner)"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="invalid_filter",
            kind="reauthor",
            help_target=_target("where"),
            action=(
                "Declare every filter dimension on the metric's target entity, then use "
                "one scalar equality value or a non-empty tuple/list of membership values."
            ),
            snippet=(
                "status = ms.dimension_column(\n"
                "    name='status', entity=orders, column='physical_status'\n"
                ")\n"
                "failed = ms.count(\n"
                "    name='failed', entity=orders, filter=ms.where(status=('FAILED', 'ERROR'))\n"
                ")"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="filter_value_runtime_incompatible",
            kind="user_choice",
            help_target=_target("where"),
            action=(
                "Preserve the authored filter literals. Do not replace business codes with "
                "physical labels or other sampled values. Ask the user or accountable business "
                "owner to confirm any code-to-physical-value mapping; until then, report runtime "
                "evidence as unavailable and continue only query-free static verification and "
                "semantic_static readiness."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="invalid_project",
            kind="configure",
            help_target=_target("authoring"),
            action=(
                "Point ms.load(workspace_dir=...) at a project root containing marivo.toml "
                "and models/semantic/."
            ),
            snippet="catalog = ms.load(workspace_dir='<project root>')",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="domain_file_missing",
            kind="reauthor",
            help_target=_target("domain"),
            action=(
                "Create the required domain entrypoint and supply its accountable owner "
                "before loading again."
            ),
            snippet=(
                "# models/semantic/<domain>/_domain.py\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='<domain>', owner=accountable_owner)"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="domain_file_mismatch",
            kind="reauthor",
            help_target=_target("domain"),
            action="Make the domain directory and ms.domain(name=...) use the same exact name.",
            snippet=(
                "# models/semantic/<domain>/_domain.py\n"
                "ms.domain(name='<domain>', owner=accountable_owner)"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="organization_error",
            kind="reauthor",
            help_target=_target("authoring"),
            action=(
                "Repair the failing semantic source file inside the registered project layout, "
                "then reload the catalog."
            ),
            preserves_evidence=True,
        ),
    )
    return MappingProxyType({row.error_kind: row for row in rows})


def _build_registry() -> SemanticCapabilityRegistry:
    """Build the immutable semantic descriptor catalog from live callables."""
    descriptor_rows = (
        # ------------------------------------------------------------------
        # browse_load
        # ------------------------------------------------------------------
        _capability(
            "load",
            "marivo.semantic.catalog.load",
            "Load the read-only semantic catalog.",
            output="SemanticCatalog",
            effects=_LOCAL,
            example="catalog = ms.load()\ncatalog.show()",
        ),
        _capability(
            "authoring",
            None,
            "Explore current sources, author a coherent semantic slice, load once, and run scoped readiness.",
            kind="boundary",
            output=None,
            effects=_NONE,
            see_also=(_target("load"), _target("readiness"), _target("preview")),
        ),
        # ------------------------------------------------------------------
        # author_families
        # ------------------------------------------------------------------
        _capability(
            "domain",
            "marivo.semantic._authoring_declarations.domain",
            "Declare a semantic domain namespace.",
            output="Ref[domain]",
            inputs=_inputs(("mapping_key", "DomainName"), ("dependency", "OwnerName")),
            effects=_AUTHOR,
            constraints=("domain_owner_required",),
            example="ms.domain(name='sales', owner=accountable_owner)",
        ),
        _capability(
            "entity",
            "marivo.semantic._authoring_decorators.entity",
            "Declare a semantic entity backed by a datasource table.",
            output="Ref[entity]",
            inputs=_inputs(
                ("mapping_key", "EntityName"),
                ("dependency", "Ref[datasource]"),
                ("dependency", "TableName"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "warehouse = ms.ref.datasource('warehouse'); "
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))"
            ),
        ),
        _capability(
            "dimension",
            "marivo.semantic._authoring_decorators.dimension",
            "Declare a calculated dimension on an entity.",
            output="Ref[dimension]",
            inputs=_inputs(
                ("mapping_key", "DimensionName"),
                ("subject", "Ref[entity]"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ast_single_return", "ast_forbidden_statement"),
            example="ms.dimension(name='region', entity=orders)",
        ),
        _capability(
            "dimension_column",
            "marivo.semantic._authoring_decorators.dimension_column",
            "Declare a column-backed dimension on an entity.",
            output="Ref[dimension]",
            inputs=_inputs(
                ("mapping_key", "DimensionName"),
                ("subject", "Ref[entity]"),
                ("dependency", "ColumnName"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=("region = ms.dimension_column(name='region', entity=orders, column='region')"),
        ),
        _capability(
            "time_dimension",
            "marivo.semantic._authoring_decorators.time_dimension",
            "Declare a calculated time dimension on an entity.",
            output="Ref[time_dimension]",
            inputs=_inputs(
                ("mapping_key", "TimeDimensionName"),
                ("subject", "Ref[entity]"),
                ("dependency", "Granularity"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ast_single_return",
                "time_dimension_dtype_compat",
                "time_granularity_parse_compatible",
            ),
            example="ms.time_dimension(name='log_date', entity=orders, granularity='day')",
        ),
        _capability(
            "time_dimension_column",
            "marivo.semantic._authoring_decorators.time_dimension_column",
            "Declare a column-backed time dimension on an entity.",
            output="Ref[time_dimension]",
            inputs=_inputs(
                ("mapping_key", "TimeDimensionName"),
                ("subject", "Ref[entity]"),
                ("dependency", "ColumnName"),
                ("dependency", "Granularity"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ref_shape",
                "time_dimension_dtype_compat",
                "time_granularity_parse_compatible",
            ),
            example=(
                "log_date = ms.time_dimension_column(name='log_date', entity=orders, "
                "column='log_date', granularity='day', parse=ms.strptime('%Y%m%d'))"
            ),
        ),
        _capability(
            "period_correspondence",
            "marivo.semantic._authoring_temporal.period_correspondence",
            "Declare one named same-level baseline-key correspondence for a period calendar.",
            output="PeriodCorrespondence",
            inputs=_inputs(
                ("mapping_key", "Text"),
                ("dependency", "Ref[dimension]"),
            ),
            effects=_NONE,
            constraints=("ref_shape",),
            example=(
                "ms.period_correspondence(level='week', "
                "baseline_key=ms.ref.dimension('sales.calendar.prior_week'))"
            ),
        ),
        _capability(
            "period_calendar",
            "marivo.semantic._authoring_temporal.period_calendar",
            "Declare a finite governed period calendar over an exhaustive civil-date spine.",
            output="Ref[period_calendar]",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="Text"),
                AuthoringInputRequirement(role="subject", family="Ref[time_dimension]"),
                AuthoringInputRequirement(role="dependency", family="Ref[dimension]"),
                _optional_input("dependency", "PeriodCorrespondence"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "ms.period_calendar(name='fiscal', "
                "date=ms.ref.time_dimension('sales.calendar.calendar_date'), "
                "boundary_timezone='UTC', "
                "coverage=(__import__('datetime').date(2026, 1, 1), "
                "__import__('datetime').date(2027, 1, 1)), "
                "levels={'week': ms.ref.dimension('sales.calendar.fiscal_week')})"
            ),
        ),
        _capability(
            "temporal_set",
            "marivo.semantic._authoring_temporal.temporal_set",
            "Declare a finite governed set of named temporal occurrences.",
            output="Ref[temporal_set]",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="Text"),
                AuthoringInputRequirement(role="subject", family="Ref[dimension]"),
                AuthoringInputRequirement(role="dependency", family="Ref[time_dimension]"),
                AuthoringInputRequirement(role="dependency", family="Ref[time_dimension]"),
                _optional_input("dependency", "Ref[dimension]"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "ms.temporal_set(name='campaigns', occurrence_id=ms.ref.dimension('sales.events.id'), "
                "start=ms.ref.time_dimension('sales.events.start'), end=ms.ref.time_dimension('sales.events.end'), "
                "boundary_timezone='UTC', coverage=(__import__('datetime').date(2026, 1, 1), __import__('datetime').date(2027, 1, 1)))"
            ),
        ),
        _capability(
            "work_schedule",
            "marivo.semantic._authoring_temporal.work_schedule",
            "Declare a finite governed final daily working-status schedule.",
            output="Ref[work_schedule]",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="Text"),
                AuthoringInputRequirement(role="subject", family="Ref[time_dimension]"),
                AuthoringInputRequirement(role="dependency", family="Ref[dimension]"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "ms.work_schedule(name='cn_sales_schedule', "
                "date=ms.ref.time_dimension('sales.calendar.date'), "
                "is_working=ms.ref.dimension('sales.calendar.is_working'), "
                "boundary_timezone='Asia/Shanghai', "
                "coverage=(__import__('datetime').date(2026, 1, 1), "
                "__import__('datetime').date(2027, 1, 1)))"
            ),
        ),
        _capability(
            "calendar_grain",
            "marivo.semantic._authoring_temporal.calendar_grain",
            "Construct the unified semantic Grain for one governed calendar level.",
            output="Grain",
            inputs=_inputs(
                ("subject", "Ref[period_calendar]"),
                ("dependency", "Granularity"),
            ),
            effects=_NONE,
            constraints=("ref_shape",),
            example=(
                "ms.calendar_grain(calendar=ms.ref.period_calendar('sales.fiscal'), level='week')"
            ),
        ),
        _capability(
            "measure",
            "marivo.semantic._authoring_decorators.measure",
            "Declare a calculated measure on an entity.",
            output="Ref[measure]",
            inputs=_inputs(
                ("mapping_key", "MeasureName"),
                ("subject", "Ref[entity]"),
                ("dependency", "Additivity"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ast_single_return", "ast_forbidden_statement"),
            example="ms.measure(name='amount', entity=orders, additivity='additive')",
        ),
        _capability(
            "measure_column",
            "marivo.semantic._authoring_decorators.measure_column",
            "Declare a column-backed measure on an entity.",
            output="Ref[measure]",
            inputs=_inputs(
                ("mapping_key", "MeasureName"),
                ("subject", "Ref[entity]"),
                ("dependency", "ColumnName"),
                ("dependency", "Additivity"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "amount = ms.measure_column("
                "name='amount', entity=orders, column='amount', additivity='additive')"
            ),
        ),
        _capability(
            "aggregate",
            "marivo.semantic._authoring_declarations.aggregate",
            "Declare an aggregate metric from a measure.",
            output="Ref[metric]",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="MetricName"),
                AuthoringInputRequirement(role="subject", family="Ref[measure]"),
                AuthoringInputRequirement(role="dependency", family="AggFunc"),
                _optional_input("dependency", "WhereFilter"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "composition_shape",
                "measure_aggregation_valid",
            ),
            example=(
                "us_revenue = ms.aggregate(name='us_revenue', measure=amount, agg='sum', "
                "filter=ms.where(region='US'))"
            ),
        ),
        _capability(
            "count",
            "marivo.semantic._authoring_declarations.count",
            "Declare a count metric on an entity.",
            output="Ref[metric]",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="MetricName"),
                AuthoringInputRequirement(role="subject", family="Ref[entity]"),
                _optional_input("dependency", "WhereFilter"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "composition_shape"),
            example=(
                "failed = ms.count(name='failed', entity=orders, filter=ms.where(state='FAILED'))"
            ),
        ),
        _capability(
            "where",
            "marivo.semantic._authoring_declarations.where",
            (
                "Build an AND filter over declared local dimensions; scalars mean "
                "equality and tuple/list values mean membership."
            ),
            output="WhereFilter",
            inputs=_inputs(("subject", "FilterConditions")),
            effects=_NONE,
            constraints=("filter_condition_valid",),
            example="ms.where(type=(2, 4), query_kind='Select')",
            see_also=(_target("count"), _target("aggregate")),
        ),
        _capability(
            "cumulative",
            "marivo.semantic._authoring_metrics.cumulative",
            "Declare a cumulative derived metric.",
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "Ref[metric]"),
                ("dependency", "AnchorSpec"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "cumulative_anchor"),
            example=(
                "import marivo.analysis as mv\n"
                "mtd_revenue = ms.cumulative(name='mtd_revenue', base=revenue, "
                "anchor=ms.grain_to_date(grain=mv.grain('month')))"
            ),
        ),
        _capability(
            "ratio",
            "marivo.semantic._authoring_metrics.ratio",
            "Declare a recursively composable ratio metric; each lowered node is validated independently.",
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "Ref[metric]"),
                ("dependency", "Ref[metric]"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "composition_shape"),
            example="profit_ratio = ms.ratio(name='profit_ratio', numerator=revenue, denominator=cost)",
        ),
        _capability(
            "weighted_mean",
            "marivo.semantic._authoring_declarations.weighted_mean",
            "Declare an exact weighted mean that multiplies and aggregates two same-row measures.",
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "Ref[measure]"),
                ("dependency", "Ref[measure]"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "measure_aggregation_valid"),
            example="avg_price = ms.weighted_mean(name='avg_price', value=unit_price, weight=volume)",
        ),
        _capability(
            "linear",
            "marivo.semantic._authoring_metrics.linear",
            "Declare a recursively composable linear metric with commensurable term checks.",
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "LinearTerm"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "composition_shape", "linear_unit_commensurable"),
            example="net_revenue = ms.linear(name='net_revenue', add=[revenue], subtract=[refund])",
        ),
        _capability(
            "relationship",
            "marivo.semantic._authoring_decorators.relationship",
            "Declare a relationship between two entities.",
            output="Ref[relationship]",
            inputs=_inputs(
                ("mapping_key", "RelationshipName"),
                ("subject", "RelationshipEndpoint"),
                ("dependency", "RelationshipEndpoint"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "relationship_endpoints",
                "ref_shape",
            ),
            example=(
                "ms.relationship(name='orders_to_customers', from_entity=orders, "
                "to_entity=customers, keys=[ms.join_on(order_customer_id, customer_id)])"
            ),
        ),
        _capability(
            "event",
            "marivo.semantic._authoring_decorators.event",
            "Declare a filtered or explicit all-rows business occurrence.",
            output="Ref[event]",
            inputs=_inputs(
                ("mapping_key", "EventName"),
                ("dependency", "Ref[dimension]"),
                ("dependency", "Ref[time_dimension]"),
                ("dependency", "Participant"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "event_source_owner",
                "event_identity",
                "event_predicate",
                "event_participant_path",
            ),
            example=(
                "@ms.event(identity=(event_id,), occurred_at=event_time, "
                "participants=(ms.participant(name='order', cardinality='one'),))\n"
                "def order_created(rows):\n"
                "    return ms.all_rows()"
            ),
            see_also=(
                _target("participant"),
                _target("participant_role"),
                _target("all_rows"),
            ),
        ),
        _capability(
            "participant",
            "marivo.semantic.event.participant",
            "Declare one named participant role inside an Event.",
            output="Participant",
            inputs=_inputs(
                ("mapping_key", "EntityName"),
                ("dependency", "Ref[relationship]"),
            ),
            effects=_NONE,
            constraints=("event_participant_path", "event_participant_cardinality"),
            example=("ms.participant(name='buyer', path=(event_to_buyer,), cardinality='one')"),
            see_also=(_target("event"), _target("participant_role")),
        ),
        _capability(
            "participant_role",
            "marivo.semantic.event.participant_role",
            "Create an immutable handle for one named participant role on an Event.",
            output="ParticipantRoleHandle",
            inputs=_inputs(
                ("subject", "Ref[event]"),
                ("mapping_key", "EntityName"),
            ),
            effects=_NONE,
            constraints=("event_participant_membership",),
            example="ms.participant_role(event=payment_succeeded, name='buyer')",
            see_also=(_target("event"), _target("participant")),
        ),
        _capability(
            "all_rows",
            "marivo.semantic.event.all_rows",
            "Return the explicit unfiltered predicate from an Event body.",
            output="IbisValue",
            effects=_NONE,
            constraints=("event_all_rows_complete_return",),
            example=(
                "@ms.event(identity=(event_id,), occurred_at=event_time, "
                "participants=(ms.participant(name='order', cardinality='one'),))\n"
                "def order_created(rows):\n"
                "    return ms.all_rows()"
            ),
            see_also=(_target("event"),),
        ),
        _capability(
            "lifecycle_state",
            "marivo.semantic.state_model.lifecycle_state",
            "Declare one immutable local state for a StateModel.",
            output="LifecycleState",
            inputs=_inputs(("mapping_key", "LifecycleStateName")),
            effects=_NONE,
            constraints=("state_model_shape",),
            example="created = ms.lifecycle_state(name='created', initial=True)",
            see_also=(_target("state_model"), _target("transition")),
        ),
        _capability(
            "inception",
            "marivo.semantic.state_model.inception",
            "Declare a trigger from unseeded history into the sole initial state.",
            output="Inception",
            inputs=_inputs(("subject", "Ref[event] | ParticipantRoleHandle")),
            effects=_NONE,
            constraints=("state_model_trigger",),
            example="seed = ms.inception(on=ms.ref.event('commerce.order_created'))",
            see_also=(_target("state_model"), _target("participant_role")),
        ),
        _capability(
            "transition",
            "marivo.semantic.state_model.transition",
            "Declare one deterministic transition between exact local states.",
            output="StateTransition",
            inputs=_inputs(
                ("subject", "LifecycleState"),
                ("dependency", "Ref[event] | ParticipantRoleHandle"),
                ("dependency", "LifecycleState"),
            ),
            effects=_NONE,
            constraints=("state_model_shape", "state_model_trigger"),
            example=(
                "paid_transition = ms.transition("
                "from_state=created, on=ms.ref.event('commerce.payment_captured'), "
                "to_state=paid)"
            ),
            see_also=(_target("lifecycle_state"), _target("state_model")),
        ),
        _capability(
            "state_model",
            "marivo.semantic.state_model.state_model",
            "Declare a closed normative lifecycle for one subject Entity.",
            output="Ref[state_model]",
            inputs=_inputs(
                ("mapping_key", "StateModelName"),
                ("subject", "Ref[entity]"),
                ("dependency", "LifecycleState"),
                ("dependency", "Inception"),
                ("dependency", "StateTransition"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "state_model_shape",
                "state_model_trigger",
            ),
            example=(
                "order_lifecycle = ms.state_model("
                "name='order_lifecycle', subject=orders, states=(created, paid), "
                "transitions=(ms.inception(on=order_created), paid_transition,))"
            ),
            see_also=(
                _target("lifecycle_state"),
                _target("inception"),
                _target("transition"),
                _target("model_state"),
            ),
        ),
        _capability(
            "model_state",
            "marivo.semantic.state_model.model_state",
            "Create the project-neutral typed identity of one StateModel state.",
            output="ModelStateHandle",
            inputs=_inputs(
                ("subject", "Ref[state_model]"),
                ("mapping_key", "LifecycleStateName"),
            ),
            effects=_NONE,
            constraints=("model_state_membership",),
            example=(
                "paid = ms.model_state("
                "model=ms.ref.state_model('commerce.order_lifecycle'), name='paid')"
            ),
            see_also=(_target("state_model"),),
        ),
        _capability(
            "join_on",
            "marivo.semantic._authoring_values.join_on",
            "Build a join-key specification for a relationship.",
            output="JoinKey",
            inputs=_inputs(("dependency", "ColumnName")),
            effects=_AUTHOR,
            example="ms.join_on(order_customer_id, customer_id)",
        ),
        _capability(
            "from_sql",
            "marivo.semantic._authoring_values.from_sql",
            "Build a SQL provenance value for parity checking.",
            output="SqlProvenance",
            inputs=_inputs(("subject", "SqlText"), ("dependency", "SqlDialect")),
            effects=_AUTHOR,
            example="ms.from_sql(sql='SELECT SUM(amount) FROM orders', dialect='duckdb')",
        ),
        # ------------------------------------------------------------------
        # Low-level expression builders (public authoring surface)
        # ------------------------------------------------------------------
        _capability(
            "bind",
            "marivo.semantic._expression_binding.bind",
            "Apply one semantic field ref to a direct entity alias in an expression body.",
            output="IbisValue",
            inputs=_inputs(
                ("subject", "Ref[dimension | time_dimension | measure]"),
                ("dependency", "EntityAlias"),
            ),
            effects=_NONE,
            constraints=("expression_binding",),
            example=(
                "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
                "def revenue_metric(orders):\n"
                "    return ms.bind(amount, orders).sum()"
            ),
        ),
        _capability(
            "metric",
            "marivo.semantic._authoring_declarations.metric",
            "Declare a base metric with an expression body.",
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "Ref[entity]"),
                ("dependency", "Additivity"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ast_single_return",
                "ast_forbidden_statement",
                "metric_entities_required",
                "metric_additivity_required",
            ),
            example="ms.metric(name='revenue', entities=[orders], additivity='additive')",
        ),
        _capability(
            "ai_context",
            "marivo.semantic._authoring_values.ai_context",
            "Build an AI context value for agent-facing metadata.",
            output="AiContextValue",
            effects=_AUTHOR,
            constraints=("ai_context_schema",),
            example="ms.ai_context(business_definition='Sum of accepted order amounts.')",
        ),
        _capability(
            "snapshot",
            "marivo.semantic._authoring_values.snapshot",
            "Build a snapshot versioning specification for an entity.",
            output="ValiditySpec",
            inputs=_inputs(("dependency", "ColumnName")),
            effects=_AUTHOR,
            constraints=("entity_versioning_valid",),
            example="ms.snapshot(partition_field=snapshot_date, grain='day')",
        ),
        _capability(
            "validity",
            "marivo.semantic._authoring_values.validity",
            "Build a validity window specification for an entity.",
            output="ValiditySpec",
            inputs=_inputs(("dependency", "ColumnName")),
            effects=_AUTHOR,
            example=(
                "ms.validity(valid_from=valid_from, valid_to=valid_to, "
                "interval='closed_open', open_end=(None,))"
            ),
        ),
        _capability(
            "semi_additive",
            "marivo.semantic._authoring_values.semi_additive",
            "Build a semi-additive additivity specification.",
            output="Additivity",
            effects=_AUTHOR,
            example="ms.semi_additive(over=snapshot_date, fold='last')",
        ),
        _capability(
            "datetime",
            "marivo.semantic._authoring_values.datetime",
            "Build a datetime parse variant for time dimensions.",
            output="DateTimeSpec",
            effects=_AUTHOR,
            constraints=("time_granularity_parse_compatible",),
            example="ms.datetime(timezone='UTC')",
        ),
        _capability(
            "timestamp",
            "marivo.semantic._authoring_values.timestamp",
            "Build a timestamp parse variant for time dimensions.",
            output="TimestampSpec",
            effects=_AUTHOR,
            constraints=("time_granularity_parse_compatible",),
            example="ms.timestamp(timezone='UTC')",
        ),
        _capability(
            "strptime",
            "marivo.semantic._authoring_values.strptime",
            "Build a strptime parse variant for time dimensions.",
            output="StrptimeSpec",
            effects=_AUTHOR,
            constraints=("time_granularity_parse_compatible",),
            example="ms.strptime('%Y%m%d')",
        ),
        _capability(
            "hour_prefix",
            "marivo.semantic._authoring_values.hour_prefix",
            "Build an hour-prefix parse variant for time dimensions.",
            output="HourPrefixSpec",
            effects=_AUTHOR,
            constraints=("time_granularity_parse_compatible",),
            example="ms.hour_prefix(log_date)",
        ),
        _capability(
            "grain_to_date",
            "marivo.semantic._authoring_metrics.grain_to_date",
            (
                "Build a grain-to-date cumulative anchor specification from a builtin reset "
                "granularity or a certified semantic Grain."
            ),
            output="GrainToDate",
            inputs=_inputs(("dependency", "Granularity | Grain")),
            effects=_AUTHOR,
            constraints=("cumulative_anchor",),
            example=(
                "ms.grain_to_date("
                "grain=ms.calendar_grain("
                "calendar=ms.ref.period_calendar('sales.fiscal'), level='fiscal_month'))"
            ),
        ),
        _capability(
            "trailing",
            "marivo.semantic._authoring_metrics.trailing",
            "Build a trailing window cumulative anchor specification.",
            output="TrailingSpec",
            inputs=_inputs(("dependency", "PositiveInt")),
            effects=_AUTHOR,
            constraints=("cumulative_anchor",),
            example="ms.trailing(count=7, unit='day')",
        ),
        # ------------------------------------------------------------------
        # runtime_probes
        # ------------------------------------------------------------------
        _capability(
            "preview",
            "marivo.semantic.catalog.SemanticCatalog.preview",
            (
                "Run one scoped data preview for a current catalog entry or exact ref. "
                "Metric previews aggregate at most 10,000 scoped input rows and report "
                "an approximate result; period calendars, temporal sets, and work "
                "schedules publish their dedicated certified artifacts."
            ),
            kind="method",
            output="PreviewResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject",
                    family="CatalogEntry | Ref",
                ),
                AuthoringInputRequirement(
                    role="scope",
                    family="AuthoringScope | Mapping[Ref[entity], AuthoringScope]",
                ),
                _optional_input(
                    "dependency",
                    "Mapping[Ref[entity], JSON source parameter mapping]",
                ),
            ),
            effects=_CERTIFYING_PREVIEW,
            constraints=("backend_factory_available",),
            example=(
                "catalog.preview(revenue, scope=md.unpruned(max_rows=1000, timeout_seconds=30))"
            ),
            preconditions=("a current loaded SemanticCatalog",),
            repair_kinds=("reconnect",),
            public_entrypoint="catalog.preview",
        ),
        _capability(
            "preview_many",
            "marivo.semantic.catalog.SemanticCatalog.preview_many",
            (
                "Run scoped data previews for a non-empty entry/ref sequence. Metric "
                "previews aggregate at most 10,000 scoped input rows and report an "
                "approximate result."
            ),
            kind="method",
            output="PreviewBatchResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject",
                    family="CatalogEntry | Ref",
                    min_count=1,
                    max_count=None,
                ),
                AuthoringInputRequirement(
                    role="scope",
                    family="AuthoringScope | Mapping[Ref[entity], AuthoringScope]",
                ),
                _optional_input(
                    "dependency",
                    "Mapping[Ref[entity], JSON source parameter mapping]",
                ),
            ),
            effects=_PREVIEW,
            constraints=("backend_factory_available",),
            example=(
                "catalog.preview_many([revenue], "
                "scope=md.unpruned(max_rows=1000, timeout_seconds=30))"
            ),
            preconditions=("a current loaded SemanticCatalog",),
            repair_kinds=("reconnect",),
            public_entrypoint="catalog.preview_many",
        ),
        _capability(
            "source_check",
            None,
            (
                "Build explicit null, enum, uniqueness, freshness, relationship, "
                "or cardinality expectations; no expectation is inferred from samples."
            ),
            kind="boundary",
            output="SourceCheck",
            effects=_NONE,
        ),
        _capability(
            "source_health",
            "marivo.semantic.catalog.SemanticCatalog.source_health",
            (
                "Check current connectivity and schema identity, plus only explicitly "
                "declared bounded data expectations, without changing readiness."
            ),
            kind="method",
            output="SourceHealthReport",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject",
                    family="CatalogEntry | Ref",
                    min_count=1,
                    max_count=None,
                ),
                _optional_input("dependency", "SourceCheck"),
                _optional_input(
                    "scope",
                    "AuthoringScope | Mapping[Ref[entity], AuthoringScope]",
                ),
            ),
            effects=_SOURCE_HEALTH,
            example="catalog.source_health([revenue])",
            preconditions=("a current loaded SemanticCatalog",),
            repair_kinds=("inspect", "reconnect", "rescope", "reauthor"),
            public_entrypoint="catalog.source_health",
        ),
        # ------------------------------------------------------------------
        # readiness
        # ------------------------------------------------------------------
        _capability(
            "readiness",
            "marivo.semantic.catalog.SemanticCatalog.readiness",
            "Statically certify current entries, exact refs, or runtime metric expressions through governed leaves and fixed graph budgets; operation-specific executability remains owned by the consuming analysis call.",
            kind="method",
            output="ReadinessReport",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "CatalogEntry | Ref | RuntimeMetricExpression"),
            ),
            effects=_LOCAL,
            example="catalog.readiness(refs=[revenue, runtime_revenue])",
            preconditions=("a current loaded SemanticCatalog",),
            public_entrypoint="catalog.readiness",
        ),
        # ------------------------------------------------------------------
        # diagnostics_boundaries
        # ------------------------------------------------------------------
        _capability(
            "richness",
            "marivo.semantic.richness",
            "Return a demand-ranked advisory richness report.",
            output="RichnessReport",
            inputs=(_optional_input("dependency", "DemandSignal"),),
            effects=_LOCAL,
            example="report = ms.richness()",
        ),
        _capability(
            "parity_check",
            "marivo.semantic.parity_check",
            "Run parity check for a metric against its source SQL.",
            output="ParityResult",
            inputs=_inputs(
                ("subject", "Ref[metric]"),
                ("dependency", "RelTol"),
                ("dependency", "AbsTol"),
                ("dependency", "ForceFlag"),
            ),
            effects=_PARITY,
            constraints=(
                "provenance_dialect_required",
                "parity_value_match",
                "parity_scalar_result",
            ),
            example="result = ms.parity_check('sales.revenue')",
            repair_kinds=("reauthor",),
        ),
        # ------------------------------------------------------------------
        # SemanticCatalog methods
        # ------------------------------------------------------------------
        _capability(
            "SemanticCatalog.items",
            "marivo.semantic.catalog.SemanticCatalog.items",
            "Return the typed collection for a SemanticKind (kind-keyed traversal).",
            kind="method",
            output="CatalogCollection",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "SemanticKind"),
            ),
            effects=_LOCAL,
            example="catalog.items(ms.SemanticKind.METRIC).refs",
            public_entrypoint="catalog.items",
        ),
        _capability(
            "SemanticCatalog.require",
            "marivo.semantic.catalog.SemanticCatalog.require",
            "Require exact membership of one ref in the compiled catalog.",
            kind="method",
            output="CatalogEntry",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "Ref"),
            ),
            effects=_LOCAL,
            example="catalog.require(ms.ref.metric('sales.revenue'))",
            public_entrypoint="catalog.require",
        ),
        _capability(
            "CatalogCollection.get",
            "marivo.semantic.catalog.CatalogCollection.get",
            "Select one current entry from a typed catalog collection.",
            kind="method",
            output="CatalogEntry",
            inputs=_inputs(
                ("receiver", "CatalogCollection"),
                ("subject", "CatalogLookupKey | Ref"),
            ),
            effects=_LOCAL,
            example="collection.get('metric:sales.revenue')",
            public_entrypoint="collection.get",
        ),
    )
    groups: Mapping[SemanticRootGroup, tuple[str, ...]] = MappingProxyType(
        {
            "browse_load": ("load", "authoring"),
            "author_families": (
                "domain",
                "entity",
                "dimension",
                "dimension_column",
                "time_dimension",
                "time_dimension_column",
                "period_correspondence",
                "period_calendar",
                "temporal_set",
                "calendar_grain",
                "measure",
                "measure_column",
                "aggregate",
                "count",
                "cumulative",
                "ratio",
                "weighted_mean",
                "linear",
                "relationship",
                "event",
                "participant",
                "participant_role",
                "all_rows",
                "lifecycle_state",
                "inception",
                "transition",
                "state_model",
                "model_state",
                "join_on",
                "from_sql",
                "bind",
                "metric",
                "ai_context",
                "snapshot",
                "validity",
                "semi_additive",
                "datetime",
                "timestamp",
                "strptime",
                "hour_prefix",
                "grain_to_date",
                "trailing",
            ),
            "runtime_probes": ("preview", "preview_many", "source_check", "source_health"),
            "readiness": ("readiness",),
            "diagnostics_boundaries": ("richness", "parity_check"),
        }
    )
    return SemanticCapabilityRegistry(
        surface="semantic",
        _descriptors=descriptor_rows,
        _groups=groups,
        _by_id=MappingProxyType({row.canonical_id: row for row in descriptor_rows}),
        _by_callable_path=MappingProxyType(
            {row.callable_path: row for row in descriptor_rows if row.callable_path is not None}
        ),
        _source_contracts=_source_contracts(),
        _repair_contracts=_repair_contracts(),
    )


REGISTRY = _build_registry()


def _type_contracts() -> Mapping[type, SemanticTypeContract]:
    """Build private type contracts without exposing constructors as help targets."""
    from marivo.refs import PeriodCalendarKind, Ref, SemanticKind, WorkScheduleKind
    from marivo.refs import ref as ref_factory
    from marivo.semantic._authoring_metrics import GrainToDate
    from marivo.semantic._authoring_temporal import PeriodCorrespondence
    from marivo.semantic.catalog import (
        CalendarLevelDetails,
        CalendarPeriodPage,
        CatalogCollection,
        CatalogEntry,
        DatasourceDetails,
        DatasourceEntry,
        DerivedMetricDetails,
        DimensionDetails,
        DimensionEntry,
        DomainDetails,
        DomainEntry,
        EntityDetails,
        EntityEntry,
        EventDetails,
        EventEntry,
        MeasureDetails,
        MeasureEntry,
        MetricEntry,
        PeriodCalendarDetails,
        PeriodCalendarEntry,
        RelationshipDetails,
        RelationshipEntry,
        SemanticCatalog,
        SimpleMetricDetails,
        StateModelDetails,
        StateModelEntry,
        TemporalOccurrencePage,
        TemporalSetDetails,
        TemporalSetEntry,
        TimeDimensionDetails,
        TimeDimensionEntry,
        WorkScheduleDetails,
        WorkScheduleEntry,
    )
    from marivo.semantic.dtos import PreviewBatchResult
    from marivo.semantic.ir import JoinKey, SqlProvenance
    from marivo.semantic.parity import ParityResult
    from marivo.semantic.readiness import (
        ReadinessInputSummary,
        ReadinessIssue,
        ReadinessReport,
    )
    from marivo.semantic.richness import RichnessReport
    from marivo.semantic.source_health import (
        SourceCheckNamespace,
        SourceHealthCheckResult,
        SourceHealthReport,
    )

    show_render = ("show", "render")
    contracts: dict[type, SemanticTypeContract] = {}

    def add(
        cls: type,
        name: str,
        producers: tuple[str, ...],
        *,
        properties: tuple[str, ...] = (),
        methods: tuple[str, ...] = (),
        consumers: tuple[str, ...] = (),
    ) -> None:
        contracts[cls] = SemanticTypeContract(
            name=name,
            producers=tuple(_target(value) for value in producers),
            public_properties=properties,
            public_methods=methods,
            consumers=tuple(_target(value) for value in consumers),
        )

    add(
        GrainToDate,
        "GrainToDate",
        ("grain_to_date",),
        properties=("grain", "kind"),
    )

    # Catalog types
    add(
        SemanticCatalog,
        "SemanticCatalog",
        ("load",),
        properties=CATALOG_COLLECTION_PROPERTIES,
        methods=(
            "items",
            "require",
            "preview",
            "preview_many",
            "source_health",
            "readiness",
            "render",
            "show",
        ),
    )
    add(
        CatalogEntry,
        "CatalogEntry",
        ("SemanticCatalog.require",),
        properties=("ref",),
        methods=("details", "show", "render"),
    )
    add(
        CatalogCollection,
        "CatalogCollection",
        (),
        properties=("items", "refs"),
        methods=("get", *show_render),
    )
    add(
        DomainEntry,
        "DomainEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        DomainDetails,
        "DomainDetails",
        (),
        methods=show_render,
    )
    add(
        EntityEntry,
        "EntityEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        EntityDetails,
        "EntityDetails",
        (),
        methods=show_render,
    )
    add(
        DimensionEntry,
        "DimensionEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        DimensionDetails,
        "DimensionDetails",
        (),
        methods=show_render,
    )
    add(
        TimeDimensionEntry,
        "TimeDimensionEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        TimeDimensionDetails,
        "TimeDimensionDetails",
        (),
        methods=show_render,
    )
    add(
        MeasureEntry,
        "MeasureEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        MeasureDetails,
        "MeasureDetails",
        (),
        methods=show_render,
    )
    add(
        MetricEntry,
        "MetricEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        SimpleMetricDetails,
        "SimpleMetricDetails",
        (),
        methods=show_render,
    )
    add(
        DerivedMetricDetails,
        "DerivedMetricDetails",
        (),
        methods=show_render,
    )
    add(
        RelationshipEntry,
        "RelationshipEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        RelationshipDetails,
        "RelationshipDetails",
        (),
        methods=show_render,
    )
    add(
        EventEntry,
        "EventEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        EventDetails,
        "EventDetails",
        (),
        methods=show_render,
    )
    add(
        StateModelEntry,
        "StateModelEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        StateModelDetails,
        "StateModelDetails",
        (),
        methods=show_render,
    )
    add(
        PeriodCalendarEntry,
        "PeriodCalendarEntry",
        (),
        properties=("ref",),
        methods=(
            "grain",
            "period",
            "period_on",
            "periods",
            "details",
            "show",
            "render",
        ),
    )
    add(
        PeriodCalendarDetails,
        "PeriodCalendarDetails",
        (),
        properties=(
            "ref",
            "boundary_timezone",
            "coverage",
            "source_date",
            "levels",
            "correspondences",
            "snapshot_status",
            "parents",
            "children",
            "dependents",
        ),
        methods=show_render,
    )
    add(
        CalendarPeriodPage,
        "CalendarPeriodPage",
        (),
        properties=("items", "next_cursor"),
        methods=show_render,
    )
    add(
        CalendarLevelDetails,
        "CalendarLevelDetails",
        (),
        properties=(
            "name",
            "key_ref",
            "period_count",
            "direct_finer_levels",
            "direct_coarser_levels",
            "rollup_targets",
        ),
        methods=show_render,
    )
    add(
        TemporalSetEntry,
        "TemporalSetEntry",
        (),
        properties=("ref",),
        methods=("occurrence", "occurrences", "details", "show", "render"),
    )
    add(
        TemporalSetDetails,
        "TemporalSetDetails",
        (),
        properties=(
            "ref",
            "boundary_timezone",
            "coverage",
            "occurrence_id",
            "start",
            "end",
            "category",
            "occurrence_count",
            "snapshot_status",
            "parents",
            "children",
            "dependents",
        ),
        methods=show_render,
    )
    add(
        TemporalOccurrencePage,
        "TemporalOccurrencePage",
        (),
        properties=("items", "next_cursor"),
        methods=show_render,
    )
    add(
        WorkScheduleEntry,
        "WorkScheduleEntry",
        (),
        properties=("ref",),
        methods=("details", "show", "render"),
    )
    add(
        WorkScheduleDetails,
        "WorkScheduleDetails",
        (),
        properties=(
            "ref",
            "boundary_timezone",
            "coverage",
            "date",
            "is_working",
            "snapshot_status",
            "parents",
            "children",
            "dependents",
        ),
        methods=show_render,
    )
    add(
        DatasourceEntry,
        "DatasourceEntry",
        (),
        methods=("details", "show", "render"),
    )
    add(
        DatasourceDetails,
        "DatasourceDetails",
        (),
        methods=show_render,
    )
    # Result types
    add(
        PreviewBatchResult,
        "PreviewBatchResult",
        ("preview_many",),
        properties=("status", "refs", "results"),
        methods=("show", "render"),
    )
    add(
        ReadinessReport,
        "ReadinessReport",
        ("readiness",),
        properties=("analysis_ready_inputs",),
        methods=("show", "render"),
    )
    add(
        SourceHealthReport,
        "SourceHealthReport",
        ("source_health",),
        properties=("status", "checks", "affected_refs"),
        methods=("show", "render", "to_dict"),
    )
    add(
        SourceHealthCheckResult,
        "SourceHealthCheckResult",
        ("source_health",),
        properties=("kind", "status", "affected_refs", "user_data_queried", "scopes"),
        methods=("show", "render", "to_dict"),
    )
    add(
        SourceCheckNamespace,
        "SourceCheckNamespace",
        ("source_check",),
        methods=(
            "not_null",
            "allowed_values",
            "unique",
            "freshness",
            "relationship_matches",
            "relationship_cardinality",
        ),
    )
    add(
        RichnessReport,
        "RichnessReport",
        ("richness",),
        methods=show_render,
    )
    add(
        ParityResult,
        "ParityResult",
        ("parity_check",),
    )
    add(
        ReadinessInputSummary,
        "ReadinessInputSummary",
        (),
    )
    add(
        ReadinessIssue,
        "ReadinessIssue",
        (),
    )
    # Identity types
    add(
        Ref,
        "Ref",
        (
            "domain",
            "entity",
            "dimension",
            "dimension_column",
            "time_dimension",
            "time_dimension_column",
            "measure",
            "measure_column",
            "aggregate",
            "count",
            "cumulative",
            "ratio",
            "weighted_mean",
            "linear",
            "metric",
            "relationship",
            "event",
            "state_model",
            "period_calendar",
            "temporal_set",
            "work_schedule",
        ),
        properties=("kind", "path", "key", "name"),
        consumers=(
            "SemanticCatalog.require",
            "preview",
            "preview_many",
            "source_health",
            "readiness",
        ),
    )
    add(
        type(ref_factory),
        "ref",
        (),
        methods=(
            "domain",
            "datasource",
            "entity",
            "dimension",
            "time_dimension",
            "measure",
            "metric",
            "relationship",
            "event",
            "state_model",
            "period_calendar",
            "temporal_set",
            "work_schedule",
        ),
    )
    add(
        PeriodCalendarKind,
        "PeriodCalendarKind",
        ("period_calendar",),
    )
    from marivo.refs import TemporalSetKind

    add(
        TemporalSetKind,
        "TemporalSetKind",
        ("temporal_set",),
    )
    add(
        WorkScheduleKind,
        "WorkScheduleKind",
        ("work_schedule",),
    )
    from marivo.semantic.event import Participant, ParticipantRoleHandle
    from marivo.semantic.state_model import (
        Inception,
        LifecycleState,
        ModelStateHandle,
        StateTransition,
    )

    add(
        Participant,
        "Participant",
        ("participant",),
        properties=("name", "path", "cardinality"),
        consumers=("event",),
    )
    add(
        ParticipantRoleHandle,
        "ParticipantRoleHandle",
        ("participant_role",),
        properties=("event", "name", "key"),
    )
    add(
        LifecycleState,
        "LifecycleState",
        ("lifecycle_state",),
        properties=("name", "initial", "terminal"),
        consumers=("state_model", "transition"),
    )
    add(
        Inception,
        "Inception",
        ("inception",),
        properties=("on",),
        consumers=("state_model",),
    )
    add(
        StateTransition,
        "StateTransition",
        ("transition",),
        properties=("from_state", "on", "to_state"),
        consumers=("state_model",),
    )
    add(
        ModelStateHandle,
        "ModelStateHandle",
        ("model_state",),
        properties=("model", "name", "key"),
    )
    add(
        PeriodCorrespondence,
        "PeriodCorrespondence",
        ("period_correspondence",),
        properties=("level", "baseline_key"),
        consumers=("period_calendar",),
    )
    # IR types
    add(
        JoinKey,
        "JoinKey",
        ("join_on",),
        methods=("to_tuple",),
    )
    add(
        SqlProvenance,
        "SqlProvenance",
        ("from_sql",),
    )
    # Enum and value types
    add(
        SemanticKind,
        "SemanticKind",
        (),
    )
    from marivo.datasource.typing import AiContextValue

    add(
        AiContextValue,
        "AiContextValue",
        ("ai_context",),
    )
    return MappingProxyType(contracts)


TYPE_CONTRACTS = _type_contracts()


def _error_types() -> Mapping[str, type]:
    from marivo.semantic.errors import (
        SemanticContractScopeError,
        SemanticDecoratorError,
        SemanticError,
        SemanticHelpTargetError,
        SemanticLoadError,
        SemanticLoadFailed,
        SemanticParityError,
        SemanticRuntimeError,
    )

    return MappingProxyType(
        {
            "SemanticError": SemanticError,
            "SemanticDecoratorError": SemanticDecoratorError,
            "SemanticLoadError": SemanticLoadError,
            "SemanticRuntimeError": SemanticRuntimeError,
            "SemanticParityError": SemanticParityError,
            "SemanticHelpTargetError": SemanticHelpTargetError,
            "SemanticContractScopeError": SemanticContractScopeError,
            "SemanticLoadFailed": SemanticLoadFailed,
        }
    )


ERROR_TYPES = _error_types()
