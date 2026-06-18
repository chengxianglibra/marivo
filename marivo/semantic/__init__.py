"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog = ms.load(domains=['sales'])  # filter to specific domains
    catalog.list().show()
    catalog.list(kind="metric").show()              # all metrics across domains
    catalog.list(domain="sales", kind="metric").show()  # metrics in one domain

    ms.domain(name="sales", default=True)
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))
    @ms.measure(entity=orders, additivity="additive", unit="USD")
    def amount(orders):
        return orders.amount

    revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from marivo.datasource.scan import ScanScope
from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    DomainRef,
    aggregate,
    csv,
    datetime,
    dimension,
    domain,
    entity,
    from_sql,
    hour_prefix,
    join_on,
    linear,
    measure,
    metric,
    parquet,
    ratio,
    ref,
    relationship,
    semi_additive,
    snapshot,
    strptime,
    table,
    time_dimension,
    timestamp,
    validity,
    weighted_average,
)
from marivo.semantic.catalog import (
    AiContextView,
    DatasourceDetails,
    DerivedMetricDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    EntityVersioning,
    MeasureDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticKindInput,
    SemanticObject,
    SemanticObjectDetails,
    SemanticObjectList,
    SemanticRef,
    SemanticRefInput,
    SimpleMetricDetails,
    SnapshotVersioning,
    TimeDimensionDetails,
    ValidityVersioning,
    load,
)
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    BriefStatus,
    ComponentFact,
    CrossEntityMetricBrief,
    DatasetSource,
    DerivedMetricBrief,
    DimensionBrief,
    DimensionValueFact,
    DomainBrief,
    DomainBriefSummary,
    EntityBrief,
    FileSource,
    FormatCandidate,
    JoinPathFact,
    MeasureBrief,
    MetricBrief,
    PrimaryKeyCandidate,
    RegisteredMatch,
    RelationshipBrief,
    TableSource,
    TimeDimensionBrief,
    VerifyResult,
    VersioningHints,
)
from marivo.semantic.errors import LadderOrderError
from marivo.semantic.help import help, help_text
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    DimensionRef,
    EntityRef,
    HourPrefixParse,
    JoinKey,
    MeasureIR,
    MeasureRef,
    MetricRef,
    RelationshipRef,
    SqlProvenance,
    StrptimeParse,
    TimeDimensionRef,
    TimestampParse,
)
from marivo.semantic.ledger import DecisionRecord
from marivo.semantic.parity import ParityResult
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.richness import DemandSignal, RichnessReport
from marivo.semantic.typing import AiContext

if TYPE_CHECKING:
    from marivo.datasource.ir import EntitySourceIR

_AGENT_FINGERPRINT = "agent_recorded"


def prepare_domain(*, name: str) -> DomainBrief:
    """Prepare a domain authoring brief from the current project."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.prepare_domain(name=name)


def prepare_derived_metric(
    *,
    numerator: str,
    denominator: str | None = None,
    weight: str | None = None,
) -> DerivedMetricBrief:
    """Prepare a derived metric brief from component metric refs."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.prepare_derived_metric(
        numerator=numerator, denominator=denominator, weight=weight
    )


def prepare_entity(
    *,
    datasource: str,
    source: EntitySourceIR,
    domain: str,
    scope: ScanScope | None = None,
) -> EntityBrief:
    """Prepare an entity authoring brief with datasource evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_entity(datasource=datasource, source=source, domain=domain, scope=scope)


def prepare_dimension(
    *,
    entity: str,
    column: str,
    scope: ScanScope | None = None,
) -> DimensionBrief:
    """Prepare a dimension authoring brief for one entity column.

    Profiles the column data from the datasource and checks for matches
    against existing dimensions.

    Args:
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        column: Column name to prepare a dimension brief for.
        scope: Bounded scan configuration.

    Returns:
        A single ``DimensionBrief`` with status, profile, and match evidence.

    Example:
        >>> import marivo.semantic as ms
        >>> brief = ms.prepare_dimension(entity="sales.orders", column="region")
        >>> brief.status
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_dimension(entity=entity, column=column, scope=scope)


