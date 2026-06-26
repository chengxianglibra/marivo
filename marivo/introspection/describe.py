"""Descriptor builders for Marivo introspection surfaces."""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Mapping
from types import ModuleType

from pydantic import BaseModel

from marivo.introspection.constraints import Constraint
from marivo.introspection.schema import Descriptor, FieldInfo, MethodInfo


def own_doc(obj: object) -> str:
    """Return only the object's directly attached docstring."""

    if inspect.isclass(obj) and issubclass(obj, BaseModel) and "__doc__" not in vars(obj):
        return ""
    doc = getattr(obj, "__doc__", None)
    if not isinstance(doc, str):
        return ""
    return inspect.cleandoc(doc)


def signature_for(symbol: str, obj: object) -> str:
    """Return a best-effort callable signature prefixed by symbol."""

    if not callable(obj):
        return f"{symbol}(...)"
    try:
        signature = inspect.signature(obj, eval_str=True)
    except Exception:
        return f"{symbol}(...)"
    has_string_annotation = isinstance(signature.return_annotation, str) or any(
        isinstance(parameter.annotation, str) for parameter in signature.parameters.values()
    )
    if has_string_annotation:
        return f"{symbol}(...)"
    return f"{symbol}{signature}"


def method_summary(method: object) -> str:
    """Return the first doc line for a method summary."""

    doc = own_doc(method)
    if not doc:
        return ""
    return doc.splitlines()[0]


def _callable_from_class_member(member: object) -> object | None:
    if isinstance(member, property):
        return None
    if isinstance(member, (staticmethod, classmethod)):
        return member.__func__
    if callable(member):
        return member
    return None


def direct_public_method(cls: type[object], name: str) -> object | None:
    """Return a directly declared public method from a class."""

    if name.startswith("_"):
        return None
    try:
        member = vars(cls)[name]
    except KeyError:
        return None
    return _callable_from_class_member(member)


def public_method(
    cls: type[object],
    name: str,
    *,
    include_inherited: bool = False,
) -> object | None:
    """Return a public method using the same policy as the L1 method list."""

    method = direct_public_method(cls, name)
    if method is not None or not include_inherited:
        return method
    for base in cls.__mro__[1:]:
        if base is object or issubclass(base, BaseModel):
            continue
        method = direct_public_method(base, name)
        if method is not None:
            return method
    return None


def _method_names(cls: type[object], *, include_inherited: bool) -> tuple[str, ...]:
    if not include_inherited:
        return tuple(vars(cls))
    names: list[str] = []
    seen: set[str] = set()
    for base in reversed(cls.__mro__):
        if base is object or issubclass(base, BaseModel):
            continue
        for name in vars(base):
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return tuple(names)


def _format_annotation(annotation: object) -> str:
    """Render a resolved type annotation as a readable string.

    Handles plain types, generics (``list[str]``), and union forms
    (``typing.Union[str, None]`` and PEP 604 ``str | None``), normalizing
    unions to the ``a | b`` display form.
    """

    if annotation is None:
        return "Any"
    if annotation is type(None):
        return "None"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return " | ".join(_format_annotation(arg) for arg in args)
    if origin is not None:
        origin_name = getattr(origin, "__name__", None) or str(origin).replace("typing.", "")
        if args:
            inner = ", ".join(_format_annotation(arg) for arg in args)
            return f"{origin_name}[{inner}]"
        return origin_name
    name = getattr(annotation, "__name__", None)
    if isinstance(name, str):
        return name
    return str(annotation).replace("typing.", "")


def pydantic_fields(cls: type) -> tuple[FieldInfo, ...]:
    """Extract field metadata from a Pydantic BaseModel subclass."""
    from pydantic_core import PydanticUndefined

    if not issubclass(cls, BaseModel):
        return ()

    fields: list[FieldInfo] = []
    for name, fi in cls.model_fields.items():
        annotation = _format_annotation(fi.annotation)

        default: str | None = None
        if fi.default is not PydanticUndefined:
            default = repr(fi.default)
        elif fi.default_factory is not None:
            default = getattr(fi.default_factory, "__name__", repr(fi.default_factory))

        fields.append(
            FieldInfo(
                name=name,
                annotation=annotation,
                required=fi.is_required(),
                default=default,
                description=fi.description,
            )
        )
    return tuple(fields)


