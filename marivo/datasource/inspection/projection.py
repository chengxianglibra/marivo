"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Literal

import ibis
from ibis.backends.sql.datatypes import (
    ClickHouseType,
    DuckDBType,
    MySQLType,
    PostgresType,
    TrinoType,
)

from marivo.datasource.errors import (
    _BackendFailureSummary,
)
from marivo.datasource.ir import (
    DatasourceIR,
    TableSourceIR,
)
from marivo.datasource.metadata_types import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    UniqueConstraintMetadata,
)


def _unprojected_table(source: TableSourceIR) -> TableSourceIR:
    return TableSourceIR(table=source.table, database=source.database)


def _declared_only_table_metadata(
    *,
    datasource_name: str,
    datasource_ir: DatasourceIR,
    source: TableSourceIR,
    failure: _BackendFailureSummary,
) -> TableMetadata:
    warnings = [
        MetadataWarning(
            kind="base_table_metadata_unavailable",
            message=(
                "base table metadata is unavailable for this projected source; "
                f"backend={failure.identity} reason={failure.message}; use an explicit "
                "bounded md.unpruned(...) acquisition to prove the table and identifiers"
            ),
        )
    ]
    columns: list[ColumnMetadata] = []
    for position, (output_name, source_name) in enumerate(source.columns, start=1):
        columns.append(
            ColumnMetadata(
                name=output_name,
                type="unknown",
                nullable=None,
                comment=None,
                ordinal_position=position,
            )
        )
        warnings.append(
            MetadataWarning(
                kind="projected_column_unverified",
                message=(
                    f"projected column output={output_name!r} source={source_name!r} "
                    "is unverified because base table metadata is unavailable"
                ),
                columns=(output_name,),
            )
        )
    return TableMetadata(
        datasource=datasource_name,
        table=source.table,
        database=source.database,
        backend_type=datasource_ir.backend_type,
        comment=None,
        columns=tuple(columns),
        partitions=(),
        partition_state="unknown",
        warnings=tuple(warnings),
    )


def _canonical_catalog_type(type_name: str, *, backend_type: str) -> str:
    if backend_type in {"postgres", "mysql", "trino"} and re.fullmatch(
        r"(?:char|character)(?:\(\d+\))?", type_name.lower()
    ):
        return type_name.lower()
    mappers = {
        "duckdb": DuckDBType,
        "mysql": MySQLType,
        "postgres": PostgresType,
        "trino": TrinoType,
        "clickhouse": ClickHouseType,
    }
    mapper = mappers.get(backend_type)
    if mapper is not None:
        try:
            dtype = mapper.from_string(type_name).copy(nullable=True)
            if (
                backend_type == "mysql"
                and dtype.is_timestamp()
                and dtype.scale is None
                and type_name.lower() in {"datetime", "timestamp"}
            ):
                dtype = dtype.copy(scale=0)
            if not dtype.is_unknown():
                return "string" if dtype.is_string() else str(dtype)
        except (TypeError, ValueError, RuntimeError):
            pass
    try:
        return str(ibis.dtype(type_name))
    except (TypeError, ValueError, RuntimeError):
        return type_name


def _ibis_table_type(type_name: str, *, backend_type: str) -> str | None:
    """Return a safe declared Ibis type while retaining the raw metadata elsewhere."""
    canonical = _canonical_catalog_type(type_name, backend_type=backend_type)
    if backend_type in {"postgres", "mysql", "trino"} and re.fullmatch(
        r"(?:char|character)(?:\(\d+\))?", canonical
    ):
        mapper = {"mysql": MySQLType, "postgres": PostgresType, "trino": TrinoType}[backend_type]
        try:
            dtype = mapper.from_string(canonical)
        except (TypeError, ValueError, RuntimeError):
            return None
        return "string" if dtype.is_string() else None
    try:
        dtype = ibis.dtype(canonical)
    except (TypeError, ValueError, RuntimeError):
        return None
    if dtype.is_unknown():
        return None
    return str(dtype)


def _mapped_constraint_columns(
    columns: tuple[str, ...],
    source_to_output: Mapping[str, str],
) -> tuple[str, ...] | None:
    if any(column not in source_to_output for column in columns):
        return None
    return tuple(source_to_output[column] for column in columns)


