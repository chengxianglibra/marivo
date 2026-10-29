"""Closed registry and consumed-type catalog for ``marivo.semantic``."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import replace
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
from marivo._data_render import _display_help
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.inputs import authoring_guidance, authoring_inputs
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
from marivo.semantic._capabilities.naming import semantic_target

OUTPUT_FAMILIES = frozenset(
    {
        "Dimension | TimeField | Measure",
        "ExpressionContract",
        "Entity | Deferred | Relationship",
        "SemanticCatalog",
        "CatalogEntry",
        "DatasourceDetails | DomainDetails | PeriodCalendarDetails | TemporalSetDetails",
        "DomainDetails",
        "DatasourceDetails",
        "TemporalSetDetails",
        "dict[str, object]",
        "dict[str, JsonValue]",
        "tuple[str, str]",
        "SemanticEntry",
        "CatalogPage",
        "str",
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
        "ValidationReport",
        "LookupSymbol",
        "DomainSymbol",
        "DatasourceSymbol",
        "EntitySymbol",
        "DimensionSymbol",
        "TimeDimensionSymbol",
        "MeasureSymbol",
        "MetricSymbol",
        "RelationshipSymbol",
        "PeriodCalendarSymbol",
        "TemporalSetSymbol",
        "DimensionSymbol | TimeDimensionSymbol",
        "DimensionSymbol | TimeDimensionSymbol | MeasureSymbol",
        "JoinKey",
        "AiContextValue",
        "Additivity",
        "ValuePolicy",
        "ValiditySpec",
        "TimestampSpec",
        "StrptimeSpec",
        "HourPrefixSpec",
        "None",
        "Entity",
        "Dimension",
        "TimeField",
        "Measure",
        "Metric",
        "Deferred",
        "MetricSamples",
        "Relationship",
        "Number",
        "bool",
        "Text",
        "IbisTable",
        "IbisValue",
        "Grain",
        "CalendarPeriodPage",
        "CalendarLevelDetails",
        "PeriodCalendarDetails",
        "PeriodCalendarEntry",
    }
)

INPUT_FAMILIES = frozenset(
    {
        "InstantAnchor",
        "TimeScope",
        "WindowRule",
        "Expression",
        "Field",
        "IbisTable",
        "Duration",
        "LookupSymbol",
        "DomainSymbol",
        "DatasourceSymbol",
        "EntitySymbol",
        "DimensionSymbol",
        "TimeDimensionSymbol",
        "MeasureSymbol",
        "MetricSymbol",
        "RelationshipSymbol",
        "tuple[RelationshipSymbol, ...]",
        "NullInputPolicyV1",
        "EmptyContributionPolicyV1",
        "PeriodCalendarSymbol",
        "TemporalSetSymbol",
        "DimensionSymbol | TimeDimensionSymbol",
        "DimensionSymbol | TimeDimensionSymbol | MeasureSymbol",
        "CatalogEntry",
        "CatalogPage",
        "DomainEntry",
        "DatasourceEntry",
        "PeriodCalendarEntry",
        "TemporalSetEntry",
        "ReadinessReport",
        "ValidationReport",
        "ValidationCheckResult",
        "ReadinessInputSummary",
        "ReadinessIssue",
        "Dimension | TimeField | Grouping | tuple[Dimension | TimeField | Grouping | Population, ...] | Population | Grain",
        "tuple[Grouping, ...]",
        "CatalogEntry | LookupSymbol",
        "str",
        "SemanticCatalog",
        "SemanticKind",
        "AuthoringScope | Sequence[tuple[EntitySymbol, AuthoringScope]]",
        "SourceCheck",
        "NotNullSourceCheck",
        "AllowedValuesSourceCheck",
        "UniqueSourceCheck",
        "FreshnessSourceCheck",
        "RelationshipMatchesSourceCheck",
        "RelationshipCardinalitySourceCheck",
        "Sequence[tuple[EntitySymbol, JSON source parameter mapping]]",
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
        "EntitySourceIR",
        "DateCoverage",
        "SqlText",
        "SqlDialect",
        "AggFunc",
        "Additivity",
        "ValuePolicy",
        "Unit",
        "Granularity",
        "Granularity | Grain",
        "ParseVariant",
        "tuple[int, minute | hour]",
        "PositiveInt",
        "TimeFold",
        "JoinKey",
        "RelationshipEndpoint",
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
        "ValiditySpec",
        "TimestampSpec",
        "StrptimeSpec",
        "HourPrefixSpec",
        "FilterConditions",
        "EntityAlias",
        "SequenceOrder",
        "LifecycleStateName",
        "Grain",
        "Entity",
        "Dimension",
        "TimeField",
        "Measure",
        "Metric",
        "Deferred",
        "MetricSamples",
        "Relationship",
        "Number",
        "bool",
        "Text",
        "SourceScalarSequence",
        "RelationshipSide",
        "RelationshipCardinality",
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
    if connection == "opens_connection" and "project_state" not in mutations:
        mutations = (*mutations, "project_state")
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
    ),
)
_VALIDATE = _effects(
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
        "may_publish_certified_artifact",
    ),
)

_ADDITIVITY_HELP = (
    "\n  additivity: ms.additive_all(), ms.additive_except(...), ms.additive_over(...), or ms.non_additive()."
    "\n  unit=None leaves the declared unit unspecified."
)
_FOLD_HELP = "\n  Temporal sample reduction is explicit in per(time_field).reducer(); leaf default folds are retired."


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
    telemetry: bool = True,
) -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id=canonical_id,
        kind=kind,
        surface="semantic",
        public_entrypoint=public_entrypoint
        or (f"ms.{canonical_id}" if callable_path is not None else None),
        callable_path=callable_path,
        summary=summary,
        input_requirements=authoring_inputs(canonical_id, inputs),
        output_family=output,
        preconditions=preconditions,
        guidance=authoring_guidance(canonical_id),
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
        invocation_shape=invocation_shape,
        telemetry=telemetry,
    )


def _authoring_source_contract(kind: SemanticKind) -> AuthoringSourceContract:
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
        catalog_kind=kind.value,
        canonical_identity_template=identity_template,
    )


def _source_contracts(
    object_contracts: tuple[SemanticObjectContract, ...],
    descriptors: tuple[AuthoringCapability, ...],
) -> Mapping[str, AuthoringSourceContract]:
    """Build closed placement/handoff facts for every source-authored object."""

    contracts: dict[str, AuthoringSourceContract] = {}
    for object_contract in object_contracts:
        source_contract = _authoring_source_contract(object_contract.semantic_kind)
        construction_ids = {
            mode.target.canonical_id for mode in object_contract.construction_modes
        } - {"measure", "metric"}
        contracts.update(
            {
                descriptor.canonical_id: source_contract
                for descriptor in descriptors
                if descriptor.canonical_id in construction_ids
            }
        )
    return MappingProxyType(contracts)


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


# One primary discovery owner per supporting value; other object links remain
# references. Keep parameter builders beneath the definitions they support.
_SUPPORT_DISCOVERY_OWNERS: Mapping[SemanticKind, tuple[str, ...]] = MappingProxyType(
    {
        SemanticKind.DOMAIN: ("ai_context",),
        SemanticKind.ENTITY: ("snapshot", "validity"),
        SemanticKind.DIMENSION: ("bind",),
        SemanticKind.MEASURE: ("additive_all", "additive_except", "additive_over", "non_additive"),
        SemanticKind.RELATIONSHIP: ("join_on",),
    }
)


def _object_contract(
    kind: SemanticKind,
    summary: str,
    *,
    decisions: tuple[SemanticObjectDecision, ...],
    construction_modes: tuple[ConstructionMode, ...],
    relationships: tuple[SemanticObjectRelationship, ...],
    supporting: tuple[str, ...],
    checks: tuple[str, ...],
    check_guidance: str | None = None,
) -> SemanticObjectContract:
    source_contract = _authoring_source_contract(kind)
    return SemanticObjectContract(
        canonical_id=f"objects.{kind.value}",
        summary=summary,
        semantic_kind=kind,
        construction_families={
            SemanticKind.MEASURE: ("MeasureSymbol", "Deferred"),
            SemanticKind.METRIC: ("Deferred",),
        }.get(kind, (_symbol_family(kind),)),
        ref_target=_target(f"ref.{kind.value}"),
        catalog_kind=source_contract.catalog_kind,
        placement_kind=source_contract.placement_kind,
        decisions=decisions,
        construction_modes=construction_modes,
        relationships=relationships,
        supporting_targets=tuple(
            _target(target_id)
            for target_id in dict.fromkeys((*supporting, *_SUPPORT_DISCOVERY_OWNERS.get(kind, ())))
        ),
        check_targets=tuple(_target(target_id) for target_id in checks),
        check_guidance=check_guidance,
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
            (
                "Reusable business entity or fact-set identity backed by one source; "
                "an optional expression body owns its output relation and grain."
            ),
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
            construction_modes=(
                _mode(
                    "Declare an Entity directly or as a one-return expression decorator "
                    "over its source; an explicit name takes the direct form.",
                    "default",
                    "entity",
                ),
            ),
            relationships=(
                _relationship("owned_by", "objects.domain", "Every Entity belongs to one Domain."),
                _relationship(
                    "requires",
                    "boundary.semantic_authoring",
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
            checks=("load", "readiness", "preview", "validate"),
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
            ),
            supporting=("ai_context", "bind"),
            checks=(
                "load",
                "readiness",
                "preview",
                "source_check.not_null",
                "source_check.allowed_values",
                "source_check.unique",
                "validate",
            ),
        ),
        _object_contract(
            SemanticKind.TIME_DIMENSION,
            "Explicit business time field with parse semantics and optional source sampling.",
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
                    "logical_time_kind",
                    "Does the source represent civil dates or exact timestamps?",
                    "time_dimension_column",
                    "time_dimension",
                ),
                _source_business_decision(
                    "physical_time_encoding",
                    "How are physical values encoded, parsed, and localized?",
                    "time_dimension_column",
                    "time_dimension",
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
                    "time_dimension_column",
                    "time_dimension",
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
            supporting=("timestamp", "strptime", "hour_prefix", "ai_context", "bind"),
            checks=(
                "load",
                "readiness",
                "preview",
                "source_check.not_null",
                "source_check.allowed_values",
                "source_check.unique",
                "source_check.freshness",
                "validate",
            ),
        ),
        _object_contract(
            SemanticKind.MEASURE,
            "A quantitative row expression with business unit and sum permission.",
            decisions=(
                _source_business_decision(
                    "numeric_fact_grain",
                    "What quantity does each contribution record represent?",
                    "measure_column",
                    "measure",
                ),
                _business_decision(
                    "unit", "What unit does this row quantity carry?", "measure_column", "measure"
                ),
                _business_decision(
                    "dimensional_additivity",
                    "Which business axes may be summed?",
                    "measure_column",
                    "measure",
                ),
                _business_decision(
                    "temporal_additivity",
                    "Which time axes must remain fixed?",
                    "measure_column",
                    "measure",
                ),
                _source_business_decision(
                    "semi_additive_axis_fold",
                    "Which status axis and sampling contract must explicit sample reduction preserve?",
                    "measure_column",
                    "row.sum",
                ),
                _business_decision(
                    "construction_mode",
                    "Is this a physical leaf or a new typed row quantity?",
                    "measure_column",
                    "measure",
                ),
            ),
            construction_modes=(
                _mode("Bind one physical numeric column.", "default", "measure_column"),
                _mode(
                    "Declare a pure row expression and any new business authorization.",
                    "alternative",
                    "measure",
                ),
            ),
            relationships=(
                _relationship(
                    "owned_by", "objects.entity", "The expression has one exact contribution root."
                ),
            ),
            supporting=(
                "ai_context",
                "additive_all",
                "additive_except",
                "additive_over",
                "non_additive",
                "row.sum",
            ),
            checks=("readiness",),
            check_guidance=(
                "Readiness accepts this entry and checks its physical dependency closure. "
                "Only directly requested ready physical refs enter analysis_ready_inputs."
            ),
        ),
        _object_contract(
            SemanticKind.METRIC,
            "A completed source reduction or composition of Metrics.",
            decisions=(
                _business_decision(
                    "population_value",
                    "Which contributions and completed value answer the question?",
                    "metric",
                ),
                _business_decision(
                    "construction_mode",
                    "Which source reductions and Metric arithmetic express the equation?",
                    "metric",
                    "row.sum",
                ),
                _business_decision(
                    "aggregation_filter",
                    "Which source contribution scope owns each predicate?",
                    "Metric.where",
                ),
                _source_business_decision(
                    "root_fanout",
                    "Are source roots and mappings explicit without implicit multi-value grouping?",
                    "metric",
                ),
                _business_decision(
                    "unit_additivity",
                    "Which inherited units and Measure permissions constrain this equation?",
                    "measure",
                ),
                _business_decision(
                    "temporal_behavior",
                    "Which source time roles and internal sample reductions are required?",
                    "Metric.per",
                    "Field.via",
                ),
                _business_decision(
                    "guardrails",
                    "Which approved business limits must remain visible?",
                    "ai_context",
                ),
            ),
            construction_modes=(
                _mode("Declare a zero-argument pure Metric expression.", "default", "metric"),
            ),
            relationships=(
                _relationship(
                    "may_reference", "objects.measure", "Source reductions consume row quantities."
                ),
                _relationship(
                    "may_reference",
                    "objects.metric",
                    "Completed Metrics compose without changing their source roots.",
                ),
            ),
            supporting=(
                "ai_context",
                "row.sum",
                "Metric.where",
                "Metric.per",
                "Field.via",
                "measure",
            ),
            checks=("readiness",),
            check_guidance=(
                "Readiness accepts this Metric entry and checks its physical dependencies. "
                "It does not return the Metric as an analysis_ready_input. "
                "AnalysisFrame.compute()/persist() qualifies the selected computation."
            ),
        ),
        _object_contract(
            SemanticKind.RELATIONSHIP,
            "Directed Entity mapping with derived structural cardinality; match completeness belongs to each consumer.",
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
                ),
                _source_decision(
                    "evidence_checks",
                    "Which source checks are required to test those claims?",
                    "source_check.relationship_matches",
                    "source_check.relationship_cardinality",
                    "validate",
                ),
            ),
            construction_modes=(
                _mode("Declare one directed Entity join contract.", "default", "relationship"),
            ),
            relationships=(
                _relationship(
                    "requires", "objects.entity", "Both directed endpoints are exact Entity refs."
                ),
            ),
            supporting=("join_on", "ai_context"),
            checks=(
                "load",
                "readiness",
                "source_check.relationship_matches",
                "source_check.relationship_cardinality",
                "validate",
            ),
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
                    "Period levels are Dimensions.",
                ),
            ),
            supporting=("ai_context",),
            checks=("load", "readiness", "preview", "validate"),
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
            checks=("load", "readiness", "preview", "validate"),
        ),
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
        canonical_id="validation",
        summary="Choose offline loading/readiness, online validation, preview or explicit data checks by proof need.",
        routes=(
            route(
                "What physical source, schema, columns, and types exist?",
                (datasource("inspect"),),
                "Current authoritative physical metadata to the backend's supported extent.",
                "Reusable business meaning.",
            ),
            route(
                "Do I need bounded sampled rows or source-specific SQL evidence?",
                (datasource("inspection"), datasource("raw_sql")),
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
                "A bounded current runtime observation; period calendars and temporal sets "
                "can also publish dedicated certified artifacts.",
                "Ordinary previews do not persist certification or change readiness; "
                "certified artifacts are inputs to a subsequent readiness check.",
            ),
            route(
                "How do I declare an exact source expectation?",
                (_target("source_check"),),
                "The expectation is explicit, typed, and closed.",
                "That the current source satisfies it.",
            ),
            route(
                "Do the definitions agree with the current physical source types?",
                (_target("validate"),),
                "Current Entity output, field bindings, numeric and temporal type contracts; "
                "bounded data expectations only when explicitly requested.",
                "Business approval or readiness mutation.",
            ),
        ),
    )


def _navigation_descriptors(
    object_contracts: tuple[SemanticObjectContract, ...],
) -> tuple[SemanticHelpDescriptor, ...]:
    """Build the need-directed model, time, catalog, and validation tree."""
    objects = {contract.semantic_kind.value: contract for contract in object_contracts}

    def object_entry(kind: str) -> SemanticObjectIndexEntry:
        return SemanticObjectIndexEntry(
            objects[kind],
            {
                "domain": "Choose the accountable business namespace before declaring its objects.",
                "entity": "Choose record grain and identity before mapping the physical source.",
                "dimension": "Choose categorical or scalar field meaning and its declaration mode.",
                "time_dimension": "Choose business time meaning and its physical representation.",
                "measure": "Choose a numeric row quantity and its aggregation permissions.",
                "metric": "Choose existing Metric composition or a reusable Metric definition.",
                "relationship": "Choose directed endpoints, cardinality, and complete join keys.",
                "period_calendar": "Choose a reusable definition of business period boundaries.",
                "temporal_set": "Choose a reusable definition of event occurrence boundaries.",
            }[kind],
        )

    def route(
        label: str, target: str, summary: str, *, owns: bool = True
    ) -> SemanticNavigationRoute:
        return _navigation_route(label, _target(target), summary=summary, owns_discovery=owns)

    return (
        SemanticNavigationTopic(
            "model",
            "Choose the missing reusable definition. Single-mode definitions link directly to construction; fields offer column or expression modes.",
            (
                *(
                    object_entry(kind)
                    for kind in (
                        "domain",
                        "entity",
                        "dimension",
                        "time_dimension",
                        "measure",
                        "metric",
                        "relationship",
                    )
                ),
                route(
                    "business calendars and events",
                    "time.calendars",
                    "Define finite business periods or occurrences.",
                    owns=False,
                ),
            ),
        ),
        SemanticNavigationTopic(
            "time",
            "Distinguish physical time parsing from reusable certified calendar and event definitions.",
            (
                route(
                    "physical time encoding",
                    "time.parsing",
                    "Parse timestamps, formatted values, or hour prefixes.",
                ),
                route(
                    "business calendars and events",
                    "time.calendars",
                    "Define reusable period calendars and temporal sets.",
                ),
            ),
        ),
        SemanticNavigationTopic(
            "time.parsing",
            "Choose the physical time encoding used by a TimeDimension declaration.",
            tuple(
                route(label, target, summary)
                for label, target, summary in (
                    (
                        "timestamp value",
                        "timestamp",
                        "Interpret a physical timestamp with explicit temporal meaning.",
                    ),
                    (
                        "formatted value",
                        "strptime",
                        "Parse a physical value using an explicit format.",
                    ),
                    ("hour prefix", "hour_prefix", "Parse supported hour-prefix source values."),
                )
            ),
        ),
        SemanticNavigationTopic(
            "time.calendars",
            "Define a governed period calendar or event set. Preview can certify these definitions; analysis uses the resulting certified periods or occurrences.",
            (
                object_entry("period_calendar"),
                object_entry("temporal_set"),
                route(
                    "define period calendar",
                    "period_calendar",
                    "Declare reusable business period boundaries.",
                    owns=False,
                ),
                route(
                    "define temporal set",
                    "temporal_set",
                    "Declare reusable event occurrence boundaries.",
                    owns=False,
                ),
                route(
                    "calendar level", "calendar_grain", "Build a named level in a period calendar."
                ),
                _navigation_route(
                    "use existing certified periods or events",
                    LiveHelpTarget(surface="analysis", canonical_id="inputs.time.calendars"),
                    summary="Choose periods or occurrences for an analysis question.",
                    owns_discovery=False,
                ),
            ),
        ),
        SemanticNavigationTopic(
            "catalog",
            "Use closed Catalog values for analysis. Declaration lookup symbols capture a recipe and remain distinct from closed analysis inputs.",
            (
                route(
                    "discover business objects",
                    "SemanticCatalog.search",
                    "Search business meaning or browse with explicit filters.",
                ),
                route(
                    "retrieve and explain a definition",
                    "SemanticCatalog.get",
                    "Retrieve a canonical identity; inspect its complete entry and native value.",
                ),
                route(
                    "declaration lookup kind",
                    "ref",
                    "Choose a typed lookup symbol when authoring a declaration or parameter.",
                ),
                route(
                    "current catalog contract",
                    "SemanticCatalog",
                    "Inspect search, get and explicit validation methods.",
                    owns=False,
                ),
            ),
        ),
        _check_topic(),
        *object_contracts,
    )


def _root_sections() -> tuple[SemanticRootSection, ...]:
    """Keep the semantic root to five explicit information needs."""
    return (
        SemanticRootSection(
            section_id="tasks",
            label="Choose by current semantic question",
            members=tuple(
                _target(target)
                for target in (
                    "model",
                    "expressions",
                    "time",
                    "catalog",
                    "validation",
                )
            ),
            route_summaries=(
                "Define reusable identity, fields, Metrics, and relationships.",
                "Compose closed inputs for this question or expressions for a declaration.",
                "Parse physical time or define business calendars and events.",
                "Retrieve closed values, inspect definitions, or capture declaration lookups.",
                "Choose offline loading/readiness, online validation, preview or a declared data expectation.",
            ),
        ),
    )


def _repair_contracts() -> Mapping[str, SemanticRepairContract]:
    """Build exact repair routes for deterministic authoring/layout failures."""

    rows = (
        SemanticRepairContract(
            error_kind="outside_loader_context",
            kind="reauthor",
            help_target=_target("load"),
            action=(
                "Move physical declarations into a semantic model loaded through "
                "ms.load(); compose native symbols directly and retrieve closed inputs from the Catalog."
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
            help_target=_target("objects.domain"),
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
            error_kind="invalid_binding_ref",
            kind="reauthor",
            help_target=_target("bind"),
            action=(
                "Assign a native field symbol or closed physical field value to a module-level "
                "name before the decorator, then pass that bare name to ms.bind. For a "
                "cross-file field, use ms.ref.<kind>(the field's exact existing path). "
                "Use ms.bind only in the retained Ibis Dimension/TimeDimension bodies."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="missing_time_fold",
            kind="reauthor",
            help_target=_target("Metric.per"),
            action=(
                "Keep the sampled status_time_dimension in additive_except(...) "
                "and express the temporal reduction with per(time_field).reducer()."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="binding_target_missing",
            kind="reauthor",
            help_target=_target("bind"),
            action=(
                "Check the field Ref path named in this error against the loaded catalog. "
                "Declare the missing field or correct its module-level Ref, then reload "
                "before binding it to its owning Entity parameter."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="binding_alias_not_direct",
            kind="reauthor",
            help_target=_target("bind"),
            action=(
                "Call ms.bind(field_ref, entity_parameter) with two positional bare names: "
                "an exact field Ref visible before decoration and a direct Entity parameter. "
                "Move Ref construction outside the body; remove attribute/factory arguments "
                "and transformed or locally aliased Entity arguments."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="binding_entity_mismatch",
            kind="reauthor",
            help_target=_target("bind"),
            action=(
                "Use the direct Entity parameter that owns the referenced field. Compare "
                "the expected and received Entity refs in this error before changing either "
                "the field Ref or the metric's Entity parameters."
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="invalid_filter",
            kind="reauthor",
            help_target=_target("Metric.where"),
            action=(
                "Declare every filter dimension on the metric's target entity, then use "
                "one finite scalar equality value or a non-empty tuple/list of finite "
                "membership values."
            ),
            snippet=(
                "status = ms.dimension_column(\n"
                "    name='status', entity=orders, column='physical_status'\n"
                ")\n"
                "failed = orders.count().where(\n"
                "    (status == 'FAILED') | (status == 'ERROR')\n"
                ")"
            ),
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="filter_value_runtime_incompatible",
            kind="user_choice",
            help_target=_target("Metric.where"),
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
            help_target=_target("load"),
            action=(
                "Point ms.load(workspace_dir=...) at the intended project root and repair "
                "any explicit invalid configuration or authored model files. Missing "
                "marivo.toml and models/ are valid empty-project defaults."
            ),
            snippet="catalog = ms.load(workspace_dir='<project root>')",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="domain_file_missing",
            kind="reauthor",
            help_target=_target("objects.domain"),
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
            error_kind="entity_constructor_as_decorator",
            kind="reauthor",
            help_target=_target("entity"),
            action="Assign ms.entity(name=...) to a name, or omit name and return one Table expression.",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="duplicate_identity_key",
            kind="reauthor",
            help_target=_target("entity"),
            action="Remove repeated names from primary_key.",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="missing_identity_key_column",
            kind="reauthor",
            help_target=_target("entity"),
            action="Add each missing identity key to columns=... or omit the source projection.",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="identity_version_overlap",
            kind="reauthor",
            help_target=_target("entity"),
            action="Remove version coordinates from primary_key and keep them in versioning.",
            preserves_evidence=True,
        ),
        SemanticRepairContract(
            error_kind="organization_error",
            kind="reauthor",
            help_target=_target("load"),
            action=(
                "Read the original exception and traceback when present, repair the failing "
                "declaration or import, then restart Python and reload the catalog."
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
        return (
            *(mode.target for mode in descriptor.construction_modes),
            *(
                _target(target)
                for target in _SUPPORT_DISCOVERY_OWNERS.get(descriptor.semantic_kind, ())
            ),
        )
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
            for member in descriptor.members:
                if not member.label.strip() or (
                    member.summary is not None and not member.summary.strip()
                ):
                    raise ValueError(
                        f"semantic navigation member is incomplete: {descriptor.canonical_id}"
                    )

    exact_ids = {descriptor.canonical_id for descriptor in exact_descriptors}
    expected_root_sections: tuple[SemanticRootGroup, ...] = ("tasks",)
    if tuple(section.section_id for section in registry.root_sections) != expected_root_sections:
        raise ValueError("semantic root sections must use the reviewed compact topology")
    root_routes = tuple(target for section in registry.root_sections for target in section.members)
    if len(root_routes) != len(set(root_routes)):
        raise ValueError("duplicate semantic root route")
    if len(root_routes) > registry.render_budget("root").max_outgoing_routes:
        raise ValueError("semantic root exceeds its route budget")

    known_semantic_type_targets = {
        "SemanticCatalog",
        "CatalogEntry",
        "semantic_catalog",
        "catalog_entry",
    }
    from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY

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
            elif target.surface in {"analysis", "ontology"}:
                # Unified route tests validate cross-surface existence. Importing the
                # analysis or ontology registry here would create a cycle during
                # an analysis-first or ontology-first cold start.
                return
            else:
                raise ValueError(f"unsupported semantic Help route surface: {target.surface}")
        except KeyError as exc:
            raise ValueError(f"unknown external semantic Help route: {owner}") from exc

    for section in registry.root_sections:
        if not section.label.strip() or not section.members:
            raise ValueError(f"semantic root section is incomplete: {section.section_id}")
        for target in section.members:
            validate_target(target, owner=f"root.{section.section_id}")
        if len(section.route_summaries) != len(section.members) or any(
            not summary.strip() for summary in section.route_summaries
        ):
            raise ValueError(f"semantic root decisions are incomplete: {section.section_id}")

    for error_kind, repair_contract in registry._repair_contracts.items():
        if error_kind != repair_contract.error_kind or not error_kind.strip():
            raise ValueError("semantic repair contract key drift")
        target = repair_contract.help_target
        target_id = target.canonical_id
        if target_id is None:
            raise ValueError(f"semantic repair target lacks a canonical id: {error_kind}")
        if target.surface != "semantic":
            continue
        validate_target(target, owner=f"repair.{error_kind}")
        descriptor = registry.by_canonical_id(target_id)
        if not isinstance(descriptor, (AuthoringCapability, SemanticObjectContract)):
            raise ValueError(f"semantic repair target is over-broad: {error_kind}")

    object_contracts = registry.object_contracts
    object_index_entries = tuple(
        member
        for descriptor in help_descriptors
        if isinstance(descriptor, SemanticNavigationTopic)
        for member in descriptor.members
        if isinstance(member, SemanticObjectIndexEntry)
    )
    if len(object_index_entries) != len(object_contracts) or {
        id(entry.contract) for entry in object_index_entries
    } != {id(contract) for contract in object_contracts}:
        raise ValueError("semantic object index must embed the registered object contracts")
    expected_object_kinds = set(SemanticKind) - {SemanticKind.DATASOURCE}
    object_kinds = tuple(contract.semantic_kind for contract in object_contracts)
    if len(object_kinds) != len(set(object_kinds)):
        raise ValueError("duplicate semantic object-kind contract")
    if set(object_kinds) != expected_object_kinds:
        raise ValueError("semantic object-kind contracts must cover every non-datasource kind")

    for contract in object_contracts:
        kind = contract.semantic_kind
        expected_ref_family = _symbol_family(kind)
        if contract.canonical_id not in {f"objects.{kind.value}", kind.value}:
            raise ValueError(f"invalid semantic object-kind canonical id: {kind.value}")
        if contract.catalog_kind != kind.value:
            raise ValueError(f"semantic object catalog kind drift: {kind.value}")
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
            if descriptor.output_family not in contract.construction_families:
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
    distances = {
        target.canonical_id: 1
        for section in registry.root_sections
        for target in section.members
        if target.canonical_id is not None
    }
    pending = deque(distances)
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
        raise ValueError("semantic Help graph exceeds four surface reads: " + ", ".join(too_deep))


def _canonical_link(target: LiveHelpTarget) -> LiveHelpTarget:
    if target.canonical_id is None:
        return target
    from marivo.introspection.live.targets import canonical_target

    identity = (
        semantic_target(target.canonical_id)
        if target.surface == "semantic"
        else canonical_target(target.canonical_id)
    )
    return target.model_copy(update={"canonical_id": identity})


def _canonical_descriptor(row: SemanticHelpDescriptor) -> SemanticHelpDescriptor:
    identity = semantic_target(row.canonical_id)
    if isinstance(row, AuthoringCapability):
        return row.model_copy(
            update={
                "canonical_id": identity,
                "see_also": tuple(_canonical_link(t) for t in row.see_also),
                "input_requirements": tuple(
                    requirement.model_copy(
                        update={
                            "acquisition": tuple(
                                _canonical_link(t) for t in requirement.acquisition
                            )
                        }
                    )
                    for requirement in row.input_requirements
                ),
            }
        )
    if isinstance(row, SemanticObjectContract):
        return replace(
            row,
            canonical_id=identity,
            ref_target=_canonical_link(row.ref_target),
            decisions=tuple(
                replace(d, next_targets=tuple(_canonical_link(t) for t in d.next_targets))
                for d in row.decisions
            ),
            construction_modes=tuple(
                replace(m, target=_canonical_link(m.target)) for m in row.construction_modes
            ),
            relationships=tuple(
                replace(r, target=_canonical_link(r.target)) for r in row.relationships
            ),
            supporting_targets=tuple(_canonical_link(t) for t in row.supporting_targets),
            check_targets=tuple(_canonical_link(t) for t in row.check_targets),
        )
    if isinstance(row, SemanticNavigationTopic):
        members: list[SemanticNavigationRoute | SemanticObjectIndexEntry] = []
        for member in row.members:
            if isinstance(member, SemanticObjectIndexEntry):
                contract = _canonical_descriptor(member.contract)
                assert isinstance(contract, SemanticObjectContract)
                members.append(replace(member, contract=contract))
            else:
                members.append(
                    replace(
                        member,
                        target=_canonical_link(member.target),
                        owns_discovery=member.owns_discovery
                        and member.target.canonical_id != "expressions.Metric",
                    )
                )
        return replace(row, canonical_id=identity, members=tuple(members))
    if isinstance(row, SemanticBuilderTopic):
        return replace(
            row, canonical_id=identity, members=tuple(_canonical_link(t) for t in row.members)
        )
    return replace(
        row,
        canonical_id=identity,
        routes=tuple(
            replace(r, targets=tuple(_canonical_link(t) for t in r.targets)) for r in row.routes
        ),
    )


def _canonical_registry(registry: SemanticCapabilityRegistry) -> SemanticCapabilityRegistry:
    # The Metric role shares its object page; its operations are already linked there.
    metric_operations = tuple(
        t
        for r in registry.help_descriptors
        if isinstance(r, SemanticBuilderTopic) and r.canonical_id == "expressions.Metric"
        for t in r.members
    )
    registry = replace(
        registry,
        _help_descriptors=tuple(
            replace(
                r,
                supporting_targets=tuple(
                    dict.fromkeys((*r.supporting_targets, *metric_operations))
                ),
            )
            if isinstance(r, SemanticObjectContract) and r.semantic_kind == SemanticKind.METRIC
            else r
            for r in registry.help_descriptors
        ),
    )
    rows = tuple(
        _canonical_descriptor(r)
        for r in registry.help_descriptors
        if r.canonical_id != "expressions.Metric"
    )
    canonical_objects = {r.canonical_id: r for r in rows if isinstance(r, SemanticObjectContract)}
    rows = tuple(
        replace(
            r,
            members=tuple(
                replace(m, contract=canonical_objects[m.contract.canonical_id])
                if isinstance(m, SemanticObjectIndexEntry)
                else m
                for m in r.members
            ),
        )
        if isinstance(r, SemanticNavigationTopic)
        else r
        for r in rows
    )
    exact = tuple(r for r in rows if isinstance(r, AuthoringCapability))
    objects = tuple(r for r in rows if isinstance(r, SemanticObjectContract))
    by_id = {r.canonical_id: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate canonical semantic Help address")
    return replace(
        registry,
        _help_descriptors=rows,
        _descriptors=exact,
        _by_id=MappingProxyType(by_id),
        _by_callable_path=MappingProxyType(
            {r.callable_path: r for r in exact if r.callable_path is not None}
        ),
        _object_contracts=objects,
        _root_sections=tuple(
            replace(s, members=tuple(_canonical_link(t) for t in s.members))
            for s in registry.root_sections
        ),
        _source_contracts=MappingProxyType(
            {semantic_target(k): v for k, v in registry._source_contracts.items()}
        ),
        _repair_contracts=MappingProxyType(
            {
                k: replace(v, help_target=_canonical_link(v.help_target))
                for k, v in registry._repair_contracts.items()
            }
        ),
        _render_classes=MappingProxyType(
            {r.canonical_id: _render_class_for_descriptor(r) for r in rows}
        ),
    )


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
    return (
        _canonical_registry(registry)
        if any(
            isinstance(r, SemanticObjectContract) and r.canonical_id.startswith("objects.")
            for r in active_help_descriptors
        )
        else registry
    )


def _symbol_family(kind: SemanticKind) -> str:
    return "".join(part.title() for part in kind.value.split("_")) + "Symbol"


def _ref_role(kind: SemanticKind) -> tuple[str, tuple[LiveHelpTarget, ...]]:
    """Keep each native lookup's static role with its owning operations."""
    roles: dict[SemanticKind, tuple[str, tuple[LiveHelpTarget, ...]]] = {
        SemanticKind.DOMAIN: (
            "Parameter-only Domain identity for domain= and Catalog navigation; no numeric operations.",
            (_target("domain"),),
        ),
        SemanticKind.DATASOURCE: (
            "Parameter-only Datasource identity for datasource= and datasource inspection; no numeric operations.",
            (_target("entity"), LiveHelpTarget(surface="datasource", canonical_id="inspect")),
        ),
        SemanticKind.ENTITY: (
            "Entity identity supports count() and identity parameters; it is not a numeric row field.",
            (_target("Entity.count"), _target("entity")),
        ),
        SemanticKind.DIMENSION: (
            "Categorical field for comparisons and grouping; numeric casts do not grant Measure sum permission.",
            (_target("field"), _target("dimension")),
        ),
        SemanticKind.TIME_DIMENSION: (
            "TimeField for time=, time predicates and grouping; its role and ownership are checked at binding.",
            (_target("TimeField.within"), _target("time_dimension")),
        ),
        SemanticKind.MEASURE: (
            "Numeric row field or declared Measure; arithmetic and source reductions retain unit and sum permission.",
            (_target("row.sum"), _target("measure")),
        ),
        SemanticKind.METRIC: (
            "Completed Metric for Metric arithmetic, filtering and per(...).reducer(); never a row-field identity.",
            (_target("Metric.per"), _target("metric")),
        ),
        SemanticKind.RELATIONSHIP: (
            "Parameter-only directed relationship for explicit via paths and relationship checks; no numeric operations.",
            (_target("Field.via"), _target("relationship")),
        ),
        SemanticKind.PERIOD_CALENDAR: (
            "Parameter-only calendar identity for calendar= and calendar_grain; authority still requires certification.",
            (_target("calendar_grain"), _target("period_calendar")),
        ),
        SemanticKind.TEMPORAL_SET: (
            "Parameter-only temporal-set identity for navigation and temporal authority; no numeric operations.",
            (_target("temporal_set"),),
        ),
    }
    return roles[kind]