def dataclass_field_infos(cls: type) -> tuple[FieldInfo, ...]:
    """Extract field metadata from a dataclass type."""

    if not dataclasses.is_dataclass(cls):
        return ()

    fields: list[FieldInfo] = []
    for field in dataclasses.fields(cls):
        if isinstance(field.type, str):
            annotation = field.type
        else:
            annotation = getattr(field.type, "__name__", str(field.type))

        has_default = field.default is not dataclasses.MISSING
        has_default_factory = field.default_factory is not dataclasses.MISSING
        default: str | None = None
        if has_default:
            default = repr(field.default)
        elif has_default_factory:
            default = getattr(field.default_factory, "__name__", repr(field.default_factory))

        metadata_description = field.metadata.get("description")
        description = metadata_description if isinstance(metadata_description, str) else None

        fields.append(
            FieldInfo(
                name=field.name,
                annotation=annotation,
                required=not has_default and not has_default_factory,
                default=default,
                description=description,
            )
        )
    return tuple(fields)


def field_infos(cls: type) -> tuple[FieldInfo, ...]:
    """Extract field metadata from supported class field systems."""

    fields = pydantic_fields(cls)
    if fields:
        return fields
    return dataclass_field_infos(cls)


def _is_pydantic_validator(cls: type, name: str) -> bool:
    """Return True if name is a Pydantic field_validator or model_validator on cls."""
    if not issubclass(cls, BaseModel):
        return False
    decorators = getattr(cls, "__pydantic_decorators__", None)
    if decorators is None:
        return False
    return name in decorators.field_validators or name in decorators.model_validators


def public_methods(
    cls: type[object],
    *,
    include_inherited: bool = False,
) -> tuple[MethodInfo, ...]:
    """Return public methods declared on a class, optionally including non-Pydantic bases."""

    methods: list[MethodInfo] = []
    for name in _method_names(cls, include_inherited=include_inherited):
        method = public_method(cls, name, include_inherited=include_inherited)
        if method is None:
            continue
        if _is_pydantic_validator(cls, name):
            continue
        methods.append(MethodInfo(name=name, summary=method_summary(method)))
    return tuple(methods)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def describe_object(
    *,
    surface: str,
    symbol: str,
    obj: object,
    summary: str,
    constraints: tuple[Constraint, ...],
    examples: tuple[str, ...],
    see_also: tuple[str, ...],
    frame_symbols: set[str] | None = None,
    constructed_by: Mapping[str, str] | None = None,
) -> Descriptor:
    """Describe a surface symbol without coupling to a public help adapter."""

    if inspect.isclass(obj):
        cls = obj
        if frame_symbols is not None and symbol in frame_symbols:
            return Descriptor(
                surface=surface,
                kind="frame",
                symbol=symbol,
                summary=summary,
                doc=own_doc(cls),
                constraints=constraints,
                examples=examples,
                see_also=see_also,
                fields=field_infos(cls),
                methods=public_methods(cls, include_inherited=True),
                next_intents=_string_tuple(getattr(cls, "_NEXT_INTENTS", ())),
                constructed_by=constructed_by.get(symbol) if constructed_by else None,
            )
        return Descriptor(
            surface=surface,
            kind="class",
            symbol=symbol,
            summary=summary,
            doc=own_doc(cls),
            signature=f"class {symbol}",
            constraints=constraints,
            examples=examples,
            see_also=see_also,
            fields=field_infos(cls),
            methods=public_methods(cls),
        )

    if isinstance(obj, ModuleType):
        return Descriptor(
            surface=surface,
            kind="module",
            symbol=symbol,
            summary=summary,
            doc=own_doc(obj),
            signature=f"module {symbol}",
            constraints=constraints,
            examples=examples,
            see_also=see_also,
        )

    if callable(obj):
        return Descriptor(
            surface=surface,
            kind="callable",
            symbol=symbol,
            summary=summary,
            doc=own_doc(obj),
            signature=signature_for(symbol, obj),
            constraints=constraints,
            examples=examples,
            see_also=see_also,
        )

    return Descriptor(
        surface=surface,
        kind="topic",
        symbol=symbol,
        summary=summary,
        doc=own_doc(obj),
        constraints=constraints,
        examples=examples,
        see_also=see_also,
        content={"value": obj} if isinstance(obj, dict) else {},
    )


def resolve_method_descriptor(
    *,
    surface: str,
    dotted_path: str,
    owner: type[object],
    summary: str,
    include_inherited: bool = False,
) -> Descriptor:
    """Describe a public method selected by dotted path."""

    _, _, method_name = dotted_path.rpartition(".")
    method = public_method(owner, method_name, include_inherited=include_inherited)
    if method is None:
        return Descriptor(
            surface=surface,
            kind="unknown",
            symbol=dotted_path,
            summary=f"Unknown help target {dotted_path!r}. Call help() to list entries.",
        )
    return Descriptor(
        surface=surface,
        kind="callable",
        symbol=dotted_path,
        summary=summary,
        doc=own_doc(method),
        signature=signature_for(dotted_path, method),
    )
