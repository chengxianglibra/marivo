"""Public datasource discovery execution functions."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.discovery import (
    DatasourceResult,
    DimensionValueDiscoveryResult,
    DimensionValueFact,
    DiscoveryIssue,
    DiscoverySignal,
    KeyTypeEvidence,
    TableSource,
)
from marivo.datasource.discovery_rules import (
    build_dimension_result,
    build_entity_result,
    build_measure_result,
    build_relationship_result,
    build_time_dimension_result,
    dimension_value_rules,
)
from marivo.datasource.manage import _inspect_columns, _probe_join_keys
from marivo.datasource.metadata import TableMetadata, _inspect_source
from marivo.datasource.scan import ColumnInspection, JoinSide, ScanScope


def _scope_or_default(scope: ScanScope | None) -> ScanScope:
    return ScanScope() if scope is None else scope


def _datasource_id(datasource: DatasourceRef) -> str:
    if not isinstance(datasource, DatasourceRef):
        raise TypeError(
            f"datasource must be md.DatasourceRef from md.ref(...), got {type(datasource).__name__}."
        )
    return datasource.id


def _source_call(source: TableSource) -> str:
    table_name = getattr(source, "table", None)
    if isinstance(table_name, str):
        return f'md.table("{table_name}")'
    return 'md.table("<table>")'


def _partition_literal(columns: tuple[str, ...]) -> str:
    return "{" + ", ".join(f'"{column}": "..."' for column in columns) + "}"


def _require_discovery_partition_scope(
    *,
    metadata: TableMetadata | None,
    source: TableSource,
    scope: ScanScope,
) -> None:
    if metadata is None or not metadata.partitions:
        return
    partition_columns = tuple(partition.name for partition in metadata.partitions)
    transformed = tuple(
        partition.name for partition in metadata.partitions if partition.transform is not None
    )
    if transformed:
        columns_text = ", ".join(partition_columns)
        transformed_text = ", ".join(transformed)
        raise ValueError(
            "Partition filter required.\n\n"
            f"The table is partitioned by: {columns_text}.\n"
            "Discovery refuses to scan partitioned tables whose transformed partition "
            "values cannot be expressed safely as md.partition({...}).\n\n"
            f"Transformed partition columns: {transformed_text}.\n"
            "Run:\n"
            f"  md.inspect_partitions(ds, {_source_call(source)}, limit=50).show()\n\n"
            "Then provide explicit partition values from backend metadata or source knowledge."
        )
    if scope.partition is None:
        columns_text = ", ".join(partition_columns)
        literal = _partition_literal(partition_columns)
        raise ValueError(
            "Partition filter required.\n\n"
            f"The table is partitioned by: {columns_text}.\n"
            "Discovery refuses to scan partitioned tables without an explicit partition filter.\n\n"
            "Run:\n"
            f"  md.inspect_partitions(ds, {_source_call(source)}, limit=50).show()\n\n"
            "Then call:\n"
            f"  scope = md.partition({literal})"
        )
    missing = tuple(column for column in partition_columns if column not in scope.partition)
    if missing:
        columns_text = ", ".join(partition_columns)
        missing_text = ", ".join(missing)
        literal = _partition_literal(partition_columns)
        raise ValueError(
            "Partition filter required.\n\n"
            f"The table is partitioned by: {columns_text}.\n"
            f"The provided md.partition(...) is missing: {missing_text}.\n\n"
            "Then call:\n"
            f"  scope = md.partition({literal})"
        )


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
) -> DatasourceResult:
    """Discover entity-level datasource evidence for one physical source.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        scope: Optional bounded scan scope. Partitioned tables require
            explicit ``md.partition({...})``.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence,
        including schema columns, partition columns when the backend exposes
        them, primary-key evidence, and sampled column profiles.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
        >>> md.discover_entity(warehouse, md.table("orders"), scope=md.partition({"dt": "20260629"}))

    Constraints:
        Discovery returns bounded evidence only. It does not author semantic
        objects or decide business meaning. Partition evidence is always
        requested as part of entity discovery; there is no public
        ``include_partitions`` switch.
    """
    scan_scope = _scope_or_default(scope)
    datasource_id = _datasource_id(datasource)
    metadata = _inspect_source(
        datasource_id,
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    _require_discovery_partition_scope(metadata=metadata, source=source, scope=scan_scope)
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
) -> DatasourceResult:
    """Discover dimension-shaped column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional physical column subset. ``None`` profiles all columns
            within ``scope.max_columns``.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
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
    _require_discovery_partition_scope(metadata=metadata, source=source, scope=scan_scope)
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
) -> DatasourceResult:
    """Discover time-dimension column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional candidate column subset.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
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
    _require_discovery_partition_scope(metadata=metadata, source=source, scope=scan_scope)
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
) -> DatasourceResult:
    """Discover measure-shaped column evidence for one source.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Optional candidate column subset.
        scope: Optional bounded scan scope helper result.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
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
    _require_discovery_partition_scope(metadata=metadata, source=source, scope=scan_scope)
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
) -> DatasourceResult:
    """Discover relationship evidence between two datasource sources.

    Args:
        from_side: Left join side with datasource ref, source, and key columns.
        to_side: Right join side with datasource ref, source, and key columns.
        scope: Optional bounded scan scope. ``scope.max_rows`` bounds each side.
        key_sample_size: Maximum distinct from-side keys used for match/fanout evidence.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
        >>> md.discover_relationship(
        ...     from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
        ...     to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
        ... )

    Constraints:
        ``key_sample_size`` bounds distinct-key evidence independently from
        ``scope.max_rows``. Discovery does not author the semantic relationship.
    """
    scan_scope = _scope_or_default(scope)
    from_metadata = _inspect_source(
        _datasource_id(from_side.datasource),
        source=from_side.source,
        include_partitions=True,
        project_root=project_root,
    )
    to_metadata = _inspect_source(
        _datasource_id(to_side.datasource),
        source=to_side.source,
        include_partitions=True,
        project_root=project_root,
    )
    _require_discovery_partition_scope(
        metadata=from_metadata,
        source=from_side.source,
        scope=scan_scope,
    )
    _require_discovery_partition_scope(
        metadata=to_metadata,
        source=to_side.source,
        scope=scan_scope,
    )
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
) -> DatasourceResult:
    """Discover bounded current value counts for one dimension column.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        column: Column to sample value counts from.
        scope: Optional bounded scan scope helper result.
        limit: Maximum number of value/count facts to return.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult``; call `.show()` to inspect bounded evidence.

    Example:
        >>> import marivo.datasource as md
        >>> warehouse = md.ref("datasource.warehouse")
        >>> md.discover_dimension_values(warehouse, md.table("orders"), column="status", limit=10)

    Constraints:
        Values are runtime evidence only. Marivo does not persist them into
        semantic ``ai_context``, enum metadata, or authored semantic objects.
    """
    if limit < 1:
        raise ValueError("limit must be positive.")
    scan_scope = _scope_or_default(scope)
    metadata = _inspect_source(
        _datasource_id(datasource),
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    _require_discovery_partition_scope(metadata=metadata, source=source, scope=scan_scope)
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
