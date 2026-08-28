"""Closed registry and consumed-type catalog for ``marivo.semantic``."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, get_args

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
    SEMANTIC_HELP_RENDER_BUDGETS,
    AuthoringSourceContract,
    ConstructionMode,
    SemanticBuilderTopic,
    SemanticCapabilityRegistry,
    SemanticCheckRoute,
    SemanticCheckTopic,
    SemanticHelpDescriptor,
    SemanticHelpRenderBudget,
    SemanticHelpRenderClass,
    SemanticNavigationRoute,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectDecision,
    SemanticObjectIndexEntry,
    SemanticObjectRelationship,
    SemanticRepairContract,
    SemanticRootGroup,
    SemanticRootSection,
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
        "NotNullSourceCheck",
        "AllowedValuesSourceCheck",
        "UniqueSourceCheck",
        "FreshnessSourceCheck",
        "RelationshipMatchesSourceCheck",
        "RelationshipCardinalitySourceCheck",
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
        "JoinKey",
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
        "SourceScalarSequence",
        "Duration",
        "RelationshipSide",
        "RelationshipCardinality",
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
        "NotNullSourceCheck",
        "AllowedValuesSourceCheck",
        "UniqueSourceCheck",
        "FreshnessSourceCheck",
        "RelationshipMatchesSourceCheck",
        "RelationshipCardinalitySourceCheck",
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


def _navigation_route(
    label: str,
    target: LiveHelpTarget,
    *,
    summary: str | None = None,
    owns_discovery: bool = True,
) -> SemanticNavigationRoute:
    return SemanticNavigationRoute(
        label=label,
        target=target,
        summary=summary,
        owns_discovery=owns_discovery,
    )


def _inputs(
    *families: tuple[AuthoringInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: AuthoringInputRole, family: str) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(role=role, family=family, min_count=0)


def _parameter_input(
    role: AuthoringInputRole,
    family: str,
    *parameter_names: str,
    optional: bool = False,
) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(
        role=role,
        family=family,
        parameter_names=parameter_names,
        min_count=0 if optional else 1,
    )


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
    invocation_shape: Literal["direct", "decorator"] = "direct",
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
        invocation_shape=invocation_shape,
    )


def _authoring_source_contract(kind: SemanticKind) -> AuthoringSourceContract:
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
        catalog_collection=member.property_name,
        canonical_identity_template=identity_template,
    )


def _source_contracts() -> Mapping[str, AuthoringSourceContract]:
    """Build closed placement/handoff facts for every source-authored object."""

    by_kind: dict[SemanticKind, AuthoringSourceContract] = {
        kind: _authoring_source_contract(kind)
        for kind in SemanticKind
        if kind is not SemanticKind.DATASOURCE
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


def _decision(
    decision_id: str,
    question: str,
    determine_from: str,
    basis: Literal["source_evidence", "business_authority", "source_and_business"],
    *next_target_ids: str,
    does_not_establish: str | None = None,
    unsupported_reason: str | None = None,
) -> SemanticObjectDecision:
    """Build one bounded object decision without storing project observations."""

    supported = unsupported_reason is None
    return SemanticObjectDecision(
        decision_id=decision_id,
        question=question,
        determine_from=determine_from,
        basis=basis,
        encoding_status="supported" if supported else "unsupported",
        next_targets=tuple(_target(target_id) for target_id in next_target_ids),
        does_not_establish=does_not_establish,
        unsupported_reason=unsupported_reason,
    )


def _business_decision(
    decision_id: str,
    question: str,
    *next_target_ids: str,
) -> SemanticObjectDecision:
    return _decision(
        decision_id,
        question,
        "Use current business authority or an approved attributable project definition.",
        "business_authority",
        *next_target_ids,
    )


def _source_decision(
    decision_id: str,
    question: str,
    *next_target_ids: str,
) -> SemanticObjectDecision:
    return _decision(
        decision_id,
        question,
        "Use the current source contract and authoritative physical metadata.",
        "source_evidence",
        *next_target_ids,
    )


def _source_business_decision(
    decision_id: str,
    question: str,
    *next_target_ids: str,
) -> SemanticObjectDecision:
    return _decision(
        decision_id,
        question,
        (
            "Use current source evidence to constrain legal encodings and current business "
            "authority to settle reusable meaning."
        ),
        "source_and_business",
        *next_target_ids,
        does_not_establish=(
            "Names, physical types, sample values, and observed distributions do not establish "
            "reusable business meaning."
        ),
    )


def _relationship(
    relation: Literal["owned_by", "requires", "may_reference", "inferred_from", "consumed_by"],
    target_id: str,
    explanation: str,
    *,
    surface: Literal["semantic", "datasource", "analysis", "ontology"] = "semantic",
) -> SemanticObjectRelationship:
    return SemanticObjectRelationship(
        relation=relation,
        target=LiveHelpTarget(surface=surface, canonical_id=target_id),
        explanation=explanation,
    )


def _mode(
    intent: str,
    role: Literal["default", "alternative", "escape_hatch"],
    target_id: str,
) -> ConstructionMode:
    return ConstructionMode(intent=intent, role=role, target=_target(target_id))


def _object_contract(
    kind: SemanticKind,
    summary: str,
    *,
    decisions: tuple[SemanticObjectDecision, ...],
    construction_modes: tuple[ConstructionMode, ...],
    relationships: tuple[SemanticObjectRelationship, ...],
    supporting: tuple[str, ...],
    checks: tuple[str, ...],
) -> SemanticObjectContract:
    source_contract = _authoring_source_contract(kind)
    return SemanticObjectContract(
        canonical_id=f"objects.{kind.value}",
        summary=summary,
        semantic_kind=kind,
        ref_target=_target(f"ref.{kind.value}"),
        catalog_collection=source_contract.catalog_collection,
        placement_kind=source_contract.placement_kind,
        decisions=decisions,
        construction_modes=construction_modes,
        relationships=relationships,
        supporting_targets=tuple(_target(target_id) for target_id in supporting),
        check_targets=tuple(_target(target_id) for target_id in checks),
    )


def _object_contracts() -> tuple[SemanticObjectContract, ...]:
    """Build the complete Slice 2 semantic object graph in teaching order."""

    return (
        _object_contract(
            SemanticKind.DOMAIN,
            "Business namespace and accountability boundary.",
            decisions=(
                _business_decision(
                    "business_boundary",
                    "What business boundary does this Domain own?",
                    "domain",
                    "ai_context",
                ),
                _business_decision(
                    "accountable_owner",
                    "Who is accountable for this Domain's semantic correctness?",
                    "domain",
                ),
                _business_decision(
                    "default_domain_behavior",
                    "May sibling declarations omit an explicit Domain ref?",
                    "domain",
                ),
                _business_decision(
                    "definition_guardrails",
                    "Which reusable definition and guardrails apply to the Domain?",
                    "ai_context",
                ),
            ),
            construction_modes=(
                _mode("Declare one governed business namespace.", "default", "domain"),
            ),
            relationships=(
                _relationship(
                    "consumed_by", "objects.entity", "Entities are authored inside one Domain."
                ),
            ),
            supporting=("ai_context",),
            checks=("load", "readiness"),
        ),
        _object_contract(
            SemanticKind.ENTITY,
            "Reusable business entity or fact-set identity backed by a source.",
            decisions=(
                _business_decision(
                    "recordset_meaning",
                    "What reusable record set does this Entity represent?",
                    "entity",
                    "ai_context",
                ),
                _source_business_decision(
                    "authoritative_source",
                    "Which governed datasource and physical source are authoritative?",
                    "entity",
                ),
                _business_decision(
                    "row_grain", "What does one Entity row represent?", "ai_context"
                ),
                _source_business_decision(
                    "identity_key", "Which fields form the stable row identity?", "entity"
                ),
                _source_business_decision(
                    "history_as_of_model",
                    "Is the Entity current, snapshot-versioned, or validity-versioned?",
                    "entity",
                    "snapshot",
                    "validity",
                ),
                _business_decision("domain_ownership", "Which Domain owns this Entity?", "entity"),
            ),
            construction_modes=(_mode("Declare a datasource-backed Entity.", "default", "entity"),),
            relationships=(
                _relationship("owned_by", "objects.domain", "Every Entity belongs to one Domain."),
                _relationship(
                    "requires",
                    "authoring",
                    "An Entity requires a registered datasource and source descriptor.",
                    surface="datasource",
                ),
                _relationship(
                    "consumed_by", "objects.dimension", "Dimensions are owned by an Entity."
                ),
                _relationship(
                    "consumed_by",
                    "objects.time_dimension",
                    "TimeDimensions are owned by an Entity.",
                ),
                _relationship("consumed_by", "objects.measure", "Measures are owned by an Entity."),
            ),
            supporting=("snapshot", "validity", "ai_context"),
            checks=("load", "readiness", "preview", "source_health"),
        ),
        _object_contract(
            SemanticKind.DIMENSION,
            "Categorical field used for grouping, filtering, identity, or joins.",
            decisions=(
                _business_decision(
                    "owning_entity",
                    "Which Entity owns this Dimension?",
                    "dimension_column",
                    "dimension",
                ),
                _business_decision(
                    "dimension_meaning",
                    "What reusable categorical, identity, filter, or join meaning does it carry?",
                    "ai_context",
                ),
                _source_business_decision(
                    "code_null_semantics",
                    "What do physical codes and nulls mean?",
                    "dimension_column",
                    "dimension",
                    "ai_context",
                ),
                _source_decision(
                    "construction_mode",
                    "Can a direct column preserve the meaning, or is a normalized expression required?",
                    "dimension_column",
                    "dimension",
                ),
            ),
            construction_modes=(
                _mode("Bind one physical column directly.", "default", "dimension_column"),
                _mode("Use one restricted row-level Ibis expression.", "escape_hatch", "dimension"),
            ),
            relationships=(
                _relationship(
                    "owned_by", "objects.entity", "The Entity ref fixes field ownership."
                ),
                _relationship(
                    "consumed_by", "objects.relationship", "Dimensions may form join keys."
                ),
                _relationship(
                    "consumed_by", "objects.event", "Dimensions may identify Event occurrences."
                ),
            ),
            supporting=("ai_context", "bind"),
            checks=(
                "load",
                "readiness",
                "preview",
                "source_check.not_null",
                "source_check.allowed_values",
                "source_check.unique",
                "source_health",
            ),
        ),
        _object_contract(
            SemanticKind.TIME_DIMENSION,
            "Explicit business time axis with grain and parse semantics.",
            decisions=(
                _business_decision(
                    "owning_entity",
                    "Which Entity owns this time axis?",
                    "time_dimension_column",
                    "time_dimension",
                ),
                _business_decision(
                    "business_time_role",
                    "Which business time does this axis represent?",
                    "ai_context",
                ),
                _source_business_decision(
                    "granularity",
                    "What is the finest meaningful business grain?",
                    "time_dimension_column",
                    "time_dimension",
                ),
                _source_business_decision(
                    "physical_time_encoding",
                    "How are physical values encoded, parsed, and localized?",
                    "time_dimension_column",
                    "time_dimension",
                    "datetime",
                    "timestamp",
                    "strptime",
                    "hour_prefix",
                ),
                _business_decision(
                    "default_axis",
                    "Should this be the Entity's default analysis time axis?",
                    "time_dimension_column",
                    "time_dimension",
                ),
                _source_business_decision(
                    "sampled_cadence",
                    "Does the source represent a fixed-cadence sampled series?",
                    "datetime",
                    "timestamp",
                ),
            ),
            construction_modes=(
                _mode(
                    "Bind one physical time column directly.", "default", "time_dimension_column"
                ),
                _mode(
                    "Use one restricted row-level Ibis expression.",
                    "escape_hatch",
                    "time_dimension",
                ),
            ),
            relationships=(
                _relationship(
                    "owned_by", "objects.entity", "The Entity ref fixes time-axis ownership."
                ),
            ),
            supporting=("datetime", "timestamp", "strptime", "hour_prefix", "ai_context", "bind"),
            checks=(
                "load",
                "readiness",
                "preview",
                "source_check.not_null",
                "source_check.allowed_values",
                "source_check.unique",
                "source_check.freshness",
                "source_health",
            ),
        ),
        _object_contract(
            SemanticKind.MEASURE,
            "Row-level numeric fact owning unit and additivity.",
            decisions=(
                _business_decision(
                    "numeric_fact_grain",
                    "What row-level numeric fact and grain does this Measure represent?",
                    "ai_context",
                ),
                _business_decision(
                    "unit", "What physical unit does the value carry?", "measure_column", "measure"
                ),
                _business_decision(
                    "dimensional_additivity",
                    "How may values aggregate across business dimensions?",
                    "measure_column",
                    "measure",
                    "semi_additive",
                ),
                _business_decision(
                    "temporal_additivity",
                    "How may values aggregate across time?",
                    "measure_column",
                    "measure",
                    "semi_additive",
                ),
                _source_business_decision(
                    "semi_additive_axis_fold",
                    "If semi-additive, which status axis and fold are authoritative?",
                    "semi_additive",
                ),
                _source_decision(
                    "construction_mode",
                    "Can a direct numeric column preserve the meaning, or is an expression required?",
                    "measure_column",
                    "measure",
                ),
            ),
            construction_modes=(
                _mode("Bind one physical numeric column directly.", "default", "measure_column"),
                _mode("Use one restricted row-level Ibis expression.", "escape_hatch", "measure"),
            ),
            relationships=(
                _relationship("owned_by", "objects.entity", "The Entity ref fixes fact ownership."),
            ),
            supporting=("semi_additive", "ai_context", "bind"),
            checks=("load", "readiness", "preview", "source_check.not_null", "source_health"),
        ),
        _object_contract(
            SemanticKind.METRIC,
            "Analyzable business value built from governed semantic inputs.",
            decisions=(
                _business_decision(
                    "population_value",
                    "Which population and business value does this Metric define?",
                    "ai_context",
                ),
                _source_business_decision(
                    "construction_mode",
                    "Which aggregate, count, composition, cumulative, or expression mode matches the definition?",
                    "aggregate",
                    "count",
                    "ratio",
                    "weighted_mean",
                    "linear",
                    "cumulative",
                    "metric",
                ),
                _business_decision(
                    "aggregation_filter",
                    "Which aggregation and governed population filter apply?",
                    "aggregate",
                    "count",
                    "where",
                ),
                _business_decision(
                    "denominator_failure",
                    "What should happen when a denominator or required input is unavailable?",
                    "ratio",
                    "metric",
                ),
                _source_business_decision(
                    "root_fanout",
                    "Which root Entity and fanout policy preserve the intended population?",
                    "metric",
                ),
                _business_decision(
                    "unit_additivity",
                    "Which unit and additivity semantics apply to the result?",
                    "aggregate",
                    "count",
                    "ratio",
                    "weighted_mean",
                    "linear",
                    "metric",
                ),
                _business_decision(
                    "temporal_behavior",
                    "Is the Metric ordinary, folded, cumulative, or trailing over time?",
                    "aggregate",
                    "cumulative",
                    "grain_to_date",
                    "trailing",
                ),
                _business_decision(
                    "provenance",
                    "Does governed SQL provenance require parity evidence?",
                    "from_sql",
                ),
                _business_decision(
                    "guardrails",
                    "Which reusable exclusions and interpretation guardrails apply?",
                    "ai_context",
                ),
            ),
            construction_modes=(
                _mode("Aggregate one Measure.", "default", "aggregate"),
                _mode("Count Entity rows.", "default", "count"),
                _mode("Divide two Metrics.", "alternative", "ratio"),
                _mode("Compute a weighted mean from Measures.", "alternative", "weighted_mean"),
                _mode("Add or subtract commensurable Metrics.", "alternative", "linear"),
                _mode("Accumulate one Metric over governed time.", "alternative", "cumulative"),
                _mode("Use one restricted Ibis expression body.", "escape_hatch", "metric"),
            ),
            relationships=(
                _relationship(
                    "requires", "objects.measure", "Aggregate Metrics consume one governed Measure."
                ),
                _relationship(
                    "may_reference",
                    "objects.entity",
                    "Count and expression Metrics may name Entities.",
                ),
                _relationship(
                    "may_reference", "objects.metric", "Derived Metrics compose other Metrics."
                ),
            ),
            supporting=("where", "from_sql", "grain_to_date", "trailing", "ai_context", "bind"),
            checks=("load", "readiness", "preview", "parity_check"),
        ),
        _object_contract(
            SemanticKind.RELATIONSHIP,
            "Executable directed join contract between Entities.",
            decisions=(
                _business_decision(
                    "directed_meaning",
                    "What directed business relationship connects the endpoints?",
                    "relationship",
                    "ai_context",
                ),
                _business_decision(
                    "endpoint_grains",
                    "What row grain does each endpoint Entity carry?",
                    "relationship",
                    "ai_context",
                ),
                _source_business_decision(
                    "join_key_equivalence",
                    "Which field pairs encode the same business identity?",
                    "relationship",
                    "join_on",
                ),
                _source_business_decision(
                    "multiplicity_fanout",
                    "Which multiplicity and fanout implications are expected?",
                    "relationship",
                    "ai_context",
                ),
                _source_decision(
                    "evidence_checks",
                    "Which source checks are required to test those claims?",
                    "source_check.relationship_matches",
                    "source_check.relationship_cardinality",
                    "source_health",
                ),
            ),
            construction_modes=(
                _mode("Declare one directed Entity join contract.", "default", "relationship"),
            ),
            relationships=(
                _relationship(
                    "requires", "objects.entity", "Both directed endpoints are exact Entity refs."
                ),
                _relationship(
                    "consumed_by",
                    "objects.event",
                    "Participant paths traverse directed Relationships.",
                ),
            ),
            supporting=("join_on", "ai_context"),
            checks=(
                "load",
                "readiness",
                "source_check.relationship_matches",
                "source_check.relationship_cardinality",
                "source_health",
            ),
        ),
        _object_contract(
            SemanticKind.EVENT,
            "Reusable occurrence with identity, occurrence time, and participants.",
            decisions=(
                _business_decision(
                    "occurrence_predicate",
                    "Which business occurrence and row predicate define this Event?",
                    "event",
                    "all_rows",
                    "ai_context",
                ),
                _source_business_decision(
                    "occurrence_identity",
                    "Which Dimensions uniquely identify an occurrence?",
                    "event",
                ),
                _business_decision(
                    "occurrence_time",
                    "Which TimeDimension is the business occurrence time?",
                    "event",
                ),
                _business_decision(
                    "participant_roles",
                    "Which named business participants belong to each occurrence?",
                    "event",
                    "participant",
                ),
                _source_business_decision(
                    "directed_paths",
                    "Which directed Relationship paths reach each participant?",
                    "participant",
                ),
                _business_decision(
                    "participant_cardinality",
                    "What cardinality does each participant role guarantee?",
                    "participant",
                ),
            ),
            construction_modes=(
                _mode("Declare one filtered or explicit all-rows occurrence.", "default", "event"),
            ),
            relationships=(
                _relationship(
                    "inferred_from",
                    "objects.entity",
                    "The occurred_at field owner determines the source Entity.",
                ),
                _relationship(
                    "may_reference",
                    "objects.relationship",
                    "Participant roles may traverse directed Relationship paths.",
                ),
                _relationship(
                    "consumed_by",
                    "objects.state_model",
                    "Events may trigger StateModel transitions.",
                ),
            ),
            supporting=("all_rows", "participant", "ai_context"),
            checks=("load", "readiness", "preview"),
        ),
        _object_contract(
            SemanticKind.STATE_MODEL,
            "Closed normative lifecycle for one subject Entity.",
            decisions=(
                _business_decision(
                    "subject_lifecycle",
                    "Which subject Entity lifecycle is governed?",
                    "state_model",
                    "ai_context",
                ),
                _business_decision(
                    "state_vocabulary",
                    "Which closed business-state vocabulary applies?",
                    "lifecycle_state",
                    "state_model",
                ),
                _business_decision(
                    "initial_terminal_meaning",
                    "Which states are initial or terminal and what do they mean?",
                    "lifecycle_state",
                    "ai_context",
                ),
                _business_decision(
                    "inception_transitions",
                    "Which Event establishes lifecycle inception?",
                    "inception",
                    "state_model",
                ),
                _business_decision(
                    "deterministic_transitions",
                    "Which exact Event triggers each state transition?",
                    "transition",
                    "participant_role",
                    "state_model",
                ),
                _decision(
                    "excluded_replay_policies",
                    "Which replay, seed, ordering, or violation policies are deliberately excluded?",
                    "StateModel owns normative lifecycle meaning only; analysis owns replay policy.",
                    "business_authority",
                    unsupported_reason=(
                        "The current StateModel object does not encode replay policy; use the exact "
                        "analysis lifecycle contract when replaying a model."
                    ),
                ),
            ),
            construction_modes=(
                _mode("Declare one finite normative lifecycle.", "default", "state_model"),
            ),
            relationships=(
                _relationship(
                    "owned_by", "objects.entity", "The subject ref fixes lifecycle ownership."
                ),
                _relationship(
                    "requires",
                    "objects.event",
                    "Inceptions and transitions use exact Event triggers or participant handles.",
                ),
            ),
            supporting=(
                "lifecycle_state",
                "inception",
                "transition",
                "model_state",
                "participant_role",
                "ai_context",
            ),
            checks=("load", "readiness"),
        ),
        _object_contract(
            SemanticKind.PERIOD_CALENDAR,
            "Governed finite business-period hierarchy.",
            decisions=(
                _business_decision(
                    "calendar_convention",
                    "Which fiscal, retail, or operational calendar convention is authoritative?",
                    "period_calendar",
                    "ai_context",
                ),
                _source_business_decision(
                    "civil_date_authority",
                    "Which exhaustive civil-date spine is authoritative?",
                    "period_calendar",
                ),
                _business_decision(
                    "boundary_timezone",
                    "Which timezone owns civil-day boundaries?",
                    "period_calendar",
                ),
                _source_business_decision(
                    "finite_coverage",
                    "Which half-open civil-date coverage is complete?",
                    "period_calendar",
                ),
                _source_business_decision(
                    "level_key_meaning",
                    "What does each period level and key mean?",
                    "period_calendar",
                    "ai_context",
                ),
                _source_decision(
                    "containment_expectations",
                    "Which level containment relationships should certification derive?",
                    "period_calendar",
                    "preview",
                ),
                _business_decision(
                    "correspondence_conventions",
                    "Which named same-level baseline correspondences are authorized?",
                    "period_correspondence",
                    "period_calendar",
                ),
            ),
            construction_modes=(
                _mode("Declare one finite governed calendar.", "default", "period_calendar"),
            ),
            relationships=(
                _relationship(
                    "requires", "objects.time_dimension", "The civil-date spine is a TimeDimension."
                ),
                _relationship(
                    "requires",
                    "objects.dimension",
                    "Period levels and correspondence keys are Dimensions.",
                ),
            ),
            supporting=("period_correspondence", "ai_context"),
            checks=("load", "readiness", "preview", "source_health"),
        ),
        _object_contract(
            SemanticKind.TEMPORAL_SET,
            "Governed finite set of named temporal occurrences.",
            decisions=(
                _business_decision(
                    "occurrence_set_meaning",
                    "What named sparse or overlapping occurrences does this set govern?",
                    "temporal_set",
                    "ai_context",
                ),
                _source_business_decision(
                    "occurrence_identity",
                    "Which Dimension uniquely identifies each occurrence?",
                    "temporal_set",
                ),
                _source_business_decision(
                    "half_open_bounds",
                    "Which start and exclusive-end fields define each occurrence?",
                    "temporal_set",
                ),
                _source_business_decision(
                    "temporal_encoding",
                    "Are bounds civil dates or timestamps with explicit semantics?",
                    "temporal_set",
                ),
                _business_decision(
                    "boundary_timezone",
                    "Which timezone owns occurrence boundaries?",
                    "temporal_set",
                ),
                _source_business_decision(
                    "finite_coverage",
                    "Which half-open civil-date coverage is complete?",
                    "temporal_set",
                ),
                _business_decision(
                    "category",
                    "Does an optional category have reusable business meaning?",
                    "temporal_set",
                    "ai_context",
                ),
                _business_decision(
                    "overlap_gap_semantics",
                    "Are overlaps and gaps intentional for this occurrence set?",
                    "ai_context",
                ),
            ),
            construction_modes=(
                _mode("Declare one finite occurrence set.", "default", "temporal_set"),
            ),
            relationships=(
                _relationship(
                    "requires",
                    "objects.dimension",
                    "Occurrence identity and optional category are Dimensions.",
                ),
                _relationship(
                    "requires", "objects.time_dimension", "Occurrence bounds are TimeDimensions."
                ),
            ),
            supporting=("ai_context",),
            checks=("load", "readiness", "preview", "source_health"),
        ),
        _object_contract(
            SemanticKind.WORK_SCHEDULE,
            "Governed final daily working-status schedule.",
            decisions=(
                _business_decision(
                    "working_status_authority",
                    "Which business source owns final working status?",
                    "work_schedule",
                    "ai_context",
                ),
                _source_business_decision(
                    "date_boolean_meaning",
                    "Which civil-date and boolean fields encode final status?",
                    "work_schedule",
                ),
                _business_decision(
                    "boundary_timezone", "Which timezone owns workday boundaries?", "work_schedule"
                ),
                _source_business_decision(
                    "finite_coverage", "Which finite date coverage is exhaustive?", "work_schedule"
                ),
                _business_decision(
                    "rule_precedence",
                    "Which business rule precedence has already been resolved by the source?",
                    "ai_context",
                ),
            ),
            construction_modes=(
                _mode("Declare one final daily status schedule.", "default", "work_schedule"),
            ),
            relationships=(
                _relationship(
                    "requires", "objects.time_dimension", "The schedule date is a TimeDimension."
                ),
                _relationship(
                    "requires", "objects.dimension", "Final working status is a Dimension."
                ),
            ),
            supporting=("ai_context",),
            checks=("load", "readiness", "preview", "source_health"),
        ),
    )


def _builder_topics() -> tuple[SemanticBuilderTopic, ...]:
    """Build supporting-builder families in need-directed teaching order."""

    rows = (
        (
            "builders.entity_history",
            "Entity history",
            "Describe snapshot or validity-versioned Entity history.",
            ("snapshot", "validity"),
        ),
        (
            "builders.temporal_parsing",
            "Temporal parsing",
            "Parse physical date, datetime, timestamp, or hour-prefix values.",
            ("datetime", "timestamp", "strptime", "hour_prefix"),
        ),
        (
            "builders.field_metric_support",
            "Field and Metric support",
            "Build Field and Metric parameters, provenance, anchors, and expressions.",
            ("where", "semi_additive", "bind", "from_sql", "grain_to_date", "trailing"),
        ),
        (
            "builders.relationship_event",
            "Relationship and Event support",
            "Build join keys, participants, participant handles, and all-row predicates.",
            ("join_on", "participant", "participant_role", "all_rows"),
        ),
        (
            "builders.state_model",
            "StateModel support",
            "Build local lifecycle states, triggers, transitions, and typed state handles.",
            ("lifecycle_state", "inception", "transition", "model_state"),
        ),
        (
            "builders.governed_temporal",
            "Governed temporal support",
            "Build period correspondences and governed calendar grains.",
            ("period_correspondence", "calendar_grain"),
        ),
    )
    return tuple(
        SemanticBuilderTopic(
            canonical_id=canonical_id,
            label=label,
            summary=summary,
            members=tuple(_target(member) for member in members),
        )
        for canonical_id, label, summary, members in rows
    )


def _check_topic() -> SemanticCheckTopic:
    """Build proof/non-proof routing for semantic inspection and checks."""

    def route(
        question: str,
        targets: tuple[LiveHelpTarget, ...],
        proves: str,
        does_not_prove: str,
    ) -> SemanticCheckRoute:
        return SemanticCheckRoute(
            question=question,
            targets=targets,
            proves=proves,
            does_not_prove=does_not_prove,
        )

    def datasource(canonical_id: str) -> LiveHelpTarget:
        return LiveHelpTarget(surface="datasource", canonical_id=canonical_id)

    return SemanticCheckTopic(
        canonical_id="checks",
        summary="Choose inspection, readiness, preview, health, parity, or richness by proof need.",
        routes=(
            route(
                "What physical source, schema, columns, and types exist?",
                (datasource("inspect"),),
                "Current authoritative physical metadata to the backend's supported extent.",
                "Reusable business meaning.",
            ),
            route(
                "Do I need bounded sampled rows or source-specific SQL evidence?",
                (datasource("authoring"),),
                "Explicitly scoped or governed physical observations.",
                "Semantic validity or typed-analysis authority.",
            ),
            route(
                "Do project sources execute, resolve refs, and compile as one project?",
                (_target("load"),),
                "Static project assembly and structural validation.",
                "Current external health or operation-shaped executability.",
            ),
            route(
                "Is this exact requested dependency closure statically ready for analysis?",
                (_target("readiness"),),
                "Governed semantic closure and analysis_ready_inputs.",
                "Successful execution of every future analysis shape.",
            ),
            route(
                "What does this entry produce under one explicit authoring scope?",
                (_target("preview"), _target("preview_many")),
                "A bounded current runtime observation for the exact requested scope.",
                "Persistent certification or readiness mutation.",
            ),
            route(
                "How do I declare an exact source expectation?",
                (_target("source_check"),),
                "The expectation is explicit, typed, and closed.",
                "That the current source satisfies it.",
            ),
            route(
                "Does the current source still satisfy explicit schema or data expectations?",
                (_target("source_health"),),
                "Ephemeral current source evidence for declared checks.",
                "Business approval or readiness mutation.",
            ),
            route(
                "Does a Metric agree with its governed SQL provenance?",
                (_target("parity_check"),),
                "The exact parity result for the declared comparison.",
                "General correctness outside that comparison.",
            ),
            route(
                "Is the semantic project rich enough for current demand?",
                (_target("richness"),),
                "Demand-ranked advisory gaps.",
                "A readiness blocker or execution failure.",
            ),
        ),
    )


def _navigation_descriptors(
    object_contracts: tuple[SemanticObjectContract, ...],
    builder_topics: tuple[SemanticBuilderTopic, ...],
) -> tuple[SemanticHelpDescriptor, ...]:
    """Build the active Slice 3 navigation topology."""

    objects = SemanticNavigationTopic(
        canonical_id="objects",
        summary="Browse semantic object kinds, relationships, decisions, and construction modes.",
        members=tuple(SemanticObjectIndexEntry(contract) for contract in object_contracts),
        member_heading="Object kinds",
    )
    builders = SemanticNavigationTopic(
        canonical_id="builders",
        summary="Choose a supporting builder by the parameter or typed-handle problem it solves.",
        members=(
            _navigation_route("exact typed semantic identity", _target("ref")),
            _navigation_route("shared authoring rationale", _target("ai_context")),
            *(
                _navigation_route(topic.label, _target(topic.canonical_id), summary=topic.summary)
                for topic in builder_topics
            ),
        ),
    )
    checks = _check_topic()
    authoring = SemanticNavigationTopic(
        canonical_id="authoring",
        summary="Choose semantic objects, supporting builders, checks, or current catalog access.",
        members=(
            _navigation_route("object meaning and construction", _target("objects")),
            _navigation_route("supporting parameter or handle", _target("builders")),
            _navigation_route("inspection or proof need", _target("checks")),
            _navigation_route("load current project", _target("load"), owns_discovery=False),
            _navigation_route(
                "current catalog contract",
                _target("SemanticCatalog"),
                owns_discovery=False,
            ),
            _navigation_route(
                "optional non-executable context",
                LiveHelpTarget(surface="ontology", canonical_id="authoring"),
                owns_discovery=False,
            ),
        ),
        member_heading="Route by current question",
    )
    return (authoring, objects, builders, checks, *builder_topics, *object_contracts)


def _root_sections() -> tuple[SemanticRootSection, ...]:
    """Build compact semantic root sections without renderer-owned membership."""

    return (
        SemanticRootSection(
            section_id="start",
            label="Start",
            members=(_target("authoring"), _target("load")),
        ),
        SemanticRootSection(
            section_id="discover_authoring",
            label="Discover authoring contracts",
            members=(_target("objects"), _target("builders"), _target("checks")),
        ),
        SemanticRootSection(
            section_id="current_catalog",
            label="Current catalog",
            members=(_target("SemanticCatalog"), _target("CatalogEntry")),
        ),
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


_SEMANTIC_NAVIGATION_DESCRIPTOR_TYPES = (
    SemanticNavigationTopic,
    SemanticBuilderTopic,
    SemanticCheckTopic,
    SemanticObjectContract,
)


def _render_class_for_descriptor(
    descriptor: SemanticHelpDescriptor,
) -> SemanticHelpRenderClass:
    """Return the registry-owned render class for one active descriptor."""

    if isinstance(descriptor, AuthoringCapability):
        if descriptor.canonical_id in {"ref", "source_check"}:
            return "navigation"
        return "exact_contract"
    if isinstance(descriptor, SemanticNavigationTopic) and descriptor.canonical_id == "authoring":
        return "decision_hub"
    return "navigation"


def _target_key(target: LiveHelpTarget) -> tuple[str, str | None]:
    return target.surface, target.canonical_id


def _discovery_members(descriptor: SemanticHelpDescriptor) -> tuple[LiveHelpTarget, ...]:
    """Return ownership edges, excluding object and hub cross-links."""

    if isinstance(descriptor, SemanticNavigationTopic):
        return tuple(member.target for member in descriptor.members if member.owns_discovery)
    if isinstance(descriptor, SemanticBuilderTopic):
        return descriptor.members
    if isinstance(descriptor, SemanticCheckTopic):
        return tuple(target for route in descriptor.routes for target in route.targets)
    if isinstance(descriptor, SemanticObjectContract):
        return tuple(mode.target for mode in descriptor.construction_modes)
    if isinstance(descriptor, AuthoringCapability) and descriptor.canonical_id in {
        "ref",
        "source_check",
    }:
        return descriptor.see_also
    return ()


def _validate_registry(registry: SemanticCapabilityRegistry) -> None:
    """Reject invalid closed registry state before the live surface is built."""

    help_descriptors = registry.help_descriptors
    canonical_ids = tuple(descriptor.canonical_id for descriptor in help_descriptors)
    if len(canonical_ids) != len(set(canonical_ids)):
        raise ValueError("duplicate semantic help canonical id")
    if any(not canonical_id.strip() for canonical_id in canonical_ids):
        raise ValueError("semantic help canonical ids must not be empty")
    if any(not descriptor.summary.strip() for descriptor in help_descriptors):
        raise ValueError("semantic help descriptor summaries must not be empty")

    exact_descriptors = tuple(
        descriptor for descriptor in help_descriptors if isinstance(descriptor, AuthoringCapability)
    )
    if exact_descriptors != registry.descriptors:
        raise ValueError("semantic exact descriptor projection is inconsistent")
    if tuple(registry._by_id) != canonical_ids:
        raise ValueError("semantic help id index is inconsistent")
    for descriptor in help_descriptors:
        if registry._by_id.get(descriptor.canonical_id) is not descriptor:
            raise ValueError("semantic help id index does not preserve descriptor identity")

    callable_descriptors = tuple(
        descriptor for descriptor in exact_descriptors if descriptor.callable_path is not None
    )
    callable_paths = tuple(descriptor.callable_path for descriptor in callable_descriptors)
    if len(callable_paths) != len(set(callable_paths)):
        raise ValueError("duplicate semantic callable path")
    if set(registry._by_callable_path) != set(callable_paths):
        raise ValueError("semantic callable index is inconsistent")
    for descriptor in callable_descriptors:
        callable_path = descriptor.callable_path
        if callable_path is None or registry._by_callable_path.get(callable_path) is not descriptor:
            raise ValueError("semantic callable index does not preserve descriptor identity")

    expected_render_classes = set(get_args(SemanticHelpRenderClass))
    if set(registry.render_budgets) != expected_render_classes:
        raise ValueError("semantic Help budgets must cover every render class")
    for render_class, budget in registry.render_budgets.items():
        if (
            budget.max_lines <= 0
            or budget.max_codepoints <= 0
            or budget.max_outgoing_routes <= 0
            or budget.max_examples_or_snippets < 0
        ):
            raise ValueError(f"invalid semantic Help render budget: {render_class}")

    if set(registry._render_classes) != set(canonical_ids):
        raise ValueError("semantic render-class assignments must cover every descriptor")
    for descriptor in help_descriptors:
        render_class = registry._render_classes[descriptor.canonical_id]
        if render_class not in expected_render_classes:
            raise ValueError(f"unknown semantic Help render class: {render_class}")
        if render_class != _render_class_for_descriptor(descriptor):
            raise ValueError(
                f"invalid semantic Help render class for {descriptor.canonical_id}: {render_class}"
            )
        if isinstance(descriptor, _SEMANTIC_NAVIGATION_DESCRIPTOR_TYPES) and (
            descriptor.public_entrypoint is not None or descriptor.callable_path is not None
        ):
            raise ValueError(
                f"semantic navigation descriptor must not be invokable: {descriptor.canonical_id}"
            )
        if isinstance(descriptor, SemanticNavigationTopic):
            if not descriptor.member_heading.strip() or not descriptor.members:
                raise ValueError(
                    f"semantic navigation descriptor is incomplete: {descriptor.canonical_id}"
                )
            object_entries = tuple(
                member
                for member in descriptor.members
                if isinstance(member, SemanticObjectIndexEntry)
            )
            if object_entries and len(object_entries) != len(descriptor.members):
                raise ValueError(
                    f"semantic object-index members must not be mixed: {descriptor.canonical_id}"
                )
            for member in descriptor.members:
                if not member.label.strip() or (
                    member.summary is not None and not member.summary.strip()
                ):
                    raise ValueError(
                        f"semantic navigation member is incomplete: {descriptor.canonical_id}"
                    )

    exact_ids = {descriptor.canonical_id for descriptor in exact_descriptors}
    expected_root_sections: tuple[SemanticRootGroup, ...] = (
        "start",
        "discover_authoring",
        "current_catalog",
    )
    if tuple(section.section_id for section in registry.root_sections) != expected_root_sections:
        raise ValueError("semantic root sections must use the reviewed compact topology")
    root_routes = tuple(target for section in registry.root_sections for target in section.members)
    if len(root_routes) != len(set(root_routes)):
        raise ValueError("duplicate semantic root route")
    if len(root_routes) > registry.render_budget("root").max_outgoing_routes:
        raise ValueError("semantic root exceeds its route budget")

    known_semantic_type_targets = {"SemanticCatalog", "CatalogEntry"}
    from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
    from marivo.ontology._capabilities.registry import REGISTRY as ONTOLOGY_REGISTRY

    def validate_target(target: LiveHelpTarget, *, owner: str) -> None:
        target_id = target.canonical_id
        if target_id is None:
            raise ValueError(f"semantic Help route lacks a canonical id: {owner}")
        if target.surface == "semantic":
            if target_id not in set(canonical_ids) | known_semantic_type_targets:
                raise ValueError(f"unknown semantic Help route: {owner} -> {target_id}")
            return
        try:
            if target.surface == "datasource":
                DATASOURCE_REGISTRY.by_canonical_id(target_id)
            elif target.surface == "ontology":
                ONTOLOGY_REGISTRY.by_canonical_id(target_id)
            else:
                raise ValueError(f"unsupported semantic Help route surface: {target.surface}")
        except KeyError as exc:
            raise ValueError(f"unknown external semantic Help route: {owner}") from exc

    for section in registry.root_sections:
        if not section.label.strip() or not section.members:
            raise ValueError(f"semantic root section is incomplete: {section.section_id}")
        for target in section.members:
            validate_target(target, owner=f"root.{section.section_id}")

    object_contracts = registry.object_contracts
    object_index_entries = tuple(
        member
        for descriptor in help_descriptors
        if isinstance(descriptor, SemanticNavigationTopic)
        for member in descriptor.members
        if isinstance(member, SemanticObjectIndexEntry)
    )
    if len(object_index_entries) != len(object_contracts) or any(
        entry.contract is not contract
        for entry, contract in zip(object_index_entries, object_contracts, strict=True)
    ):
        raise ValueError("semantic object index must embed the registered object contracts")
    expected_object_kinds = set(SemanticKind) - {SemanticKind.DATASOURCE}
    object_kinds = tuple(contract.semantic_kind for contract in object_contracts)
    if len(object_kinds) != len(set(object_kinds)):
        raise ValueError("duplicate semantic object-kind contract")
    if set(object_kinds) != expected_object_kinds:
        raise ValueError("semantic object-kind contracts must cover every non-datasource kind")

    catalog_collection_by_kind = {
        member.kind: member.property_name for member in CATALOG_MEMBER_CONTRACTS
    }
    for contract in object_contracts:
        kind = contract.semantic_kind
        expected_ref_family = f"Ref[{kind.value}]"
        if contract.canonical_id != f"objects.{kind.value}":
            raise ValueError(f"invalid semantic object-kind canonical id: {kind.value}")
        if contract.catalog_collection != catalog_collection_by_kind[kind]:
            raise ValueError(f"semantic object catalog collection drift: {kind.value}")
        if contract.ref_target != _target(f"ref.{kind.value}"):
            raise ValueError(f"semantic object ref target drift: {kind.value}")
        ref_descriptor = registry.by_canonical_id(contract.ref_target.canonical_id or "")
        if not isinstance(ref_descriptor, AuthoringCapability):
            raise ValueError(f"semantic object ref target is not exact: {kind.value}")
        if ref_descriptor.output_family != expected_ref_family:
            raise ValueError(f"semantic object ref-kind output drift: {kind.value}")

        construction_ids = tuple(
            mode.target.canonical_id or "" for mode in contract.construction_modes
        )
        if not construction_ids:
            raise ValueError(f"semantic object kind has no construction mode: {kind.value}")
        if len(construction_ids) != len(set(construction_ids)):
            raise ValueError(f"duplicate semantic object construction mode: {kind.value}")
        for mode in contract.construction_modes:
            descriptor = registry.by_canonical_id(mode.target.canonical_id or "")
            if not isinstance(descriptor, AuthoringCapability):
                raise ValueError(f"semantic construction target is not exact: {kind.value}")
            if descriptor.output_family != expected_ref_family:
                raise ValueError(f"semantic construction output-kind drift: {kind.value}")

        supporting_ids = tuple(target.canonical_id or "" for target in contract.supporting_targets)
        check_ids = tuple(target.canonical_id or "" for target in contract.check_targets)
        if len(supporting_ids) != len(set(supporting_ids)):
            raise ValueError(f"duplicate semantic supporting target: {kind.value}")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError(f"duplicate semantic check target: {kind.value}")
        for target in (*contract.supporting_targets, *contract.check_targets):
            if target.surface != "semantic" or target.canonical_id not in exact_ids:
                raise ValueError(f"unknown semantic object edge: {kind.value}")

        decision_ids = tuple(decision.decision_id for decision in contract.decisions)
        if not decision_ids or any(not decision_id.strip() for decision_id in decision_ids):
            raise ValueError(f"semantic object decisions must be named: {kind.value}")
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError(f"duplicate semantic object decision id: {kind.value}")
        allowed_next_ids = {*construction_ids, *supporting_ids, *check_ids}
        for decision in contract.decisions:
            if not decision.question.strip() or not decision.determine_from.strip():
                raise ValueError(f"semantic object decision guidance is incomplete: {kind.value}")
            next_ids = tuple(target.canonical_id or "" for target in decision.next_targets)
            if any(
                target.surface != "semantic" or target_id not in allowed_next_ids
                for target, target_id in zip(decision.next_targets, next_ids, strict=True)
            ):
                raise ValueError(f"semantic object decision target escapes its owner: {kind.value}")
            if decision.encoding_status == "supported":
                if not next_ids or decision.unsupported_reason is not None:
                    raise ValueError(f"invalid supported semantic object decision: {kind.value}")
            elif next_ids or not decision.unsupported_reason:
                raise ValueError(f"invalid unsupported semantic object decision: {kind.value}")

        for relationship in contract.relationships:
            target_id = relationship.target.canonical_id
            if not relationship.explanation.strip() or target_id is None:
                raise ValueError(f"semantic object relationship is incomplete: {kind.value}")
            registered_relationship_ids = {
                *canonical_ids,
                *(object_contract.canonical_id for object_contract in object_contracts),
            }
            if relationship.target.surface == "semantic":
                if target_id not in registered_relationship_ids:
                    raise ValueError(f"unknown semantic object relationship: {kind.value}")
            elif relationship.target.surface == "datasource":
                try:
                    DATASOURCE_REGISTRY.by_canonical_id(target_id)
                except KeyError as exc:
                    raise ValueError(
                        f"unknown datasource object relationship: {kind.value}"
                    ) from exc
            else:
                raise ValueError(f"unsupported object relationship surface: {kind.value}")

    ref_method_ids = tuple(f"ref.{kind.value}" for kind in SemanticKind)
    ref_parent = registry.by_canonical_id("ref")
    if not isinstance(ref_parent, AuthoringCapability):
        raise ValueError("semantic ref factory contract must be exact")
    if tuple(target.canonical_id for target in ref_parent.see_also) != ref_method_ids or any(
        target.surface != "semantic" for target in ref_parent.see_also
    ):
        raise ValueError("semantic ref factory membership drift")
    for target_id in ref_method_ids:
        try:
            descriptor = registry.by_canonical_id(target_id)
        except KeyError as exc:
            raise ValueError("semantic ref factory target is not exact") from exc
        if (
            not isinstance(descriptor, AuthoringCapability)
            or descriptor.kind != "method"
            or descriptor.callable_path is None
        ):
            raise ValueError("semantic ref factory target is not exact")

    source_check_method_ids = (
        "source_check.not_null",
        "source_check.allowed_values",
        "source_check.unique",
        "source_check.freshness",
        "source_check.relationship_matches",
        "source_check.relationship_cardinality",
    )
    source_check_parent = registry.by_canonical_id("source_check")
    if not isinstance(source_check_parent, AuthoringCapability):
        raise ValueError("semantic source-check factory contract must be exact")
    if tuple(target.canonical_id for target in source_check_parent.see_also) != (
        source_check_method_ids
    ) or any(target.surface != "semantic" for target in source_check_parent.see_also):
        raise ValueError("semantic source-check factory membership drift")
    for target_id in source_check_method_ids:
        try:
            descriptor = registry.by_canonical_id(target_id)
        except KeyError as exc:
            raise ValueError("semantic source-check factory target is not exact") from exc
        if (
            not isinstance(descriptor, AuthoringCapability)
            or descriptor.kind != "method"
            or descriptor.callable_path is None
        ):
            raise ValueError("semantic source-check factory target is not exact")

    discovery_owners: dict[tuple[str, str | None], str] = {}
    for descriptor in help_descriptors:
        routes = registry.routes(descriptor.canonical_id)
        budget = registry.render_budget(registry.render_class(descriptor.canonical_id))
        if len(routes) > budget.max_outgoing_routes:
            raise ValueError(
                f"semantic Help descriptor exceeds route budget: {descriptor.canonical_id}"
            )
        for target in routes:
            validate_target(target, owner=descriptor.canonical_id)

        discovery_members = _discovery_members(descriptor)
        if len(discovery_members) != len(set(discovery_members)):
            raise ValueError(f"duplicate semantic discovery member: {descriptor.canonical_id}")
        for discovery_target in discovery_members:
            key = _target_key(discovery_target)
            previous_owner = discovery_owners.setdefault(key, descriptor.canonical_id)
            if previous_owner != descriptor.canonical_id:
                raise ValueError(f"multiple semantic discovery owners: {discovery_target.display}")

    required_discovery_ids = (
        {descriptor.canonical_id for descriptor in exact_descriptors if descriptor.kind != "method"}
        | set(ref_method_ids)
        | set(source_check_method_ids)
    )
    owned_semantic_ids = {
        target_id
        for (surface, target_id) in discovery_owners
        if surface == "semantic" and target_id is not None
    }
    missing_owners = sorted(required_discovery_ids - owned_semantic_ids)
    if missing_owners:
        raise ValueError("semantic discovery owner missing: " + ", ".join(missing_owners))

    required_graph_ids = required_discovery_ids | {
        descriptor.canonical_id
        for descriptor in help_descriptors
        if not isinstance(descriptor, AuthoringCapability)
    }
    distances = {"authoring": 0}
    pending = deque(("authoring",))
    while pending:
        current = pending.popleft()
        for target in registry.routes(current):
            target_id = target.canonical_id
            if (
                target.surface != "semantic"
                or target_id is None
                or target_id not in registry._by_id
                or target_id in distances
            ):
                continue
            distances[target_id] = distances[current] + 1
            pending.append(target_id)

    unreachable = sorted(required_graph_ids - set(distances))
    if unreachable:
        raise ValueError("semantic Help graph is unreachable: " + ", ".join(unreachable))
    too_deep = sorted(
        canonical_id for canonical_id in required_graph_ids if distances[canonical_id] > 3
    )
    if too_deep:
        raise ValueError("semantic Help graph exceeds four global edges: " + ", ".join(too_deep))


def _finalize_registry(
    descriptors: tuple[AuthoringCapability, ...],
    *,
    root_sections: tuple[SemanticRootSection, ...],
    source_contracts: Mapping[str, AuthoringSourceContract],
    repair_contracts: Mapping[str, SemanticRepairContract],
    help_descriptors: tuple[SemanticHelpDescriptor, ...] | None = None,
    object_contracts: tuple[SemanticObjectContract, ...] = (),
    render_budgets: Mapping[
        SemanticHelpRenderClass,
        SemanticHelpRenderBudget,
    ] = SEMANTIC_HELP_RENDER_BUDGETS,
) -> SemanticCapabilityRegistry:
    """Build immutable indexes and eagerly validate the semantic Help registry."""

    active_help_descriptors = descriptors if help_descriptors is None else help_descriptors
    registry = SemanticCapabilityRegistry(
        surface="semantic",
        _help_descriptors=active_help_descriptors,
        _descriptors=descriptors,
        _root_sections=root_sections,
        _by_id=MappingProxyType(
            {descriptor.canonical_id: descriptor for descriptor in active_help_descriptors}
        ),
        _by_callable_path=MappingProxyType(
            {
                descriptor.callable_path: descriptor
                for descriptor in descriptors
                if descriptor.callable_path is not None
            }
        ),
        _source_contracts=MappingProxyType(dict(source_contracts)),
        _repair_contracts=MappingProxyType(dict(repair_contracts)),
        _object_contracts=object_contracts,
        _render_classes=MappingProxyType(
            {
                descriptor.canonical_id: _render_class_for_descriptor(descriptor)
                for descriptor in active_help_descriptors
            }
        ),
        _render_budgets=MappingProxyType(dict(render_budgets)),
    )
    _validate_registry(registry)
    return registry


def _ref_factory_capabilities() -> tuple[AuthoringCapability, ...]:
    """Build the closed public ``ms.ref`` factory contract and exact leaves."""

    kinds = tuple(SemanticKind)
    parent = _capability(
        "ref",
        None,
        "Create one immutable exact-kind semantic identity for a known path.",
        kind="boundary",
        output="Ref",
        effects=_NONE,
        see_also=tuple(_target(f"ref.{kind.value}") for kind in kinds),
    )
    leaves = tuple(
        _capability(
            f"ref.{kind.value}",
            f"marivo.refs._RefFactory.{kind.value}",
            f"Create an exact {kind.value} ref from its kind-relative canonical path.",
            kind="method",
            output=f"Ref[{kind.value}]",
            inputs=(_parameter_input("subject", "Text", "path"),),
            effects=_NONE,
            constraints=("ref_shape",),
            example=f"ms.ref.{kind.value}({_ref_example_path(kind)!r})",
            public_entrypoint=f"ms.ref.{kind.value}",
        )
        for kind in kinds
    )
    return (parent, *leaves)


def _ref_example_path(kind: SemanticKind) -> str:
    if kind in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
        return "sales" if kind is SemanticKind.DOMAIN else "warehouse"
    if kind in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        return f"sales.orders.{kind.value}"
    return f"sales.{kind.value}"


def _source_check_factory_capabilities() -> tuple[AuthoringCapability, ...]:
    """Build exact leaves for every public ``ms.source_check`` method."""

    return (
        _capability(
            "source_check.not_null",
            "marivo.semantic.source_health.SourceCheckNamespace.not_null",
            "Require one field to contain no null values.",
            kind="method",
            output="NotNullSourceCheck",
            inputs=(
                _parameter_input("subject", "Ref[dimension | time_dimension | measure]", "field"),
            ),
            effects=_NONE,
            example="ms.source_check.not_null(ms.ref.dimension('sales.orders.region'))",
            public_entrypoint="ms.source_check.not_null",
        ),
        _capability(
            "source_check.allowed_values",
            "marivo.semantic.source_health.SourceCheckNamespace.allowed_values",
            "Require one categorical or temporal field to use only the declared values.",
            kind="method",
            output="AllowedValuesSourceCheck",
            inputs=(
                _parameter_input("subject", "Ref[dimension | time_dimension]", "field"),
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
            "marivo.semantic.source_health.SourceCheckNamespace.unique",
            "Require a non-empty same-Entity field tuple to be unique.",
            kind="method",
            output="UniqueSourceCheck",
            inputs=(
                _parameter_input(
                    "subject",
                    "Ref[dimension | time_dimension | measure]",
                    "fields",
                ),
            ),
            effects=_NONE,
            example=("ms.source_check.unique(fields=(ms.ref.dimension('sales.orders.order_id'),))"),
            public_entrypoint="ms.source_check.unique",
        ),
        _capability(
            "source_check.freshness",
            "marivo.semantic.source_health.SourceCheckNamespace.freshness",
            "Require one time dimension's maximum value to be within a declared age.",
            kind="method",
            output="FreshnessSourceCheck",
            inputs=(
                _parameter_input("subject", "Ref[time_dimension]", "field"),
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
            "marivo.semantic.source_health.SourceCheckNamespace.relationship_matches",
            "Require declared relationship keys to match on the selected side.",
            kind="method",
            output="RelationshipMatchesSourceCheck",
            inputs=(
                _parameter_input("subject", "Ref[relationship]", "relationship"),
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
            "marivo.semantic.source_health.SourceCheckNamespace.relationship_cardinality",
            "Require current relationship multiplicity to match one exact expectation.",
            kind="method",
            output="RelationshipCardinalitySourceCheck",
            inputs=(
                _parameter_input("subject", "Ref[relationship]", "relationship"),
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


_PARAMETER_NAMES_BY_CAPABILITY: Mapping[str, tuple[tuple[str, ...], ...]] = MappingProxyType(
    {
        "domain": (("name",), ("owner",)),
        "entity": (("name",), ("datasource",), ("source",)),
        "dimension": (("name",), ("entity",)),
        "dimension_column": (("name",), ("entity",), ("column",)),
        "time_dimension": (("name",), ("entity",), ("granularity",)),
        "time_dimension_column": (("name",), ("entity",), ("column",), ("granularity",)),
        "period_correspondence": (("level",), ("baseline_key",)),
        "period_calendar": (("name",), ("date",), ("levels",), ("correspondences",)),
        "temporal_set": (("name",), ("occurrence_id",), ("start",), ("end",), ("category",)),
        "work_schedule": (("name",), ("date",), ("is_working",)),
        "calendar_grain": (("calendar",), ("level",)),
        "measure": (("name",), ("entity",), ("additivity",)),
        "measure_column": (("name",), ("entity",), ("column",), ("additivity",)),
        "aggregate": (("name",), ("measure",), ("agg",), ("fold",), ("filter",)),
        "count": (("name",), ("entity",), ("filter",)),
        "where": ((),),
        "cumulative": (("name",), ("base",), ("anchor",)),
        "ratio": (("name",), ("numerator",), ("denominator",)),
        "weighted_mean": (("name",), ("value",), ("weight",)),
        "linear": (("name",), ("add",)),
        "relationship": (("name",), ("from_entity",), ("to_entity",), ("keys",)),
        "event": (("name",), ("identity",), ("occurred_at",), ("participants",)),
        "participant": (("name",), ("path",)),
        "participant_role": (("event",), ("name",)),
        "lifecycle_state": (("name",),),
        "inception": (("on",),),
        "transition": (("from_state",), ("on",), ("to_state",)),
        "state_model": (("name",), ("subject",), ("states",), ("transitions",), ("transitions",)),
        "model_state": (("model",), ("name",)),
        "join_on": (("from_key", "to_key"),),
        "from_sql": (("sql",), ("dialect",)),
        "bind": (("field",), ("entity_alias",)),
        "metric": (("name",), ("entities",), ("additivity",)),
        "snapshot": (("partition_field",),),
        "validity": (("valid_from", "valid_to"),),
        "grain_to_date": (("grain",),),
        "trailing": (("count",),),
        "preview": ((), ("ref",), ("scope",), ("source_bindings",)),
        "preview_many": ((), ("refs",), ("scope",), ("source_bindings",)),
        "source_health": ((), ("refs",), ("checks",), ("scope",)),
        "readiness": ((), ("refs",)),
        "richness": (("demand",),),
        "parity_check": (("name",), ("rel_tol",), ("abs_tol",), ("force",)),
        "SemanticCatalog.items": ((), ("kind",)),
        "SemanticCatalog.require": ((), ("ref",)),
        "CatalogCollection.get": ((), ("key",)),
    }
)

_OPTIONAL_PARAMETER_REQUIREMENTS = frozenset(
    {
        ("dimension", 0),
        ("time_dimension", 0),
        ("measure", 0),
        ("cumulative", 2),
        ("event", 0),
        ("participant", 1),
        ("metric", 0),
        ("readiness", 1),
        ("parity_check", 1),
        ("parity_check", 2),
        ("parity_check", 3),
    }
)


def _attach_parameter_names(
    descriptors: tuple[AuthoringCapability, ...],
) -> tuple[AuthoringCapability, ...]:
    """Attach explicit live-parameter ownership to existing semantic input facts."""

    attached: list[AuthoringCapability] = []
    for descriptor in descriptors:
        parameter_names = _PARAMETER_NAMES_BY_CAPABILITY.get(descriptor.canonical_id)
        if parameter_names is None:
            attached.append(descriptor)
            continue
        if len(parameter_names) != len(descriptor.input_requirements):
            raise ValueError(
                f"semantic parameter-name metadata length drift: {descriptor.canonical_id}"
            )
        requirements = tuple(
            requirement.model_copy(
                update={
                    "parameter_names": names,
                    "min_count": (
                        0
                        if (descriptor.canonical_id, index) in _OPTIONAL_PARAMETER_REQUIREMENTS
                        else requirement.min_count
                    ),
                }
            )
            for index, (requirement, names) in enumerate(
                zip(descriptor.input_requirements, parameter_names, strict=True)
            )
        )
        attached.append(descriptor.model_copy(update={"input_requirements": requirements}))
    return tuple(attached)


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
        *_ref_factory_capabilities(),
        # ------------------------------------------------------------------
        # semantic constructors and supporting builders
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
            invocation_shape="decorator",
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
            invocation_shape="decorator",
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
            invocation_shape="decorator",
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
                _optional_input("dependency", "TimeFold"),
                _optional_input("dependency", "WhereFilter"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "composition_shape",
                "measure_aggregation_valid",
                "time_fold_valid",
                "time_fold_requires_semi_additive",
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
                ("dependency", "JoinKey"),
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
            see_also=(_target("join_on"),),
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
            invocation_shape="decorator",
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
            invocation_shape="decorator",
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
        # scoped runtime observations and health
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
    descriptor_rows = _attach_parameter_names(descriptor_rows)
    object_contracts = _object_contracts()
    builder_topics = _builder_topics()
    return _finalize_registry(
        descriptor_rows,
        root_sections=_root_sections(),
        source_contracts=_source_contracts(),
        repair_contracts=_repair_contracts(),
        help_descriptors=(
            *descriptor_rows,
            *_navigation_descriptors(object_contracts, builder_topics),
        ),
        object_contracts=object_contracts,
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

    def factory_methods(canonical_id: str) -> tuple[str, ...]:
        descriptor = REGISTRY.by_canonical_id(canonical_id)
        if not isinstance(descriptor, AuthoringCapability):
            raise TypeError(f"semantic factory contract is not exact: {canonical_id}")
        return tuple(
            (target.canonical_id or "").rsplit(".", 1)[-1] for target in descriptor.see_also
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
        methods=factory_methods("source_check"),
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
        methods=factory_methods("ref"),
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
