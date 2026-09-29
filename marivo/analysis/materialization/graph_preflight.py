"""Schema-only R1 admission facts for a prospective source graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
from marivo.datasource.adapters import BoundSource, SourceSession, provider_for
from marivo.datasource.ir import ParquetSourceIR, TableSourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.ir import TargetEntityContract, TargetSnapshotVersion, TargetValidityVersion
from marivo.semantic.validator import Registry, normalize_target_entity


def _reject(expected: str, received: str, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair=repair,
        location="analysis.graph_preflight",
    )


@dataclass(frozen=True, slots=True)
class EntitySchema:
    """Exact physical facts observed without a business-row submission."""

    contract: TargetEntityContract
    schema: pa.Schema
    identity_types: tuple[ScalarType, ...]
    shape: SourceShape

    @property
    def identity_type(self) -> ScalarType:
        """Scalar carrier for existing single-value method qualification."""
        return self.identity_types[0]

    def field_type(self, column: str) -> ScalarType:
        """Return an exact supported physical scalar for a bound source field."""
        index = self.schema.get_field_index(column)
        if index < 0:
            raise _reject(
                f"projected field column {column!r}",
                "column absent from the bound source schema",
                "Correct the Entity source projection or field declaration.",
            )
        physical = self.schema.field(index).type
        if physical == pa.int64():
            return ScalarType("int64")
        if physical == pa.string():
            return ScalarType("string")
        if physical == pa.float64():
            return ScalarType("float64")
        if physical == pa.bool_():
            return ScalarType("boolean")
        if physical == pa.date32():
            return ScalarType("date")
        if pa.types.is_timestamp(physical):
            return ScalarType("timestamp")
        raise _reject(
            "an exact supported physical field type",
            f"{column}: {physical}",
            "Use a source field with a qualified physical type.",
        )

    def verify(self, bound: BoundSource) -> None:
        """Reject a changed source binding before any business-row submission."""
        if bound.source != self.contract.source or not bound.facts.schema.equals(
            self.schema, check_metadata=True
        ):
            raise _reject(
                "the exact source schema selected before Run allocation",
                f"changed physical binding for {self.contract.ref.path}",
                "Rebuild and execute the logical graph against the current source schema.",
            )


def _identity_type(schema: pa.Schema, column: str) -> ScalarType:
    index = schema.get_field_index(column)
    if index < 0:
        raise _reject(
            f"projected identity column {column!r}",
            "column absent from the bound source schema",
            "Correct the Entity source projection or primary key.",
        )
    physical = schema.field(index).type
    if physical == pa.int64():
        return ScalarType("int64")
    if physical == pa.string():
        return ScalarType("string")
    raise _reject(
        "an exact int64 or string Entity identity",
        f"{column}: {physical}",
        "Use an Entity with a qualified physical identity type.",
    )


def preflight_entities(
    registry: Registry, project_root: Path, entity_paths: tuple[str, ...]
) -> tuple[EntitySchema, ...]:
    """Bind selected Entity schemas through R1 before allocating a graph Run.

    This function performs no compiled read or batch iteration. Callers must
    reject mixed and foreign graph inputs before invoking it, then compare the
    preflight schemas to the bindings opened for the admitted Run.
    """
    if not entity_paths or len(set(entity_paths)) != len(entity_paths):
        raise _reject(
            "distinct selected Entity paths",
            repr(entity_paths),
            "Bind each governed source Entity once.",
        )
    contracts = tuple(normalize_target_entity(registry, path) for path in entity_paths)
    datasource_paths = {contract.datasource_ref.path for contract in contracts}
    if len(datasource_paths) != 1:
        raise _reject(
            "one selected datasource",
            repr(sorted(datasource_paths)),
            "Build a source-only graph over one governed datasource.",
        )
    datasource_path = next(iter(datasource_paths))
    datasource = registry.datasources[datasource_path]
    if datasource.backend_type not in ("duckdb", "sqlite"):
        raise _reject(
            "the qualified DuckDB source route",
            datasource.backend_type,
            "Use a qualified DuckDB source for this Analysis graph.",
        )
    shapes: list[SourceShape] = []
    for contract in contracts:
        if not contract.primary_key:
            raise _reject(
                "an Entity with a complete declared key",
                contract.ref.path,
                "Declare the complete Entity key with qualified physical identity types.",
            )
        if isinstance(contract.source, TableSourceIR):
            shapes.append(
                SourceShape(
                    "sqlite" if datasource.backend_type == "sqlite" else "duckdb",
                    "table",
                    "native",
                    NoTime(),
                )
            )
        elif isinstance(contract.source, ParquetSourceIR) and datasource.backend_type == "duckdb":
            shapes.append(SourceShape("duckdb", "parquet", "parquet", NoTime()))
        else:
            raise _reject(
                "a qualified table or Parquet Entity source",
                type(contract.source).__name__,
                "Use a source form with exact R1 schema qualification.",
            )
    service = DatasourceConnectionService(project_root, include_semantic_layers=True)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(
            provider_for(datasource.backend_type), datasource, backend, owns_backend=False
        ) as source,
    ):
        results = []
        for contract, shape in zip(contracts, shapes, strict=True):
            bound = source.bind(contract.source, source_identity=contract.ref.path)
            schema = bound.facts.schema
            identity_types = tuple(_identity_type(schema, key) for key in contract.primary_key)
            version = contract.version
            axes = (
                (version.source_column,)
                if isinstance(version, TargetSnapshotVersion)
                else (version.valid_from_column, version.valid_to_column)
                if isinstance(version, TargetValidityVersion)
                else ()
            )
            for column in axes:
                index = schema.get_field_index(column)
                if index < 0 or not (
                    pa.types.is_date(schema.field(index).type)
                    or pa.types.is_timestamp(schema.field(index).type)
                ):
                    raise _reject(
                        "native temporal version axes",
                        column,
                        "Bind a qualified native date or timestamp version field.",
                    )
            results.append(EntitySchema(contract, schema, identity_types, shape))
        return tuple(results)
