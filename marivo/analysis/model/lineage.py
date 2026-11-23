"""Immutable, non-executable semantic identities and complete column directories."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from typing_extensions import assert_never, override

from marivo._definition.value_ir import (
    CoordinateSlot,
    PairStatistic,
    PresenceSlot,
    Statistic,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo._kernel.refs import SemanticKind
from marivo._kernel.validation import is_exact_pair
from marivo.analysis.model.errors import reject


class _AxisToken(Protocol):
    @property
    def token(self) -> str: ...


class LineageSchema(Protocol):
    @property
    def axes(self) -> tuple[_AxisToken, ...]: ...
    @property
    def value_paths(self) -> tuple[tuple[str, ...], ...]: ...
    @property
    def presence_paths(self) -> tuple[tuple[str, ...], ...]: ...


def _fail(received: str) -> None:
    reject(
        "complete immutable semantic column lineage",
        received,
        "Retain the captured lineage with its exact output structure.",
        "frame.lineage",
        canonical_id="analysis_frame.lineage",
    )


@dataclass(frozen=True, slots=True, repr=False)
class SemanticObjectIdentity:
    """Captured kind, qualified path and consumed definition identity; no execution authority."""

    kind: str
    path: str
    definition_id: str

    def __post_init__(self) -> None:
        if self.kind not in ({kind.value for kind in SemanticKind}) or any(
            type(value) is not str or not value or not value.isprintable()
            for value in (self.kind, self.path, self.definition_id)
        ):
            _fail("invalid semantic object identity")

    @override
    def __repr__(self) -> str:
        return f"<SemanticObjectIdentity kind={self.kind} path={self.path[:80]!r}; use .show()>"

    def show(self) -> None:
        """Print captured identity. Args: None. Returns: None.

        Example: ``origin.show()``. Constraints: Metadata only; no definition lookup.
        """
        print(
            f"semantic origin kind={self.kind} path={self.path[:160]} definition={self.definition_id[:160]}"
        )


Origins = tuple[SemanticObjectIdentity, ...]


def union(*groups: Origins) -> Origins:
    return tuple(
        sorted(
            {item for group in groups for item in group},
            key=lambda item: (item.kind, item.path, item.definition_id),
        )
    )


@dataclass(frozen=True, slots=True, repr=False)
class ColumnLineage:
    """Static value and context origins; neither certifies execution or business identity."""

    value_origins: Origins = ()
    context_origins: Origins = ()

    def __post_init__(self) -> None:
        for group in (self.value_origins, self.context_origins):
            if (
                type(group) is not tuple
                or any(type(item) is not SemanticObjectIdentity for item in group)
                or group != union(group)
            ):
                _fail("origins must be unique, sorted identities")

    @override
    def __repr__(self) -> str:
        return f"<ColumnLineage values={len(self.value_origins)} context={len(self.context_origins)}; use .show()>"

    def show(self) -> None:
        """Print bounded static origins. Args: None. Returns: None.

        Example: ``frame.lineage.values['revenue'].show()``.
        Constraints: At most 20 identities per role; typed fields retain all origins.
        """
        print(repr(self))
        for role, group in (("value", self.value_origins), ("context", self.context_origins)):
            for origin in group[:20]:
                print(f"{role}: {origin.kind}:{origin.path[:160]} @{origin.definition_id[:80]}")
            if len(group) > 20:
                print(f"{role}: {len(group) - 20} omitted; read typed origin fields")


def merge(*columns: ColumnLineage, context: Origins = ()) -> ColumnLineage:
    return ColumnLineage(
        union(*(c.value_origins for c in columns)),
        union(context, *(c.context_origins for c in columns)),
    )


def all_origins(column: ColumnLineage) -> Origins:
    return union(column.value_origins, column.context_origins)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLineage:
    """Complete immutable output directories, acquired through AnalysisFrame.lineage."""

    _values: tuple[tuple[str, ColumnLineage], ...] = ()
    _axes: tuple[tuple[str, ColumnLineage], ...] = ()
    _presence: tuple[tuple[str, ColumnLineage], ...] = ()
    _domain: Origins = ()
    input_id: str = ""

    def __post_init__(self) -> None:
        for entries in (self._values, self._axes, self._presence):
            if (
                type(entries) is not tuple
                or any(
                    not is_exact_pair(entry)
                    or type(entry[0]) is not str
                    or not entry[0]
                    or type(entry[1]) is not ColumnLineage
                    for entry in entries
                )
                or len(dict(entries)) != len(entries)
            ):
                _fail("invalid output lineage directory")
        ColumnLineage(context_origins=self._domain)

    @property
    def values(self) -> Mapping[str, ColumnLineage]:
        """Return value origins by complete output name.

        Args: None. Returns: Read-only ordered mapping. Example: ``frame.lineage.values['revenue']``.
        Constraints: Captured metadata only; names do not grant correspondence authority.
        """
        return MappingProxyType(dict(self._values))

    @property
    def axes(self) -> Mapping[str, ColumnLineage]:
        """Return complete-axis origins by local token.

        Args: None. Returns: Read-only mapping. Example: ``frame.lineage.axes[frame.axes[0].token]``.
        Constraints: Composite keys remain one axis.
        """
        return MappingProxyType(dict(self._axes))

    @property
    def presence(self) -> Mapping[str, ColumnLineage]:
        """Return independent presence origins by complete name.

        Args: None. Returns: Read-only mapping. Example: ``frame.lineage.presence``.
        Constraints: Presence describes row existence, independently of value NULL.
        """
        return MappingProxyType(dict(self._presence))

    @override
    def __repr__(self) -> str:
        return f"<FrameLineage input={self.input_id[:64]!r} values={len(self._values)} axes={len(self._axes)} presence={len(self._presence)}; use .show()>"

    def show(self) -> None:
        """Print bounded column summaries. Args: None. Returns: None.

        Example: ``frame.lineage.show()``.
        Constraints: At most 20 entries per directory; read typed fields for complete origins.
        """
        print(repr(self))
        for role, entries in (
            ("value", self._values),
            ("axis", self._axes),
            ("presence", self._presence),
        ):
            for name, column in entries[:20]:
                print(
                    f"{role} {name[:160]}: values={len(column.value_origins)} context={len(column.context_origins)}"
                )
            if len(entries) > 20:
                print(f"{role}: {len(entries) - 20} omitted; read typed directories")


def lineage_domain(value: FrameLineage) -> Origins:
    """Read the shared origin context attached to a Frame's row domain."""
    return value._domain  # noqa: SLF001 - Keep the internal domain out of the public directories.