def prepare_time_dimension(
    *,
    entity: str,
    column: str,
    scope: ScanScope | None = None,
) -> TimeDimensionBrief:
    """Prepare a time dimension authoring brief with format detection."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_time_dimension(entity=entity, column=column, scope=scope)


def prepare_metric(
    *,
    entity: str,
    measure_columns: tuple[str, ...] | list[str] = (),
    filter_dimensions: tuple[str, ...] | list[str] = (),
    scope: ScanScope | None = None,
) -> MetricBrief:
    """Prepare a metric authoring brief with measure evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_metric(
        entity=entity,
        measure_columns=measure_columns,
        filter_dimensions=filter_dimensions,
        scope=scope,
    )


def prepare_measure(
    *,
    entity: str,
    column: str,
    scope: ScanScope | None = None,
) -> MeasureBrief:
    """Prepare a measure authoring brief for one entity column.

    Profiles the column data from the datasource and provides an additivity
    hint based on the column's data type. Checks for matches against existing
    measures in the registry.

    Args:
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        column: Column name to prepare a measure brief for.
        scope: Scan scope controlling partition, max rows, and timeout.

    Returns:
        A ``MeasureBrief`` with status, profile, additivity hint, and match evidence.

    Example:
        >>> brief = ms.prepare_measure(entity="sales.orders", column="amount")
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_measure(entity=entity, column=column, scope=scope)


def prepare_relationship(
    *,
    from_entity: str,
    to_entity: str,
    keys: list[tuple[str, str]],
    scope: ScanScope | None = None,
) -> RelationshipBrief:
    """Prepare a relationship authoring brief with join-key probe evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_relationship(
        from_entity=from_entity,
        to_entity=to_entity,
        keys=keys,
        scope=scope,
    )


def prepare_cross_entity_metric(
    *,
    root_entity: str,
    entities: tuple[str, ...] | list[str],
    measure_columns: tuple[str, ...] | list[str] = (),
    scope: ScanScope | None = None,
) -> CrossEntityMetricBrief:
    """Prepare a cross-entity metric brief with relationship path evidence."""
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.prepare_cross_entity_metric(
        root_entity=root_entity,
        entities=entities,
        measure_columns=measure_columns,
        scope=scope,
    )


def verify_object(
    ref: str,
    *,
    scope: ScanScope | None = None,
) -> VerifyResult:
    """Verify a single authored semantic object is reachable and valid.

    For domains, relationships, and dimensions this is a static-only check.
    For entities, a scoped preview confirms the datasource is reachable and
    the expression is valid. For time dimensions, metrics, and derived
    metrics, the check is static and auto-records a decision into the
    evidence ledger.

    Args:
        ref: Fully qualified semantic ref (e.g. ``"sales.orders"``).
        scope: Scan scope controlling partition, max rows, and timeout.
            Defaults to ``ScanScope()``.

    Returns:
        VerifyResult with status, issues, and optional scan report.

    Example:
        >>> import marivo.semantic as ms
        >>> result = ms.verify_object("sales.orders")
        >>> result.status

    Constraints:
        ``verify_object`` is enforced by the authoring ladder: prepare APIs
        for dimensions, time dimensions, metrics, relationships, and
        cross-entity metrics raise ``LadderOrderError`` if the entity has
        not passed verification.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.verify_object(ref, scope=scope)


def readiness(
    *,
    refs: Sequence[SemanticRefInput] | None = None,
) -> ReadinessReport:
    """Run structural readiness check for the given semantic refs.

    Performs pure in-memory checks without datasource connectivity.
    For runtime validation, use ``catalog.preview(...)``,
    ``ms.parity_check(...)``, and ``ms.richness()``.

    Args:
        refs: Semantic refs to check. Accepts strings or SemanticRef objects.
            Resolves the full dependency closure for each ref. None checks
            all loaded objects.

    Returns:
        ReadinessReport indicating whether analysis handoff is safe.

    Example:
        >>> import marivo.semantic as ms
        >>> report = ms.readiness()
        >>> if report.blocked:
        ...     report.show()

    Constraints:
        This is the required semantic gate before passing refs to analysis APIs.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.readiness(refs=refs)


