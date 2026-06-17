"""Prepare APIs for stepwise semantic authoring.

Registry-only functions (``prepare_domain``, ``prepare_derived_metric``) inspect
the semantic registry alone.  Data-backed functions (``prepare_entity``,
``prepare_dimension``, ``prepare_time_dimension``, ``prepare_metric``) use
datasource inspection to surface evidence for authoring decisions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

import ibis

from marivo.datasource.ir import EntitySourceIR, source_to_dict
from marivo.datasource.scan import (
    ColumnInspection,
    JoinSide,
    ScanReport,
    ScanScope,
)
from marivo.datasource.scan import (
    ColumnProfile as ScanColumnProfile,
)
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringQuestion,
    BriefStatus,
    ComponentFact,
    CrossEntityMetricBrief,
    DerivedMetricBrief,
    DimensionBrief,
    DimensionValueFact,
    DomainBrief,
    DomainBriefSummary,
    EntityBrief,
    FormatCandidate,
    JoinPathFact,
    MeasureBrief,
    MetricBrief,
    PrimaryKeyCandidate,
    RegisteredMatch,
    RelationshipBrief,
    TimeDimensionBrief,
    VersioningHints,
    derive_brief_status,
)
from marivo.semantic.reader import SemanticProject, _require_registry

# Module-level default for ScanScope to avoid B008 function-call-in-default-argument
_DEFAULT_SCOPE = ScanScope()

# ---------------------------------------------------------------------------
# ibis Table attribute shadowing detection
# ---------------------------------------------------------------------------

# ibis Table public method/property names that shadow column dot-access.
# Built once at import from the installed ibis version so the list stays
# accurate across ibis upgrades.
_IBIS_TABLE_ATTR_NAMES: frozenset[str] = frozenset(
    name
    for name in dir(ibis.Table)
    if not name.startswith("_")
    if callable(getattr(ibis.Table, name, None))
    or isinstance(getattr(ibis.Table, name, None), property)
)


def _ibis_shadowing_issue(
    entity: str,
    column: str,
) -> AssessmentIssue | None:
    """Return an advisory issue if *column* shadows an ibis Table attribute."""
    if column not in _IBIS_TABLE_ATTR_NAMES:
        return None
    return AssessmentIssue(
        kind="ibis_attribute_shadowing",
        severity="warning",
        refs=(f"{entity}.{column}",),
        message=(
            f"Column {column!r} shadows an ibis Table attribute. "
            f'Use bracket notation: table["{column}"] instead of table.{column} '
            f"in decorator bodies."
        ),
        rule_id="ibis_attribute_shadowing",
    )


# ---------------------------------------------------------------------------
# Common date formats used by _looks_temporal and _detect_time_formats
# ---------------------------------------------------------------------------

_COMMON_DATE_FORMATS: tuple[tuple[str, str], ...] = (
    ("%Y%m%d", r"^\d{8}$"),
    ("%Y-%m-%d", r"^\d{4}-\d{2}-\d{2}$"),
    ("%Y%m%d%H", r"^\d{10}$"),
    ("%Y-%m-%d %H:%M:%S", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
    ("%Y-%m-%dT%H:%M:%S", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"),
)


# ---------------------------------------------------------------------------
# Registry-only prepare APIs
# ---------------------------------------------------------------------------


def prepare_domain(project: SemanticProject, *, name: str) -> DomainBrief:
    """Prepare a domain authoring brief from the project registry."""
    reg = _require_registry(project._registry, project=project)
    domains = sorted(reg.domains)
    domain_summaries = tuple(
        DomainBriefSummary(
            name=domain_name,
            description=reg.domains[domain_name].description,
            default=reg.domains[domain_name].default,
            object_counts={
                "entity": sum(1 for d in reg.entities.values() if d.domain == domain_name),
                "dimension": sum(
                    1
                    for f in reg.dimensions.values()
                    if f.domain == domain_name and not f.is_time_dimension
                ),
                "time_dimension": sum(
                    1
                    for f in reg.dimensions.values()
                    if f.domain == domain_name and f.is_time_dimension
                ),
                "measure": sum(1 for m in reg.measures.values() if m.domain == domain_name),
                "metric": sum(1 for m in reg.metrics.values() if m.domain == domain_name),
                "datasource": 0,
                "relationship": sum(
                    1 for r in reg.relationships.values() if r.domain == domain_name
                ),
            },
        )
        for domain_name in domains
    )
    matches = tuple(
        RegisteredMatch(ref=domain_name, basis="name_exact")
        for domain_name in domains
        if domain_name == name
    )
    status: BriefStatus = "needs_input" if matches else "sufficient"
    return DomainBrief(
        status=status,
        proposed_name=name,
        existing_domains=domain_summaries,
        matches=matches,
        questions=(),
        issues=(),
    )


def _build_derived_metric_template(
    *,
    composition_kind: Literal["ratio", "weighted_average", "linear"],
    name_hint: str,
    numerator: str | None,
    denominator: str | None,
    weight: str | None,
) -> str:
    """Build a ready-to-use flat derived-metric constructor template."""
    if composition_kind == "ratio":
        return (
            f"{name_hint} = ms.ratio(\n"
            f"    name={name_hint!r},\n"
            f"    numerator={numerator!r}, denominator={denominator!r},\n"
            f")"
        )
    if composition_kind == "weighted_average":
        return (
            f"{name_hint} = ms.weighted_average(\n"
            f"    name={name_hint!r},\n"
            f"    value={numerator!r}, weight={weight!r},\n"
            f")"
        )
    return f"{name_hint} = ms.linear(\n    name={name_hint!r},\n    add=[...], subtract=[...],\n)"


def _preview_unit_hint(
    reg: object,
    composition_kind: str,
    numerator: str | None,
    denominator: str | None,
    weight: str | None,
    missing: tuple[str, ...],
) -> str | None:
    """Preview the unit the loader will derive, for the brief's representable shapes.

    Only ratio and weighted_average are previewable — the brief API has no linear
    term list, so linear previews as None (the loader still derives it at load).
    """
    from marivo.semantic.unit_algebra import ratio_unit, weighted_average_unit

    if reg is None or missing:
        return None
    metrics = reg.metrics  # type: ignore[attr-defined]
    if composition_kind == "ratio" and numerator is not None and denominator is not None:
        num = metrics.get(numerator)
        den = metrics.get(denominator)
        if num is None or den is None:
            return None
        return ratio_unit(num.unit, den.unit)
    if composition_kind == "weighted_average" and numerator is not None:
        value = metrics.get(numerator)
        return weighted_average_unit(value.unit) if value is not None else None
    return None


def prepare_derived_metric(
    project: SemanticProject,
    *,
    name_hint: str = "metric",
    numerator: str | None = None,
    denominator: str | None = None,
    weight: str | None = None,
    composition_kind: Literal["ratio", "weighted_average", "linear"] | None = None,
) -> DerivedMetricBrief:
    """Prepare a derived metric brief from component metric refs.

    Args:
        project: Loaded SemanticProject instance.
        name_hint: Suggested metric name for the authoring template.
        numerator: Component ref for the numerator (ratio) or value (weighted_average).
        denominator: Component ref for the denominator (ratio only).
        weight: Component ref for the weight (weighted_average only).
        composition_kind: Override the inferred composition kind. When ``None``,
            inferred as ``"ratio"`` when *denominator* is provided,
            ``"weighted_average"`` when *weight* is provided, or ``"linear"``
            when only *numerator* is provided (or none at all).

    Returns:
        A ``DerivedMetricBrief`` with inferred composition kind, component facts,
        and a ready-to-use authoring template.
    """
    reg = project._registry
    refs = tuple(ref for ref in (numerator, denominator, weight) if ref is not None)
    missing = tuple(ref for ref in refs if reg is None or ref not in reg.metrics)
    issues = tuple(
        AssessmentIssue(
            kind="missing_prerequisite",
            severity="blocker",
            refs=missing,
            message=f"Missing component metrics: {', '.join(missing)}.",
            rule_id="prepare_derived_metric_components_loaded",
        )
        for _ in (0,)
        if missing
    )
    components = tuple(
        ComponentFact(
            ref=ref,
            role=(
                "numerator"
                if ref == numerator
                else "denominator"
                if ref == denominator
                else "weight"
            ),
            additivity="unknown",
            composition_kind="unknown",
            verification_status="unverified",
            unit=None,
        )
        for ref in refs
        if ref not in missing
    )
    if composition_kind is None:
        composition_kind = (
            "ratio"
            if denominator is not None
            else "weighted_average"
            if weight is not None
            else "linear"
        )
    status: BriefStatus = "blocked" if issues else "sufficient"
    template = _build_derived_metric_template(
        composition_kind=composition_kind,
        name_hint=name_hint,
        numerator=numerator,
        denominator=denominator,
        weight=weight,
    )
    unit_hint = _preview_unit_hint(reg, composition_kind, numerator, denominator, weight, missing)
    return DerivedMetricBrief(
        status=status,
        composition_kind=composition_kind,
        components=components,
        propagated_verification="unverified",
        unit_hint=unit_hint,
        authoring_template=template,
        matches=(),
        questions=(),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Data-backed prepare APIs
# ---------------------------------------------------------------------------


def prepare_entity(
    project: SemanticProject,
    *,
    datasource: str,
    source: EntitySourceIR,
    domain: str,
    scope: ScanScope = _DEFAULT_SCOPE,
) -> EntityBrief:
    """Prepare an entity authoring brief with datasource evidence.

    Inspects the datasource source for table metadata and column profiles,
    detects time-like columns, and identifies primary-key candidates.

    Args:
        project: The loaded semantic project.
        datasource: Name of the project datasource.
        source: Physical source (from ``md.table()``, ``md.parquet()``, or ``md.csv()``).
        domain: Target domain name for the entity.
        scope: Bounded scan configuration.

    Returns:
        An ``EntityBrief`` with datasource evidence for entity authoring.
    """
    from marivo import datasource as md

    project.load(domains=list(project._filtered_domains) if project._filtered_domains else None)
    table = md.inspect_source(datasource, source=source, project_root=project.workspace_dir)
    inspection = md.inspect_columns(
        datasource, source, scope=scope, project_root=project.workspace_dir
    )
    source_key = source_to_dict(source)
    matches = _entity_matches(project, datasource=datasource, source_key=source_key, domain=domain)
    time_like = tuple(profile.name for profile in inspection.profiles if _looks_temporal(profile))
    issues: tuple[AssessmentIssue, ...] = ()
    questions: tuple[AuthoringQuestion, ...] = ()
    return EntityBrief(
        status=derive_brief_status(issues=issues, questions=questions),
        datasource=datasource,
        source=source,
        domain=domain,
        table=table,
        column_profiles=inspection.profiles,
        primary_key_candidates=_primary_key_candidates(inspection),
        versioning_hints=VersioningHints(
            snapshot_partition=None, cadence_estimate=None, validity_pair=None
        ),
        time_like_columns=time_like,
        matches=matches,
        questions=questions,
        issues=issues,
        scan=inspection.scan,
    )


def prepare_dimensions(
    project: SemanticProject,
    *,
    entity: str,
    columns: Sequence[str],
    scope: ScanScope = _DEFAULT_SCOPE,
) -> tuple[DimensionBrief, ...]:
    """Prepare dimension authoring briefs for the given entity columns.

    For each column, profiles the column data from the datasource and
    checks for matches against existing dimensions.

    Args:
        project: The loaded semantic project.
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        columns: Column names to prepare dimension briefs for.
        scope: Bounded scan configuration.

    Returns:
        A tuple of ``DimensionBrief`` objects, one per column.
    """
    from marivo import datasource as md

    entity_ir, source = _require_entity(project, entity)
    inspection = md.inspect_columns(
        entity_ir.datasource,
        source,
        columns=tuple(columns),
        scope=scope,
        project_root=project.workspace_dir,
    )
    profile_by_name = {profile.name: profile for profile in inspection.profiles}
    briefs: list[DimensionBrief] = []
    for column in columns:
        profile = profile_by_name.get(column)
        is_missing = profile is None or profile.data_type == "UNKNOWN"
        issues = list(_missing_column_issue(entity, column) if is_missing else ())
        shadow_issue = _ibis_shadowing_issue(entity, column)
        if shadow_issue is not None:
            issues.append(shadow_issue)
        briefs.append(
            DimensionBrief(
                status=derive_brief_status(issues=tuple(issues), questions=()),
                entity=entity,
                column=column,
                profile=profile or _unknown_profile(column),
                value_shape=_value_shape(profile)
                if profile is not None and not is_missing
                else "free_text",
                matches=_dimension_matches(project, entity=entity, column=column),
                questions=(),
                issues=tuple(issues),
                scan=inspection.scan,
            )
        )
    return tuple(briefs)


def prepare_dimension(
    project: SemanticProject,
    *,
    entity: str,
    column: str,
    scope: ScanScope = _DEFAULT_SCOPE,
) -> DimensionBrief:
    """Prepare a dimension authoring brief for one entity column.

    Profiles the column data from the datasource and checks for matches
    against existing dimensions.

    Args:
        project: The loaded semantic project.
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        column: Column name to prepare a dimension brief for.
        scope: Bounded scan configuration.

    Returns:
        A ``DimensionBrief`` with status, profile, and match evidence.
    """
    briefs = prepare_dimensions(project, entity=entity, columns=(column,), scope=scope)
    # prepare_dimensions always returns one brief per input column.
    if not briefs:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

        raise SemanticRuntimeError(
            kind=ErrorKind.MATERIALIZE_FAILED,
            message=f"prepare_dimension produced no brief for column {column!r} on {entity!r}.",
        )
    return briefs[0]


def prepare_time_dimension(
    project: SemanticProject,
    *,
    entity: str,
    column: str,
    scope: ScanScope = _DEFAULT_SCOPE,
) -> TimeDimensionBrief:
    """Prepare a time dimension authoring brief with format detection.

    Profiles the column, detects candidate time formats for string/integer
    columns, and lists existing time dimensions on the entity.

    Args:
        project: The loaded semantic project.
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        column: Column name to prepare a time dimension brief for.
        scope: Bounded scan configuration.

    Returns:
        A ``TimeDimensionBrief`` with detected formats and evidence.
    """
    from marivo import datasource as md

    entity_ir, source = _require_entity(project, entity)
    inspection = md.inspect_columns(
        entity_ir.datasource,
        source,
        columns=(column,),
        scope=scope,
        project_root=project.workspace_dir,
    )
    profile = inspection.profiles[0] if inspection.profiles else _unknown_profile(column)
    detected = _detect_time_formats(profile) if profile.name == column else ()
    existing_time_dims = _existing_time_dimensions(project, entity)
    issues: list[AssessmentIssue] = []
    questions: tuple[AuthoringQuestion, ...] = ()
    shadow_issue = _ibis_shadowing_issue(entity, column)
    if shadow_issue is not None:
        issues.append(shadow_issue)
    return TimeDimensionBrief(
        status=derive_brief_status(issues=tuple(issues), questions=questions),
        entity=entity,
        column=column,
        profile=profile,
        detected_formats=detected,
        value_range=(profile.min_value, profile.max_value),
        partition_aligned=False,
        granularity_evidence=None,
        cadence_estimate=None,
        existing_time_dimensions=existing_time_dims,
        questions=questions,
        issues=tuple(issues),
        scan=inspection.scan,
    )


def prepare_metric(
    project: SemanticProject,
    *,
    entity: str,
    measure_columns: Sequence[str] = (),
    filter_dimensions: Sequence[str] = (),
    scope: ScanScope = _DEFAULT_SCOPE,
) -> MetricBrief:
    """Prepare a metric authoring brief with measure evidence.

    Profiles measure columns and lists time dimensions and filter
    dimension values for the entity.

    Args:
        project: The loaded semantic project.
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        measure_columns: Column names that will serve as measure inputs.
        filter_dimensions: Dimension refs whose top values to include.
        scope: Bounded scan configuration.

    Returns:
        A ``MetricBrief`` with measure profiles and filter evidence.
    """
    from marivo import datasource as md

    entity_ir, source = _require_entity(project, entity)
    if measure_columns:
        inspection = md.inspect_columns(
            entity_ir.datasource,
            source,
            columns=tuple(measure_columns),
            scope=scope,
            project_root=project.workspace_dir,
        )
        measure_profiles = inspection.profiles
        scan = inspection.scan
    else:
        measure_profiles = ()
        scan = ScanReport(
            partition_used=None,
            partition_resolution="none",
            rows_scanned=0,
            columns_scanned=(),
            truncated=False,
            elapsed_seconds=0.0,
            warnings=(),
        )
    time_dimensions = _existing_time_dimensions(project, entity)
    filter_values = tuple(
        DimensionValueFact(
            dimension=dim,
            top_values=(),
        )
        for dim in filter_dimensions
    )
    issues: list[AssessmentIssue] = []
    for measure_col in measure_columns:
        shadow_issue = _ibis_shadowing_issue(entity, measure_col)
        if shadow_issue is not None:
            issues.append(shadow_issue)
    questions: tuple[AuthoringQuestion, ...] = ()
    return MetricBrief(
        status=derive_brief_status(issues=tuple(issues), questions=questions),
        entity=entity,
        measure_profiles=measure_profiles,
        filter_dimension_values=filter_values,
        time_dimensions=time_dimensions,
        matches=(),
        questions=questions,
        issues=tuple(issues),
        scan=scan,
    )


def _additivity_hint(
    profile: ScanColumnProfile,
) -> Literal["additive", "non_additive", "semi_additive", "unknown"]:
    """Infer additivity from the column's data type."""
    dt = profile.data_type.lower()
    if dt in ("int", "integer", "bigint", "smallint", "tinyint"):
        return "additive"
    if dt in ("float", "double", "decimal", "numeric"):
        return "additive"
    return "unknown"


