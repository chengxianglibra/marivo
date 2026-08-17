"""Neutral reflection helpers for registered Python callables."""

from __future__ import annotations

import ast
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


def return_annotation_text(value: Callable[..., object]) -> str | None:
    """Return one stable source-like spelling of a callable's return annotation."""

    annotation = inspect.signature(value).return_annotation
    if annotation is inspect.Signature.empty:
        return None
    if isinstance(annotation, str):
        return annotation.strip()
    if annotation is None:
        return "None"
    return inspect.formatannotation(annotation).strip()


def _annotation_members_from_node(node: ast.expr) -> frozenset[str] | None:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_members_from_node(node.left)
        right = _annotation_members_from_node(node.right)
        if left is None or right is None:
            return None
        return left | right
    if isinstance(node, ast.Name):
        return frozenset({"None" if node.id == "NoneType" else node.id})
    if isinstance(node, ast.Attribute):
        return frozenset({"None" if node.attr == "NoneType" else node.attr})
    if isinstance(node, ast.Constant):
        if node.value is None:
            return frozenset({"None"})
        if isinstance(node.value, str):
            return _annotation_members(node.value)
        return None
    if isinstance(node, ast.Subscript):
        wrapper = _annotation_members_from_node(node.value)
        if wrapper == frozenset({"Optional"}):
            optional_members = _annotation_members_from_node(node.slice)
            return None if optional_members is None else optional_members | {"None"}
        if wrapper == frozenset({"Union"}):
            elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
            union_members: frozenset[str] = frozenset()
            for element in elements:
                parsed = _annotation_members_from_node(element)
                if parsed is None:
                    return None
                union_members |= parsed
            return union_members
    return None


def _annotation_members(annotation: str) -> frozenset[str] | None:
    try:
        expression = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return None
    return _annotation_members_from_node(expression)


def return_annotation_mismatch(
    value: Callable[..., object],
    *,
    expected_family: object,
    nullable: bool,
) -> str | None:
    """Compare a concrete family contract with a callable return annotation.

    Non-string families represent receiver-dependent outputs such as
    ``SameAsInputFamily`` and are intentionally skipped.
    """

    if not isinstance(expected_family, str):
        return None
    try:
        annotation_text = return_annotation_text(value)
    except (TypeError, ValueError) as error:
        return f"cannot inspect return annotation: {type(error).__name__}: {error}"
    expected = f"{expected_family} | None" if nullable else expected_family
    if annotation_text is None:
        return f"descriptor expects {expected}, callable has no return annotation"
    actual_members = _annotation_members(annotation_text)
    expected_members = (
        frozenset({expected_family, "None"}) if nullable else frozenset({expected_family})
    )
    if actual_members != expected_members:
        return f"descriptor expects {expected}, callable annotation is {annotation_text}"
    return None


def owned_docstring(value: object) -> str:
    """Return the installed object's normalized owned docstring."""
    return inspect.getdoc(value) or ""