def richness(
    *,
    demand: DemandSignal | None = None,
) -> RichnessReport:
    """Return a demand-ranked advisory richness report.

    Pure advisory: it never blocks and never mutates readiness. ``demand``
    seeds coverage/depth ranking from example questions, analysis intents,
    run-history refs, and the build purpose.

    Args:
        demand: Optional demand signal for ranking richness gaps.

    Returns:
        RichnessReport with demand-ranked coverage and depth gaps.

    Example:
        >>> import marivo.semantic as ms
        >>> report = ms.richness()
        >>> report.show()

    Constraints:
        Advisory only — does not block readiness or analysis handoff.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.richness(demand=demand)


def parity_check(
    name: str,
    *,
    rel_tol: float | None = None,
    abs_tol: float | None = None,
    force: bool = False,
) -> ParityResult:
    """Run parity check for a metric against its source SQL.

    Datasource backends are resolved internally via the connection service.

    Args:
        name: Fully qualified metric ref (e.g. ``"sales.revenue"``).
        rel_tol: Relative tolerance for numeric comparison. None uses default.
        abs_tol: Absolute tolerance for numeric comparison. None uses default.
        force: If True, re-runs parity even if cached results exist.

    Returns:
        ParityResult with comparison details and pass/fail status.

    Example:
        >>> import marivo.semantic as ms
        >>> result = ms.parity_check("sales.revenue")
        >>> result.show()

    Constraints:
        Requires the metric to declare ``provenance=ms.from_sql(sql=..., dialect=...)``.
        Raises ``SemanticRuntimeError`` if the metric has no provenance.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.parity_check(name, rel_tol=rel_tol, abs_tol=abs_tol, force=force)


def record_decision(
    *,
    subject: str,
    decision_kind: str,
    chosen: str,
    agreement_confidence: str,
    qualifying_sources: tuple[str, ...] | list[str],
    blast_radius: int = 0,
    cited_source: dict[str, object] | None = None,
    cited_columns: tuple[str, ...] | list[str] = (),
) -> None:
    """Record an authoring decision into the evidence ledger.

    Persists a ``DecisionRecord`` for the given semantic subject so that
    subsequent ``verify_object`` and readiness checks can trace the
    reasoning behind authored objects.

    Args:
        subject: Fully qualified semantic ref (e.g. ``"sales.orders"``).
        decision_kind: Decision type (e.g. ``"entity_primary_key"``,
            ``"authoring_abandoned"``).
        chosen: The option chosen for this decision.
        agreement_confidence: Confidence level (``"high"`` or ``"low"``).
        qualifying_sources: Evidence sources supporting this decision
            (e.g. ``("user_confirmation",)``).
        blast_radius: Number of transitive dependents affected. Defaults to 0.
        cited_source: Optional dict of source metadata backing the decision.
        cited_columns: Optional columns cited as evidence.

    Example:
        >>> import marivo.semantic as ms
        >>> ms.record_decision(
        ...     subject="sales.orders",
        ...     decision_kind="entity_primary_key",
        ...     chosen="order_id",
        ...     agreement_confidence="high",
        ...     qualifying_sources=("user_confirmation",),
        ... )

    Constraints:
        Decisions are idempotent by kind — recording the same
        ``decision_kind`` for a subject replaces the prior entry.
    """
    from datetime import UTC, datetime

    from marivo.semantic.ledger import DecisionRecord, LedgerStore, ObjectEvidence
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()

    if isinstance(qualifying_sources, list):
        qualifying_sources = tuple(qualifying_sources)
    if isinstance(cited_columns, list):
        cited_columns = tuple(cited_columns)

    materiality = "high" if blast_radius > 0 else "low"

    record = DecisionRecord(
        decision_kind=decision_kind,
        chosen=chosen,
        agreement_confidence=agreement_confidence,
        qualifying_sources=qualifying_sources,
        materiality=materiality,
        blast_radius=blast_radius,
        evidence_fingerprint=_AGENT_FINGERPRINT,
        question_id=None,
        decided_at=datetime.now(UTC).isoformat(),
        cited_source=cited_source,
        cited_columns=cited_columns,
    )

    store = LedgerStore(project.state_root)
    # Idempotent by kind: replace any existing decision with the same
    # decision_kind.  This diverges from LedgerStore.record_decision, which
    # always appends — the public wrapper is stricter to avoid duplicate
    # entries when agents retry.
    obj = store.read_object(subject)
    if obj is not None and any(d.decision_kind == decision_kind for d in obj.decisions):
        updated_decisions = tuple(d for d in obj.decisions if d.decision_kind != decision_kind)
        store.write_object(
            ObjectEvidence(
                semantic_id=obj.semantic_id,
                authored_at=obj.authored_at,
                decisions=(*updated_decisions, record),
                rejected_candidates=obj.rejected_candidates,
            )
        )
    else:
        store.record_decision(subject, record)


