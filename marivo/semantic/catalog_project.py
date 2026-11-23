"""Project authority needed to open and inspect a Semantic Catalog."""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from marivo._kernel.refs import Ref, SemanticKindTag
from marivo.datasource.spi import DatasourceConnectionService, DatasourceIR
from marivo.semantic.errors import SemanticError
from marivo.semantic.readiness import ReadinessReport
from marivo.semantic.runtime_check.materializer import MaterializerProject


class CatalogProject(MaterializerProject, Protocol):
    @property
    def semantic_root(self) -> Path: ...

    @property
    def workspace_dir(self) -> Path: ...

    @property
    def _datasource_irs(self) -> tuple[DatasourceIR, ...]: ...

    @property
    def _filtered_domains(self) -> tuple[str, ...]: ...

    @property
    def _excluded_refs(self) -> tuple[str, ...]: ...

    def is_ready(self) -> bool: ...

    def errors(self) -> tuple[SemanticError, ...]: ...

    def _readiness(
        self, *, refs: Iterable[Ref[SemanticKindTag] | str] | None = None
    ) -> ReadinessReport: ...

    def _connection_operation(self) -> AbstractContextManager[DatasourceConnectionService]: ...


def project_filtered_domains(project: CatalogProject) -> tuple[str, ...]:
    return project._filtered_domains  # noqa: SLF001 - Project load scope retained by the Catalog.


def project_excluded_refs(project: CatalogProject) -> tuple[str, ...]:
    return project._excluded_refs  # noqa: SLF001 - Identities excluded during the captured load.


def project_readiness(
    project: CatalogProject, *, refs: Iterable[Ref[SemanticKindTag] | str] | None
) -> ReadinessReport:
    return project._readiness(refs=refs)  # noqa: SLF001 - Run readiness against the owning project.


def project_connections(
    project: CatalogProject,
) -> AbstractContextManager[DatasourceConnectionService]:
    return project._connection_operation()  # noqa: SLF001 - Scope physical reads to the owning project.
