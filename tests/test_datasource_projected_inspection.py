"""Truthful metadata and evidence tests for typed table projections."""

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
) -> TableMetadata:
    return TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
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
        "order_key": md.source_column("order_id", data_type="string"),
        "value": md.source_column("amount", data_type="float64"),
        "virtual_score": md.source_column("catalog.hidden", data_type="float64"),
    }
    if include_partition:
        columns["event_day"] = md.source_column("dt", data_type="date")
    return md.table("orders", columns=columns)


def test_projected_metadata_uses_declared_interface_and_preserves_base_facts() -> None:
    source = _projected_source()

    projected = _project_table_metadata(_metadata(), source)

    assert [(column.name, column.type) for column in projected.columns] == [
        ("event_day", "date"),
        ("order_key", "string"),
        ("value", "float64"),
        ("virtual_score", "float64"),
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
    assert [warning.kind for warning in projected.warnings] == ["declared_column_unverified"]


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
        "declared_column_unverified",
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


def test_public_inspection_projects_schema_and_blocks_type_mismatch_before_query(
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

    mismatched = md.table(
        "orders",
        columns={"order_key": md.source_column("order_id", data_type="int64")},
    )
    with pytest.raises(DatasourceAuthoringError) as exc_info:
        md.inspect(ms.ref.datasource("warehouse"), mismatched)

    error = exc_info.value
    assert error.code == "declared_type_mismatch"
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed is False
    assert "datasource='warehouse'" in error.received
    assert "table='orders'" in error.received
    assert "database=None" in error.received
    assert error.repair is not None
    assert error.repair.snippet == "md.source_column('order_id', data_type='string')"


def test_projected_metadata_validates_adapter_discovered_physical_column_type() -> None:
    base = _metadata(
        projectable_columns=(
            ColumnMetadata("string_map%2Eregion*ICDS*", "string", True, None, None),
        )
    )
    source = md.table(
        "orders",
        columns={
            "region": md.source_column(
                "string_map%2Eregion*ICDS*",
                data_type="string",
            )
        },
    )

    projected = _project_table_metadata(base, source)

    assert projected.columns == (ColumnMetadata("region", "string", True, None, 1),)
    assert not any(warning.kind == "declared_column_unverified" for warning in projected.warnings)

    mismatched = md.table(
        "orders",
        columns={
            "region": md.source_column(
                "string_map%2Eregion*ICDS*",
                data_type="float64",
            )
        },
    )
    with pytest.raises(DatasourceAuthoringError) as exc_info:
        _project_table_metadata(base, mismatched)
    assert exc_info.value.code == "declared_type_mismatch"
    assert exc_info.value.effect_observed is not None
    assert exc_info.value.effect_observed.query_executed is False


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
    assert inspection.partitions().contract().transitions[0].available is True

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
        columns={
            f"alias_{index:03d}": md.source_column(
                f"physical_{index:03d}",
                data_type="string",
            )
            for index in range(80)
        },
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: replace(_metadata(columns=columns), table="wide_events"),
    )

    rendered = md.inspect(ms.ref.datasource("warehouse"), source).render(max_output_bytes=1500)

    assert "projected columns: 80" in rendered
    assert "full source: .source.to_dict()" in rendered
    assert "column bindings" in rendered
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
                "order_key": md.source_column("order_id", data_type="string"),
            },
        )

        rendered = md.inspect(ms.ref.datasource("warehouse"), source).render()

        assert f"database: {expected}" in rendered