__all__ = [
    "AiContext",
    "AiContextView",
    "AssessmentIssue",
    "AuthoringAssessment",
    "AuthoringQuestion",
    "BriefStatus",
    "ComponentFact",
    "CrossEntityMetricBrief",
    "DatasetSource",
    "DatasourceDetails",
    "DateParse",
    "DatetimeParse",
    "DecisionRecord",
    "DemandSignal",
    "DerivedMetricBrief",
    "DerivedMetricDetails",
    "DimensionBrief",
    "DimensionDetails",
    "DimensionRef",
    "DimensionValueFact",
    "DomainBrief",
    "DomainBriefSummary",
    "DomainDetails",
    "DomainRef",
    "EntityBrief",
    "EntityDetails",
    "EntityRef",
    "EntityVersioning",
    "FileSource",
    "FormatCandidate",
    "HourPrefixParse",
    "JoinKey",
    "JoinPathFact",
    "LadderOrderError",
    "MeasureBrief",
    "MeasureDetails",
    "MeasureIR",
    "MeasureRef",
    "MetricBrief",
    "MetricDetails",
    "MetricRef",
    "ParityResult",
    "PrimaryKeyCandidate",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RegisteredMatch",
    "RelationshipBrief",
    "RelationshipDetails",
    "RelationshipRef",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticKindInput",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SemanticRefInput",
    "SimpleMetricDetails",
    "SnapshotVersioning",
    "SqlProvenance",
    "StrptimeParse",
    "TableSource",
    "TimeDimensionBrief",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "TimestampParse",
    "ValidityVersioning",
    "VerifyResult",
    "VersioningHints",
    "aggregate",
    "csv",
    "datetime",
    "dimension",
    "domain",
    "entity",
    "errors",
    "from_sql",
    "help",
    "help_text",
    "hour_prefix",
    "join_on",
    "linear",
    "load",
    "measure",
    "metric",
    "parity_check",
    "parquet",
    "prepare_cross_entity_metric",
    "prepare_derived_metric",
    "prepare_dimension",
    "prepare_domain",
    "prepare_entity",
    "prepare_measure",
    "prepare_metric",
    "prepare_relationship",
    "prepare_time_dimension",
    "ratio",
    "readiness",
    "record_decision",
    "ref",
    "relationship",
    "richness",
    "semi_additive",
    "snapshot",
    "strptime",
    "table",
    "time_dimension",
    "timestamp",
    "typing",
    "validity",
    "verify_object",
    "weighted_average",
]