def _project_table_metadata(metadata: TableMetadata, source: TableSourceIR) -> TableMetadata:
    catalog_columns = {
        column.name: column for column in (*metadata.columns, *metadata.projectable_columns)
    }
    source_to_output = {source_name: output for output, source_name in source.columns}
    columns: list[ColumnMetadata] = []
    warnings = list(metadata.warnings)

    for position, (output_name, source_name) in enumerate(source.columns, start=1):
        catalog_column = catalog_columns.get(source_name)
        if catalog_column is None:
            columns.append(
                ColumnMetadata(
                    name=output_name,
                    type="unknown",
                    nullable=None,
                    comment=None,
                    ordinal_position=position,
                )
            )
            warnings.append(
                MetadataWarning(
                    kind="projected_column_unverified",
                    message=(
                        f"projected column output={output_name!r} source={source_name!r} "
                        "is absent from base metadata; bounded runtime acquisition is "
                        "required to prove queryability"
                    ),
                    columns=(output_name,),
                )
            )
            continue

        observed_type = (
            catalog_column.type
            if catalog_column._type_is_ibis
            else _canonical_catalog_type(
                catalog_column.type,
                backend_type=metadata.backend_type,
            )
        )
        columns.append(
            ColumnMetadata(
                name=output_name,
                type=observed_type,
                nullable=catalog_column.nullable,
                comment=catalog_column.comment,
                ordinal_position=position,
                _type_is_ibis=True,
            )
        )

    primary_keys = _mapped_constraint_columns(metadata.primary_keys, source_to_output)
    if metadata.primary_keys and primary_keys is None:
        missing = tuple(
            column for column in metadata.primary_keys if column not in source_to_output
        )
        warnings.append(
            MetadataWarning(
                kind="projected_constraint_incomplete",
                message=(
                    "projected source omits physical primary-key columns "
                    f"{missing!r}; the effective source does not claim that constraint"
                ),
                columns=missing,
            )
        )

    unique_constraints: list[UniqueConstraintMetadata] = []
    for constraint in metadata.unique_constraints:
        mapped = _mapped_constraint_columns(constraint.columns, source_to_output)
        if mapped is None:
            missing = tuple(
                column for column in constraint.columns if column not in source_to_output
            )
            warnings.append(
                MetadataWarning(
                    kind="projected_constraint_incomplete",
                    message=(
                        f"projected source omits physical {constraint.kind} constraint "
                        f"columns {missing!r}; constraint={constraint.name!r} is not exposed"
                    ),
                    columns=missing,
                )
            )
            continue
        unique_constraints.append(replace(constraint, columns=mapped))

    partitions, partition_state = _project_partition_fields(metadata, source_to_output, warnings)
    return replace(
        metadata,
        columns=tuple(columns),
        partitions=tuple(partitions),
        partition_state=partition_state,
        warnings=tuple(warnings),
        primary_keys=primary_keys or (),
        unique_constraints=tuple(unique_constraints),
    )


def _project_physical_schema(
    metadata: TableMetadata, source: TableSourceIR
) -> tuple[ColumnMetadata, ...]:
    native_columns = {
        column.name: column for column in (*metadata.columns, *metadata.projectable_columns)
    }
    return tuple(
        replace(native_columns[physical], name=output)
        for output, physical in source.columns
        if physical in native_columns
    )


def _project_partition_fields(
    metadata: TableMetadata, source_to_output: dict[str, str], warnings: list[MetadataWarning]
) -> tuple[list[PartitionMetadata], Literal["known", "none", "unknown"]]:
    partitions: list[PartitionMetadata] = []
    partition_state = metadata.partition_state
    if metadata.partition_state == "known":
        missing_partitions = tuple(
            field.name for field in metadata.partitions if field.name not in source_to_output
        )
        if missing_partitions:
            partition_state = "unknown"
            warnings.append(
                MetadataWarning(
                    kind="projected_partition_unavailable",
                    message=(
                        "base table partition fields are not fully exposed by the projected "
                        f"source; missing={missing_partitions!r}; add bindings or use explicit "
                        "bounded md.unpruned(...) acquisition"
                    ),
                    columns=missing_partitions,
                )
            )
        else:
            partitions.extend(
                replace(field, name=source_to_output[field.name]) for field in metadata.partitions
            )

    return partitions, partition_state