def _measure_matches(
    project: SemanticProject,
    *,
    entity: str,
    column: str,
) -> tuple[RegisteredMatch, ...]:
    """Find registered measures with the same column or name."""
    reg = project._registry
    if reg is None:
        return ()
    matches: list[RegisteredMatch] = []
    for measure_ir in reg.measures.values():
        if measure_ir.entity != entity:
            continue
        if measure_ir.name == column:
            matches.append(RegisteredMatch(ref=measure_ir.semantic_id, basis="name_exact"))
        elif measure_ir.python_symbol == column:
            matches.append(RegisteredMatch(ref=measure_ir.semantic_id, basis="same_column"))
    return tuple(matches)


def prepare_measure(
    project: SemanticProject,
    *,
    entity: str,
    column: str,
    scope: ScanScope = _DEFAULT_SCOPE,
) -> MeasureBrief:
    """Prepare a measure authoring brief for one entity column.

    Profiles the column data from the datasource and provides an additivity
    hint based on the column's data type. Checks for matches against existing
    measures in the registry.

    Args:
        project: The loaded semantic project.
        entity: Qualified entity reference (e.g. ``"sales.orders"``).
        column: Column name to prepare a measure brief for.
        scope: Bounded scan configuration.

    Returns:
        A ``MeasureBrief`` with status, profile, additivity hint, and match evidence.
    """
    from marivo import datasource as md

    entity_ir, source = _require_entity(project, entity)
    inspection = md.inspect_columns(
        entity_ir.datasource,
        source,
        columns=(column,),
        scope=scope,
        project_root=project.workspace_dir,
    )
    profile = inspection.profiles[0] if inspection.profiles else _unknown_profile(column)
    is_missing = profile is None or profile.data_type == "UNKNOWN"
    issues: list[AssessmentIssue] = list(
        _missing_column_issue(entity, column) if is_missing else ()
    )
    shadow_issue = _ibis_shadowing_issue(entity, column)
    if shadow_issue is not None:
        issues.append(shadow_issue)
    questions: tuple[AuthoringQuestion, ...] = ()
    return MeasureBrief(
        status=derive_brief_status(issues=tuple(issues), questions=questions),
        entity=entity,
        column=column,
        profile=profile or _unknown_profile(column),
        additivity_hint=_additivity_hint(profile) if not is_missing else "unknown",
        matches=_measure_matches(project, entity=entity, column=column),
        questions=questions,
        issues=tuple(issues),
        scan=inspection.scan,
    )


