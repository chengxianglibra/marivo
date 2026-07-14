"""Datasource live capability registry contracts."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo.datasource._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.datasource._capabilities.validation import validate_datasource_live_surface
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.introspection.live.model import AuthoringEffects, AuthoringStateRef

PUBLIC_CALLABLE_TARGETS = {
    "duckdb",
    "trino",
    "mysql",
    "postgres",
    "clickhouse",
    "ref",
    "register",
    "remove",
    "load",
    "list",
    "describe",
    "connect",
    "test",
    "table",
    "parquet",
    "csv",
    "json",
    "partition",
    "unpruned",
    "inspect",
    "raw_sql",
    "help",
    "help_text",
    "DatasourceCatalog.list",
    "DatasourceCatalog.get",
    "DatasourceCatalog.describe",
    "DatasourceCatalog.connect",
    "DatasourceCatalog.test",
    "DatasourceConnection.disconnect",
    "SourceInspection.partitions",
    "SourceInspection.sample",
    "DiscoverySnapshot.entity",
    "DiscoverySnapshot.dimensions",
    "DiscoverySnapshot.values",
    "DiscoverySnapshot.time_dimensions",
    "DiscoverySnapshot.measures",
    "DiscoverySnapshot.relationships",
}


EXPECTED_EFFECTS = {
    "duckdb": AuthoringEffects(data_access="none", connection="none"),
    "trino": AuthoringEffects(data_access="none", connection="none"),
    "mysql": AuthoringEffects(data_access="none", connection="none"),
    "postgres": AuthoringEffects(data_access="none", connection="none"),
    "clickhouse": AuthoringEffects(data_access="none", connection="none"),
    "ref": AuthoringEffects(data_access="none", connection="none"),
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
    "parquet": AuthoringEffects(data_access="none", connection="none"),
    "csv": AuthoringEffects(data_access="none", connection="none"),
    "json": AuthoringEffects(data_access="none", connection="none"),
    "partition": AuthoringEffects(data_access="none", connection="none"),
    "unpruned": AuthoringEffects(data_access="none", connection="none"),
    "inspect": AuthoringEffects(data_access="live_metadata_read", connection="opens_connection"),
    "raw_sql": AuthoringEffects(
        data_access="potentially_unbounded_read",
        connection="opens_connection",
        flags=("requires_positive_row_guard",),
    ),
    "help": AuthoringEffects(data_access="none", connection="none"),
    "help_text": AuthoringEffects(data_access="none", connection="none"),
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
    "SourceInspection.partitions": AuthoringEffects(data_access="none", connection="none"),
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
    "DiscoverySnapshot.entity": AuthoringEffects(data_access="none", connection="none"),
    "DiscoverySnapshot.dimensions": AuthoringEffects(data_access="none", connection="none"),
    "DiscoverySnapshot.values": AuthoringEffects(data_access="none", connection="none"),
    "DiscoverySnapshot.time_dimensions": AuthoringEffects(data_access="none", connection="none"),
    "DiscoverySnapshot.measures": AuthoringEffects(data_access="none", connection="none"),
    "DiscoverySnapshot.relationships": AuthoringEffects(data_access="none", connection="none"),
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
        requirement.family for requirement in REGISTRY.by_canonical_id("raw_sql").input_requirements
    ) == ("DatasourceRef", "SqlText", "RawSqlReason")
    relationship_requirements = REGISTRY.by_canonical_id(
        "DiscoverySnapshot.relationships"
    ).input_requirements
    assert relationship_requirements[-1].exact_keys == ("left", "right")


def test_registry_declares_mechanical_inspection_acquisition_and_projection_lifecycle() -> None:
    inspection = REGISTRY.by_canonical_id("inspect")
    assert inspection.preconditions == ("datasource.registered",)
    assert inspection.required_states == (AuthoringStateRef(id="datasource.registered"),)
    assert inspection.repair_kinds == ("register", "reconnect")

    sample = REGISTRY.by_canonical_id("SourceInspection.sample")
    assert sample.preconditions == ("source.inspected", "scope.explicit")
    assert sample.required_states == (
        AuthoringStateRef(id="source.inspected"),
        AuthoringStateRef(id="scope.explicit"),
    )
    assert sample.repair_kinds == ("rescope", "reacquire")

    projection = REGISTRY.by_canonical_id("DiscoverySnapshot.entity")
    assert projection.preconditions == ("evidence.acquired",)
    assert projection.required_states == (AuthoringStateRef(id="evidence.acquired"),)
    assert projection.repair_kinds == ("reacquire",)
    assert REGISTRY.by_canonical_id("DiscoverySnapshot.relationships").repair_kinds == (
        "retry",
        "reacquire",
    )


def test_registry_closes_required_datasource_state_edges() -> None:
    for constructor in ("duckdb", "trino", "mysql", "postgres", "clickhouse"):
        assert REGISTRY.by_canonical_id(constructor).produced_state == AuthoringStateRef(
            id="datasource.declared"
        )

    register = REGISTRY.by_canonical_id("register")
    assert register.required_states == (AuthoringStateRef(id="datasource.declared"),)
    assert register.produced_state == AuthoringStateRef(id="datasource.registered")

    assert REGISTRY.by_canonical_id("connect").produced_state is None
    assert REGISTRY.by_canonical_id("test").produced_state == AuthoringStateRef(
        id="datasource.connection_validated"
    )

    for scope_builder in ("partition", "unpruned"):
        assert REGISTRY.by_canonical_id(scope_builder).produced_state == AuthoringStateRef(
            id="scope.explicit"
        )

    produced_states = {
        descriptor.produced_state.id
        for canonical_id in REGISTRY.canonical_ids()
        if (descriptor := REGISTRY.by_canonical_id(canonical_id)).produced_state is not None
    }
    required_states = {
        state.id
        for canonical_id in REGISTRY.canonical_ids()
        for state in REGISTRY.by_canonical_id(canonical_id).required_states
    }
    assert required_states <= produced_states


def test_stateful_type_contracts_list_registered_consumption_methods() -> None:
    assert tuple(target.canonical_id for target in TYPE_CONTRACTS[SourceInspection].consumers) == (
        "SourceInspection.partitions",
        "SourceInspection.sample",
    )
    assert tuple(target.canonical_id for target in TYPE_CONTRACTS[DiscoverySnapshot].consumers) == (
        "DiscoverySnapshot.entity",
        "DiscoverySnapshot.dimensions",
        "DiscoverySnapshot.values",
        "DiscoverySnapshot.time_dimensions",
        "DiscoverySnapshot.measures",
        "DiscoverySnapshot.relationships",
    )


def test_registry_resolves_functions_and_bound_methods() -> None:
    assert REGISTRY.by_callable(md.inspect) is REGISTRY.by_canonical_id("inspect")
    assert REGISTRY.by_callable(md.load().list) is REGISTRY.by_canonical_id(
        "DatasourceCatalog.list"
    )


def test_type_contracts_cover_public_classes_without_exporting_registry_types() -> None:
    public_classes = {value for name in md.__all__ if isinstance(value := getattr(md, name), type)}
    assert public_classes <= set(TYPE_CONTRACTS)
    assert "DatasourceCapabilityRegistry" not in md.__all__


def test_registry_mechanical_validation() -> None:
    validate_datasource_live_surface()
