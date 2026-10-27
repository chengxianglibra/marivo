"""Complete immutable catalog definitions and bounded search result pages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar

from marivo.datasource.ir import AiContextIR, EntitySourceIR
from marivo.render import Card, RenderableResult
from marivo.semantic.catalog_search import Retrieval, SearchHit
from marivo.semantic.ir import EntityVersioningIR, SampleIntervalIR, SemanticParse

JsonValue: TypeAlias = None | str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"]

FrozenJsonValue: TypeAlias = (
    None
    | str
    | int
    | float
    | bool
    | tuple["FrozenJsonValue", ...]
    | Mapping[str, "FrozenJsonValue"]
)


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"Catalog definition cannot encode {type(value).__name__}")


def _json_value(value: object) -> JsonValue:
    """Project captured data, never an executable value or resolver."""
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Catalog JSON cannot encode {type(value).__name__}")


@dataclass(frozen=True)
class CatalogScope:
    """The complete loaded definition scope, independent of search filters."""

    workspace_dir: str
    model_roots: tuple[str, ...]
    domains: tuple[str, ...]
    definition_fingerprint: str


@dataclass(frozen=True)
class CatalogFilters:
    """Explicit filters for this page only."""

    query: str
    kind: str | None = None
    domain: str | None = None
    entity: str | None = None
    origin: str | None = None
    relation: str | None = None


@dataclass(frozen=True)
class EntryProvenance:
    """Source declaration and snapshot identity of an entry."""

    source_file: str
    source_line: int
    python_symbol: str
    definition_fingerprint: str


@dataclass(frozen=True)
class DependencyContext:
    """One loaded direct dependency's authored context, with its identity."""

    id: str
    business_definition: str | None
    guardrails: tuple[str, ...]


@dataclass(frozen=True)
class DomainDefinition:
    """Authored domain responsibility and default selection."""

    context: AiContextIR
    owner: str
    default: bool
    members: tuple[str, ...]


@dataclass(frozen=True)
class DatasourceDefinition:
    """Non-secret connection declaration and backed entities."""

    context: AiContextIR
    backend_type: str
    fields: tuple[tuple[str, FrozenJsonValue], ...]
    env_refs: tuple[tuple[str, str], ...]
    entities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "fields", tuple((key, _freeze_json(value)) for key, value in self.fields)
        )


@dataclass(frozen=True)
class EntityDefinition:
    """Declared contribution grain, source binding and owned fields."""

    context: AiContextIR
    datasource: str
    source: EntitySourceIR
    expression: str
    primary_key: tuple[str, ...]
    versioning: EntityVersioningIR | None
    default_time: str | None
    fields: tuple[str, ...]
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class FieldDefinition:
    """Complete captured row field facts; expression text is non-executable."""

    context: AiContextIR
    entity: str
    expression: str
    unit: str | None
    additivity: str | None
    default_time: str | None
    parse: SemanticParse | None
    sample_interval: SampleIntervalIR | None
    is_default_time: bool
    dependencies: tuple[str, ...]
    dependency_context: tuple[DependencyContext, ...]
    facts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MetricTimeBinding:
    """Effective source-aggregation time, distinct from a dependency read."""

    branch: str
    field: str | None
    path: tuple[str, ...]
    source: Literal["explicit", "entity_default", "none"]
    entity_default: str | None


@dataclass(frozen=True)
class MetricDefinition:
    """Captured formula, source reductions, time roles and null semantics."""

    context: AiContextIR
    roots: tuple[str, ...]
    formula: str
    reductions: tuple[str, ...]
    conditions: tuple[str, ...]
    time_bindings: tuple[MetricTimeBinding, ...]
    unit: str | None
    null_rules: tuple[str, ...]
    dependencies: tuple[str, ...]
    dependency_context: tuple[DependencyContext, ...]
    facts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RelationshipDefinition:
    """Directed declared key mapping; cardinality is not source validation."""

    context: AiContextIR
    from_entity: str
    to_entity: str
    keys: tuple[tuple[str, str], ...]
    cardinality: Literal[
        "one_to_one", "many_to_one", "one_to_many", "many_to_many", "outside_scope"
    ]
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class PeriodCalendarDefinition:
    """Finite calendar declaration and current local certification status."""

    context: AiContextIR
    source_date: str
    boundary_timezone: str
    coverage: tuple[date, date]
    levels: tuple[tuple[str, str], ...]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    snapshot_digest: str | None
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class TemporalSetDefinition:
    """Finite occurrence declaration and current local certification status."""

    context: AiContextIR
    occurrence_id: str
    start: str
    end: str
    category: str | None
    boundary_timezone: str
    coverage: tuple[date, date]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    occurrence_count: int | None
    dependencies: tuple[str, ...]