def prepare_relationship(
    project: SemanticProject,
    *,
    from_entity: str,
    to_entity: str,
    keys: Sequence[tuple[str, str]],
    scope: ScanScope = _DEFAULT_SCOPE,
) -> RelationshipBrief:
    """Prepare a relationship authoring brief with join-key probe evidence.

    Probes join compatibility between the from-entity and to-entity on the
    specified key pairs and checks for matching relationships in the registry.

    Args:
        project: The loaded semantic project.
        from_entity: Qualified entity reference (e.g. ``"sales.orders"``).
        to_entity: Qualified entity reference (e.g. ``"sales.customers"``).
        keys: Join-key pairs as ``(from_key, to_key)`` tuples, matching
            ``ms.join_on(left, right)``.
        scope: Bounded scan configuration.

    Returns:
        A ``RelationshipBrief`` with probe evidence and registry matches.
    """
    from marivo import datasource as md

    from_ir, from_source = _require_entity(project, from_entity)
    to_ir, to_source = _require_entity(project, to_entity)
    from_keys = tuple(k[0] for k in keys)
    to_keys = tuple(k[1] for k in keys)
    _require_keys(project, from_keys + to_keys)
    probe = md.probe_join_keys(
        from_side=JoinSide(
            from_ir.datasource, from_source, columns=_key_columns(project, from_keys)
        ),
        to_side=JoinSide(to_ir.datasource, to_source, columns=_key_columns(project, to_keys)),
        scope=scope,
        project_root=project.workspace_dir,
    )
    issues: tuple[AssessmentIssue, ...] = ()
    questions: tuple[AuthoringQuestion, ...] = ()
    return RelationshipBrief(
        status=derive_brief_status(issues=issues, questions=questions),
        from_entity=from_entity,
        to_entity=to_entity,
        keys=tuple(keys),
        probe=probe,
        to_entity_versioning=None,
        matches=_relationship_matches(project, from_entity, to_entity, from_keys, to_keys),
        questions=questions,
        issues=issues,
    )


