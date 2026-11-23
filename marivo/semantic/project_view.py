"""Typed reads of the state already held by a Semantic project."""

from __future__ import annotations

from typing import Protocol

from marivo.datasource.spi import DatasourceIR
from marivo.semantic.compiled_types import CompiledSemanticState
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.static_check.registry import Registry


class _RegistryOwner(Protocol):
    @property
    def _registry(self) -> Registry | None: ...


class _CompiledStateOwner(Protocol):
    @property
    def _compiled_state(self) -> CompiledSemanticState | None: ...


class _SidecarOwner(Protocol):
    @property
    def _expression_sidecar(self) -> CompiledExpressionSidecar | None: ...


class _DatasourceDeclarations(Protocol):
    @property
    def _datasource_irs(self) -> tuple[DatasourceIR, ...]: ...


def project_registry(project: _RegistryOwner) -> Registry | None:
    return project._registry  # noqa: SLF001 - Read the project-owned declaration registry.


def project_compiled_state(project: _CompiledStateOwner) -> CompiledSemanticState | None:
    return project._compiled_state  # noqa: SLF001 - Read the exact loaded state.


def project_expression_sidecar(project: _SidecarOwner) -> CompiledExpressionSidecar | None:
    return project._expression_sidecar  # noqa: SLF001 - Read the captured expression bodies.


def project_datasources(project: _DatasourceDeclarations) -> tuple[DatasourceIR, ...]:
    return project._datasource_irs  # noqa: SLF001 - Read the declarations selected during load.
