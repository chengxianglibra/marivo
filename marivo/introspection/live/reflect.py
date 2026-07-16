"""Neutral reflection helpers for registered Python callables."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from importlib import import_module
from types import ModuleType


def callable_identity(value: object) -> str:
    """Return the stable dotted identity for a callable or bound method."""
    property_getter = getattr(value, "fget", None)
    function = property_getter if property_getter is not None else getattr(value, "__func__", value)
    module = getattr(function, "__module__", None)
    qualname = getattr(function, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise KeyError(value)
    return f"{module}.{qualname}"


def import_registered_callable(path: str) -> object:
    """Import a registered path containing optional class segments."""
    parts = path.split(".")
    for index in range(len(parts), 0, -1):
        module_name = ".".join(parts[:index])
        try:
            value: object = import_module(module_name)
        except ModuleNotFoundError:
            continue
        if isinstance(value, ModuleType) and index == len(parts):
            continue
        try:
            for attribute in parts[index:]:
                value = getattr(value, attribute)
        except AttributeError:
            continue
        return value
    raise ImportError(f"cannot import registered callable {path!r}")


def installed_signature(value: Callable[..., object]) -> inspect.Signature:
    """Return the signature of an installed registered callable."""
    return inspect.signature(value)


def owned_docstring(value: object) -> str:
    """Return the installed object's normalized owned docstring."""
    return inspect.getdoc(value) or ""
