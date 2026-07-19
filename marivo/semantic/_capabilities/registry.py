"""Closed registry and consumed-type catalog for ``marivo.semantic``."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringStateId,
    AuthoringStateRef,
    ConnectionEffect,
    DataAccessEffect,
    EffectFlag,
    MutationEffect,
    RepairKind,
    TransitionInputRole,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._capabilities.model import (
    SemanticCapabilityRegistry,
    SemanticRootGroup,
    SemanticTypeContract,
)

INPUT_FAMILIES = frozenset(
    {
        "SemanticRef",
        "CatalogObject",
        "SemanticCatalog",
        "DiscoverySnapshot",
        "HelpTarget",
        "DomainName",
        "EntityName",
        "DimensionName",
        "TimeDimensionName",
        "MeasureName",
        "MetricName",
        "RelationshipName",
        "ColumnName",
        "TableName",
        "DatasourceRef",
        "SqlText",
        "SqlDialect",
        "AggFunc",
        "Additivity",
        "Unit",
        "Granularity",
        "ParseVariant",
        "PositiveInt",
        "TimeFold",
        "JoinKeySpec",
        "RelationshipEndpoint",
        "DemandSignal",
        "DomainRef",
        "EntityRef",
        "DimensionRef",
        "TimeDimensionRef",
        "MeasureRef",
        "MetricRef",
        "RelationshipRef",
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
        "GrainToDateSpec",
        "TrailingSpec",
        "WhereFilter",
        "FilterConditions",
    }
)

OUTPUT_FAMILIES = frozenset(
    {
        "SemanticCatalog",
        "CatalogObject",
        "VerifyResult",
        "PreviewBatchResult",
        "PreviewResult | PreviewBatchResult",
        "ReadinessReport",
        "RichnessReport",
        "ParityResult",
        "DomainRef",
        "EntityRef",
        "DimensionRef",
        "TimeDimensionRef",
        "MeasureRef",
        "MetricRef",
        "RelationshipRef",
        "SemanticRef",
        "JoinKey",
        "SqlProvenance",
        "AiContextValue",
        "Additivity",
        "ValiditySpec",
        "DateTimeSpec",
        "TimestampSpec",
        "StrptimeSpec",
        "HourPrefixSpec",
        "GrainToDateSpec",
        "TrailingSpec",
        "None",
        "Text",
        "WhereFilter",
    }
)

ERROR_TYPES: Mapping[str, type] = {}
TYPE_CONTRACTS: Mapping[type, SemanticTypeContract] = {}


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=canonical_id)


def _states(*state_ids: AuthoringStateId) -> tuple[AuthoringStateRef, ...]:
    return tuple(AuthoringStateRef(id=state_id) for state_id in state_ids)


def _inputs(
    *families: tuple[TransitionInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: TransitionInputRole, family: str) -> AuthoringInputRequirement:
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
    mutations=("project_state",),
    flags=("requires_existing_snapshot_binding",),
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
    produced_state: AuthoringStateId | None = None,
    required_states: tuple[AuthoringStateRef, ...] = (),
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
        produced_state=(
            AuthoringStateRef(id=produced_state) if produced_state is not None else None
        ),
        required_states=required_states,
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
    )


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
            example="catalog = ms.load()",
            produced_state="semantic.loaded",
        ),
        _capability(
            "authoring",
            None,
            "Semantic authoring lifecycle: browse, author, verify, preview, readiness, handoff.",
            kind="transition",
            output=None,
            effects=_NONE,
            see_also=(_target("load"), _target("verify_object"), _target("preview")),
        ),
        # ------------------------------------------------------------------
        # author_families
        # ------------------------------------------------------------------
        _capability(
            "domain",
            "marivo.semantic._authoring_declarations.domain",
            "Declare a semantic domain namespace.",
            output="DomainRef",
            inputs=_inputs(("mapping_key", "DomainName"), ("dependency", "OwnerName")),
            effects=_AUTHOR,
            constraints=("domain_owner_required",),
            example="ms.domain(name='sales', owner='Mina Zhang')",
        ),
        _capability(
            "entity",
            "marivo.semantic._authoring_decorators.entity",
            "Declare a semantic entity backed by a datasource table.",
            output="EntityRef",
            inputs=_inputs(
                ("mapping_key", "EntityName"),
                ("dependency", "DatasourceRef"),
                ("dependency", "TableName"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example="orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))",
        ),
        _capability(
            "dimension",
            "marivo.semantic._authoring_decorators.dimension",
            "Declare a calculated dimension on an entity.",
            output="DimensionRef",
            inputs=_inputs(
                ("mapping_key", "DimensionName"),
                ("subject", "EntityRef"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ast_single_return", "ast_forbidden_statement"),
            example="ms.dimension(name='region', entity=orders)",
        ),
        _capability(
            "dimension_column",
            "marivo.semantic._authoring_decorators.dimension_column",
            "Declare a column-backed dimension on an entity.",
            output="DimensionRef",
            inputs=_inputs(
                ("mapping_key", "DimensionName"),
                ("subject", "EntityRef"),
                ("dependency", "ColumnName"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example="ms.dimension_column(name='region', entity=orders, column='region')",
        ),
        _capability(
            "time_dimension",
            "marivo.semantic._authoring_decorators.time_dimension",
            "Declare a calculated time dimension on an entity.",
            output="TimeDimensionRef",
            inputs=_inputs(
                ("mapping_key", "TimeDimensionName"),
                ("subject", "EntityRef"),
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
            output="TimeDimensionRef",
            inputs=_inputs(
                ("mapping_key", "TimeDimensionName"),
                ("subject", "EntityRef"),
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
                "ms.time_dimension_column(name='log_date', entity=orders, "
                "column='log_date', granularity='day', parse=ms.strptime('%Y%m%d'))"
            ),
        ),
        _capability(
            "measure",
            "marivo.semantic._authoring_decorators.measure",
            "Declare a calculated measure on an entity.",
            output="MeasureRef",
            inputs=_inputs(
                ("mapping_key", "MeasureName"),
                ("subject", "EntityRef"),
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
            output="MeasureRef",
            inputs=_inputs(
                ("mapping_key", "MeasureName"),
                ("subject", "EntityRef"),
                ("dependency", "ColumnName"),
                ("dependency", "Additivity"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example="ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')",
        ),
        _capability(
            "aggregate",
            "marivo.semantic._authoring_declarations.aggregate",
            "Declare an aggregate metric from a measure.",
            output="MetricRef",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="MetricName"),
                AuthoringInputRequirement(role="subject", family="MeasureRef"),
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
            output="MetricRef",
            inputs=(
                AuthoringInputRequirement(role="mapping_key", family="MetricName"),
                AuthoringInputRequirement(role="subject", family="EntityRef"),
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
            "Build an AND equality filter for ms.count/aggregate (subset count/aggregate).",
            output="WhereFilter",
            inputs=_inputs(("subject", "FilterConditions")),
            effects=_NONE,
            constraints=("ref_shape",),
            example="ms.where(state='FAILED')",
            see_also=(_target("count"), _target("aggregate")),
        ),
        _capability(
            "cumulative",
            "marivo.semantic._authoring_metrics.cumulative",
            "Declare a cumulative derived metric.",
            output="MetricRef",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "MetricRef"),
                ("dependency", "AnchorSpec"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "cumulative_anchor"),
            example=(
                "mtd_revenue = ms.cumulative(name='mtd_revenue', base=revenue, "
                "anchor=ms.grain_to_date(grain='month'))"
            ),
        ),
        _capability(
            "ratio",
            "marivo.semantic._authoring_metrics.ratio",
            "Declare a recursively composable ratio metric; each lowered node is validated independently.",
            output="MetricRef",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "MetricRef"),
                ("dependency", "MetricRef"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "composition_shape"),
            example="profit_ratio = ms.ratio(name='profit_ratio', numerator=revenue, denominator=cost)",
        ),
        _capability(
            "weighted_average",
            "marivo.semantic._authoring_metrics.weighted_average",
            "Declare a recursively composable weighted-average metric with value/weight node checks.",
            output="MetricRef",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "MetricRef"),
                ("dependency", "WeightSpec"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "composition_shape"),
            example="avg_price = ms.weighted_average(name='avg_price', value=price, weight=volume)",
        ),
        _capability(
            "linear",
            "marivo.semantic._authoring_metrics.linear",
            "Declare a recursively composable linear metric with commensurable term checks.",
            output="MetricRef",
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
            output="RelationshipRef",
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
            "ref",
            "marivo.semantic._authoring_values.ref",
            "Build a kind-qualified semantic reference.",
            output="SemanticRef",
            inputs=_inputs(("subject", "MetricName")),
            effects=_AUTHOR,
            constraints=("ref_shape",),
            example="ms.ref('metric.sales.revenue')",
        ),
        _capability(
            "metric",
            "marivo.semantic._authoring_declarations.metric",
            "Declare a base metric with an expression body.",
            output="MetricRef",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "EntityRef"),
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
            "Build a grain-to-date cumulative anchor specification.",
            output="GrainToDateSpec",
            inputs=_inputs(("dependency", "Granularity")),
            effects=_AUTHOR,
            constraints=("cumulative_anchor",),
            example="ms.grain_to_date(grain='month')",
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
        # verify_preview
        # ------------------------------------------------------------------
        _capability(
            "verify_object",
            "marivo.semantic.catalog.SemanticCatalog.verify_object",
            "Statically verify one loaded semantic object.",
            kind="method",
            output="VerifyResult",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "CatalogObject"),
            ),
            effects=_LOCAL,
            example="catalog.verify_object(revenue.ref)",
            produced_state="semantic.verified",
            required_states=_states("semantic.loaded"),
            public_entrypoint="catalog.verify_object",
        ),
        _capability(
            "preview",
            "marivo.semantic.catalog.SemanticCatalog.preview",
            "Run scoped data previews for one semantic object or an explicit batch.",
            kind="method",
            output="PreviewResult | PreviewBatchResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject", family="CatalogObject", min_count=1, max_count=None
                ),
                AuthoringInputRequirement(
                    role="evidence", family="DiscoverySnapshot", min_count=1, max_count=None
                ),
            ),
            effects=_PREVIEW,
            constraints=("backend_factory_available",),
            example="catalog.preview(refs=report.preview_required_refs, using=orders_snapshot)",
            preconditions=("semantic.loaded",),
            produced_state="semantic.previewed",
            required_states=_states("semantic.loaded"),
            repair_kinds=("reconnect",),
            public_entrypoint="catalog.preview",
        ),
        # ------------------------------------------------------------------
        # readiness
        # ------------------------------------------------------------------
        _capability(
            "readiness",
            "marivo.semantic.catalog.SemanticCatalog.readiness",
            "Certify loaded refs, including recursive metric graph lowering and fixed v1 budgets.",
            kind="method",
            output="ReadinessReport",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "SemanticRef"),
            ),
            effects=_LOCAL,
            example="catalog.readiness()",
            preconditions=("semantic.loaded",),
            produced_state="semantic.ready",
            required_states=_states("semantic.loaded"),
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
                ("subject", "MetricRef"),
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
        # help capabilities
        # ------------------------------------------------------------------
        _capability(
            "help",
            "marivo.semantic.help.help",
            "Render the semantic help surface or one target.",
            output="Text",
            inputs=(_optional_input("subject", "HelpTarget"),),
            example="ms.help()",
        ),
        _capability(
            "help_text",
            "marivo.semantic.help.help_text",
            "Return semantic help as plain text.",
            output="Text",
            inputs=(_optional_input("subject", "HelpTarget"),),
            example="ms.help_text('load')",
        ),
        # ------------------------------------------------------------------
        # SemanticCatalog methods
        # ------------------------------------------------------------------
        _capability(
            "SemanticCatalog.get",
            "marivo.semantic.catalog.SemanticCatalog.get",
            "Get one catalog object by semantic ref.",
            kind="method",
            output="CatalogObject",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "SemanticRef"),
            ),
            effects=_LOCAL,
            example="catalog.get('metric.sales.revenue')",
            public_entrypoint="catalog.get",
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
                "measure",
                "measure_column",
                "aggregate",
                "count",
                "cumulative",
                "ratio",
                "weighted_average",
                "linear",
                "relationship",
                "join_on",
                "from_sql",
                "ref",
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
            "verify_preview": ("verify_object", "preview"),
            "readiness": ("readiness",),
            "diagnostics_boundaries": ("richness", "parity_check", "help", "help_text"),
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
    )


REGISTRY = _build_registry()


def _type_contracts() -> Mapping[type, SemanticTypeContract]:
    """Build private type contracts without exposing constructors as help targets."""
    from marivo.refs import SemanticRef, SymbolKind
    from marivo.semantic.catalog import (
        CatalogCollection,
        CatalogObject,
        Datasource,
        DatasourceDetails,
        DerivedMetricDetails,
        Dimension,
        DimensionDetails,
        Domain,
        DomainDetails,
        Entity,
        EntityDetails,
        Measure,
        MeasureDetails,
        Metric,
        Relationship,
        RelationshipDetails,
        SemanticCatalog,
        SimpleMetricDetails,
        TimeDimension,
        TimeDimensionDetails,
    )
    from marivo.semantic.dtos import PreviewBatchResult, VerifyResult
    from marivo.semantic.ir import JoinKey, SqlProvenance
    from marivo.semantic.parity import ParityResult
    from marivo.semantic.readiness import (
        ReadinessInputSummary,
        ReadinessIssue,
        ReadinessReport,
    )
    from marivo.semantic.refs import (
        DimensionRef,
        DomainRef,
        EntityRef,
        MeasureRef,
        MetricRef,
        RelationshipRef,
        TimeDimensionRef,
    )
    from marivo.semantic.richness import RichnessReport

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
        state_bearing: bool = False,
    ) -> None:
        contracts[cls] = SemanticTypeContract(
            name=name,
            producers=tuple(_target(value) for value in producers),
            public_properties=properties,
            public_methods=methods,
            consumers=tuple(_target(value) for value in consumers),
            state_bearing=state_bearing,
        )

    # Catalog types
    add(
        SemanticCatalog,
        "SemanticCatalog",
        ("load",),
        methods=("get", "verify_object", "preview", "readiness", "contract"),
        state_bearing=True,
    )
    add(
        CatalogObject,
        "CatalogObject",
        ("SemanticCatalog.get",),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        CatalogCollection,
        "CatalogCollection",
        (),
        methods=show_render,
    )
    add(
        Domain,
        "Domain",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        DomainDetails,
        "DomainDetails",
        (),
        methods=show_render,
    )
    add(
        Entity,
        "Entity",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        EntityDetails,
        "EntityDetails",
        (),
        methods=show_render,
    )
    add(
        Dimension,
        "Dimension",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        DimensionDetails,
        "DimensionDetails",
        (),
        methods=show_render,
    )
    add(
        TimeDimension,
        "TimeDimension",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        TimeDimensionDetails,
        "TimeDimensionDetails",
        (),
        methods=show_render,
    )
    add(
        Measure,
        "Measure",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        MeasureDetails,
        "MeasureDetails",
        (),
        methods=show_render,
    )
    add(
        Metric,
        "Metric",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
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
        Relationship,
        "Relationship",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        RelationshipDetails,
        "RelationshipDetails",
        (),
        methods=show_render,
    )
    add(
        Datasource,
        "Datasource",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        DatasourceDetails,
        "DatasourceDetails",
        (),
        methods=show_render,
    )
    # Result types
    add(
        VerifyResult,
        "VerifyResult",
        ("verify_object",),
        methods=("show", "contract", "render"),
        state_bearing=True,
    )
    add(
        PreviewBatchResult,
        "PreviewBatchResult",
        ("preview",),
        properties=("status", "refs", "results"),
        methods=("show", "contract", "render"),
        state_bearing=True,
    )
    add(
        ReadinessReport,
        "ReadinessReport",
        ("readiness",),
        properties=("preview_required_refs",),
        methods=("show", "contract", "render"),
        state_bearing=True,
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
    # Ref types
    add(
        SemanticRef,
        "SemanticRef",
        ("ref",),
    )
    add(
        DomainRef,
        "DomainRef",
        ("domain",),
    )
    add(
        EntityRef,
        "EntityRef",
        ("entity",),
    )
    add(
        DimensionRef,
        "DimensionRef",
        ("dimension", "dimension_column"),
    )
    add(
        TimeDimensionRef,
        "TimeDimensionRef",
        ("time_dimension", "time_dimension_column"),
    )
    add(
        MeasureRef,
        "MeasureRef",
        ("measure", "measure_column"),
    )
    add(
        MetricRef,
        "MetricRef",
        ("aggregate", "count", "cumulative", "ratio", "weighted_average", "linear", "metric"),
    )
    add(
        RelationshipRef,
        "RelationshipRef",
        ("relationship",),
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
        SymbolKind,
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
