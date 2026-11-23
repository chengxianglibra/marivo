"""Declaration and business decisions for each semantic object kind."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from marivo._kernel.refs import SemanticKind
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._help_specs.builders import _canonical_link, _symbol_family, _target
from marivo.semantic._help_specs.model import (
    ConstructionMode,
    SemanticObjectContract,
    SemanticObjectDecision,
    SemanticObjectRelationship,
)
from marivo.semantic._help_specs.placement import _authoring_source_contract


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
        target=_canonical_link(LiveHelpTarget(surface=surface, canonical_id=target_id)),
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
        canonical_id=kind.value,
        summary=summary,
        semantic_kind=kind,
        construction_families={
            SemanticKind.MEASURE: ("MeasureSymbol", "NumericRow"),
            SemanticKind.METRIC: ("NumericMetric",),
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


OBJECT_CONTRACTS: tuple[SemanticObjectContract, ...] = (
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
        "Reusable business entity or fact-set identity backed by one source; an optional expression body owns its output relation and grain.",
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
            _business_decision("row_grain", "What does one Entity row represent?", "ai_context"),
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
                "Declare an Entity directly or as a one-return expression decorator over its source; an explicit name takes the direct form.",
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
            _relationship("consumed_by", "objects.dimension", "Dimensions are owned by an Entity."),
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
            _relationship("owned_by", "objects.entity", "The Entity ref fixes field ownership."),
            _relationship("consumed_by", "objects.relationship", "Dimensions may form join keys."),
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
                "Should reductions inherit this TimeDimension as their contribution clock?",
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
            _mode("Bind one physical time column directly.", "default", "time_dimension_column"),
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
        supporting=("date", "timestamp", "strptime", "hour_prefix", "ai_context", "bind"),
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
        check_guidance="Readiness accepts this entry and checks its physical dependency closure. Only directly requested ready physical refs enter analysis_ready_inputs.",
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
                "Which field fixes contribution time for each source reduction? Inspect metric.definition.time_bindings; see marivo.help('semantic.metric_definition.time_bindings').",
                "metric",
                "row.sum",
                "entity",
            ),
            _business_decision(
                "guardrails",
                "Which approved business limits must remain visible?",
                "ai_context",
            ),
        ),
        construction_modes=(
            _mode(
                "Declare a zero-parameter Metric expression with time fixed by its source reductions.",
                "default",
                "metric",
            ),
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
            "entity",
            "Metric.where",
            "Metric.per",
            "Field.via",
            "measure",
        ),
        checks=("readiness",),
        check_guidance="Readiness accepts this Metric entry and checks its physical dependencies. It does not return the Metric as an analysis_ready_input. AnalysisFrame.compute()/persist() qualifies the selected computation.",
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
        supporting=("join_on", "ai_context", "Relationship.at"),
        checks=(
            "load",
            "readiness",
            "source_check.relationship_matches",
            "source_check.relationship_cardinality",
            "validate",
        ),
    ),
)
