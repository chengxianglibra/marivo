"""Surface-level orchestration for Marivo introspection help."""

from __future__ import annotations

import difflib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from marivo.introspection.constraints import Constraint
from marivo.introspection.describe import (
    describe_object,
    method_summary,
    own_doc,
    public_method,
    resolve_method_descriptor,
)
from marivo.introspection.render import render_json, render_text
from marivo.introspection.schema import Descriptor, FamilyFold, Kind, TopLevelEntry

_FAMILY_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("Details", "Detail shapes"),
    ("Brief", "Briefs"),
    ("Ref", "References"),
    ("Frame", "Frames"),
    ("Input", "Type aliases"),
    ("IR", "Internal IR types"),
    ("Metadata", "Metadata types"),
)
_OTHER_FAMILY = "Other types"
_FAMILY_ORDER: tuple[str, ...] = (*(label for _, label in _FAMILY_SUFFIXES), _OTHER_FAMILY)


def _family_order(surface: Surface) -> tuple[str, ...]:
    surface_labels = tuple(label for _, label in surface.family_suffixes)
    return (*surface_labels, *_FAMILY_ORDER)


_ENUMERATED_KINDS: frozenset[Kind] = frozenset({"callable", "module", "topic"})


def _family_label(surface: Surface, name: str) -> str:
    for suffix, label in (*surface.family_suffixes, *_FAMILY_SUFFIXES):
        if name.endswith(suffix):
            return label
    return _OTHER_FAMILY


def _summary_line(obj: object | None) -> str:
    doc = own_doc(obj) if obj is not None else ""
    return doc.splitlines()[0].strip() if doc else ""


