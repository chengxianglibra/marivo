"""Datasource live capability registry contracts."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo._authoring.model import AuthoringEffects
from marivo.datasource._capabilities.registry import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.datasource._capabilities.validation import validate_datasource_live_surface
from marivo.datasource.constraints import constraints_for_symbol
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.snapshot import DiscoverySnapshot

PUBLIC_CALLABLE_TARGETS = {
    "credential_scope",
    "credential_resolver.resolve",
    "secret_value.reveal",
    "credential_request.show",
    "duckdb",
    "trino",
    "mysql",
    "postgres",
    "clickhouse",
    "register",
    "remove",
    "load",
    "table",
    "parquet",
    "csv",
    "source_param",
    "json",
    "partition",
    "time_range",
    "unpruned",
    "inspect",
    "raw_sql",
    "datasource_catalog.list",
    "datasource_catalog.describe",
    "datasource_catalog.test",
    "source_inspection.partitions",
    "source_inspection.source_column",
    "source_inspection.sample",
}


def test_datasource_surface_uses_the_native_registry_without_copying() -> None:
    assert DATASOURCE_LIVE_SURFACE.registry is REGISTRY
    for canonical_id in REGISTRY.canonical_ids():
        native = REGISTRY.by_canonical_id(canonical_id)
        assert DATASOURCE_LIVE_SURFACE.registry.by_canonical_id(canonical_id) is native


EXPECTED_EFFECTS = {
    "credential_scope": AuthoringEffects(data_access="none", connection="none"),
    "credential_resolver.resolve": AuthoringEffects(data_access="none", connection="none"),
    "secret_value.reveal": AuthoringEffects(data_access="none", connection="none"),
    "credential_request.show": AuthoringEffects(data_access="none", connection="none"),
    "duckdb": AuthoringEffects(data_access="none", connection="none"),
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
    "table": AuthoringEffects(data_access="none", connection="none"),
    "parquet": AuthoringEffects(data_access="none", connection="none"),
    "csv": AuthoringEffects(data_access="none", connection="none"),
    "source_param": AuthoringEffects(data_access="none", connection="none"),
    "json": AuthoringEffects(data_access="none", connection="none"),
    "partition": AuthoringEffects(data_access="none", connection="none"),
    "time_range": AuthoringEffects(data_access="none", connection="none"),
    "unpruned": AuthoringEffects(data_access="none", connection="none"),
    "inspect": AuthoringEffects(
        data_access="live_metadata_read",
        connection="opens_connection",
        mutations=("project_state",),
    ),
    "raw_sql": AuthoringEffects(
        data_access="potentially_unbounded_read",
        connection="opens_connection",
        mutations=("project_state",),
        flags=(),
    ),
    "datasource_catalog.list": AuthoringEffects(
        data_access="local_metadata_read", connection="none"
    ),
    "datasource_catalog.describe": AuthoringEffects(
        data_access="local_metadata_read", connection="none"
    ),
    "datasource_catalog.test": AuthoringEffects(
        data_access="local_metadata_read",
        connection="opens_connection",
        mutations=("user_global_state", "project_state"),
        flags=("may_cache_resolved_secret",),
    ),
    "source_inspection.partitions": AuthoringEffects(
        data_access="live_metadata_read",
        connection="opens_connection",
        mutations=("project_state",),
    ),
    "source_inspection.source_column": AuthoringEffects(data_access="none", connection="none"),
    "source_inspection.sample": AuthoringEffects(
        data_access="scoped_data_read",
        connection="opens_connection",
        mutations=("project_state",),
        flags=(
            "requires_explicit_scope",
            "requires_positive_row_guard",
            "may_persist_plaintext_values",
        ),
    ),
}


def test_registry_covers_every_datasource_callable_once() -> None:
    assert set(REGISTRY.callable_ids()) == PUBLIC_CALLABLE_TARGETS
    assert len(REGISTRY.callable_ids()) == len(set(REGISTRY.callable_ids()))
    assert set(EXPECTED_EFFECTS) == set(REGISTRY.callable_ids())


def test_register_discloses_its_model_loading_boundary() -> None:
    descriptor = REGISTRY.by_canonical_id("register")
    assert descriptor.preconditions == (
        "a validated DatasourceSpec",
        "execution outside model loading",
    )
    assert "datasource_register_outside_loader" in descriptor.constraints
    constraints = {constraint.id: constraint for constraint in constraints_for_symbol("register")}
    constraint = constraints["datasource_register_outside_loader"]
    assert constraint.error_kind == "DatasourceLoad"
    assert "constructor directly" in constraint.hint


def test_every_discoverable_capability_belongs_to_one_root_group() -> None:
    groups = (
        REGISTRY.group("declare_manage"),
        REGISTRY.group("physical_sources"),
        REGISTRY.group("inspect_scope"),
        REGISTRY.group("acquire_project"),
        REGISTRY.group("diagnostics_boundaries"),
    )
    grouped_ids = {descriptor.canonical_id for group in groups for descriptor in group}

    assert set(REGISTRY.discovery_ids()) <= grouped_ids


@pytest.mark.parametrize(("canonical_id", "expected"), EXPECTED_EFFECTS.items())
def test_registry_effects_match_callable_contracts(
    canonical_id: str, expected: AuthoringEffects
) -> None:
    assert REGISTRY.by_canonical_id(canonical_id).effects == expected


def test_registry_input_contracts_match_required_datasource_arguments() -> None:
    assert tuple(
        requirement.family for requirement in REGISTRY.by_canonical_id("table").input_requirements
    ) == ("TableName", "ProjectionColumns")
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
        "DatasourceSymbol",
        "SqlText",
        "RawSqlReason",
        "PositiveTimeoutGuard",
    )


def test_registry_retains_direct_inspection_and_acquisition_facts() -> None:
    inspection = REGISTRY.by_canonical_id("inspect")
    assert inspection.preconditions == ("a built-in or registered datasource ref",)
    assert inspection.repair_kinds == ("register", "reconnect")

    sample = REGISTRY.by_canonical_id("source_inspection.sample")
    assert sample.preconditions == (
        "a current SourceInspection",
        "an explicit AuthoringScope",
    )
    assert sample.repair_kinds == ("rescope", "reacquire")


def test_type_contracts_list_registered_consumption_methods() -> None:
    assert tuple(target.canonical_id for target in TYPE_CONTRACTS[SourceInspection].consumers) == (
        "source_inspection.partitions",
        "source_inspection.source_column",
        "source_inspection.sample",
    )
    assert TYPE_CONTRACTS[DiscoverySnapshot].consumers == ()
    assert TYPE_CONTRACTS[DiscoverySnapshot].public_methods == ("show", "render")
    assert "retained_values" in TYPE_CONTRACTS[DiscoverySnapshot].public_properties


def test_registry_resolves_functions_and_bound_methods() -> None:
    assert REGISTRY.by_callable(md.inspect) is REGISTRY.by_canonical_id("inspect")
    assert REGISTRY.by_callable(md.source_param) is REGISTRY.by_canonical_id("source_param")
    assert REGISTRY.by_callable(SourceInspection.source_column) is REGISTRY.by_canonical_id(
        "source_inspection.source_column"
    )
    assert REGISTRY.by_callable(md.load().list) is REGISTRY.by_canonical_id(
        "datasource_catalog.list"
    )


def test_type_contracts_cover_public_classes() -> None:
    public_classes = {value for name in md.__all__ if isinstance(value := getattr(md, name), type)}
    assert public_classes <= set(TYPE_CONTRACTS) | set(ERROR_TYPES.values())


def test_registry_mechanical_validation() -> None:
    validate_datasource_live_surface()
