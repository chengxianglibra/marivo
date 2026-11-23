"""Captured metadata for Domain and Datasource entries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from typing_extensions import override

from marivo._kernel.ai_context import AiContextIR as AiContextView
from marivo._kernel.refs import (
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo._kernel.render import (
    Card,
    FieldSection,
    ListSection,
    RenderableResult,
    Section,
)
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    DomainSymbol,
    LookupSymbol,
    symbol_factory,
    symbol_from_ref,
)
from marivo.datasource.spi import (
    DEFAULT_DATASOURCE_DESCRIPTION,
    DEFAULT_DATASOURCE_NAME,
    credential_labels,
)
from marivo.semantic.ir import (
    SourceLocation,
)


def _source_location_text(source_location: SourceLocation) -> str:
    return f"{source_location.file}:{source_location.line}"


def _format_refs(refs: tuple[Ref[SemanticKindTag], ...], *, limit: int = 6) -> str:
    if not refs:
        return "(none)"
    visible = [ref.key for ref in refs[:limit]]
    if len(refs) > limit:
        visible.append(f"... (+{len(refs) - limit} more)")
    return ", ".join(visible)


def _format_card_refs(
    refs: tuple[Ref[SemanticKindTag], ...],
    *,
    empty: str = "none",
    limit: int = 6,
) -> str:
    if not refs:
        return empty
    rendered = _format_refs(refs, limit=limit)
    if len(refs) > limit:
        rendered += "; full: .details().show()"
    return rendered


def _format_mapping(mapping: Mapping[str, object]) -> str:
    if not mapping:
        return "(none)"
    return ", ".join(f"{key}: {value}" for key, value in sorted(mapping.items()))


def _common_detail_sections(
    *,
    context: AiContextView,
    python_symbol: str,
    source_location: SourceLocation,
    parents: tuple[Ref[SemanticKindTag], ...],
    children: tuple[Ref[SemanticKindTag], ...],
    dependents: tuple[Ref[SemanticKindTag], ...],
) -> list[Section]:
    sections: list[Section] = [
        FieldSection(label="business_definition", value=context.business_definition or "(none)"),
        ListSection(label="guardrails", items=tuple(context.guardrails) or ()),
    ]
    sections.extend(
        (
            FieldSection(label="source_location", value=_source_location_text(source_location)),
            FieldSection(label="python_symbol", value=python_symbol or "(none)"),
            FieldSection(label="parents", value=_format_refs(parents)),
            FieldSection(label="children", value=_format_refs(children)),
            FieldSection(label="dependents", value=_format_refs(dependents)),
        )
    )
    return sections


@dataclass(frozen=True, repr=False)
class _DetailsBase(RenderableResult):
    """Common fields and result protocol shared by all *Details classes."""

    _ref: Ref[SemanticKindTag]
    kind: SemanticKind
    name: str
    domain: str | None
    context: AiContextView
    source_location: SourceLocation
    _parents: tuple[Ref[SemanticKindTag], ...]
    _children: tuple[Ref[SemanticKindTag], ...]
    _dependents: tuple[Ref[SemanticKindTag], ...]
    python_symbol: str

    @property
    def ref(self) -> LookupSymbol:
        return symbol_from_ref(self._ref)

    @property
    def parents(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._parents)

    @property
    def children(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._children)

    @property
    def dependents(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._dependents)

    @override
    def _repr_identity(self) -> str:
        return f"{self.__class__.__name__} ref={self._ref.key}"

    def _detail_sections(self) -> list[Section]:
        return _common_detail_sections(
            context=self.context,
            python_symbol=self.python_symbol,
            source_location=self.source_location,
            parents=self._parents,
            children=self._children,
            dependents=self._dependents,
        )

    @override
    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for section in self._detail_sections():
            card = card.section(section)
        return card


@dataclass(frozen=True, repr=False)
class DatasourceDetails(_DetailsBase):
    """Details for a datasource object."""

    backend_type: str
    wall_clock_timezone: str | None
    fields: Mapping[str, object]
    env_refs: Mapping[str, str]

    @property
    @override
    def ref(self) -> DatasourceSymbol:
        return symbol_factory.datasource(self._ref.path)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "env_refs", MappingProxyType(dict(self.env_refs)))

    @override
    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="backend_type", value=self.backend_type),
                FieldSection(
                    label="wall_clock_timezone", value=self.wall_clock_timezone or "(none)"
                ),
                FieldSection(label="fields", value=_format_mapping(self.fields)),
                FieldSection(
                    label="credentials",
                    value=" | ".join(credential_labels(self.fields, self.env_refs)),
                ),
            )
        )
        if self.name == DEFAULT_DATASOURCE_NAME:
            sections.append(FieldSection(label="source", value=DEFAULT_DATASOURCE_DESCRIPTION))
        return sections


@dataclass(frozen=True, repr=False)
class DomainDetails(_DetailsBase):
    """Details for a domain object."""

    owner: str
    default: bool

    @property
    @override
    def ref(self) -> DomainSymbol:
        return symbol_factory.domain(self._ref.path)

    @override
    def _detail_sections(self) -> list[Section]:
        sections = super()._detail_sections()
        sections.extend(
            (
                FieldSection(label="owner", value=self.owner),
                FieldSection(label="default", value=str(self.default)),
            )
        )
        return sections


_CatalogObjectDetails = DatasourceDetails | DomainDetails
