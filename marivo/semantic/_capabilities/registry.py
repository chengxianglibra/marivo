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
        "Ref[dimension | time_dimension]",
        "Ref[dimension | time_dimension | measure]",
        "Ref | RuntimeMetricExpression",
        "CatalogEntry",
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
        "EventName",
        "ColumnName",
        "TableName",
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
        "EntityAlias",
        "Participant",
        "ParticipantRoleHandle",
    }
)

OUTPUT_FAMILIES = frozenset(
    {
        "SemanticCatalog",
        "CatalogEntry",
        "VerifyResult",
        "PreviewBatchResult",
        "PreviewResult",
        "ReadinessReport",
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
        "GrainToDateSpec",
        "TrailingSpec",
        "None",
        "Text",
        "WhereFilter",
        "IbisValue",
        "Participant",
        "ParticipantRoleHandle",
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
            see_also=(_target("load"), _target("verify"), _target("preview")),
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
            example="ms.domain(name='sales', owner='Mina Zhang')",
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
                "warehouse = md.duckdb('warehouse', path='warehouse.duckdb'); "
                "orders = ms.entity(name='orders', datasource=warehouse.ref, source=md.table('orders'))"
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
            example="ms.dimension_column(name='region', entity=orders, column='region')",
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
                "ms.time_dimension_column(name='log_date', entity=orders, "
                "column='log_date', granularity='day', parse=ms.strptime('%Y%m%d'))"
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
            example="ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')",
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
            output="Ref[metric]",
            inputs=_inputs(
                ("mapping_key", "MetricName"),
                ("subject", "Ref[metric]"),
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
            example="ms.bind(amount, orders)",
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
            "verify",
            "marivo.semantic.catalog.SemanticCatalog.verify",
            "Statically verify one exact loaded ref.",
            kind="method",
            output="VerifyResult",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "Ref"),
            ),
            effects=_LOCAL,
            example="catalog.verify(revenue.ref)",
            produced_state="semantic.verified",
            required_states=_states("semantic.loaded"),
            public_entrypoint="catalog.verify",
        ),
        _capability(
            "preview",
            "marivo.semantic.catalog.SemanticCatalog.preview",
            "Run one scoped data preview for an exact loaded ref.",
            kind="method",
            output="PreviewResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(role="subject", family="Ref"),
                AuthoringInputRequirement(
                    role="evidence", family="DiscoverySnapshot", min_count=1, max_count=None
                ),
            ),
            effects=_PREVIEW,
            constraints=("backend_factory_available",),
            example="catalog.preview(revenue.ref, using=orders_snapshot)",
            preconditions=("semantic.loaded",),
            produced_state="semantic.previewed",
            required_states=_states("semantic.loaded"),
            repair_kinds=("reconnect",),
            public_entrypoint="catalog.preview",
        ),
        _capability(
            "preview_many",
            "marivo.semantic.catalog.SemanticCatalog.preview_many",
            "Run scoped data previews for a non-empty exact ref sequence.",
            kind="method",
            output="PreviewBatchResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject", family="Ref", min_count=1, max_count=None
                ),
                AuthoringInputRequirement(
                    role="evidence", family="DiscoverySnapshot", min_count=1, max_count=None
                ),
            ),
            effects=_PREVIEW,
            constraints=("backend_factory_available",),
            example="catalog.preview_many(report.preview_required_refs, using=orders_snapshot)",
            preconditions=("semantic.loaded",),
            produced_state="semantic.previewed",
            required_states=_states("semantic.loaded"),
            repair_kinds=("reconnect",),
            public_entrypoint="catalog.preview_many",
        ),
        # ------------------------------------------------------------------
        # readiness
        # ------------------------------------------------------------------
        _capability(
            "readiness",
            "marivo.semantic.catalog.SemanticCatalog.readiness",
            "Certify loaded refs or runtime metric expressions through governed leaves and fixed graph budgets.",
            kind="method",
            output="ReadinessReport",
            inputs=_inputs(
                ("receiver", "SemanticCatalog"),
                ("subject", "Ref | RuntimeMetricExpression"),
            ),
            effects=_LOCAL,
            example="catalog.readiness(refs=[revenue, runtime_revenue])",
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
                "weighted_mean",
                "linear",
                "relationship",
                "event",
                "participant",
                "participant_role",
                "all_rows",
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
            "verify_preview": ("verify", "preview", "preview_many"),
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
    from marivo.refs import Ref, SemanticKind
    from marivo.refs import ref as ref_factory
    from marivo.semantic.catalog import (
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
        RelationshipDetails,
        RelationshipEntry,
        SemanticCatalog,
        SimpleMetricDetails,
        TimeDimensionDetails,
        TimeDimensionEntry,
    )
    from marivo.semantic.dtos import PreviewBatchResult, VerifyResult
    from marivo.semantic.ir import JoinKey, SqlProvenance
    from marivo.semantic.parity import ParityResult
    from marivo.semantic.readiness import (
        ReadinessInputSummary,
        ReadinessIssue,
        ReadinessReport,
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
        methods=("require", "verify", "preview", "preview_many", "readiness", "contract"),
        state_bearing=True,
    )
    add(
        CatalogEntry,
        "CatalogEntry",
        ("SemanticCatalog.require",),
        properties=("ref",),
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
        DomainEntry,
        "DomainEntry",
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
        EntityEntry,
        "EntityEntry",
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
        DimensionEntry,
        "DimensionEntry",
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
        TimeDimensionEntry,
        "TimeDimensionEntry",
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
        MeasureEntry,
        "MeasureEntry",
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
        MetricEntry,
        "MetricEntry",
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
        RelationshipEntry,
        "RelationshipEntry",
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
        EventEntry,
        "EventEntry",
        (),
        methods=("details", "show", "contract", "render"),
        state_bearing=True,
    )
    add(
        EventDetails,
        "EventDetails",
        (),
        methods=show_render,
    )
    add(
        DatasourceEntry,
        "DatasourceEntry",
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
        ("verify",),
        methods=("show", "contract", "render"),
        state_bearing=True,
    )
    add(
        PreviewBatchResult,
        "PreviewBatchResult",
        ("preview_many",),
        properties=("status", "refs", "results"),
        methods=("show", "contract", "render"),
        state_bearing=True,
    )
    add(
        ReadinessReport,
        "ReadinessReport",
        ("readiness",),
        properties=("analysis_ready_refs", "analysis_ready_inputs", "preview_required_refs"),
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
        ),
        properties=("kind", "path", "key", "name"),
        consumers=(
            "SemanticCatalog.require",
            "verify",
            "preview",
            "preview_many",
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
        ),
    )
    from marivo.semantic.event import Participant, ParticipantRoleHandle

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