def derive_summaries(
    all_names: tuple[str, ...],
    resolve: Callable[[str], object | None],
    topics: Mapping[str, Descriptor | Mapping[str, Any]],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the top-level summary map from each entry's own definition.

    Resolution order per name: explicit override, then the topic's own
    summary, then the resolved object's first docstring line.
    """

    resolved_overrides = overrides or {}
    summaries: dict[str, str] = {}
    for name in all_names:
        if name in resolved_overrides:
            summaries[name] = resolved_overrides[name]
            continue
        topic = topics.get(name)
        if topic is not None:
            if isinstance(topic, Descriptor):
                summaries[name] = topic.summary or ""
            else:
                value = topic.get("summary")
                summaries[name] = value if isinstance(value, str) else ""
            continue
        summaries[name] = _summary_line(resolve(name))
    return summaries


@dataclass(frozen=True)
class Surface:
    """Configuration for one public Marivo help surface."""

    name: str
    all_names: tuple[str, ...]
    summaries: Mapping[str, str]
    resolve: Callable[[str], object | None]
    catalog: Mapping[str, Constraint]
    topics: Mapping[str, Descriptor | Mapping[str, Any]]
    frame_symbols: set[str] = field(default_factory=set)
    type_aliases: set[str] = field(default_factory=set)
    constructed_by: Mapping[str, str] = field(default_factory=dict)
    see_also: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    pinned_entries: tuple[str, ...] = ()
    family_suffixes: tuple[tuple[str, str], ...] = ()
    hidden_names: frozenset[str] = frozenset()


def _catalog_by_id(surface: Surface) -> dict[str, Constraint]:
    return {str(constraint.id): constraint for constraint in surface.catalog.values()}


def _entry_kind(surface: Surface, name: str) -> Kind:
    if name in surface.topics:
        return "topic"
    if name in surface.type_aliases:
        return "type-alias"
    obj = surface.resolve(name)
    if inspect.isclass(obj):
        if name in surface.frame_symbols:
            return "frame"
        return "class"
    if isinstance(obj, ModuleType):
        return "module"
    if callable(obj):
        return "callable"
    return "unknown"


def _top_level_descriptor(surface: Surface) -> Descriptor:
    entries: list[TopLevelEntry] = []
    folded: dict[str, list[str]] = {}
    for name in surface.all_names:
        if name in surface.hidden_names:
            continue
        kind = _entry_kind(surface, name)
        if kind in _ENUMERATED_KINDS or name in surface.pinned_entries:
            entries.append(
                TopLevelEntry(name=name, kind=kind, summary=surface.summaries.get(name, ""))
            )
        else:
            folded.setdefault(_family_label(surface, name), []).append(name)
    families = tuple(
        FamilyFold(label=label, members=tuple(folded[label]))
        for label in _family_order(surface)
        if label in folded
    )
    return Descriptor(
        surface=surface.name,
        kind="surface",
        symbol=None,
        summary=f"{surface.name} help surface. Call help('<name>') for details.",
        entries=tuple(entries),
        families=families,
    )


def _constraints_for(surface: Surface, symbol: str) -> tuple[Constraint, ...]:
    return tuple(
        constraint for constraint in surface.catalog.values() if symbol in constraint.applies_to
    )


def _examples_from(constraints: tuple[Constraint, ...]) -> tuple[str, ...]:
    examples: list[str] = []
    seen: set[str] = set()
    for constraint in constraints:
        if constraint.example is None or constraint.example in seen:
            continue
        seen.add(constraint.example)
        examples.append(constraint.example)
    return tuple(examples)


def _topic_descriptor(surface: Surface, symbol: str) -> Descriptor:
    topic = surface.topics[symbol]
    if isinstance(topic, Descriptor):
        return topic
    content = dict(topic)
    summary = surface.summaries.get(symbol)
    if summary is None and isinstance(content.get("summary"), str):
        summary = content["summary"]
    return Descriptor(
        surface=surface.name,
        kind="topic",
        symbol=symbol,
        summary=summary or f"{symbol} topic.",
        content=content,
    )


def _constraint_descriptor(surface: Surface, symbol: str, constraint: Constraint) -> Descriptor:
    return Descriptor(
        surface=surface.name,
        kind="topic",
        symbol=symbol,
        summary=constraint.title,
        content=constraint.to_dict(),
    )


def _method_descriptor(surface: Surface, symbol: str) -> Descriptor | None:
    owner_name, separator, method_name = symbol.partition(".")
    if not separator or not owner_name or method_name.startswith("_"):
        return None
    owner = surface.resolve(owner_name)
    if not inspect.isclass(owner):
        return None
    method = public_method(
        owner,
        method_name,
        include_inherited=owner_name in surface.frame_symbols,
    )
    if method is None:
        return None
    return resolve_method_descriptor(
        surface=surface.name,
        dotted_path=symbol,
        owner=owner,
        summary=method_summary(method),
        include_inherited=owner_name in surface.frame_symbols,
    )


def _symbol_descriptor(surface: Surface, symbol: str, obj: object) -> Descriptor:
    constraints = _constraints_for(surface, symbol)
    return describe_object(
        surface=surface.name,
        symbol=symbol,
        obj=obj,
        summary=surface.summaries.get(symbol, ""),
        constraints=constraints,
        examples=_examples_from(constraints),
        see_also=surface.see_also.get(symbol, ()),
        frame_symbols=surface.frame_symbols,
        constructed_by=surface.constructed_by,
    )


def _unknown_descriptor(surface: Surface, symbol: str) -> Descriptor:
    suggestions = tuple(difflib.get_close_matches(symbol, surface.all_names, n=3))
    return Descriptor(
        surface=surface.name,
        kind="unknown",
        symbol=symbol,
        summary=f"Unknown help target {symbol!r}. Call help() to list entries.",
        did_you_mean=suggestions,
    )


def _type_alias_descriptor(surface: Surface, symbol: str) -> Descriptor:
    obj = surface.resolve(symbol)
    signature = repr(obj) if obj is not None else symbol
    return Descriptor(
        surface=surface.name,
        kind="type-alias",
        symbol=symbol,
        summary=surface.summaries.get(symbol, ""),
        signature=signature,
    )


def _resolve_descriptor(surface: Surface, symbol: str | None) -> Descriptor:
    if symbol is None:
        return _top_level_descriptor(surface)
    if symbol in surface.topics:
        return _topic_descriptor(surface, symbol)

    catalog = _catalog_by_id(surface)
    if symbol in catalog:
        return _constraint_descriptor(surface, symbol, catalog[symbol])

    if "." in symbol:
        method_descriptor = _method_descriptor(surface, symbol)
        if method_descriptor is not None:
            return method_descriptor

    if symbol in surface.type_aliases:
        return _type_alias_descriptor(surface, symbol)

    obj = surface.resolve(symbol)
    if obj is not None:
        return _symbol_descriptor(surface, symbol, obj)
    return _unknown_descriptor(surface, symbol)


def top_level_families(surface: Surface) -> tuple[FamilyFold, ...]:
    """Return the folded families for a surface's top-level help index."""

    return _top_level_descriptor(surface).families


def render(surface: Surface, symbol: str | None, format: str) -> dict[str, Any] | str:
    """Render a surface help target as text or JSON."""

    if format not in {"text", "json"}:
        raise ValueError("format must be 'text' or 'json'")

    descriptor = _resolve_descriptor(surface, symbol)
    if format == "json":
        return render_json(descriptor)
    return render_text(descriptor)
