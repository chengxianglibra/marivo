"""Identify native typed builtin UDF wrappers without invoking authored code."""

from __future__ import annotations

import inspect
import json
from types import CodeType, FunctionType
from typing import TypeAlias

import ibis.expr.datatypes as dt
import ibis.expr.rules as rlz
from ibis.common.annotations import Argument
from ibis.common.collections import FrozenDict
from ibis.common.deferred import deferrable
from ibis.expr.operations.udf import _UDF, InputType, ScalarUDF

LiteralValue: TypeAlias = str | int | float | bool | tuple["LiteralValue", ...] | None


def _nested_code(function: object, *names: str) -> CodeType:
    """Resolve the installed Ibis implementation's exact wrapper code objects."""
    if not isinstance(function, FunctionType):
        raise TypeError("Ibis UDF wrapper implementation must be a Python function")
    code = function.__code__
    for name in names:
        code = next(
            value
            for value in code.co_consts
            if isinstance(value, CodeType) and value.co_name == name
        )
    return code


_DEFERRED_WRAPPER = _nested_code(deferrable, "wrapper", "inner")
_UDF_METHOD: object = vars(_UDF)["_make_wrapper"]
assert isinstance(_UDF_METHOD, classmethod)
_UDF_WRAPPER = _nested_code(_UDF_METHOD.__func__, "construct")


def _closure_value(function: FunctionType, name: str) -> object:
    for key, cell in zip(function.__code__.co_freevars, function.__closure__ or (), strict=True):
        if key == name:
            return cell.cell_contents
    return None


def _literal(value: object) -> LiteralValue:
    if value is None or type(value) in (str, int, float, bool):
        assert value is None or isinstance(value, (str, int, float, bool))
        return value
    if type(value) in (tuple, list):
        assert isinstance(value, (tuple, list))
        return tuple(_literal(item) for item in value)
    raise TypeError("builtin UDF defaults and configuration must be literal values")


def builtin_udf_token(value: object) -> str | None:
    """Return a stable token for an installed Ibis typed scalar builtin wrapper.

    Code identity verifies both Ibis wrapper layers before reading their captured
    operation class. Metadata on an unrelated callable cannot admit that callable.
    The installed Ibis implementation's private wrapper layout is isolated here.
    """
    if not isinstance(value, FunctionType) or value.__code__ is not _DEFERRED_WRAPPER:
        return None
    construct = _closure_value(value, "func")
    if not isinstance(construct, FunctionType) or construct.__code__ is not _UDF_WRAPPER:
        return None
    node = _closure_value(construct, "node")
    if not isinstance(node, type) or node.__bases__ != (ScalarUDF,):
        return None
    if getattr(node, "__input_type__", None) is not InputType.BUILTIN:
        return None
    signature: object = getattr(node, "__signature__", None)
    result_type: object = getattr(node, "dtype", None)
    name: object = getattr(node, "__func_name__", None)
    namespace: object = getattr(node, "__udf_namespace__", None)
    if not isinstance(signature, inspect.Signature) or not signature.parameters:
        return None
    if not isinstance(result_type, dt.DataType) or type(name) is not str:
        return None
    arguments: list[tuple[str, str, LiteralValue]] = []
    try:
        for parameter in signature.parameters.values():
            annotation: object = parameter.annotation
            if not isinstance(annotation, Argument) or not isinstance(
                annotation.pattern, rlz.ValueOf
            ):
                return None
            dtype: object = annotation.pattern.dtype
            if not isinstance(dtype, dt.DataType):
                return None
            default = (
                ("required",)
                if parameter.default is inspect.Parameter.empty
                else ("default", _literal(parameter.default))
            )
            arguments.append((parameter.name, str(dtype), default))
        configuration: object = getattr(node, "__config__", None)
        if not isinstance(configuration, FrozenDict):
            return None
        options = tuple(sorted((str(key), _literal(item)) for key, item in configuration.items()))
        catalog = _literal(getattr(namespace, "catalog", None))
        database = _literal(getattr(namespace, "database", None))
        payload = (name, catalog, database, tuple(arguments), str(result_type), options)
        token = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return "ibis:scalar_builtin:" + token