def directory(
    schema: LineageSchema,
    values: tuple[ColumnLineage, ...],
    axes: tuple[ColumnLineage, ...],
    presence: tuple[ColumnLineage, ...] = (),
    domain: Origins = (),
) -> FrameLineage:
    result = FrameLineage(
        tuple(
            zip(
                (".".join(p) for p in schema.value_paths),
                (merge(c, context=domain) for c in values),
                strict=True,
            )
        ),
        tuple(
            zip(
                (a.token for a in schema.axes),
                (merge(c, context=domain) for c in axes),
                strict=True,
            )
        ),
        tuple(
            zip(
                (".".join(p) for p in schema.presence_paths),
                (merge(c, context=domain) for c in presence),
                strict=True,
            )
        ),
        domain,
    )
    validate(result, schema)
    return result


def empty(schema: LineageSchema) -> FrameLineage:
    return directory(
        schema,
        tuple(ColumnLineage() for _ in schema.value_paths),
        tuple(ColumnLineage() for _ in schema.axes),
        tuple(ColumnLineage() for _ in schema.presence_paths),
    )


def validate(lineage: FrameLineage, schema: LineageSchema) -> None:
    if type(lineage) is not FrameLineage:
        _fail("expected a FrameLineage record")
    if (
        tuple(lineage.values) != tuple(".".join(p) for p in schema.value_paths)
        or tuple(lineage.axes) != tuple(a.token for a in schema.axes)
        or tuple(lineage.presence) != tuple(".".join(p) for p in schema.presence_paths)
    ):
        _fail("lineage directories differ from output schema")


def statistic_lineage(lineage: FrameLineage, value: Statistic | PairStatistic) -> ColumnLineage:
    """Capture one statistic's operands using the same logical lineage traversal."""
    operands = (
        (value.left, value.right)
        if isinstance(value, PairStatistic)
        else (() if value.value is None else (value.value,))
    )
    return merge(
        *(expression_lineage(lineage, e) for e in operands), context=lineage_domain(lineage)
    )


def expression_lineage(lineage: FrameLineage, value: ValueExpression) -> ColumnLineage:
    """Capture one actual operand without merging it with other result operands."""
    values = tuple(lineage.values.values())
    axes = tuple(lineage.axes.values())
    if isinstance(value, ValueSlot):
        return values[value.index]
    if isinstance(value, CoordinateSlot):
        return merge(axes[value.index], context=lineage_domain(lineage))
    if isinstance(value, PresenceSlot):
        return lineage.presence[".".join(value.path)]
    if isinstance(value, ValueLiteral):
        return ColumnLineage(context_origins=lineage_domain(lineage))
    if isinstance(value, ValueUnary):
        return expression_lineage(lineage, value.operand)
    if isinstance(value, ValueBinary):
        return merge(
            expression_lineage(lineage, value.left), expression_lineage(lineage, value.right)
        )
    if isinstance(value, ValueShare):
        return merge(
            expression_lineage(lineage, value.value),
            context=union(
                lineage_domain(lineage),
                *(all_origins(a) for p, a in enumerate(axes) if p not in value.positions),
            ),
        )
    if isinstance(value, ValuePartition):
        return merge(
            statistic_lineage(lineage, value.statistic),
            context=union(*(all_origins(axes[p]) for p in value.positions)),
        )
    assert_never(value)
