"""Truthful metadata and evidence tests for projection-only table sources."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from marivo.datasource.engines.clickhouse import (
    classify_table_resolution_failure as classify_clickhouse_resolution,
)
from marivo.datasource.engines.duckdb import PROFILE as DUCKDB_PROFILE
from marivo.datasource.engines.mysql import (
    classify_table_resolution_failure as classify_mysql_resolution,
)
from marivo.datasource.engines.postgres import (
    classify_table_resolution_failure as classify_postgres_resolution,
)
from marivo.datasource.engines.trino import (
    classify_table_resolution_failure as classify_trino_resolution,
)
from marivo.datasource.errors import DatasourceAuthoringError, DatasourceMetadataError
from marivo.datasource.inspection import (
    Partitioning,
    _project_partitioning,
    _project_table_metadata,
    _structured_inspection_warnings,
)
from marivo.datasource.metadata import (
    ColumnMetadata,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    UniqueConstraintMetadata,
)


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "projected-inspection-test"\n')
    monkeypatch.chdir(tmp_path)
    md.register(
        md.duckdb(name="warehouse", path=str(tmp_path / "warehouse.duckdb")),
        project_root=tmp_path,
    )
    return tmp_path


def _metadata(
    *,
    columns: tuple[ColumnMetadata, ...] | None = None,
    projectable_columns: tuple[ColumnMetadata, ...] = (),
    partitions: tuple[PartitionMetadata, ...] = (),
    partition_state: Literal["known", "none", "unknown"] = "none",
    backend_type: str = "duckdb",
) -> TableMetadata:
    return TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type=backend_type,
        comment="orders table",
        columns=columns
        or (
            ColumnMetadata("order_id", "varchar", False, "stable id", 1),
            ColumnMetadata("amount", "double", True, "gross amount", 2),
            ColumnMetadata("dt", "date", False, None, 3),
        ),
        partitions=partitions,
        partition_state=partition_state,
        warnings=(),
        projectable_columns=projectable_columns,
        primary_keys=("order_id",),
        unique_constraints=(
            UniqueConstraintMetadata(
                name="orders_id_dt_key",
                columns=("order_id", "dt"),
                kind="unique",
            ),
        ),
        physical_profile=TablePhysicalProfile(
            row_count=12,
            row_count_kind="metadata",
            size_bytes=4096,
            size_kind="on_disk",
            source="catalog",
        ),
    )


def _projected_source(*, include_partition: bool = True) -> md.TableSourceIR:
    columns = {
        "order_key": "order_id",
        "value": "amount",
        "virtual_score": "catalog.hidden",
    }
    if include_partition:
        columns["event_day"] = "dt"
    return md.table("orders", columns=columns)


def test_projected_metadata_uses_observed_types_and_preserves_base_facts() -> None:
    source = _projected_source()

    projected = _project_table_metadata(_metadata(), source)

    assert [(column.name, column.type) for column in projected.columns] == [
        ("event_day", "date"),
        ("order_key", "string"),
        ("value", "float64"),
        ("virtual_score", "unknown"),
    ]
    by_name = {column.name: column for column in projected.columns}
    assert (by_name["order_key"].nullable, by_name["order_key"].comment) == (
        False,
        "stable id",
    )
    assert (by_name["virtual_score"].nullable, by_name["virtual_score"].comment) == (
        None,
        None,
    )
    assert projected.primary_keys == ("order_key",)
    assert projected.unique_constraints[0].columns == ("order_key", "event_day")
    assert projected.physical_profile == _metadata().physical_profile
    assert [warning.kind for warning in projected.warnings] == ["projected_column_unverified"]


def test_projected_metadata_omits_incomplete_constraints_and_partitions() -> None:
    base = _metadata(
        partitions=(PartitionMetadata(name="dt", type="date"),),
        partition_state="known",
    )

    projected = _project_table_metadata(base, _projected_source(include_partition=False))

    assert projected.primary_keys == ("order_key",)
    assert projected.unique_constraints == ()
    assert projected.partition_state == "unknown"
    assert projected.partitions == ()
    assert {warning.kind for warning in projected.warnings} == {
        "projected_column_unverified",
        "projected_constraint_incomplete",
        "projected_partition_unavailable",
    }


def test_projected_partition_fields_and_values_are_renamed_after_capture() -> None:
    source = _projected_source()
    effective = _project_table_metadata(
        _metadata(
            partitions=(PartitionMetadata(name="dt", type="date"),),
            partition_state="known",
        ),
        source,
    )
    captured = Partitioning(
        state="known",
        fields=(PartitionMetadata(name="dt", type="date"),),
        value_source="system_catalog",
        values=((("dt", "2026-08-17"),),),
        values_complete=True,
        truncated=False,
    )

    projected = _project_partitioning(captured, source, effective)

    assert tuple(field.name for field in projected.fields) == ("event_day",)
    assert projected.values == ((("event_day", "2026-08-17"),),)


def test_public_inspection_projects_schema_from_observed_metadata(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source", lambda *_a, **_k: _metadata()
    )
    source = _projected_source()

    inspection = md.inspect(ms.ref.datasource("warehouse"), source)

    assert tuple(column.name for column in inspection.schema) == (
        "event_day",
        "order_key",
        "value",
        "virtual_score",
    )
    assert inspection.physical_extent.row_count == 12
    assert any("absent from base metadata" in warning for warning in inspection.warnings)
    assert inspection.source_column("order_key") == "order_id"

    projected_source = md.table(
        "orders",
        columns={"order_key": "order_id"},
    )
    projected_inspection = md.inspect(ms.ref.datasource("warehouse"), projected_source)
    assert [(column.name, column.type) for column in projected_inspection.schema] == [
        ("order_key", "string")
    ]


def test_inspection_source_column_bridges_raw_catalog_types(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    columns = (
        ColumnMetadata("group_id", "bigint", False, None, 1),
        ColumnMetadata("group_name", "varchar", True, None, 2),
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_a, **_k: _metadata(columns=columns, backend_type="trino"),
    )
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))

    assert [(column.name, column.type) for column in inspection.schema] == [
        ("group_id", "bigint"),
        ("group_name", "varchar"),
    ]
    assert inspection.source_column("group_id") == "group_id"
    assert inspection.source_column("group_name") == "group_name"
    projected = md.table(
        "orders",
        columns={
            "group_id": inspection.source_column("group_id"),
            "group_name": inspection.source_column("group_name"),
        },
    )
    assert [column.type for column in md.inspect(inspection.datasource, projected).schema] == [
        "int64",
        "string",
    ]
    rendered = inspection.render()
    assert "physical type" in rendered and "ibis type" in rendered
    assert "'group_id'" in rendered
    assert "'group_name'" in rendered


def test_inspection_source_column_returns_names_without_type_admission(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata(
        columns=(
            ColumnMetadata("fixed_name", "char(8)", False, None, 1),
            ColumnMetadata("unknown_value", "some_new_backend_type", True, None, 2),
        ),
        backend_type="trino",
    )
    monkeypatch.setattr("marivo.datasource.inspection._inspect_source", lambda *_a, **_k: metadata)
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    assert inspection.source_column("fixed_name") == "fixed_name"
    assert inspection.source_column("unknown_value") == "unknown_value"
    with pytest.raises(DatasourceAuthoringError) as caught:
        inspection.source_column("missing")
    assert caught.value.code == "source_column_unknown"
    assert caught.value.effect_observed is not None
    assert caught.value.effect_observed.query_executed is False
    rendered = inspection.render()
    assert "unavailable" in rendered
    assert "md.source_column" not in rendered


def test_inspection_source_column_accepts_clickhouse_projectable_column(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata(
        columns=(ColumnMetadata("id", "int64", False, None, 1),),
        projectable_columns=(
            ColumnMetadata("string_map%2Eregion*ICDS*", "string", True, None, None),
        ),
        backend_type="clickhouse",
    )
    monkeypatch.setattr("marivo.datasource.inspection._inspect_source", lambda *_a, **_k: metadata)
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    assert inspection.source_column("string_map%2Eregion*ICDS*") == "string_map%2Eregion*ICDS*"


def test_projected_metadata_uses_adapter_discovered_physical_type() -> None:
    base = _metadata(
        projectable_columns=(
            ColumnMetadata("string_map%2Eregion*ICDS*", "string", True, None, None),
        )
    )
    source = md.table(
        "orders",
        columns={"region": "string_map%2Eregion*ICDS*"},
    )

    projected = _project_table_metadata(base, source)

    assert projected.columns == (ColumnMetadata("region", "string", True, None, 1),)
    assert not any(warning.kind == "projected_column_unverified" for warning in projected.warnings)

    assert projected.columns[0].type == "string"


@pytest.mark.parametrize(
    ("catalog_type", "observed_type"),
    [
        ("DateTime64(3)", "timestamp(3)"),
        ("DateTime64(3, 'UTC')", "timestamp('UTC', 3)"),
        ("Nullable(DateTime64(3))", "timestamp(3)"),
    ],
)
def test_projected_metadata_compares_clickhouse_types_in_canonical_ibis_form(
    catalog_type: str,
    observed_type: str,
) -> None:
    base = _metadata(
        columns=(ColumnMetadata("timestamp", catalog_type, True, None, 1),),
        backend_type="clickhouse",
    )
    source = md.table(
        "orders",
        columns={
            "event_time": "timestamp",
        },
    )

    projected = _project_table_metadata(base, source)

    assert projected.columns == (ColumnMetadata("event_time", observed_type, True, None, 1),)


def test_unknown_partition_warning_is_structured_before_public_rendering() -> None:
    metadata = _metadata(partition_state="unknown")
    partitioning = Partitioning(
        state="unknown",
        fields=(),
        value_source=None,
        values=(),
        values_complete=False,
        truncated=False,
    )

    warnings = _structured_inspection_warnings(
        metadata=metadata,
        partitioning=partitioning,
        partition_warnings=(),
    )

    assert [(warning.kind, warning.message) for warning in warnings] == [
        ("partition_state_unknown", "partition state is unknown")
    ]


def test_partition_probe_uses_physical_names_then_public_scope_uses_aliases(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _metadata(
        partitions=(PartitionMetadata(name="dt", type="date"),),
        partition_state="known",
    )
    requests: list[PartitionProbeRequest] = []

    def inspect_values(request: PartitionProbeRequest) -> PartitionProbeResult:
        requests.append(request)
        return PartitionProbeResult(
            rows=({"dt": "2026-08-17"},),
            value_source="system_catalog",
        )

    profile = replace(DUCKDB_PROFILE, inspect_partition_values=inspect_values)
    monkeypatch.setattr("marivo.datasource.inspection._inspect_source", lambda *_a, **_k: base)
    monkeypatch.setattr(
        "marivo.datasource.inspection.require_profile_for_backend_type",
        lambda _backend_type: profile,
    )

    inspection = md.inspect(ms.ref.datasource("warehouse"), _projected_source())

    assert requests[0].partition_columns == ("dt",)
    assert requests[0].source.columns == ()
    assert tuple(field.name for field in inspection.partitioning.fields) == ("event_day",)
    assert inspection.partitioning.values == ((("event_day", "2026-08-17"),),)
    partition_result = inspection.partitions()
    assert partition_result.status == "complete"
    assert not hasattr(partition_result, "contract")

    ascending = inspection.partitions(limit=1, order="asc")
    assert requests[1].partition_columns == ("dt",)
    assert requests[1].source.columns == ()
    assert requests[1].limit == 2
    assert requests[1].order == "asc"
    assert ascending.partitioning.values == ((("event_day", "2026-08-17"),),)
    assert ascending.limit == 1
    assert ascending.order == "asc"

    omitted = md.inspect(
        ms.ref.datasource("warehouse"),
        _projected_source(include_partition=False),
    )
    assert omitted.partitioning.state == "unknown"
    assert omitted.partitioning.values == ()
    assert omitted.execution_capabilities.partition_predicate_supported is False
    assert any("md.unpruned" in warning for warning in omitted.warnings)


class _StructuredBackendError(Exception):
    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__("password=do-not-render")


class _NamedBackendError(Exception):
    def __init__(self, *, name: str | None = None, error_name: str | None = None) -> None:
        self.name = name
        self.error_name = error_name
        super().__init__(name or error_name)


class _SqlstateBackendError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        super().__init__(sqlstate)


class _WrappedBackendError(Exception):
    def __init__(self, orig: Exception) -> None:
        self.orig = orig
        super().__init__("wrapped")


class _ResolutionBackend:
    def __init__(self, code: int) -> None:
        self.code = code
        self.disconnected = False

    def table(self, _table: str, **_kwargs: object) -> object:
        raise _StructuredBackendError(self.code)

    def disconnect(self) -> None:
        self.disconnected = True


def test_engine_profiles_classify_only_structured_metadata_permission_failures() -> None:
    assert (
        classify_clickhouse_resolution(_WrappedBackendError(_StructuredBackendError(497)))
        == "metadata_unavailable"
    )
    assert (
        classify_clickhouse_resolution(_NamedBackendError(name="ACCESS_DENIED"))
        == "metadata_unavailable"
    )
    assert (
        classify_trino_resolution(_NamedBackendError(error_name="PERMISSION_DENIED"))
        == "metadata_unavailable"
    )
    assert classify_postgres_resolution(_SqlstateBackendError("42501")) == "metadata_unavailable"
    assert classify_mysql_resolution(Exception(1142, "command denied")) == "metadata_unavailable"

    assert classify_clickhouse_resolution(_StructuredBackendError(60)) is None
    assert classify_trino_resolution(_NamedBackendError(error_name="TABLE_NOT_FOUND")) is None
    assert classify_postgres_resolution(_SqlstateBackendError("08006")) is None
    assert classify_mysql_resolution(Exception(1045, "authentication failed")) is None


def test_only_classified_projected_resolution_failure_degrades_to_declared_only(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _ResolutionBackend(497)
    profile = replace(
        DUCKDB_PROFILE,
        metadata=replace(
            DUCKDB_PROFILE.metadata,
            classify_table_resolution_failure=(
                lambda exc: "metadata_unavailable" if getattr(exc, "code", None) == 497 else None
            ),
        ),
    )
    monkeypatch.setattr(
        "marivo.datasource.metadata._backends.build_backend",
        lambda *_args, **_kwargs: backend,
    )
    monkeypatch.setattr(
        "marivo.datasource.engines.require_profile_for_backend_type",
        lambda _backend_type: profile,
    )

    inspection = md.inspect(ms.ref.datasource("warehouse"), _projected_source())

    assert inspection.partitioning.state == "unknown"
    assert inspection.physical_extent.source == "metadata_unavailable"
    assert inspection.execution_capabilities.partition_predicate_supported is False
    assert all(column.nullable is None for column in inspection.schema)
    assert "do-not-render" not in "\n".join(inspection.warnings)
    assert any("base table metadata is unavailable" in warning for warning in inspection.warnings)
    assert backend.disconnected is True

    with pytest.raises(DatasourceMetadataError):
        md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))


def test_unclassified_resolution_failure_remains_closed(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _ResolutionBackend(516)
    monkeypatch.setattr(
        "marivo.datasource.metadata._backends.build_backend",
        lambda *_args, **_kwargs: backend,
    )

    with pytest.raises(DatasourceMetadataError) as exc_info:
        md.inspect(ms.ref.datasource("warehouse"), _projected_source())

    assert exc_info.value.received == "_StructuredBackendError code=516"
    assert backend.disconnected is True


def test_projected_source_inspection_render_is_bounded_and_recoverable(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    columns = tuple(
        ColumnMetadata(f"physical_{index:03d}", "varchar", False, None, index + 1)
        for index in range(80)
    )
    source = md.table(
        "wide_events",
        columns={f"alias_{index:03d}": f"physical_{index:03d}" for index in range(80)},
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: replace(_metadata(columns=columns), table="wide_events"),
    )

    rendered = md.inspect(ms.ref.datasource("warehouse"), source).render(max_output_bytes=1500)

    assert "projected columns: 80" in rendered
    assert "full source: .source.to_dict()" in rendered
    assert "column projection" in rendered
    assert "total=80" in rendered
    assert '"columns":' not in rendered


def test_projected_source_render_preserves_database_identity_shape(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: _metadata(),
    )
    cases: tuple[tuple[str | tuple[str, ...] | None, str], ...] = (
        (None, "unspecified (datasource default)"),
        ("analytics.with.dot", "name='analytics.with.dot'"),
        (("analytics", "with.dot"), "segments=('analytics', 'with.dot')"),
    )

    for database, expected in cases:
        source = md.table(
            "orders",
            database=database,
            columns={
                "order_key": "order_id",
            },
        )

        rendered = md.inspect(ms.ref.datasource("warehouse"), source).render()

        assert f"database: {expected}" in rendered


@pytest.mark.parametrize(
    "engine,physical,logical",
    [
        ("mysql", "tinyint(1)", "int8"),
        ("mysql", "tinyint(1)", "int8"),
        ("mysql", "bigint unsigned", "uint64"),
        ("sqlite", "INT2", "int64"),
        ("sqlite", "TIMESTAMP", "timestamp(6)"),
        ("sqlite", "VARCHAR(20)", "string"),
    ],
)
def test_scalar_representation_metadata_is_not_a_cast(
    engine: str, physical: str, logical: str
) -> None:
    metadata = _metadata(
        columns=(ColumnMetadata("value", physical, True, None, 1),), backend_type=engine
    )
    projected = _project_table_metadata(
        metadata,
        md.table("orders", columns={"value": "value"}),
    )
    assert projected.columns[0].type == logical
    assert projected.columns[0].nullable is True


@pytest.mark.parametrize(
    "engine,physical", [("postgres", "character(8)"), ("mysql", "char(8)"), ("trino", "char(8)")]
)
def test_projected_fixed_char_retains_observed_type(engine: str, physical: str) -> None:
    metadata = _metadata(
        columns=(ColumnMetadata("value", physical, True, None, 1),), backend_type=engine
    )
    projected = _project_table_metadata(
        metadata,
        md.table("orders", columns={"value": "value"}),
    )
    assert projected.columns[0].type == physical.lower()