def _ref_factory_capabilities() -> tuple[AuthoringCapability, ...]:
    """Build the closed public ``ms.ref`` factory contract and exact leaves."""

    kinds = tuple(SemanticKind)
    parent = _capability(
        "ref",
        None,
        "Create a native typed Deferred lookup symbol for an exact path; no lookup or source read occurs.",
        kind="boundary",
        output="LookupSymbol",
        effects=_NONE,
        see_also=tuple(_target(f"ref.{kind.value}") for kind in kinds),
    )
    leaves = tuple(
        _capability(
            f"ref.{kind.value}",
            f"marivo._symbolic._SymbolFactory.{kind.value}",
            f"Create a native {kind.value} Deferred symbol from an exact path. "
            + _ref_role(kind)[0]
            + " Compose supported recipes directly; the loader binds them. Metadata selectors require a native leaf. Equality builds a recipe; bool, hash, dictionary keys and container membership are not identity operations. Invalid attributes and methods fail when a declaration or parameter captures the recipe; unconsumed recipes and formatted strings have no such guarantee.",
            kind="method",
            output=_symbol_family(kind),
            inputs=(_parameter_input("subject", "Text", "path"),),
            effects=_NONE,
            constraints=("ref_shape",),
            example=f"ms.ref.{kind.value}({_ref_example_path(kind)!r})",
            public_entrypoint=f"ms.ref.{kind.value}",
            see_also=_ref_role(kind)[1],
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
            "marivo.semantic.validation.SourceCheckNamespace.not_null",
            "Require one field to contain no null values.",
            kind="method",
            output="NotNullSourceCheck",
            inputs=(
                _parameter_input(
                    "subject", "DimensionSymbol | TimeDimensionSymbol | MeasureSymbol", "field"
                ),
            ),
            effects=_NONE,
            example="ms.source_check.not_null(ms.ref.dimension('sales.orders.region'))",
            public_entrypoint="ms.source_check.not_null",
        ),
        _capability(
            "source_check.allowed_values",
            "marivo.semantic.validation.SourceCheckNamespace.allowed_values",
            "Require one categorical or temporal field to use only the declared values.",
            kind="method",
            output="AllowedValuesSourceCheck",
            inputs=(
                _parameter_input("subject", "DimensionSymbol | TimeDimensionSymbol", "field"),
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
            "marivo.semantic.validation.SourceCheckNamespace.unique",
            "Require a non-empty same-Entity field tuple to be unique.",
            kind="method",
            output="UniqueSourceCheck",
            inputs=(
                _parameter_input(
                    "subject",
                    "DimensionSymbol | TimeDimensionSymbol | MeasureSymbol",
                    "fields",
                ),
            ),
            effects=_NONE,
            example=("ms.source_check.unique(fields=(ms.ref.dimension('sales.orders.order_id'),))"),
            public_entrypoint="ms.source_check.unique",
        ),
        _capability(
            "source_check.freshness",
            "marivo.semantic.validation.SourceCheckNamespace.freshness",
            "Require one time dimension's maximum value to be within a declared age.",
            kind="method",
            output="FreshnessSourceCheck",
            inputs=(
                _parameter_input("subject", "TimeDimensionSymbol", "field"),
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
            "marivo.semantic.validation.SourceCheckNamespace.relationship_matches",
            "Check unmatched relationship keys only within the selected bounded source scopes.",
            kind="method",
            output="RelationshipMatchesSourceCheck",
            inputs=(
                _parameter_input("subject", "RelationshipSymbol", "relationship"),
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
            "marivo.semantic.validation.SourceCheckNamespace.relationship_cardinality",
            "Require current relationship multiplicity to match one exact expectation.",
            kind="method",
            output="RelationshipCardinalitySourceCheck",
            inputs=(
                _parameter_input("subject", "RelationshipSymbol", "relationship"),
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


def _expression_capabilities() -> tuple[AuthoringCapability, ...]:
    """Canonical receiver contracts; dispatch never depends on method spelling alone."""
    rows: list[AuthoringCapability] = [
        _capability(
            "Entity.show",
            "marivo.semantic.expressions._ExpressionDisplay.show",
            "Print bounded Entity or Relationship metadata without source reads. Native numerical Deferred inputs have no show method; inspect Catalog definitions and AnalysisFrame.show() for captured analysis facts. Pending Frame display does not evaluate or prepare schema.",
            kind="method",
            output="None",
            inputs=(AuthoringInputRequirement(role="receiver", family="Expression"),),
            effects=_NONE,
            example="orders.show()",
            public_entrypoint="orders.show",
            see_also=(
                _target("expressions"),
                LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.show"),
            ),
        ),
        _capability(
            "Entity.contract",
            "marivo.semantic.expressions._ExpressionDisplay.contract",
            "Return bounded Entity or Relationship definition metadata without source reads. Native numerical Deferred inputs carry no contract method; the consuming AnalysisFrame.show() discloses captured analysis facts without evaluating pending inputs.",
            kind="method",
            output="ExpressionContract",
            inputs=(AuthoringInputRequirement(role="receiver", family="Expression"),),
            effects=_NONE,
            example="orders.contract().show()",
            public_entrypoint="orders.contract",
            see_also=(
                _target("expressions"),
                LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.show"),
            ),
        ),
        _capability(
            "Field.isin",
            None,
            "Test membership in literal values on a resolved scalar field. The predicate retains "
            "the field's contribution root and version. Combine predicates with & and |; "
            "use ~ to negate. Pass the predicate to Metric.where or Session.observe(where=...).",
            kind="method",
            output="Deferred",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="Field"),
                _parameter_input("dependency", "SourceScalarSequence", "values"),
            ),
            effects=_NONE,
            example="region.isin(('US', 'CA'))",
            public_entrypoint="region.isin",
            see_also=(_target("predicate"), _target("Metric.where")),
        ),
        _capability(
            "Dimension.isnull",
            None,
            "Construct a missing-value predicate on a native categorical, time, or numeric field. "
            "The predicate retains the field's exact version and contribution root. "
            "Use it in Metric.where(...) or Session.observe(where=...); scalar paths and attribute anchors remain captured.",
            kind="method",
            output="Deferred",
            inputs=(AuthoringInputRequirement(role="receiver", family="Field"),),
            effects=_NONE,
            example="region.isnull()",
            public_entrypoint="region.isnull",
            see_also=(_target("predicate"),),
        ),
    ]
    for prefix, _owner, family, methods in (
        (
            "row",
            "Measure",
            "Deferred",
            (
                "sum",
                "mean",
                "min",
                "max",
                "median",
                "quantile",
                "weighted_mean",
                "count",
                "count_distinct",
                "approx_nunique",
                "approx_median",
                "approx_quantile",
            ),
        ),
        ("Metric", "Metric", "Deferred", ("where", "per", "window")),
        (
            "MetricSamples",
            "MetricSamples",
            "MetricSamples",
            (
                "sum",
                "mean",
                "min",
                "max",
                "median",
                "quantile",
                "weighted_mean",
                "count",
                "count_distinct",
                "approx_nunique",
                "approx_median",
                "approx_quantile",
                "first",
                "last",
            ),
        ),
        ("Entity", "Entity", "Entity", ("count",)),
    ):
        for method in methods:
            inputs = [AuthoringInputRequirement(role="receiver", family=family)]
            if prefix == "row":
                inputs.append(_parameter_input("dependency", "TimeField", "time", optional=True))
                argument = (
                    "0.95, "
                    if method in {"quantile", "approx_quantile"}
                    else "weight=quantity, "
                    if method == "weighted_mean"
                    else ""
                )
                example = f"amount.{method}({argument}time=paid_at)"
            elif prefix == "MetricSamples":
                argument = (
                    "0.95"
                    if method in {"quantile", "approx_quantile"}
                    else "weight=order_count"
                    if method == "weighted_mean"
                    else ""
                )
                example = (
                    f"samples = revenue.per(paid_at)\nsamples.{method}(order_by=paid_at, include_null=False)"
                    if method in {"first", "last"}
                    else f"samples = revenue.per(customer_id)\nsamples.{method}({argument})"
                )
            elif prefix == "Metric":
                parameter, input_family, example = {
                    "where": ("predicate", "Deferred", "revenue.where(status == 'paid')"),
                    "per": (
                        "samples",
                        "Dimension | TimeField | Grouping | tuple[Dimension | TimeField | Grouping | Population, ...] | Population | Grain",
                        'weeks = session.time_scope(start="2026-10-05", end="2026-10-12").by(mv.grain("week"))\n'
                        'daily_average = revenue.per(mv.grain("day")).mean()\n'
                        'folded = session.observe({"daily_average": daily_average}, by=(weeks,)).compute()',
                    ),
                    "window": ("rule", "WindowRule", "revenue.window(mv.all_history())"),
                }[method]
                inputs.append(_parameter_input("dependency", input_family, parameter))
            elif prefix == "Entity":
                inputs.append(_parameter_input("dependency", "TimeField", "time", optional=True))
                example = "orders.count(time=paid_at)"
            else:
                inputs.append(_parameter_input("dependency", "Relationship", "path"))
                example = "payment_time.via((orders_to_payments,))"
            if method in {"quantile", "approx_quantile"}:
                inputs.append(_parameter_input("dependency", "Number", "q"))
            if method == "weighted_mean":
                inputs.append(
                    _parameter_input(
                        "dependency",
                        "Metric" if prefix == "MetricSamples" else "Deferred",
                        "weight",
                    )
                )
            if method in {"first", "last"}:
                inputs.extend(
                    (
                        _parameter_input("dependency", "TimeField", "order_by", optional=True),
                        _parameter_input("dependency", "bool", "include_null", optional=True),
                    )
                )
            if prefix in {"row", "MetricSamples"} and method not in {
                "count",
                "count_distinct",
            }:
                inputs.append(_parameter_input("dependency", "Text", "nulls", optional=True))
            summary = ""
            if prefix == "Metric" and method == "per":
                summary += (
                    "per accepts a Dimension, TimeField, native field Grouping or membership, a nonempty tuple of these, "
                    "or a complete Population; finish with a sample reducer. Field samples use observed "
                    "keys; Population binds every compatible tuple, including empty samples. "
                    "Place correlated mv.membership(...) rules directly in samples, or use (P, rule, ...). "
                    "Membership fields expand in order; explicit non-first fields locate interleaved axes. "
                    "Reject repeated first fields, overlapping rules and reordered member fields. "
                    "Internal P may select a subset; empty or unmatched samples retain external coordinates. "
                    "Each nested layer keeps its own domain and complete allocation rules. "
                    "A Grain uses every inner calendar cell, including empty cells under the child's empty policy. "
                    "Each contribution uses its own declared time role. A finite observation TimeScope or CalendarAxis "
                    "supplies timezone and calendar authority; pass the Grain, not scope.by(grain), to per. "
                    "Inner grains must nest: day in week/month is valid; week in month is not. Certified grains "
                    "retain exact snapshot containment. Partial edge cells clip actual bounds and each counts once. "
                    "Nested calendar layers validate against their nearest enclosing cell. "
                    "Semantic Grain lookups bind the Session's current certified snapshot and require certification. "
                    "Windows inside calendar samples or wrapping calendar folds are unsupported and reject before execution. "
                    "TimeField sample reducers use represented samples without scheduled coverage or implicit fill; "
                    "sample_interval describes cadence and does not bucket timestamps or require a finite observation period. "
                )
            elif prefix == "Metric" and method == "where":
                summary += (
                    "Filter source contributions before reduction within one source reduction or common paired scope using a native "
                    "Deferred condition, including (status == 'paid') | mv.in_population(population). "
                    "Repeated where calls conjoin conditions on the same input check domain; neither "
                    "boolean side shields the other's errors. See semantic.predicate for grammar. "
                    "Composite or sampled Metrics must filter the eligible child reduction. "
                    "This is distinct from AnalysisFrame.where on current rows."
                )
            elif prefix == "Metric" and method == "window":
                summary += (
                    "Attach one source-free observation WindowRule; nested windows are rejected."
                )
            elif method in {"first", "last"}:
                summary += (
                    f"Select the {method} internal sample. "
                    "order_by may name an internal TimeField; when omitted, exactly one internal "
                    "TimeField must be inferable. Time keys must uniquely order the samples. "
                    "include_null defaults to False (skip NA); True retains the selected sample's NA."
                    " Calendar Grain samples use captured cell order; omit order_by."
                )
            elif prefix == "row" and method in {
                "median",
                "quantile",
                "approx_median",
                "approx_quantile",
            }:
                summary += (
                    "Exact median/quantile uses native linear interpolation h=(n-1)q on DuckDB, PostgreSQL "
                    "and ClickHouse; MySQL/Trino reject exact source quantiles at compilation. "
                    "ClickHouse exact inputs must be integer/float; source Duration quantiles are unavailable. Explicit approximate methods use DuckDB approx_quantile, "
                    "Trino approx_percentile or ClickHouse TDigest; PostgreSQL/MySQL reject approximation. "
                    "Median uses q=0.5; quantile requires q in [0, 1]. Empty/all-NULL inputs return NA; nulls='error' rejects NULL inputs. "
                    "Native nonfinite values follow the backend. Integer/float results are float64; Decimal retains its declared "
                    "precision and scale. Algorithms may differ by backend; no cross-backend error bound "
                    "or automatic exact/approximate substitution is promised."
                )
            elif method == "sum":
                summary += (
                    "sum requires business permission; empty input returns zero and nonempty all-NA input returns null. "
                    "Integer SUM uses native int64 accumulation; Decimal SUM returns Decimal(38,input_scale). "
                    "Native errors/wrapping differ by kernel; no protective integer widening. "
                    "MySQL retains native widened SUM until safe Decimal transport. "
                    "Native NaN/Infinity are retained in completed and saved Frames."
                )
            elif method == "mean":
                summary += (
                    "Mean skips NULLs and returns NA without valid inputs; it retains the declared mean type. "
                    "Qualified sample folds execute natively without failure fallback."
                )
            elif method == "weighted_mean":
                summary += (
                    "Compute sum(value * weight) / sum(weight) over valid pairs. "
                    "Zero or cancelling weight sums yield NA. "
                    + (
                        "weight must be a completed Metric over the same internal samples."
                        if prefix == "MetricSamples"
                        else "weight must be a numeric row expression on the same contribution root."
                    )
                )
            elif method in {"min", "max"}:
                summary += (
                    "Return the minimum numeric value. "
                    if method == "min"
                    else "Return the maximum numeric value. "
                ) + "Skip NULLs; empty/all-NULL inputs return NA."
            elif method == "count":
                summary += (
                    "Count complete Entity contribution rows. "
                    if prefix == "Entity"
                    else "Count non-NULL values. "
                ) + "Empty input returns zero; the result is an int64 count with unit '1'."
            elif method == "count_distinct":
                summary += "Count distinct non-NULL values exactly; empty input returns zero. The result is an int64 count with unit '1'."
                if prefix == "row":
                    summary += (
                        " Native source compilation uses COUNT(DISTINCT), or ClickHouse uniqExact."
                    )
            elif method == "approx_nunique":
                summary += "Estimate the distinct non-NULL count explicitly. The result is an int64 count with unit '1'. "
                summary += (
                    "Native source compilation uses DuckDB approx_count_distinct, Trino approx_distinct or ClickHouse uniqHLL12; PostgreSQL/MySQL reject this operation."
                    if prefix == "row"
                    else "Internal samples have no qualified local approximate distinct kernel."
                )
            else:
                summary += (
                    "Reduce internal sample values with "
                    + ("an explicit approximate " if method.startswith("approx_") else "an exact ")
                    + (
                        "median (q=0.5). "
                        if method.endswith("median")
                        else "quantile; pass q in [0, 1]. "
                    )
                    + "Empty/all-NULL inputs return NA; no exact/approximate substitution is inferred."
                )
            if prefix == "MetricSamples":
                summary += (
                    " Time samples use represented values without scheduled coverage or implicit fill, "
                    "independently of formula shape or execution route. Explicit domains "
                    "and NULL/weight policies still apply."
                )
            if prefix in {"row", "MetricSamples"} and method not in {"count", "count_distinct"}:
                summary += (
                    " nulls='ignore' adds no assertion; nulls='error' asserts no NULLs on the demanded input; "
                    "empty input passes. Successful values use the same computation."
                )
            rows.append(
                _capability(
                    f"{prefix}.{method}",
                    f"marivo.semantic.expressions.Entity.{method}" if prefix == "Entity" else None,
                    summary,
                    kind="method",
                    output="Deferred",
                    inputs=tuple(inputs),
                    effects=_NONE,
                    example=example,
                    public_entrypoint=f"{'amount' if prefix == 'row' else 'samples' if prefix == 'MetricSamples' else 'revenue' if prefix == 'Metric' else 'orders' if prefix == 'Entity' else 'payment_time'}.{method}",
                    see_also=(
                        _target("predicate"),
                        _target("expressions"),
                        LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.where"),
                    )
                    if prefix == "Metric" and method == "where"
                    else (
                        (_target("expressions.row"),)
                        if prefix in {"row", "MetricSamples", "Entity"}
                        else (_target("expressions"),)
                    ),
                )
            )
    for target, _owner, _method, _output, parameter, family, example in (
        (
            "Field.via",
            "_FieldComparisons",
            "via",
            "Dimension | TimeField | Measure",
            "path",
            "Relationship",
            "region.via((orders_to_customers,))",
        ),
        (
            "Field.at",
            "_FieldComparisons",
            "at",
            "Dimension | TimeField | Measure",
            "anchor",
            "InstantAnchor",
            "region.at(session.instant('2026-01-01'))",
        ),
        (
            "TimeField.within",
            "_FieldComparisons",
            "within",
            "Deferred",
            "scope",
            "TimeScope",
            "created_at.within(scope)",
        ),
    ):
        rows.append(
            _capability(
                target,
                None,
                "Construct an immutable scalar field binding or half-open time predicate. Directed paths must be single-valued; at requires a versioned owner and an exact anchor. No schema or business reads.",
                kind="method",
                output="Deferred",
                inputs=(
                    AuthoringInputRequirement(role="receiver", family="Field"),
                    _parameter_input("dependency", family, parameter),
                ),
                effects=_NONE,
                example=example,
                public_entrypoint=example.split("(", 1)[0],
                see_also=(_target("expressions"),),
            )
        )
    return tuple(rows)


def _result_method_capabilities() -> tuple[AuthoringCapability, ...]:
    """Register native consumption methods advertised by semantic result types."""
    rows: list[AuthoringCapability] = []
    for module, owner, name, output, example in (
        (
            "catalog",
            "CatalogEntry",
            "related",
            "CatalogPage",
            'entry.related("dependencies").show()',
        ),
        ("catalog", "CatalogEntry", "to_dict", "dict[str, JsonValue]", "data = entry.to_dict()"),
        ("catalog", "CatalogEntry", "to_json", "Text", "text = entry.to_json()"),
        (
            "catalog",
            "CatalogEntry",
            "show",
            "None",
            'entry.show(section="context", max_output_bytes=None)',
        ),
        (
            "catalog_entries",
            "CatalogPage",
            "to_dict",
            "dict[str, JsonValue]",
            "data = page.to_dict()",
        ),
        ("catalog_entries", "CatalogPage", "to_json", "Text", "text = page.to_json()"),
        ("catalog_entries", "CatalogPage", "show", "None", "page.show(max_output_bytes=None)"),
        (
            "catalog_entries",
            "CatalogPage",
            "render",
            "Text",
            "text = page.render(max_output_bytes=None)",
        ),
    ):
        rows.append(
            _capability(
                f"{owner}.{name}",
                f"marivo.semantic.{module}.{owner}.{name}",
                "Display the current page with exact shown/omitted counts. max_output_bytes=None removes the display budget, not pagination; items/to_json contain the page and next_cursor retrieves later pages. No datasource or model calls."
                if owner == "CatalogPage" and name in ("show", "render")
                else "Read complete captured directory data or display a bounded projection. Related navigation stays within the loaded scope; no datasource or model calls.",
                kind="method",
                output=output,
                inputs=(AuthoringInputRequirement(role="receiver", family=owner),),
                effects=_LOCAL,
                example=example,
                public_entrypoint=f"{'page' if owner == 'CatalogPage' else 'entry'}.{name}",
            )
        )
    for module, owner in (
        ("readiness", "ReadinessReport"),
        ("readiness", "ReadinessInputSummary"),
        ("readiness", "ReadinessIssue"),
        ("validation", "ValidationReport"),
        ("validation", "ValidationCheckResult"),
    ):
        rows.append(
            _capability(
                f"{owner}.to_dict",
                f"marivo.semantic.{module}.{owner}.to_dict",
                "Serialize this result's current facts to a dictionary without rerunning checks or reading business data.",
                kind="method",
                output="dict[str, object]",
                inputs=(AuthoringInputRequirement(role="receiver", family=owner),),
                example="facts = result.to_dict()",
                public_entrypoint="result.to_dict",
            )
        )
    return tuple(rows)


def _build_registry() -> SemanticCapabilityRegistry:
    """Build the immutable semantic descriptor catalog from live callables."""
    descriptor_rows = (
        # ------------------------------------------------------------------
        # browse_load
        # ------------------------------------------------------------------
        _capability(
            "load",
            "marivo.semantic.catalog.load",
            (
                "Load the read-only semantic catalog.\n  workspace_dir is an exact project root;"
                " when omitted: MARIVO_PROJECT_ROOT > nearest ancestor manifest > current directory."
                "\n  Always loads the local models/ root; marivo.toml [semantic].layer_paths adds"
                " external models/ roots containing datasources/ and semantic/."
                "\n  domains=None loads all domains; a name or sequence selects only those domain"
                " directories, without automatic dependency expansion. Include every required domain."
                " Filtered Relationship references may warn; unresolved required Metric dependencies fail loading."
            ),
            output="SemanticCatalog",
            effects=_LOCAL,
            preconditions=(
                "Restart Python after package upgrades or model edits/deletions; ordinary imports are not guaranteed to hot-reload.",
                "Execution failures expose exception_type and the complete original traceback.",
            ),
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
            output="DomainSymbol",
            inputs=(
                _parameter_input("mapping_key", "DomainName", "name"),
                _parameter_input("dependency", "OwnerName", "owner"),
            ),
            effects=_AUTHOR,
            constraints=("domain_owner_required",),
            example="ms.domain(name='sales', owner=accountable_owner)",
        ),
        _capability(
            "entity",
            "marivo.semantic._authoring_decorators.entity",
            (
                "Declare an Entity over one datasource source. Table columns map output "
                "names to physical names; md.inspect supplies current names and types."
                "\n  Explicit name declares directly; omitting name returns a one-expression "
                "Table decorator whose output schema and grain own downstream fields and identity."
                "\n  Entity bodies admit the injected Table, inline literals, deterministic public "
                "ibis root symbols and typed scalar builtin UDFs from ibis.udf.scalar.builtin "
                "with at least one argument and concrete argument/return types. Python UDF "
                "bodies are not executed. Arbitrary helpers, Ibis submodules and "
                "Python/Pandas/Arrow UDFs are rejected."
            ),
            output="EntitySymbol",
            inputs=(
                _parameter_input("mapping_key", "EntityName", "name", optional=True),
                _parameter_input("dependency", "DatasourceSymbol", "datasource"),
                _parameter_input("dependency", "EntitySourceIR", "source"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ref_shape",
                "entity_constructor_assignment",
                "entity_identity_key_unique",
                "entity_version_key_separate",
            ),
            example=(
                "warehouse = ms.ref.datasource('warehouse')\n"
                "orders = ms.entity(\n"
                "    name='orders', datasource=warehouse,\n"
                "    source=md.table('orders', columns={'order_id': 'id', 'amount': 'total'}),\n"
                "    primary_key=['order_id'],\n"
                ")"
            ),
            see_also=(
                LiveHelpTarget(surface="datasource", canonical_id="table"),
                LiveHelpTarget(surface="datasource", canonical_id="inspect"),
                _target("expressions.row"),
            ),
            invocation_shape="direct",
        ),
        _capability(
            "dimension",
            "marivo.semantic._authoring_decorators.dimension",
            "Declare a calculated dimension on an entity.",
            output="DimensionSymbol",
            inputs=(
                _parameter_input("mapping_key", "DimensionName", "name", optional=True),
                _parameter_input("subject", "EntitySymbol", "entity"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ast_single_return", "ast_forbidden_statement"),
            example=(
                "@ms.dimension(name='region', entity=orders)\n"
                "def region(rows):\n"
                "    return rows.region"
            ),
            invocation_shape="decorator",
        ),
        _capability(
            "dimension_column",
            "marivo.semantic._authoring_decorators.dimension_column",
            "Declare a column-backed dimension on an entity.",
            output="DimensionSymbol",
            inputs=(
                _parameter_input("mapping_key", "DimensionName", "name"),
                _parameter_input("subject", "EntitySymbol", "entity"),
                _parameter_input("dependency", "ColumnName", "column"),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=("region = ms.dimension_column(name='region', entity=orders, column='region')"),
        ),
        _capability(
            "time_dimension",
            "marivo.semantic._authoring_decorators.time_dimension",
            "Declare a calculated time dimension on an entity.",
            output="TimeDimensionSymbol",
            inputs=(
                _parameter_input("mapping_key", "TimeDimensionName", "name", optional=True),
                _parameter_input("subject", "EntitySymbol", "entity"),
                _parameter_input("dependency", "ParseVariant", "parse", optional=True),
                _parameter_input(
                    "dependency", "tuple[int, minute | hour]", "sample_interval", optional=True
                ),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ast_single_return",
                "time_dimension_dtype_compat",
                "sample_interval_valid",
            ),
            example=(
                "@ms.time_dimension(name='log_date', entity=orders)\n"
                "def log_date(rows):\n"
                "    return rows.log_date"
            ),
            invocation_shape="decorator",
        ),
        _capability(
            "time_dimension_column",
            "marivo.semantic._authoring_decorators.time_dimension_column",
            "Declare a column-backed time dimension on an entity.",
            output="TimeDimensionSymbol",
            inputs=(
                _parameter_input("mapping_key", "TimeDimensionName", "name"),
                _parameter_input("subject", "EntitySymbol", "entity"),
                _parameter_input("dependency", "ColumnName", "column"),
                _parameter_input("dependency", "ParseVariant", "parse", optional=True),
                _parameter_input(
                    "dependency", "tuple[int, minute | hour]", "sample_interval", optional=True
                ),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "ref_shape",
                "time_dimension_dtype_compat",
                "sample_interval_valid",
            ),
            example=(
                "log_date = ms.time_dimension_column(name='log_date', entity=orders, "
                "column='log_date', parse=ms.strptime('%Y%m%d'))"
            ),
        ),
        _capability(
            "period_calendar",
            "marivo.semantic._authoring_temporal.period_calendar",
            "Declare a finite governed period calendar over an exhaustive civil-date spine.",
            output="PeriodCalendarSymbol",
            inputs=(
                _parameter_input("mapping_key", "Text", "name"),
                _parameter_input("subject", "TimeDimensionSymbol", "date"),
                _parameter_input("dependency", "DimensionSymbol", "levels"),
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
            output="TemporalSetSymbol",
            inputs=(
                _parameter_input("mapping_key", "Text", "name"),
                _parameter_input("subject", "DimensionSymbol", "occurrence_id"),
                _parameter_input("dependency", "TimeDimensionSymbol", "start"),
                _parameter_input("dependency", "TimeDimensionSymbol", "end"),
                _parameter_input("dependency", "DimensionSymbol", "category", optional=True),
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
            "calendar_grain",
            "marivo.semantic._authoring_temporal.calendar_grain",
            "Construct a source-free semantic Grain lookup for one governed calendar level. Metric windows and calendar samples bind it against the consuming Session's frozen certified scope; naming does not require a loaded calendar.",
            output="Grain",
            inputs=(
                _parameter_input("subject", "PeriodCalendarSymbol", "calendar"),
                _parameter_input("dependency", "Granularity", "level"),
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
            "Name a pure numeric row expression over established quantities; it does not convert a Dimension. For one numeric Entity output column, use dimension_column and measure_column with different semantic names for grouping and quantity roles. Omitted unit/additivity inherit facts; new assertions preserve compatible units and fixed time constraints. Additivity selects existing axes by kind/path. Naming and ai_context do not change computation. No loader, registration or source access.",
            output="Deferred",
            inputs=(
                _parameter_input("dependency", "Text", "unit", optional=True),
                _parameter_input("dependency", "Additivity", "additivity", optional=True),
                _parameter_input("dependency", "AiContextValue", "ai_context", optional=True),
            ),
            effects=_NONE,
            constraints=("ast_single_return", "ast_forbidden_statement"),
            example=(
                "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', additivity=ms.additive_all(), unit='1')\n"
                "unit_price = ms.measure_column(name='unit_price', entity=orders, column='price', additivity=ms.non_additive(), unit='CNY')\n"
                "@ms.measure(additivity=ms.additive_all())\n"
                "def order_amount():\n    return quantity * unit_price"
            ),
            see_also=(_target("measure_column"), _target("metric")),
            invocation_shape="decorator",
        ),
        _capability(
            "measure_column",
            "marivo.semantic._authoring_decorators.measure_column",
            "Declare a column-backed measure on an entity." + _ADDITIVITY_HELP,
            output="MeasureSymbol",
            inputs=(
                _parameter_input("mapping_key", "MeasureName", "name"),
                _parameter_input("subject", "EntitySymbol", "entity"),
                _parameter_input("dependency", "ColumnName", "column"),
                _parameter_input("dependency", "Additivity", "additivity"),
                _parameter_input(
                    "dependency", "TimeDimensionSymbol", "status_time_dimension", optional=True
                ),
            ),
            effects=_AUTHOR,
            constraints=("active_loader_context", "ref_shape"),
            example=(
                "amount = ms.measure_column("
                "name='amount', entity=orders, column='amount', additivity=ms.additive_all())"
            ),
        ),
        _capability(
            "relationship",
            "marivo.semantic._authoring_decorators.relationship",
            "Declare a directed Entity mapping; key coverage derives structural cardinality, not required matches.",
            output="RelationshipSymbol",
            inputs=(
                _parameter_input("mapping_key", "RelationshipName", "name"),
                _parameter_input("subject", "RelationshipEndpoint", "from_entity"),
                _parameter_input("dependency", "RelationshipEndpoint", "to_entity"),
                _parameter_input("dependency", "JoinKey", "keys"),
            ),
            effects=_AUTHOR,
            constraints=(
                "active_loader_context",
                "relationship_endpoints",
                "relationship_mapping",
                "ref_shape",
            ),
            example=(
                "ms.relationship(name='orders_to_customers', from_entity=orders, "
                "to_entity=customers, keys=[ms.join_on(order_customer_id, customer_id)])"
            ),
            see_also=(_target("join_on"),),
        ),
        _capability(
            "join_on",
            "marivo.semantic._authoring_values.join_on",
            "Build a join-key specification for a relationship.",
            output="JoinKey",
            inputs=(
                _parameter_input(
                    "dependency", "DimensionSymbol | TimeDimensionSymbol", "from_key", "to_key"
                ),
            ),
            effects=_AUTHOR,
            example="ms.join_on(order_customer_id, customer_id)",
        ),
        # ------------------------------------------------------------------
        # Low-level expression builders (public authoring surface)
        # ------------------------------------------------------------------
        _capability(
            "rescale",
            "marivo.semantic._rescale.rescale",
            "Divide a numeric Row or completed Metric by a finite, strictly positive int, float or Decimal constant and explicitly declare the target unit. A known source unit is required after binding. Boolean and dynamic divisors are rejected. Existing factorable units are canonicalized and opaque unit strings are retained. The result preserves stage, source roots, time restrictions and sum permissions. The author owns the factor and physical/business correctness, including cross-dimension assertions and divisor=1. No conversion is inferred; ordinary scalar division retains the source unit. Fixed duration factors do not follow an analysis window automatically.",
            output="Deferred",
            inputs=(
                _parameter_input("subject", "Deferred", "expr"),
                _parameter_input("dependency", "Number", "divisor"),
                _parameter_input("dependency", "Text", "unit"),
            ),
            effects=_NONE,
            example=(
                "# total_bytes is a closed byte-valued Metric over 86400 seconds.\n"
                "daily_bandwidth = ms.rescale(\n"
                "    total_bytes, divisor=86400 * 1024**3, unit='GiB/s',\n"
                ")"
            ),
            see_also=(_target("expressions"), _target("measure")),
        ),
        _capability(
            "bind",
            "marivo.semantic._authoring_values.bind",
            "Apply a constructed leaf field or native lookup symbol to a direct Entity table parameter inside a restricted Ibis Dimension/TimeDimension declaration. Loader binding checks kind and ownership in the completed consumer scope; lookup symbols allow forward dependencies. Pure Measure/Metric bodies use constructed fields directly.",
            output="IbisValue",
            inputs=(
                _parameter_input(
                    "subject", "DimensionSymbol | TimeDimensionSymbol | MeasureSymbol", "field"
                ),
                _parameter_input("dependency", "IbisTable", "entity_alias"),
            ),
            effects=_NONE,
            constraints=("ast_single_return", "ref_shape"),
            example="@ms.dimension(entity=orders)\ndef has_amount(rows):\n    return ms.bind(amount, rows) > 0",
            see_also=(_target("dimension"), _target("time_dimension")),
        ),
        _capability(
            "metric",
            "marivo.semantic._authoring_declarations.metric",
            "Name a completed Metric with a zero-argument pure expression body. Fresh local expressions, a leading docstring and tuple/list literal arguments are allowed; lists are frozen as tuples. Arbitrary calls, table parameters, Ibis returns and ms.bind are rejected. Operator policies stay on their operators. Declaration is source-free and does not publish a Catalog Metric.",
            output="Deferred",
            inputs=(
                _parameter_input("dependency", "DomainSymbol", "domain", optional=True),
                _parameter_input("dependency", "AiContextValue", "ai_context", optional=True),
            ),
            effects=_NONE,
            constraints=("ast_single_return", "ast_forbidden_statement"),
            example=(
                "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', additivity=ms.additive_all(), unit='1')\n"
                "unit_price = ms.measure_column(name='unit_price', entity=orders, column='price', additivity=ms.non_additive(), unit='CNY')\n"
                "@ms.measure(additivity=ms.additive_all())\n"
                "def amount():\n    return quantity * unit_price\n"
                "@ms.metric()\ndef revenue():\n    return amount.sum()"
            ),
            see_also=(_target("measure"), _target("expressions")),
            invocation_shape="decorator",
        ),
        _capability(
            "ai_context",
            "marivo.semantic._authoring_values.ai_context",
            "Build captured business context, guardrails and normalized explicit search aliases. Aliases do not register new semantic identities.",
            output="AiContextValue",
            effects=_AUTHOR,
            constraints=("ai_context_schema",),
            example="ms.ai_context(business_definition='Sum of accepted order amounts.', aliases=['accepted revenue'])",
        ),
        _capability(
            "snapshot",
            "marivo.semantic._authoring_values.snapshot",
            "Declare daily Entity snapshots with required partition_field and business timezone. The field owns encoding; date D applies from D midnight and exact missing days remain absent.",
            output="ValiditySpec",
            inputs=(_parameter_input("dependency", "TimeDimensionSymbol", "partition_field"),),
            effects=_AUTHOR,
            constraints=("entity_versioning_valid",),
            example="ms.snapshot(partition_field=snapshot_date, timezone='Asia/Shanghai')",
        ),
        _capability(
            "validity",
            "marivo.semantic._authoring_values.validity",
            "Declare validity bounds with required valid_from, valid_to, interval and open_end. DATE bounds require business timezone; timestamp bounds reject version timezone and use field parser/source authority.",
            output="ValiditySpec",
            inputs=(
                _parameter_input("dependency", "TimeDimensionSymbol", "valid_from", "valid_to"),
            ),
            effects=_AUTHOR,
            example=(
                "ms.validity(valid_from=valid_from, valid_to=valid_to, "
                "interval='closed_open', open_end=(None,))"
            ),
        ),
        _capability(
            "additive_over",
            "marivo.semantic._authoring_values.additive_over",
            "Declare exactly the native coordinates on which values may be added. Pass one or more Dimension or TimeDimension fields positionally; unpack a collection with *axes.",
            output="Additivity",
            inputs=(
                AuthoringInputRequirement(
                    role="dependency",
                    family="DimensionSymbol | TimeDimensionSymbol",
                    parameter_names=("axes",),
                    max_count=None,
                ),
            ),
            effects=_AUTHOR,
            constraints=("ref_shape",),
            example="ms.additive_over(region, product)",
        ),
        _capability(
            "additive_all",
            "marivo.semantic._authoring_values.additive_all",
            "Declare additivity on all native coordinates. Accepts no arguments. Use additive_except for fixed axes, or additive_over to permit only named axes. This does not prove coverage.",
            output="Additivity",
            effects=_AUTHOR,
            example="ms.additive_all()",
            see_also=(_target("additive_except"), _target("additive_over")),
        ),
        _capability(
            "additive_except",
            "marivo.semantic._authoring_values.additive_except",
            (
                "Declare additivity on all native coordinates except one or more fixed axes, "
                "passed positionally. Unpack a collection with *axes. Fixed axes do not "
                "select a temporal reduction or declare a status-time role. A physical Measure "
                "declares status_time_dimension separately and includes it among these axes. "
                "Temporal sample reduction is explicit in per(time_field).reducer()."
            ),
            output="Additivity",
            constraints=("ref_shape",),
            inputs=(
                AuthoringInputRequirement(
                    role="dependency",
                    family="DimensionSymbol | TimeDimensionSymbol",
                    parameter_names=("axes",),
                    max_count=None,
                ),
            ),
            effects=_AUTHOR,
            example=(
                "sample_time = ms.time_dimension_column(\n"
                "    name='sample_time', entity=inventory, column='sample_time',\n"
                "    parse=ms.timestamp(timezone='UTC'), sample_interval=(5, 'minute'),\n"
                ")\n"
                "quantity = ms.measure_column(\n"
                "    name='quantity', entity=inventory, column='quantity',\n"
                "    additivity=ms.additive_except(sample_time),\n"
                "    status_time_dimension=sample_time,\n"
                ")"
            ),
            see_also=(_target("measure_column"), _target("metric")),
        ),
        _capability(
            "non_additive",
            "marivo.semantic._dsl_authoring.non_additive",
            "Declare that displayed values cannot be added across native coordinates.",
            output="Additivity",
            effects=_AUTHOR,
            example="ms.non_additive()",
        ),
        _capability(
            "timestamp",
            "marivo.semantic._authoring_values.timestamp",
            "Interpret a native timestamp using parser or resolved reader authority. Projection uses native gap/fold behavior; raw local-clock membership uses wall order (later fold occurrence, gap transition instant). Aware sources reject parser timezone. Applies only to the TimeDimension projection; it does not rewrite Entity primary_key. Naive identity keeps its original wall-clock value and precision.",
            output="TimestampSpec",
            effects=_AUTHOR,
            example="ms.timestamp(timezone='UTC')",
        ),
        _capability(
            "strptime",
            "marivo.semantic._authoring_values.strptime",
            "Declare canonical source encoding. Date-only formats reject timezone; time-bearing values use parser or reader authority. Raw ordered local-clock membership uses wall order; projection remains backend-native.",
            output="StrptimeSpec",
            effects=_AUTHOR,
            constraints=("time_dimension_partition_pushdown",),
            example="ms.strptime('%Y%m%d')",
        ),
        _capability(
            "hour_prefix",
            "marivo.semantic._authoring_values.hour_prefix",
            "Combine a 0..23 integer or canonical two-digit hour string with a civil-date prefix, inheriting its source timezone context and reader default. Raw local-clock membership uses wall order; no independent timezone parameter.",
            output="HourPrefixSpec",
            effects=_AUTHOR,
            example="ms.hour_prefix(log_date)",
        ),
        # ------------------------------------------------------------------
        # scoped runtime observations and online validation
        # ------------------------------------------------------------------
        _capability(
            "preview",
            "marivo.semantic.catalog.SemanticCatalog.preview",
            (
                "Run one scoped data preview for a current catalog entry or exact ref. "
                "Ordinary Metric previews support DuckDB, PostgreSQL, MySQL, Trino and ClickHouse, reusing the nullable Row/Metric kernel; Population/sample/window preparation rejects. Each Entity contributes at most min(scope.max_rows, 10,000) output rows and reports "
                "an approximate result; period calendars and temporal sets "
                "publish their dedicated certified artifacts. Build scopes with "
                "md.unpruned (no pruning predicate), md.time_range (physical time range), or "
                "md.partition (physical partition values), all with positive row/time guards. "
                "Physical scopes reuse datasource partition preflight; no partition is selected automatically. For multiple dependency Entities, supply exact (EntitySymbol, scope) pairs. "
                "limit must be an integer from 1 to 100 inclusive and bounds retained preview "
                "rows; scope.max_rows independently guards source acquisition. A temporal "
                "preview can certify complete coverage while retaining fewer preview rows. coverage reports Entity input counts and caps; exhaustion is unknown when a cap is reached. execution_limits reports native timeout enforcement; no scan row/byte cap is requested and scan statistics are unknown. Unordered samples are not full-source estimates."
            ),
            kind="method",
            output="PreviewResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                _parameter_input("subject", "CatalogEntry | LookupSymbol", "ref"),
                _parameter_input(
                    "scope",
                    "AuthoringScope | Sequence[tuple[EntitySymbol, AuthoringScope]]",
                    "scope",
                ),
                _parameter_input(
                    "dependency",
                    "Sequence[tuple[EntitySymbol, JSON source parameter mapping]]",
                    "source_bindings",
                    optional=True,
                ),
            ),
            effects=_CERTIFYING_PREVIEW,
            constraints=("backend_factory_available",),
            example=(
                "catalog.preview(revenue, scope=md.unpruned(max_rows=1000, timeout_seconds=30))"
            ),
            preconditions=("a current loaded SemanticCatalog",),
            repair_kinds=("reconnect", "reauthor", "user_choice"),
            public_entrypoint="catalog.preview",
            see_also=(
                *(
                    LiveHelpTarget(surface="datasource", canonical_id=target)
                    for target in ("unpruned", "time_range", "partition")
                ),
                LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
            ),
        ),
        _capability(
            "preview_many",
            "marivo.semantic.catalog.SemanticCatalog.preview_many",
            (
                "Run scoped data previews for a non-empty entry/ref sequence. Ordinary Metric "
                "previews support all five SQL backends and reuse datasource partition preflight "
                "for every input before any business read. Each Entity contributes at most "
                "min(scope.max_rows, 10,000) output rows and reports an "
                "approximate result; period calendars and temporal sets "
                "publish their dedicated certified artifacts through preview. limit must "
                "be an integer from 1 to 100 inclusive and bounds retained preview rows "
                "per result; scope.max_rows independently guards Entity output acquisition. "
                "coverage reports actual Entity input counts and caps. execution_limits "
                "reports native timeout enforcement; no scan row/byte cap is requested. "
                "Physical scan statistics remain unknown."
            ),
            kind="method",
            output="PreviewBatchResult",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject",
                    family="CatalogEntry | LookupSymbol",
                    parameter_names=("refs",),
                    max_count=None,
                ),
                _parameter_input(
                    "scope",
                    "AuthoringScope | Sequence[tuple[EntitySymbol, AuthoringScope]]",
                    "scope",
                ),
                _parameter_input(
                    "dependency",
                    "Sequence[tuple[EntitySymbol, JSON source parameter mapping]]",
                    "source_bindings",
                    optional=True,
                ),
            ),
            effects=_PREVIEW,
            constraints=("backend_factory_available",),
            example=(
                "catalog.preview_many([revenue], "
                "scope=md.unpruned(max_rows=1000, timeout_seconds=30))"
            ),
            preconditions=("a current loaded SemanticCatalog",),
            repair_kinds=("reconnect", "reauthor", "user_choice"),
            public_entrypoint="catalog.preview_many",
            see_also=(LiveHelpTarget(surface="analysis", canonical_id="session.observe"),),
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
            "validate",
            "marivo.semantic.catalog.SemanticCatalog.validate",
            (
                "Connect to the selected dependency closure and validate Entity outputs, "
                "field bindings, numeric/duration types and temporal parser/timezone contracts against "
                "real physical types. Unavailable types disclose column names and raw types in "
                'observed["unavailable_columns"]; direct Entities still check independent fields. '
                "Preview and analysis reuse these field checks. "
                "Explicit data expectations require a bounded scope. Does not change readiness "
                "or qualify arbitrary analysis methods."
            ),
            kind="method",
            output="ValidationReport",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                AuthoringInputRequirement(
                    role="subject",
                    family="CatalogEntry | LookupSymbol",
                    parameter_names=("refs",),
                    max_count=None,
                ),
                _parameter_input("dependency", "SourceCheck", "checks", optional=True),
                _parameter_input(
                    "scope",
                    "AuthoringScope | Sequence[tuple[EntitySymbol, AuthoringScope]]",
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
        # ------------------------------------------------------------------
        # readiness
        # ------------------------------------------------------------------
        _capability(
            "readiness",
            "marivo.semantic.catalog.SemanticCatalog.readiness",
            "Check selected definitions' physical dependency closures and certified temporal artifacts without connecting or querying. Declared Measures and Metrics are accepted; requested_ids and checked_ids retain the distinction. Unrelated load warnings are excluded. Analysis execution qualifies physical types and operation support.",
            kind="method",
            output="ReadinessReport",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                _parameter_input("subject", "CatalogEntry | LookupSymbol", "refs", optional=True),
            ),
            effects=_LOCAL,
            example="catalog.readiness(refs=[ms.ref.measure('sales.orders.amount')])",
            preconditions=("a current loaded SemanticCatalog",),
            public_entrypoint="catalog.readiness",
            see_also=(
                LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.compute"),
                LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.persist"),
            ),
        ),
        # ------------------------------------------------------------------
        # diagnostics_boundaries
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # SemanticCatalog methods
        # ------------------------------------------------------------------
        _capability(
            "SemanticCatalog.get",
            "marivo.semantic.catalog.SemanticCatalog.get",
            "Retrieve one immutable directory entry by canonical kind:path identity. Check entry.kind before accessing variant-specific .value. No model calls or datasource reads; entry.show() and entry.definition disclose captured facts.",
            kind="method",
            output="SemanticEntry",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),
                _parameter_input("subject", "str", "id"),
            ),
            effects=_LOCAL,
            example="entry = catalog.get('metric:sales.revenue')\nif entry.kind == 'metric':\n    revenue = entry.value",
            public_entrypoint="catalog.get",
            see_also=(LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.show"),),
        ),
        _capability(
            "SemanticCatalog.search",
            "marivo.semantic.catalog.SemanticCatalog.search",
            "Discover business objects with text and explicit kind/domain/entity filters. Empty query browses without models. Text uses the frozen lexical or configured hybrid profile; hybrid may call the configured model and write derived local caches. Filters precede both recall channels. Scores express relevance, not execution authority. Follow next_cursor using the same query and filters; candidate_count counts the fixed candidate pool, not every possible semantic match.",
            kind="method",
            output="CatalogPage",
            inputs=(AuthoringInputRequirement(role="receiver", family="SemanticCatalog"),),
            effects=AuthoringEffects(
                data_access="local_metadata_read",
                connection="none",
                mutations=("project_state",),
                flags=("may_call_model", "may_write_search_cache"),
            ),
            example="page = catalog.search('accepted revenue', kind='metric', limit=5)\npage.show()",
            public_entrypoint="catalog.search",
        ),
    )
    descriptor_rows = tuple(
        row.model_copy(
            update={
                "summary": row.summary
                + " Capture returns a native typed Deferred symbol. Compose it directly; the loader binds the complete dependency scope before Catalog publication."
            }
        )
        if row.canonical_id
        in {
            "entity",
            "dimension",
            "dimension_column",
            "measure_column",
            "time_dimension",
            "time_dimension_column",
            "relationship",
        }
        else row
        for row in descriptor_rows
    )
    descriptor_rows = (
        *descriptor_rows,
        *_expression_capabilities(),
        *_result_method_capabilities(),
    )
    object_contracts = _object_contracts()
    return _finalize_registry(
        descriptor_rows,
        root_sections=_root_sections(),
        source_contracts=_source_contracts(object_contracts, descriptor_rows),
        repair_contracts=_repair_contracts(),
        help_descriptors=(
            *descriptor_rows,
            *(
                SemanticBuilderTopic(
                    canonical_id=f"expressions.{prefix}",
                    label=prefix,
                    summary=(
                        "Use row arithmetic within one contribution root, then reduce before Metric arithmetic. Numeric Dimensions do not gain aggregation permission."
                        "\n  Decimal add/subtract uses p=max(p1-s1,p2-s2)+max(s1,s2)+1, s=max(s1,s2); multiply uses p=p1+p2, s=s1+s2. "
                        "Cap p at 38 and reduce s by the excess, down to zero. Mixed integers supply their bound digit width (int64: 19); "
                        "untyped Decimal literals bind their own digits/exponent. Source output casts bind p/s; rounding follows the kernel. "
                        "\n  Integer SUM uses native accumulation (Ibis int64); Decimal SUM returns Decimal(38,input_scale). Count stays int64; division converts numeric operands to float64 before dividing; mean keeps its declared type."
                        "\n  Source and local overflow/rounding follow the actual kernel, including errors, wrapping and NaN/Infinity. No extra scans or protective widening. Saved values retain committed types."
                        "\n  UInt64 inputs use exact Decimal(20,0) before semantic arithmetic. Explicit Entity casts can use .cast('decimal(20,0)') or .cast(ibis.dtype('decimal(38,0)')); .cast('int64') requires the signed range."
                        "\n  Ordinary scalar division retains its source unit; zero denominators yield NA before arithmetic."
                        if prefix == "row"
                        else "Choose an operation on the constructed receiver. Internal Metric samples must finish with a reducer before observation."
                    ),
                    members=tuple(
                        _target(row.canonical_id)
                        for row in descriptor_rows
                        if row.canonical_id.startswith(prefix + ".")
                    )
                    + ((_target("Entity.count"),) if prefix == "row" else ()),
                )
                for prefix in ("row", "Metric", "MetricSamples")
            ),
            SemanticNavigationTopic(
                canonical_id="field",
                summary="A constructed scalar Dimension, TimeField or Measure retains its semantic owner. Compose native field symbols directly; the loader binds their complete dependency scope before Catalog publication. TimeField is a resolved TimeDimension. Compare with ==, !=, <, <=, >, >=; for example (region == 'US') & (amount > 0). Combine predicates with &, | and ~ (not Python and/or/not). Deferred values have no .show(); inspect catalog.get('kind:path').show().",
                members=(
                    _navigation_route(
                        "source boolean conditions", _target("predicate"), owns_discovery=False
                    ),
                    _navigation_route(
                        "Dimension definition", _target("objects.dimension"), owns_discovery=False
                    ),
                    _navigation_route(
                        "TimeDimension definition",
                        _target("objects.time_dimension"),
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "Measure definition", _target("objects.measure"), owns_discovery=False
                    ),
                    _navigation_route("single-valued relationship path", _target("Field.via")),
                    _navigation_route("attribute version anchor", _target("Field.at")),
                    _navigation_route("literal membership predicate", _target("Field.isin")),
                    _navigation_route("missing-value predicate", _target("Dimension.isnull")),
                ),
            ),
            SemanticNavigationTopic(
                canonical_id="predicate",
                summary=(
                    "Source conditions are native Deferred recipes. Compare resolved fields, use "
                    "isnull/isin, and combine with mv.in_population(P) using &, | and ~; use "
                    "parentheses, never Python and/or/not or bool. Example with resolved status, "
                    "revenue and a compatible Population P: session.observe({'revenue': revenue}, "
                    "where=(status == 'paid') | mv.in_population(P)). "
                    "Membership tests complete correlated tuples with null-safe field matching; "
                    "they return True or False. Field NA retains three-valued boolean semantics; "
                    "only a final True selects a contribution. Both boolean sides retain static "
                    "validation, numeric checks and producer dependencies, even under True/False. "
                    "Observe, single-source Metric.where and members accept mixed conditions on "
                    "their own contribution/identity domain. Population.where accepts membership "
                    "leaves only over existing axes. Source reduction where= accepts field leaves "
                    "only. Member leaves support boolean operators/constants, not comparisons, "
                    "arithmetic, ifelse, cast or methods. Bare Python bool is not a condition. "
                    "AnalysisFrame.where requires that exact Frame's boolean Column. Capture and "
                    "show perform no business reads; Deferred has no show. Inspect captured "
                    "conditions on the receiving Population or AnalysisFrame."
                ),
                members=(
                    _navigation_route("field expressions", _target("field"), owns_discovery=False),
                    _navigation_route(
                        "source Metric filtering", _target("Metric.where"), owns_discovery=False
                    ),
                    _navigation_route(
                        "membership leaf",
                        LiveHelpTarget(surface="analysis", canonical_id="in_population"),
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "observation contributions",
                        LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "source identities",
                        LiveHelpTarget(surface="analysis", canonical_id="session.members"),
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "existing Population coordinates",
                        LiveHelpTarget(surface="analysis", canonical_id="population.where"),
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "current Frame rows",
                        LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.where"),
                        owns_discovery=False,
                    ),
                ),
            ),
            SemanticNavigationTopic(
                canonical_id="expressions",
                summary=(
                    "For analysis, compose closed Catalog fields and Metrics using their governed meaning and aggregation permissions; "
                    "a question-scoped expression does not require authoring or publishing a Metric. For example, with closed compatible "
                    "revenue and order_count, session.observe({'average_order_value': revenue / order_count}) captures the question. "
                    "For reusable declarations, native lookup symbols compose before loading and the loader closes the complete dependency scope; "
                    "bare lookup symbols are not analysis inputs. Choose the expression role before an operation. "
                    "Row arithmetic stays within one contribution root; reduce before Metric arithmetic. per creates internal samples that must be reduced. "
                    "where, per, and window keep their receiver constraints; a compound Metric is not automatically filterable."
                ),
                members=(
                    _navigation_route(
                        "scalar fields",
                        _target("field"),
                        summary="Compare or navigate a governed Dimension, TimeField, or Measure.",
                    ),
                    _navigation_route(
                        "source conditions",
                        _target("predicate"),
                        summary="Combine field and Population membership conditions on their receiving domain.",
                    ),
                    _navigation_route(
                        "row quantities",
                        _target("expressions.row"),
                        summary="Compute within one contribution root and reduce source contributions.",
                    ),
                    _navigation_route(
                        "Metric values",
                        _target("expressions.Metric"),
                        summary="Compose existing reduced values; publishing a new Metric is optional.",
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "explicit scale and unit",
                        _target("rescale"),
                        summary="Divide a numeric Row or Metric by an authored constant and declare its target unit.",
                    ),
                    _navigation_route(
                        "internal samples",
                        _target("expressions.MetricSamples"),
                        summary="Reduce a Metric's per-sample values to a Metric.",
                    ),
                    _navigation_route(
                        "filter contributions",
                        _target("Metric.where"),
                        summary="Filter an eligible source Metric before reduction.",
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "choose internal samples",
                        _target("Metric.per"),
                        summary="Choose the internal sample domain before a reducer.",
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "choose a window",
                        _target("Metric.window"),
                        summary="Attach an explicit Metric observation window.",
                        owns_discovery=False,
                    ),
                    _navigation_route(
                        "inspect Entity/Relationship metadata", _target("Entity.show")
                    ),
                    _navigation_route(
                        "inspect captured expression facts",
                        _target("Entity.contract"),
                        owns_discovery=False,
                    ),
                    _navigation_route("half-open time predicates", _target("TimeField.within")),
                ),
            ),
            *_navigation_descriptors(object_contracts),
        ),
        object_contracts=object_contracts,
    )


REGISTRY = _build_registry()


def _type_contracts() -> Mapping[type, SemanticTypeContract]:
    """Build private type contracts without exposing constructors as help targets."""
    from dataclasses import fields

    from marivo._symbolic import symbol_factory as ref_factory
    from marivo.preview import PreviewResult
    from marivo.refs import (
        PeriodCalendarKind,
        SemanticKind,
    )
    from marivo.semantic.catalog import (
        CalendarPeriodPage,
        CatalogEntry,
        DatasourceEntry,
        DimensionEntry,
        DomainEntry,
        EntityEntry,
        MeasureEntry,
        MetricEntry,
        PeriodCalendarEntry,
        RelationshipEntry,
        SemanticCatalog,
        TemporalOccurrencePage,
        TemporalSetEntry,
        TimeDimensionEntry,
    )
    from marivo.semantic.catalog_entries import (
        CatalogFilters,
        CatalogPage,
        CatalogScope,
        DatasourceDefinition,
        DomainDefinition,
        EntityDefinition,
        EntryProvenance,
        FieldDefinition,
        MetricDefinition,
        MetricTimeBinding,
        PeriodCalendarDefinition,
        RelationshipDefinition,
        TemporalSetDefinition,
    )
    from marivo.semantic.dtos import PreviewBatchResult
    from marivo.semantic.ir import JoinKey
    from marivo.semantic.readiness import (
        ReadinessInputSummary,
        ReadinessIssue,
        ReadinessReport,
    )
    from marivo.semantic.validation import (
        SourceCheckNamespace,
        ValidationCheckResult,
        ValidationReport,
    )

    show_render = ("show", "render")
    contracts: dict[type, SemanticTypeContract] = {}

    def add(
        cls: type,
        name: str,
        producers: tuple[str | LiveHelpTarget, ...],
        *,
        properties: tuple[str, ...] = (),
        methods: tuple[str, ...] = (),
        type_page_methods: tuple[str, ...] = (),
        consumers: tuple[str | LiveHelpTarget, ...] = (),
        guidance: tuple[str, ...] = (),
        notes: tuple[str, ...] = (),
        example: str | None = None,
    ) -> None:
        def targets(values: tuple[str | LiveHelpTarget, ...]) -> tuple[LiveHelpTarget, ...]:
            return tuple(
                _canonical_link(value if isinstance(value, LiveHelpTarget) else _target(value))
                for value in values
            )

        contracts[cls] = SemanticTypeContract(
            name=name,
            producers=targets(producers),
            public_properties=properties,
            public_methods=methods,
            type_page_methods=type_page_methods,
            guidance=guidance,
            consumers=targets(consumers),
            notes=notes,
            example=example,
        )

    def factory_methods(canonical_id: str) -> tuple[str, ...]:
        descriptor = REGISTRY.by_canonical_id(canonical_id)
        if not isinstance(descriptor, AuthoringCapability):
            raise TypeError(f"semantic factory contract is not exact: {canonical_id}")
        return tuple(
            (target.canonical_id or "").rsplit(".", 1)[-1] for target in descriptor.see_also
        )

    add(
        SemanticCatalog,
        "SemanticCatalog",
        ("load",),
        properties=("definition_fingerprint", "semantic_root", "workspace_dir"),
        methods=(
            "get",
            "search",
            "preview",
            "preview_many",
            "validate",
            "readiness",
            "render",
            "show",
        ),
    )
    add(
        CatalogEntry,
        "CatalogEntry",
        ("SemanticCatalog.get", "SemanticCatalog.search"),
        properties=("id", "kind", "path", "name", "domain", "summary", "definition", "provenance"),
        methods=("related", "to_dict", "to_json", "show", "render"),
    )
    add(
        CatalogPage,
        "CatalogPage",
        ("SemanticCatalog.search",),
        properties=(
            "items",
            "candidate_count",
            "scope",
            "filters",
            "retrieval",
            "matches",
            "next_cursor",
            "outside_scope",
        ),
        methods=("to_dict", "to_json", "show", "render"),
        guidance=(
            "candidate_count is the fixed candidate pool size; next_cursor resumes API pages, independent of display truncation.",
            "The card counts displayed and omitted current-page items. show(max_output_bytes=None), items and to_json() recover the current page; follow next_cursor for later pages.",
        ),
    )
    for entry_type in (
        EntityEntry,
        DimensionEntry,
        TimeDimensionEntry,
        MeasureEntry,
        MetricEntry,
        RelationshipEntry,
    ):
        add(
            entry_type,
            entry_type.__name__,
            ("SemanticCatalog.get", "SemanticCatalog.search"),
            properties=(
                "id",
                "kind",
                "path",
                "name",
                "domain",
                "summary",
                "definition",
                "provenance",
                "value",
            ),
            methods=("related", "to_dict", "to_json", "show", "render"),
        )
    for record_type in (
        CatalogScope,
        CatalogFilters,
        EntryProvenance,
        DomainDefinition,
        DatasourceDefinition,
        EntityDefinition,
        FieldDefinition,
        MetricDefinition,
        MetricTimeBinding,
        RelationshipDefinition,
        PeriodCalendarDefinition,
        TemporalSetDefinition,
    ):
        add(
            record_type,
            record_type.__name__,
            ("SemanticCatalog.get",),
            properties=tuple(field.name for field in fields(record_type)),
            guidance=(
                "Immutable captured definition data, independent of display budgets and query matching metadata.",
            ),
        )
    add(
        DomainEntry,
        "DomainEntry",
        (),
        properties=("id", "kind", "path", "name", "domain", "summary", "definition", "provenance"),
        methods=("related", "to_dict", "to_json", "show", "render"),
    )

    add(
        PeriodCalendarEntry,
        "PeriodCalendarEntry",
        (),
        properties=(
            "id",
            "kind",
            "path",
            "name",
            "domain",
            "summary",
            "definition",
            "provenance",
            "ref",
        ),
        methods=(
            "related",
            "to_dict",
            "to_json",
            "grain",
            "period",
            "period_on",
            "periods",
            "show",
            "render",
        ),
    )

    add(
        CalendarPeriodPage,
        "CalendarPeriodPage",
        (LiveHelpTarget(surface="analysis", canonical_id="calendar.periods"),),
        properties=("items", "next_cursor"),
        methods=show_render,
    )

    add(
        TemporalSetEntry,
        "TemporalSetEntry",
        (),
        properties=(
            "id",
            "kind",
            "path",
            "name",
            "domain",
            "summary",
            "definition",
            "provenance",
            "ref",
        ),
        methods=("occurrence", "occurrences", "related", "to_dict", "to_json", "show", "render"),
    )

    add(
        TemporalOccurrencePage,
        "TemporalOccurrencePage",
        (LiveHelpTarget(surface="analysis", canonical_id="temporal_set.occurrences"),),
        properties=("items", "next_cursor"),
        methods=show_render,
    )
    add(
        DatasourceEntry,
        "DatasourceEntry",
        (),
        properties=("id", "kind", "path", "name", "domain", "summary", "definition", "provenance"),
        methods=("related", "to_dict", "to_json", "show", "render"),
    )

    add(
        PreviewResult,
        "PreviewResult",
        ("preview",),
        properties=(
            "kind",
            "ref",
            "columns",
            "types",
            "rows",
            "requested_limit",
            "returned_row_count",
            "is_truncated",
            "status",
            "coverage",
            "warnings",
            "timezones",
            "sample_policy",
            "execution_limits",
        ),
        methods=show_render,
        type_page_methods=show_render,
        guidance=_display_help(),
    )
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
        properties=(
            "scope",
            "status",
            "analysis_ready_inputs",
            "blockers",
            "warnings",
            "input_summary",
            "checked_at",
            "catalog_definition_fingerprint",
        ),
        methods=("show", "render", "to_dict"),
    )
    add(
        ValidationReport,
        "ValidationReport",
        ("validate",),
        properties=(
            "status",
            "checks",
            "checked_at",
            "catalog_definition_fingerprint",
            "affected_refs",
        ),
        methods=("show", "render", "to_dict"),
    )
    add(
        ValidationCheckResult,
        "ValidationCheckResult",
        ("validate",),
        guidance=("Read report.checks from the online validation report.",),
        properties=(
            "kind",
            "status",
            "datasource",
            "source",
            "target_refs",
            "affected_refs",
            "checked_at",
            "observed_schema_fingerprint",
            "observed_capability_fingerprint",
            "observed",
            "repair",
            "user_data_queried",
            "scopes",
        ),
        methods=("show", "render", "to_dict"),
    )
    add(
        SourceCheckNamespace,
        "SourceCheckNamespace",
        ("source_check",),
        methods=factory_methods("source_check"),
    )
    add(
        ReadinessInputSummary,
        "ReadinessInputSummary",
        (),
        guidance=("Read report.input_summary from the readiness report.",),
        properties=("datasources", "refs", "tables"),
        methods=("to_dict",),
    )
    add(
        ReadinessIssue,
        "ReadinessIssue",
        (),
        guidance=("Read report.blockers and report.warnings from the readiness report.",),
        properties=(
            "kind",
            "severity",
            "refs",
            "message",
            "repair",
            "catalog_definition_fingerprint",
        ),
        methods=("to_dict",),
    )
    from marivo.semantic.expressions import (
        Entity,
        ExpressionContract,
        Relationship,
    )

    for expression_type, family, producers, methods in (
        (Entity, "Entity", ("SemanticCatalog.get",), ("count", "contract", "show")),
        (Relationship, "Relationship", ("SemanticCatalog.get",), ("contract", "show")),
    ):
        add(
            expression_type,
            family,
            producers,
            methods=methods,
            notes=(
                "Immutable physical metadata; numerical and conditional author inputs use native Ibis Deferred.",
            ),
        )
    add(
        ExpressionContract,
        "ExpressionContract",
        (),
        methods=("show",),
        notes=(
            "Bounded source-free definition facts; analysis computation belongs to AnalysisFrame.compute()/persist().",
        ),
    )
    # Identity types
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
    # IR types
    add(
        JoinKey,
        "JoinKey",
        ("join_on",),
        properties=("from_key", "to_key"),
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
        guidance=("Construct with ms.ai_context(...); annotate with ms.typing.AiContextValue.",),
    )
    return MappingProxyType(contracts)


TYPE_CONTRACTS = _type_contracts()


def _error_types() -> Mapping[str, type]:
    from marivo.semantic.errors import (
        CatalogSearchError,
        SemanticContractScopeError,
        SemanticDecoratorError,
        SemanticError,
        SemanticExpressionError,
        SemanticHelpTargetError,
        SemanticLoadError,
        SemanticLoadFailed,
        SemanticRuntimeError,
    )

    return MappingProxyType(
        {
            "CatalogSearchError": CatalogSearchError,
            "SemanticError": SemanticError,
            "SemanticExpressionError": SemanticExpressionError,
            "SemanticDecoratorError": SemanticDecoratorError,
            "SemanticLoadError": SemanticLoadError,
            "SemanticRuntimeError": SemanticRuntimeError,
            "SemanticHelpTargetError": SemanticHelpTargetError,
            "SemanticContractScopeError": SemanticContractScopeError,
            "SemanticLoadFailed": SemanticLoadFailed,
        }
    )


ERROR_TYPES = _error_types()