EntryDefinition: TypeAlias = (
    DomainDefinition
    | DatasourceDefinition
    | EntityDefinition
    | FieldDefinition
    | MetricDefinition
    | RelationshipDefinition
    | PeriodCalendarDefinition
    | TemporalSetDefinition
)


class _EntryLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def summary(self) -> str: ...
    @property
    def definition(self) -> EntryDefinition: ...
    def to_dict(self) -> dict[str, JsonValue]: ...


EntryT = TypeVar("EntryT", bound=_EntryLike, covariant=True)


@dataclass(frozen=True, repr=False)
class CatalogPage(RenderableResult, Generic[EntryT]):
    """One complete result page; display budgets never truncate its typed data."""

    items: tuple[EntryT, ...]
    candidate_count: int
    scope: CatalogScope
    filters: CatalogFilters
    retrieval: Retrieval
    matches: Mapping[str, SearchHit]
    next_cursor: str | None
    outside_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "matches", MappingProxyType(dict(self.matches)))

    def _repr_identity(self) -> str:
        return f"CatalogPage items={len(self.items)} candidates={self.candidate_count}"

    def _card(self) -> Card:
        from marivo._data_render import _text

        card = Card(
            identity=f"{self._repr_identity()} next_cursor={self.next_cursor or '(end)'}",
            available=(".items", ".to_dict()", ".to_json()", ".show()"),
        )
        card.field("page_items", str(len(self.items)))
        card.field("next_cursor", self.next_cursor or "(end)")
        card.field("scope", json.dumps(_json_value(self.scope), ensure_ascii=False))
        card.field("filters", json.dumps(_json_value(self.filters), ensure_ascii=False))
        card.field("retrieval", json.dumps(_json_value(self.retrieval), ensure_ascii=False))
        card.table(
            columns=("id", "business definition / formula", "match"),
            rows=(
                (
                    _text(item.id),
                    _text(_page_summary(item)),
                    _text(
                        "; ".join(
                            f"{label}: {snippet}"
                            for label, snippet in self.matches[item.id].snippets
                        )
                        if item.id in self.matches
                        else ""
                    ),
                )
                for item in self.items
            ),
            row_count=len(self.items),
            show_omission_counts=True,
            report_rows=True,
            recovery=".items and .to_json() contain the complete page",
        )
        if self.outside_scope:
            card.listing("outside_scope", self.outside_scope)
        return card

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print a current-page projection and its exact display counts.

        Args:
            max_output_bytes: UTF-8 budget including the newline, or None for no limit.
        Returns: None; prints this page without retrieving the next page.
        Example: ``page.show(max_output_bytes=None)``.
        Constraints: Display limits do not alter items or next_cursor.
        """
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or max_output_bytes < 1
        ):
            raise ValueError("max_output_bytes must be a positive integer or None")
        print(self._card().render(max_output_bytes=max_output_bytes, _include_newline=True))

    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Render this page without retrieving subsequent pages.

        Args: max_output_bytes: UTF-8 budget, or None for no limit.
        Returns: The current-page card as text without a trailing newline.
        Example: ``text = page.render(max_output_bytes=None)``.
        Constraints: Display limits do not alter items or next_cursor.
        """
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or max_output_bytes < 1
        ):
            raise ValueError("max_output_bytes must be a positive integer or None")
        return self._card().render(max_output_bytes=max_output_bytes)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the complete JSON-compatible page, independent of display limits.

        Returns:
            A fresh dictionary retaining all current-page items and navigation facts.

        Example:
            items = page.to_dict()["items"]

        Constraints:
            Display truncation cannot remove entries from this projection.
        """
        return {
            "schema_version": 1,
            "items": [item.to_dict() for item in self.items],
            "candidate_count": self.candidate_count,
            "scope": _json_value(self.scope),
            "filters": _json_value(self.filters),
            "retrieval": _json_value(self.retrieval),
            "matches": _json_value(self.matches),
            "next_cursor": self.next_cursor,
            "outside_scope": list(self.outside_scope),
        }

    def to_json(self) -> str:
        """Encode the complete result page as UTF-8-compatible JSON text.

        Returns:
            JSON text containing the versioned current API page.

        Example:
            text = page.to_json()

        Constraints:
            Does not follow the next cursor or query a datasource or model.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _page_summary(item: _EntryLike) -> str:
    definition = item.definition
    extra = ""
    if isinstance(definition, MetricDefinition):
        extra = f" | formula: {definition.formula}"
    elif isinstance(definition, FieldDefinition):
        extra = f" | expression: {definition.expression}"
    return item.summary + extra
