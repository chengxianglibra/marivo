"""Datasource live capability registry contracts."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo._authoring.model import AuthoringEffects
from marivo.datasource._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.datasource._capabilities.validation import validate_datasource_live_surface
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.snapshot import DiscoverySnapshot

PUBLIC_CALLABLE_TARGETS = {
    "duckdb",
    "sqlite",
    "trino",
    "mysql",
    "postgres",
    "clickhouse",
    "register",
    "remove",
    "load",
    "list",
    "describe",
    "connect",
    "test",
    "table",
    "source_column",
    "parquet",
    "csv",
    "source_param",
    "json",
    "partition",
    "time_range",
    "unpruned",
    "inspect",
    "raw_sql",
    "DatasourceCatalog.list",
    "DatasourceCatalog.get",
    "DatasourceCatalog.describe",
    "DatasourceCatalog.connect",
    "DatasourceCatalog.test",
    "DatasourceConnection.disconnect",
    "SourceInspection.partitions",
    "SourceInspection.sample",
}


def test_datasource_surface_uses_the_native_registry_without_copying() -> None:
    assert DATASOURCE_LIVE_SURFACE.registry is REGISTRY
    for canonical_id in REGISTRY.canonical_ids():
        native = REGISTRY.by_canonical_id(canonical_id)
        assert DATASOURCE_LIVE_SURFACE.registry.by_canonical_id(canonical_id) is native


EXPECTED_EFFECTS = {
    "duckdb": AuthoringEffects(data_access="none", connection="none"),
    "sqlite": AuthoringEffects(data_access="none", connection="none"),
    "trino": AuthoringEffects(data_access="none", connection="none"),
    "mysql": AuthoringEffects(data_access="none", connection="none"),
    "postgres": AuthoringEffects(data_access="none", connection="none"),
    "clickhouse": AuthoringEffects(data_access="none", connection="none"),
    "register": AuthoringEffects(
        data_access="local_metadata_read",
        connection="none",
        mutations=("project_state",),
    ),
    "remove": AuthoringEffects(
        data_access="local_metadata_read",
        connection="none",
        mutations=("project_state",),
    ),
    "load": AuthoringEffects(data_access="local_metadata_read", connection="none"),
    "list": AuthoringEffects(data_access="local_metadata_read", connection="none"),
    "describe": AuthoringEffects(data_access="local_metadata_read", connection="none"),
    "connect": AuthoringEffects(
        data_access="local_metadata_read",
        connection="opens_connection",
        flags=("may_cache_resolved_secret",),
    ),
    "test": AuthoringEffects(
        data_access="local_metadata_read",
        connection="opens_connection",
        mutations=("user_global_state",),
        flags=("may_cache_resolved_secret",),
    ),
    "table": AuthoringEffects(data_access="none", connection="none"),
    "source_column": AuthoringEffects(data_access="none", connection="none"),
    "parquet": AuthoringEffects(data_access="none", connection="none"),
    "csv": AuthoringEffects(data_access="none", connection="none"),
    "source_param": AuthoringEffects(data_access="none", connection="none"),
    "json": AuthoringEffects(data_access="none", connection="none"),
    "partition": AuthoringEffects(data_access="none", connection="none"),
    "time_range": AuthoringEffects(data_access="none", connection="none"),
    "unpruned": AuthoringEffects(data_access="none", connection="none"),
    "inspect": AuthoringEffects(data_access="live_metadata_read", connection="opens_connection"),
    "raw_sql": AuthoringEffects(
        data_access="potentially_unbounded_read",
        connection="opens_connection",
        flags=("requires_positive_row_guard", "requires_positive_timeout_guard"),
    ),
    "DatasourceCatalog.list": AuthoringEffects(
        data_access="local_metadata_read", connection="none"
    ),
    "DatasourceCatalog.get": AuthoringEffects(data_access="local_metadata_read", connection="none"),
    "DatasourceCatalog.describe": AuthoringEffects(
        data_access="local_metadata_read", connection="none"
    ),
    "DatasourceCatalog.connect": AuthoringEffects(
        data_access="local_metadata_read",
        connection="opens_connection",
        flags=("may_cache_resolved_secret",),
    ),
    "DatasourceCatalog.test": AuthoringEffects(
        data_access="local_metadata_read",
        connection="opens_connection",
        mutations=("user_global_state",),
        flags=("may_cache_resolved_secret",),
    ),
    "DatasourceConnection.disconnect": AuthoringEffects(data_access="none", connection="none"),
    "SourceInspection.partitions": AuthoringEffects(
        data_access="live_metadata_read", connection="opens_connection"
    ),
    "SourceInspection.sample": AuthoringEffects(
        data_access="scoped_data_read",
        connection="opens_connection",
        mutations=("project_state",),
        flags=(
            "requires_explicit_scope",
            "requires_positive_row_guard",
            "requires_positive_timeout_guard",
            "may_persist_plaintext_values",
        ),
    ),
}


def test_registry_covers_every_datasource_callable_once() -> None:
    assert set(REGISTRY.callable_ids()) == PUBLIC_CALLABLE_TARGETS
    assert len(REGISTRY.callable_ids()) == len(set(REGISTRY.callable_ids()))


@pytest.mark.parametrize(("canonical_id", "expected"), EXPECTED_EFFECTS.items())
def test_registry_effects_match_phase2_inventory(
    canonical_id: str, expected: AuthoringEffects
) -> None:
    assert set(EXPECTED_EFFECTS) == set(REGISTRY.callable_ids())
    assert REGISTRY.by_canonical_id(canonical_id).effects == expected


def test_sample_effects_are_complete_and_orthogonal() -> None:
    effects = REGISTRY.by_canonical_id("SourceInspection.sample").effects
    assert effects == AuthoringEffects(
        data_access="scoped_data_read",
        connection="opens_connection",
        mutations=("project_state",),
        flags=(
            "requires_explicit_scope",
            "requires_positive_row_guard",
            "requires_positive_timeout_guard",
            "may_persist_plaintext_values",
        ),
    )


def test_raw_sql_never_claims_bounded_backend_work() -> None:
    assert REGISTRY.by_canonical_id("raw_sql").effects.data_access == "potentially_unbounded_read"


def test_registry_input_contracts_match_required_datasource_arguments() -> None:
    assert tuple(
        requirement.family for requirement in REGISTRY.by_canonical_id("table").input_requirements
    ) == ("TableName", "TableColumnBindings")
    assert tuple(
        requirement.family
        for requirement in REGISTRY.by_canonical_id("source_column").input_requirements
    ) == ("PhysicalColumnName", "IbisDataType")
    partition_families = tuple(
        requirement.family
        for requirement in REGISTRY.by_canonical_id("partition").input_requirements
    )
    assert partition_families == (
        "PartitionValues",
        "PositiveRowGuard",
        "PositiveTimeoutGuard",
    )
    assert tuple(
        requirement.family
        for requirement in REGISTRY.by_canonical_id("time_range").input_requirements
    ) == ("TemporalColumn", "TemporalBound", "PositiveRowGuard", "PositiveTimeoutGuard")
    assert tuple(
        requirement.family for requirement in REGISTRY.by_canonical_id("raw_sql").input_requirements
    ) == (
        "Ref[datasource]",
        "SqlText",
        "RawSqlReason",
        "PositiveLimit",
        "PositiveTimeoutGuard",
    )


def test_registry_retains_direct_inspection_and_acquisition_facts() -> None:
    inspection = REGISTRY.by_canonical_id("inspect")
    assert inspection.preconditions == ("a registered datasource ref",)
    assert inspection.repair_kinds == ("register", "reconnect")

    sample = REGISTRY.by_canonical_id("SourceInspection.sample")
    assert sample.preconditions == (
        "a current SourceInspection",
        "an explicit AuthoringScope",
    )
    assert sample.repair_kinds == ("rescope", "reacquire")


def test_type_contracts_list_registered_consumption_methods() -> None:
    assert tuple(target.canonical_id for target in TYPE_CONTRACTS[SourceInspection].consumers) == (
        "SourceInspection.partitions",
        "SourceInspection.sample",
    )
    assert TYPE_CONTRACTS[DiscoverySnapshot].consumers == ()
    assert TYPE_CONTRACTS[DiscoverySnapshot].public_methods == ("contract", "show", "render")
    assert "retained_values" in TYPE_CONTRACTS[DiscoverySnapshot].public_properties


def test_registry_resolves_functions_and_bound_methods() -> None:
    assert REGISTRY.by_callable(md.inspect) is REGISTRY.by_canonical_id("inspect")
    assert REGISTRY.by_callable(md.source_param) is REGISTRY.by_canonical_id("source_param")
    assert REGISTRY.by_callable(md.source_column) is REGISTRY.by_canonical_id("source_column")
    assert REGISTRY.by_callable(md.load().list) is REGISTRY.by_canonical_id(
        "DatasourceCatalog.list"
    )


def test_type_contracts_cover_public_classes_without_exporting_registry_types() -> None:
    public_classes = {value for name in md.__all__ if isinstance(value := getattr(md, name), type)}
    assert public_classes <= set(TYPE_CONTRACTS)
    assert "DatasourceCapabilityRegistry" not in md.__all__


def test_registry_mechanical_validation() -> None:
    validate_datasource_live_surface()
