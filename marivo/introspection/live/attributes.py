"""Static attribute discovery; never evaluate a receiver or a property getter."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields, is_dataclass
from typing import get_origin

from pydantic import BaseModel


@dataclass(frozen=True)
class AttributeHelp:
    """One class-owned field or property contract."""

    value_type: str
    meaning: str
    access: str
    read_effects: str


def _annotation_text(annotation: object) -> str:
    if annotation is inspect.Signature.empty:
        return "Not annotated"
    return annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)


def _field_access(type_obj: type, name: str) -> str:
    """Read mutability from the field owner's declared model contract."""
    if issubclass(type_obj, BaseModel):
        field = type_obj.model_fields.get(name)
        if type_obj.model_config.get("frozen") or (field is not None and field.frozen):
            return "read-only"
        return "read/write"
    if is_dataclass(type_obj):
        parameters = inspect.getattr_static(type_obj, "__dataclass_params__")
        return "read-only" if parameters.frozen else "read/write"
    return "readable; write policy is not declared"


def public_attributes(type_obj: type) -> dict[str, AttributeHelp]:
    """Read public annotated fields and properties without invoking descriptors."""
    attributes: dict[str, AttributeHelp] = {}
    field_meanings = (
        {field.name: field.metadata.get("meaning") for field in fields(type_obj)}
        if is_dataclass(type_obj)
        else {}
    )
    for base in reversed(type_obj.__mro__):
        if base is not type_obj and not base.__module__.startswith("marivo."):
            continue
        annotations = inspect.get_annotations(base)
        if isinstance(annotations, dict):
            for name, annotation in annotations.items():
                if (
                    isinstance(name, str)
                    and not name.startswith("_")
                    and str(get_origin(annotation)) != "typing.ClassVar"
                    and not (isinstance(annotation, str) and annotation.startswith("ClassVar["))
                ):
                    meaning = field_meanings.get(name)
                    attributes[name] = AttributeHelp(
                        _annotation_text(annotation),
                        meaning
                        if isinstance(meaning, str)
                        else f"Stored {name.replace('_', ' ')} of {type_obj.__name__}.",
                        _field_access(type_obj, name),
                        "Reads the stored field; no property getter or execution is involved.",
                    )
        for name, member in base.__dict__.items():
            if not name.startswith("_") and isinstance(member, property) and member.fget:
                annotation = inspect.signature(member.fget).return_annotation
                attributes[name] = AttributeHelp(
                    _annotation_text(annotation),
                    inspect.getdoc(member) or "Public property.",
                    "read-only" if member.fset is None else "read/write",
                    inspect.getdoc(member) or "Property getter; see the owning type contract.",
                )
    return attributes


def render_attribute(
    type_obj: type,
    member: str,
    *,
    receiver: str,
    applicability: tuple[str, ...] = (),
) -> str:
    """Render static reading guidance for an already resolved attribute."""
    attribute = public_attributes(type_obj)[member]
    return "\n".join(
        (
            f"Python: {receiver}.{member}",
            f"Value type: {attribute.value_type}",
            f"Access: {attribute.access}",
            f"Meaning: {attribute.meaning.replace(type_obj.__name__, receiver)}",
            "Applies when: " + " or ".join(applicability)
            if applicability
            else f"Applies to all {receiver} values.",
            f"Read contract: {attribute.read_effects}"
            if attribute.read_effects != attribute.meaning
            else "Help inspects this property without evaluating its getter.",
            "Query this attribute by its string target; access the receiver to read its current value.",
        )
    )
