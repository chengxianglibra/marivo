"""Public datasource discovery execution functions."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.discovery import (
    DimensionDiscoveryResult,
    DimensionValueDiscoveryResult,
    DimensionValueFact,
    DiscoveryIssue,
    DiscoverySignal,
    EntityDiscoveryResult,
    KeyTypeEvidence,
    MeasureDiscoveryResult,
    RelationshipDiscoveryResult,
    TableSource,
    TimeDimensionDiscoveryResult,
)
from marivo.datasource.discovery_rules import (
    build_dimension_result,
    build_entity_result,
    build_measure_result,
    build_relationship_result,
    build_time_dimension_result,
    dimension_value_rules,
)
from marivo.datasource.manage import inspect_columns as _inspect_columns
from marivo.datasource.manage import probe_join_keys as _probe_join_keys
from marivo.datasource.metadata import inspect_source as _inspect_source
from marivo.datasource.scan import ColumnInspection, JoinSide, ScanScope


def _scope_or_default(scope: ScanScope | None) -> ScanScope:
    return ScanScope() if scope is None else scope


def _datasource_id(datasource: DatasourceRef) -> str:
    if not isinstance(datasource, DatasourceRef):
        raise TypeError(
            f"datasource must be md.DatasourceRef from md.ref(...), got {type(datasource).__name__}."
        )
    return datasource.id


def _profiles(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    columns: tuple[str, ...] | None,
    scope: ScanScope,
    project_root: Path | None,
) -> ColumnInspection:
    return _inspect_columns(
        _datasource_id(datasource),
        source,
        columns=columns,
        scope=scope,
        project_root=project_root,
    )


def _split_rule_items(
    items: tuple[DiscoverySignal | DiscoveryIssue, ...],
) -> tuple[tuple[DiscoverySignal, ...], tuple[DiscoveryIssue, ...]]:
    return (
        tuple(item for item in items if isinstance(item, DiscoverySignal)),
        tuple(item for item in items if isinstance(item, DiscoveryIssue)),
    )


def discover_entity(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    scope: ScanScope | None = None,
    project_root: Path | None = None,
) -> EntityDiscoveryResult:
    """Discover entity-level datasource evidence for one physical source.

    Args:
        datasource: Datasource reference returned by ``md.ref("warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        scope: Optional bounded scan scope. Use ``md.latest_partition()``,
            ``md.partition({...})``, or ``md.unpruned()``.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``EntityDiscoveryResult`` with table metadata, scan evidence, primary-key
        evidence, time-like columns, partition columns, column profiles, signals,
        and issues.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_entity(warehouse, md.table("orders"), scope=md.latest_partition())

    Constraints:
        Discovery returns bounded evidence only. It does not author semantic
        objects or decide business meaning.
    """
    scan_scope = _scope_or_default(scope)
    datasource_id = _datasource_id(datasource)
    metadata = _inspect_source(
        datasource_id,
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    inspection = _profiles(
        datasource,
        source,
        columns=None,
        scope=scan_scope,
        project_root=project_root,
    )
    return build_entity_result(
        datasource=datasource,
        source=source,
        table_metadata=metadata,
        scan=inspection.scan,
        scope=scan_scope,
        column_profiles=inspection.profiles,
    )


def discover_dimensions(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: ScanScope | None = None,
    project_root: Path | None = None,
) -> DimensionDiscoveryResult:
    """Discover dimension-shaped column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional physical column subset. ``None`` profiles all columns
            within ``scope.max_columns``.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DimensionDiscoveryResult`` with one ``.columns`` entry per profiled column.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_dimensions(warehouse, md.table("orders"), columns=("status",))

    Constraints:
        Signals describe sampled column shape only. Distinct values are runtime
        evidence and belong in ``md.discover_dimension_values(...)`` when needed.
    """
    scan_scope = _scope_or_default(scope)
    metadata = _inspect_source(
        _datasource_id(datasource),
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    inspection = _profiles(
        datasource,
        source,
        columns=columns,
        scope=scan_scope,
        project_root=project_root,
    )
    return build_dimension_result(
        datasource=datasource,
        source=source,
        table_metadata=metadata,
        scan=inspection.scan,
        scope=scan_scope,
        column_profiles=inspection.profiles,
    )


def discover_time_dimensions(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: ScanScope | None = None,
    project_root: Path | None = None,
) -> TimeDimensionDiscoveryResult:
    """Discover time-dimension column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional candidate column subset.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``TimeDimensionDiscoveryResult`` with ``.columns`` evidence, detected
        formats, value ranges, partition alignment evidence, signals, and issues.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_time_dimensions(warehouse, md.table("orders"), columns=("created_at",))

    Constraints:
        Discovery does not decide timezone policy, default business time, or
        semantic granularity.
    """
    scan_scope = _scope_or_default(scope)
    metadata = _inspect_source(
        _datasource_id(datasource),
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    inspection = _profiles(
        datasource,
        source,
        columns=columns,
        scope=scan_scope,
        project_root=project_root,
    )
    return build_time_dimension_result(
        datasource=datasource,
        source=source,
        table_metadata=metadata,
        scan=inspection.scan,
        scope=scan_scope,
        column_profiles=inspection.profiles,
    )


def discover_measures(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: ScanScope | None = None,
    project_root: Path | None = None,
) -> MeasureDiscoveryResult:
    """Discover measure-shaped column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional candidate column subset.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``MeasureDiscoveryResult`` with ``.columns`` evidence and deterministic
        measure evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_measures(warehouse, md.table("orders"), columns=("amount",))

    Constraints:
        Discovery does not choose authoritative units, additivity, or metric
        aggregation.
    """
    scan_scope = _scope_or_default(scope)
    metadata = _inspect_source(
        _datasource_id(datasource),
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    inspection = _profiles(
        datasource,
        source,
        columns=columns,
        scope=scan_scope,
        project_root=project_root,
    )
    return build_measure_result(
        datasource=datasource,
        source=source,
        table_metadata=metadata,
        scan=inspection.scan,
        scope=scan_scope,
        column_profiles=inspection.profiles,
    )


def _key_type_evidence(
    from_side: JoinSide,
    to_side: JoinSide,
    *,
    scope: ScanScope,
    project_root: Path | None,
) -> tuple[KeyTypeEvidence, ...]:
    entries: list[KeyTypeEvidence] = []
    pairs: tuple[tuple[Literal["from", "to"], JoinSide], ...] = (
        ("from", from_side),
        ("to", to_side),
    )
    for label, side in pairs:
        inspection = _profiles(
            side.datasource,
            side.source,
            columns=tuple(side.columns),
            scope=scope,
            project_root=project_root,
        )
        by_name = {profile.name: profile for profile in inspection.profiles}
        for column in side.columns:
            profile = by_name.get(column)
            if profile is None:
                continue
            entries.append(
                KeyTypeEvidence(
                    side=label,
                    column=column,
                    type_family=profile.type_family,
                    data_type=profile.data_type,
                )
            )
    return tuple(entries)


def discover_relationship(
    *,
    from_side: JoinSide,
    to_side: JoinSide,
    scope: ScanScope | None = None,
    key_sample_size: int = 500,
    project_root: Path | None = None,
) -> RelationshipDiscoveryResult:
    """Discover relationship evidence between two datasource sources.

    Args:
        from_side: Left join side with datasource ref, source, and key columns.
        to_side: Right join side with datasource ref, source, and key columns.
        scope: Optional bounded scan scope. ``scope.max_rows`` bounds each side.
        key_sample_size: Maximum distinct from-side keys used for match/fanout evidence.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``RelationshipDiscoveryResult`` with sampled key evidence, match rate,
        fanout evidence, key type evidence, signals, and issues.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_relationship(
        ...     from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
        ...     to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
        ... )

    Constraints:
        ``key_sample_size`` bounds distinct-key evidence independently from
        ``scope.max_rows``. Discovery does not author the semantic relationship.
    """
    scan_scope = _scope_or_default(scope)
    probe = _probe_join_keys(
        from_side=from_side,
        to_side=to_side,
        scope=scan_scope,
        key_sample_size=key_sample_size,
        project_root=project_root,
    )
    return build_relationship_result(
        from_side=from_side,
        to_side=to_side,
        key_type_evidence=_key_type_evidence(
            from_side,
            to_side,
            scope=scan_scope,
            project_root=project_root,
        ),
        sampled_key_count=probe.sampled_key_count,
        matched_key_count=probe.matched_key_count,
        max_rows_per_key=probe.max_rows_per_key,
        avg_rows_per_key=probe.avg_rows_per_key,
        cardinality_evidence=probe.cardinality_estimate,
        from_scan=probe.from_scan,
        to_scan=probe.to_scan,
    )


def discover_dimension_values(
    datasource: DatasourceRef,
    source: TableSource,
    *,
    column: str,
    scope: ScanScope | None = None,
    limit: int = 50,
    project_root: Path | None = None,
) -> DimensionValueDiscoveryResult:
    """Discover bounded current value counts for one dimension column.

    Args:
        datasource: Datasource reference returned by ``md.ref("warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        column: Column to sample value counts from.
        scope: Optional bounded scan scope helper result.
        limit: Maximum number of value/count facts to return.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DimensionValueDiscoveryResult`` with bounded runtime value evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("warehouse")
        >>> md.discover_dimension_values(warehouse, md.table("orders"), column="status", limit=10)

    Constraints:
        Values are runtime evidence only. Marivo does not persist them into
        semantic ``ai_context``, enum metadata, or authored semantic objects.
    """
    if limit < 1:
        raise ValueError("limit must be positive.")
    scan_scope = _scope_or_default(scope)
    inspection = _profiles(
        datasource,
        source,
        columns=(column,),
        scope=scan_scope,
        project_root=project_root,
    )
    profile = inspection.profiles[0]
    raw_values = profile.top_values[:limit]
    facts = tuple(
        DimensionValueFact(value=value, count=count)
        for value, count in raw_values
        if isinstance(value, (str, int, float, bool)) or value is None
    )
    complete = not inspection.scan.truncated and len(profile.top_values) <= limit
    rule_items = dimension_value_rules(facts, complete=complete)
    signals, issues = _split_rule_items(rule_items)
    return DimensionValueDiscoveryResult(
        datasource=datasource,
        source=source,
        column=column,
        values=facts,
        complete=complete,
        scan=inspection.scan,
        signals=signals,
        issues=issues,
    )
