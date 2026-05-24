from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from marivo.semantic_py.errors import SemanticError
from marivo.semantic_py.ir import ModelIR


@dataclass
class PySemanticRegistry:
    models: dict[str, ModelIR] = field(default_factory=dict)
    state: Literal["unloaded", "loading", "ready", "errored"] = "unloaded"
    load_errors: list[SemanticError] = field(default_factory=list)

    def clear(self) -> None:
        self.models.clear()
        self.state = "unloaded"
        self.load_errors.clear()


@dataclass
class SemanticProject:
    root: str
    registry: PySemanticRegistry = field(default_factory=PySemanticRegistry)
    lock: RLock = field(default_factory=RLock)


_DEFAULT_PROJECT = SemanticProject(root=".")
_REGISTRY_STACK: ContextVar[tuple[PySemanticRegistry, ...]] = ContextVar(
    "_REGISTRY_STACK",
    default=(_DEFAULT_PROJECT.registry,),
)
_MODEL_STACK: ContextVar[tuple[tuple[str, ...], ...]] = ContextVar(
    "_MODEL_STACK",
    default=((),),
)


def active_registry() -> PySemanticRegistry:
    return _REGISTRY_STACK.get()[-1]


def active_model_names() -> tuple[str, ...]:
    return _MODEL_STACK.get()[-1]


@contextmanager
def use_registry(registry: PySemanticRegistry) -> Iterator[PySemanticRegistry]:
    stack = _REGISTRY_STACK.get()
    token = _REGISTRY_STACK.set((*stack, registry))
    try:
        yield registry
    finally:
        _REGISTRY_STACK.reset(token)


@contextmanager
def use_model(name: str) -> Iterator[str]:
    stack = _MODEL_STACK.get()
    token = _MODEL_STACK.set((*stack, (name,)))
    try:
        yield name
    finally:
        _MODEL_STACK.reset(token)


@contextmanager
def use_model_candidates(names: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    stack = _MODEL_STACK.get()
    token = _MODEL_STACK.set((*stack, names))
    try:
        yield names
    finally:
        _MODEL_STACK.reset(token)


def default_project() -> SemanticProject:
    return _DEFAULT_PROJECT
