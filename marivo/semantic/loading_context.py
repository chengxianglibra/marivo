"""Task-local capture scope for authoring one semantic model."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ibis.common.deferred import Deferred

from marivo._definition.types import InputContext
from marivo.datasource.spi import DatasourceIR
from marivo.semantic._model_source import _ModelSources

if TYPE_CHECKING:
    from marivo.semantic.declaration_records import PendingDefinition


@dataclass
class LoaderContext:
    """Context active during loader execution.

    Set via ``_LOADER_CTX`` ContextVar; decorator functions read
    this to enforce outside-loader-context guards.
    """

    model_name: str | None = None
    file_path: str | None = None
    current_model_file: str | None = None
    default_domain: str | None = None
    pending_definitions: list[PendingDefinition] = field(default_factory=list)
    datasource_irs: tuple[DatasourceIR, ...] = ()
    # Module-level Measure/Metric declarations, collected once per object.
    declarations: list[Deferred] = field(default_factory=list)
    declaration_contexts: dict[int, InputContext] = field(default_factory=dict)
    project_root: Path | None = None
    model_package: str | None = field(default=None, repr=False, compare=False)
    sources: _ModelSources = field(default_factory=_ModelSources, repr=False, compare=False)


_LOADER_CTX: ContextVar[LoaderContext | None] = ContextVar(
    "_LOADER_CTX",
    default=None,
)


class LoaderContextManager:
    """Context manager that sets/resets the loader context for test use.

    Usage::

        ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
        with LoaderContextManager(ctx):
            sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
            ...
    """

    def __init__(self, ctx: LoaderContext) -> None:
        self._ctx = ctx
        self._token: Token[LoaderContext | None] | None = None

    def __enter__(self) -> LoaderContext:
        self._token = _LOADER_CTX.set(self._ctx)
        return self._ctx

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            _LOADER_CTX.reset(self._token)


loader_context = LoaderContextManager
