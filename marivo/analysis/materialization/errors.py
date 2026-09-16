"""Safe structured failures for private Dataset execution and retained reads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.semantic.ir import TableSourceIR


class MaterializationError(DatasetConstructionError):
    """An admitted execution or selected retained read cannot satisfy its contract."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        stage: str = "materialization",
        run_ref: str | None = None,
    ) -> None:
        self.stage = stage
        self.run_ref = run_ref
        super().__init__(
            expected=expected,
            received=received,
            repair=repair,
            location=f"dataset.{stage}",
            message="Dataset materialization contract failed.",
        )


class IntegrityError(MaterializationError):
    """Selected committed metadata or backing contradicts its immutable contract."""


class StorageAccessError(IntegrityError):
    """A safe storage classification, independent of Artifact and Evidence integrity."""

    def __init__(
        self,
        status: Literal["unauthorized", "missing", "mutated", "unknown"],
        *,
        stage: str = "storage_access",
    ) -> None:
        self.storage_status = status
        super().__init__(
            expected="authorized access to the exact committed storage version",
            received=f"selected storage is {status}",
            repair="Restore access to the exact committed backing and inspect the selected Artifact again.",
            stage=stage,
        )


class RecoveryPendingError(MaterializationError):
    """Commit state, publication ownership or storage integrity prevents safe continuation."""


class SessionBusyError(MaterializationError):
    """Another action owns the Session writer boundary; no contender Run exists."""

    def __init__(self, *, session_ref: str | None = None) -> None:
        self.session_ref = session_ref
        super().__init__(
            expected="one active writer for this Session",
            received="an active Session writer"
            if session_ref is None
            else f"an active writer for Session {session_ref}",
            repair="Wait for the current Session action to finish, then retry the same definition.",
            stage="writer_guard",
        )


class SourceSchemaError(MaterializationError):
    """A necessary source column failed physical schema validation."""

    def __init__(
        self,
        dependency: EntitySourceDependency,
        column: str,
        reason: Literal["missing_column", "unsupported_physical_type", "type_mismatch"],
        actual_type: str | None,
        *,
        run_ref: str | None = None,
    ) -> None:
        binding = next(item for item in dependency.columns if item.physical == column)
        self.reason = reason
        self.entity_ref = dependency.entity.ref.path
        self.datasource_ref = dependency.entity.datasource_ref.path
        self.catalog, self.database = dependency.namespace
        source = dependency.entity.source
        self.table = source.table if isinstance(source, TableSourceIR) else None
        self.logical_column = binding.logical
        self.physical_column = column
        self.declared_type = binding.declared_type
        self.actual_type = actual_type
        relation = ".".join(part for part in (self.catalog, self.database, self.table) if part)
        super().__init__(
            expected=f"Entity {self.entity_ref[:160]} on {relation[:200]}: {binding.logical[:100]} -> {column[:100]} declared as {binding.declared_type[:100]}",
            received=f"{reason}: necessary source column has type {(actual_type or '<missing>')[:120]}",
            repair="Correct this column binding or the physical source type; use a type qualified for this backend and retry.",
            stage="output_validation",
            run_ref=run_ref,
        )


def unsupported_source_type(
    dependency: EntitySourceDependency | None, column: str, actual_type: str
) -> MaterializationError:
    if dependency is not None:
        return SourceSchemaError(dependency, column, "unsupported_physical_type", actual_type)
    return MaterializationError(
        expected="a supported physical source type",
        received=f"unsupported type {actual_type[:120]} on column {column[:100]}",
        repair="Use a physical column type qualified for this backend.",
        stage="output_validation",
    )


@contextmanager
def source_type_errors(
    dependency: EntitySourceDependency | None,
    column: str,
    actual_type: str,
) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        raise unsupported_source_type(dependency, column, actual_type) from exc