def prepare_cross_entity_metric(
    project: SemanticProject,
    *,
    root_entity: str,
    entities: Sequence[str],
    measure_columns: Sequence[str] = (),
    scope: ScanScope = _DEFAULT_SCOPE,
) -> CrossEntityMetricBrief:
    """Prepare a cross-entity metric brief with relationship path evidence.

    Checks for relationship paths between the root entity and each target
    entity, profiles measure columns from the root entity, and reports
    unreachable entities as blockers.

    Args:
        project: The loaded semantic project.
        root_entity: Qualified root entity reference.
        entities: Target entity refs that must be reachable.
        measure_columns: Column names on the root entity to profile.
        scope: Bounded scan configuration.

    Returns:
        A ``CrossEntityMetricBrief`` with join paths and issue evidence.
    """
    from marivo import datasource as md

    root_ir, root_source = _require_entity(project, root_entity)
    paths, unreachable = _relationship_paths(project, root_entity, tuple(entities))
    issues = tuple(
        AssessmentIssue(
            kind="unreachable_entity",
            severity="blocker",
            refs=(entity,),
            message=f"No relationship path from {root_entity} to {entity}.",
            rule_id="prepare_cross_entity_metric_relationship_path",
        )
        for entity in unreachable
    )
    if measure_columns:
        inspection = md.inspect_columns(
            root_ir.datasource,
            root_source,
            columns=tuple(measure_columns),
            scope=scope,
            project_root=project.workspace_dir,
        )
        measure_profiles = inspection.profiles
        scan = inspection.scan
    else:
        measure_profiles = ()
        scan = ScanReport(
            partition_used=None,
            partition_resolution="none",
            rows_scanned=0,
            columns_scanned=(),
            truncated=False,
            elapsed_seconds=0.0,
            warnings=(),
        )
    root_time_dims = _existing_time_dimensions(project, root_entity)
    return CrossEntityMetricBrief(
        status=derive_brief_status(issues=issues, questions=()),
        root_entity=root_entity,
        entities=tuple(entities),
        join_paths=paths,
        unreachable_entities=unreachable,
        measure_profiles=measure_profiles,
        root_time_dimensions=root_time_dims,
        questions=(),
        issues=issues,
        scan=scan,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_entity(project: SemanticProject, entity_ref: str) -> tuple[Any, EntitySourceIR]:
    """Return (EntityIR, source) from the registry, raising if not found."""
    reg = project._registry
    if reg is None:
        from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError

        raise SemanticLoadFailed(
            [
                SemanticRuntimeError(
                    kind=ErrorKind.PROJECT_NOT_LOADED,
                    message="Project is not loaded. Call project.load() first.",
                )
            ]
        )
    entity_ir = reg.entities.get(entity_ref)
    if entity_ir is None:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

        raise SemanticRuntimeError(
            kind=ErrorKind.INVALID_REF,
            message=f"Entity {entity_ref!r} not found in the project registry.",
        )
    return entity_ir, entity_ir.source


def _looks_temporal(profile: ScanColumnProfile) -> bool:
    """Return True if a column profile looks like it holds temporal data."""
    dt = profile.data_type.lower()
    if "date" in dt or "datetime" in dt or "timestamp" in dt:
        return True
    if dt in ("string", "varchar", "text", "char"):
        for value in profile.sample_values:
            if isinstance(value, str) and _matches_date_format(value):
                return True
    return False


def _matches_date_format(value: str) -> bool:
    """Return True if the string value matches a common date format."""
    for fmt, pattern in _COMMON_DATE_FORMATS:
        if re.match(pattern, value):
            try:
                datetime.strptime(value, fmt)
                return True
            except ValueError:
                continue
    return False


def _primary_key_candidates(inspection: ColumnInspection) -> tuple[PrimaryKeyCandidate, ...]:
    """Identify columns that could be primary keys from the inspection."""
    total_rows = inspection.scan.rows_scanned
    if total_rows == 0:
        return ()
    candidates: list[PrimaryKeyCandidate] = []
    for profile in inspection.profiles:
        if profile.null_count == 0 and profile.distinct_count == total_rows:
            ratio = profile.distinct_count / total_rows if total_rows > 0 else 0.0
            candidates.append(
                PrimaryKeyCandidate(
                    columns=(profile.name,),
                    sampled_unique=True,
                    distinct_ratio=ratio,
                )
            )
    return tuple(candidates)


def _entity_matches(
    project: SemanticProject,
    *,
    datasource: str,
    source_key: dict[str, object],
    domain: str,
) -> tuple[RegisteredMatch, ...]:
    """Find registered entities with the same datasource and source."""
    reg = project._registry
    if reg is None:
        return ()
    matches: list[RegisteredMatch] = []
    for entity_ir in reg.entities.values():
        if entity_ir.datasource != datasource:
            continue
        if source_to_dict(entity_ir.source) == source_key:
            matches.append(RegisteredMatch(ref=entity_ir.semantic_id, basis="same_source"))
    return tuple(matches)


def _missing_column_issue(entity: str, column: str) -> tuple[AssessmentIssue, ...]:
    """Return a missing_column issue tuple."""
    return (
        AssessmentIssue(
            kind="missing_column",
            severity="blocker",
            refs=(f"{entity}.{column}",),
            message=f"Column {column!r} not found in entity {entity!r}.",
            rule_id="prepare_dimension_column_exists",
        ),
    )


def _unknown_profile(column: str) -> ScanColumnProfile:
    """Return a placeholder ColumnProfile for an unknown column."""
    return ScanColumnProfile(
        name=column,
        data_type="UNKNOWN",
        nullable=None,
        comment=None,
        null_count=0,
        empty_count=0,
        distinct_count=0,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
    )


def _value_shape(
    profile: ScanColumnProfile,
) -> Literal["enum_like", "id_like", "numeric", "boolean_like", "temporal_like", "free_text"]:
    """Infer the value shape from a column profile."""
    dt = profile.data_type.lower()
    if dt in (
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "float",
        "double",
        "decimal",
        "numeric",
    ):
        return "numeric"
    if dt in ("boolean", "bool"):
        return "boolean_like"
    if "date" in dt or "timestamp" in dt or "datetime" in dt:
        return "temporal_like"
    total_rows = profile.null_count + profile.distinct_count
    if (
        total_rows > 0
        and profile.distinct_count <= 20
        and profile.distinct_count < 0.5 * total_rows
    ):
        return "enum_like"
    if profile.distinct_count == total_rows and total_rows > 0:
        return "id_like"
    return "free_text"


def _detect_time_formats(profile: ScanColumnProfile) -> tuple[FormatCandidate, ...]:
    """Detect candidate time formats for string/integer columns."""
    dt = profile.data_type.lower()
    if dt in ("date",):
        return (FormatCandidate(variant="date", match_rate=1.0, backend_caveats=()),)
    if dt in ("datetime",):
        return (
            FormatCandidate(variant="datetime", timezone=None, match_rate=1.0, backend_caveats=()),
        )
    if "timestamp" in dt:
        return (
            FormatCandidate(variant="timestamp", timezone=None, match_rate=1.0, backend_caveats=()),
        )
    if dt not in ("string", "varchar", "text", "integer", "int", "bigint"):
        return ()
    candidates: list[FormatCandidate] = []
    data_type_hint = "string" if dt in ("string", "varchar", "text") else "integer"
    for value in profile.sample_values:
        if not isinstance(value, str):
            continue
        for fmt, pattern in _COMMON_DATE_FORMATS:
            if re.match(pattern, value):
                try:
                    datetime.strptime(value, fmt)
                    if not any(c.strptime_format == fmt for c in candidates):
                        candidates.append(
                            FormatCandidate(
                                variant="strptime",
                                strptime_format=fmt,
                                data_type=data_type_hint,
                                match_rate=1.0,
                                backend_caveats=(),
                            )
                        )
                except ValueError:
                    continue
    return tuple(candidates)


def _dimension_matches(
    project: SemanticProject,
    *,
    entity: str,
    column: str,
) -> tuple[RegisteredMatch, ...]:
    """Find registered dimensions with the same column or name."""
    reg = project._registry
    if reg is None:
        return ()
    matches: list[RegisteredMatch] = []
    for field_ir in reg.dimensions.values():
        if field_ir.entity != entity:
            continue
        # Match by name (dimension name equals column name)
        if field_ir.name == column:
            matches.append(RegisteredMatch(ref=field_ir.semantic_id, basis="name_exact"))
        # Match by same column via python_symbol matching column name
        elif field_ir.python_symbol == column:
            matches.append(RegisteredMatch(ref=field_ir.semantic_id, basis="same_column"))
    return tuple(matches)


def _existing_time_dimensions(project: SemanticProject, entity: str) -> tuple[str, ...]:
    """Return semantic_ids of time dimensions on the given entity."""
    reg = project._registry
    if reg is None:
        return ()
    return tuple(
        field_ir.semantic_id
        for field_ir in reg.dimensions.values()
        if field_ir.entity == entity and field_ir.is_time_dimension
    )


def _require_dimensions(project: SemanticProject, dimension_refs: tuple[str, ...]) -> None:
    """Validate that all dimension refs exist in the registry."""
    reg = project._registry
    if reg is None:
        from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError

        raise SemanticLoadFailed(
            [
                SemanticRuntimeError(
                    kind=ErrorKind.PROJECT_NOT_LOADED,
                    message="Project is not loaded. Call project.load() first.",
                )
            ]
        )
    missing = tuple(ref for ref in dimension_refs if ref not in reg.dimensions)
    if missing:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

        raise SemanticRuntimeError(
            kind=ErrorKind.INVALID_REF,
            message=f"Dimension(s) not found: {', '.join(missing)}.",
        )


def _dimension_columns(project: SemanticProject, dimensions: Sequence[str]) -> tuple[str, ...]:
    """Resolve dimension refs to their physical column names.

    For the MVP, the dimension name (last segment of the semantic_id) is
    used as the physical column name. This is correct for simple column-
    reference dimensions and is the simplest correct approach for the join
    probe.
    """
    reg = project._registry
    if reg is None:
        return ()
    columns: list[str] = []
    for dim_ref in dimensions:
        field_ir = reg.dimensions.get(dim_ref)
        if field_ir is not None:
            columns.append(field_ir.name)
        else:
            # Fallback: use the last segment of the semantic_id
            columns.append(dim_ref.rsplit(".", 1)[-1])
    return tuple(columns)


def _require_keys(project: SemanticProject, key_refs: tuple[str, ...]) -> None:
    """Validate that all key refs exist in the registry as dimensions or measures."""
    reg = project._registry
    if reg is None:
        from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError

        raise SemanticLoadFailed(
            [
                SemanticRuntimeError(
                    kind=ErrorKind.PROJECT_NOT_LOADED,
                    message="Project is not loaded. Call project.load() first.",
                )
            ]
        )
    missing = tuple(
        ref for ref in key_refs if ref not in reg.dimensions and ref not in reg.measures
    )
    if missing:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

        raise SemanticRuntimeError(
            kind=ErrorKind.INVALID_REF,
            message=f"Key ref(s) not found: {', '.join(missing)}.",
        )


def _key_columns(project: SemanticProject, key_refs: Sequence[str]) -> tuple[str, ...]:
    """Resolve key refs to their physical column names.

    Checks both dimensions and measures registries.
    """
    reg = project._registry
    if reg is None:
        return ()
    columns: list[str] = []
    for key_ref in key_refs:
        field_ir = reg.dimensions.get(key_ref)
        if field_ir is not None:
            columns.append(field_ir.name)
            continue
        measure_ir = reg.measures.get(key_ref)
        if measure_ir is not None:
            columns.append(measure_ir.name)
            continue
        columns.append(key_ref.rsplit(".", 1)[-1])
    return tuple(columns)


def _relationship_matches(
    project: SemanticProject,
    from_entity: str,
    to_entity: str,
    from_dims: Sequence[str],
    to_dims: Sequence[str],
) -> tuple[RegisteredMatch, ...]:
    """Find registered relationships with the same endpoints."""
    reg = project._registry
    if reg is None:
        return ()
    matches: list[RegisteredMatch] = []
    for rel_ir in reg.relationships.values():
        if rel_ir.from_entity != from_entity or rel_ir.to_entity != to_entity:
            continue
        if {k.from_key for k in rel_ir.keys} == set(from_dims) and {
            k.to_key for k in rel_ir.keys
        } == set(to_dims):
            matches.append(RegisteredMatch(ref=rel_ir.semantic_id, basis="same_endpoints"))
    return tuple(matches)


def _relationship_paths(
    project: SemanticProject,
    root_entity: str,
    target_entities: tuple[str, ...],
) -> tuple[tuple[JoinPathFact, ...], tuple[str, ...]]:
    """Walk the relationship graph from root to targets.

    Returns a pair of (reachable paths, unreachable entity refs). For each
    target entity that has a path (possibly multi-hop) from the root, a
    ``JoinPathFact`` entry is constructed. Entities with no path are
    returned as unreachable.
    """
    from collections import deque

    from marivo.semantic.ir import RelationshipIR

    reg = project._registry
    if reg is None:
        return (), target_entities

    # Build an adjacency graph from relationships.
    # Each edge is (neighbor_entity, relationship_ir).
    edges: dict[str, list[tuple[str, RelationshipIR]]] = {}
    for rel_ir in reg.relationships.values():
        edges.setdefault(rel_ir.from_entity, []).append((rel_ir.to_entity, rel_ir))
        # Relationships are bidirectional for path-finding purposes.
        edges.setdefault(rel_ir.to_entity, []).append((rel_ir.from_entity, rel_ir))

    paths: list[JoinPathFact] = []
    unreachable: list[str] = []

    _hop_type = tuple[str, str, RelationshipIR]

    for target in target_entities:
        if target == root_entity:
            # Same entity is trivially reachable (no join needed).
            continue

        # BFS from root_entity to target.
        found = False
        visited: set[str] = {root_entity}
        queue: deque[tuple[str, list[_hop_type]]] = deque([(root_entity, [])])
        while queue:
            current, path_so_far = queue.popleft()
            for neighbor, rel_ir in edges.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                new_path: list[_hop_type] = [*path_so_far, (current, neighbor, rel_ir)]
                if neighbor == target:
                    # Found a path; emit JoinPathFact entries.
                    for from_ref, to_ref, rel in new_path:
                        paths.append(
                            JoinPathFact(
                                from_ref=from_ref,
                                to_ref=to_ref,
                                relationship=rel.semantic_id,
                                cardinality="unknown",
                                fanout_risk=False,
                            )
                        )
                    found = True
                    break
                queue.append((neighbor, new_path))
            if found:
                break

        if not found:
            unreachable.append(target)

    return tuple(paths), tuple(unreachable)
