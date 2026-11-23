"""Shared immutable Frame fields and bounded fact presentation."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from typing_extensions import override

from marivo._kernel.render import Card, RenderableResult
from marivo.analysis.model.data_types import DataType, require_data_text
from marivo.analysis.model.errors import reject

ValueKind: TypeAlias = Literal[
    "numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved"
]
AxisRole: TypeAlias = Literal["field", "identity", "calendar", "interval", "paired"]


@dataclass(frozen=True, slots=True, repr=False)
class UnresolvedDataType:
    """Captured value kind whose physical type is unresolved; acquired from pending schema."""

    kind: ValueKind

    def __post_init__(self) -> None:
        if self.kind not in (
            "numeric",
            "boolean",
            "string",
            "date",
            "timestamp",
            "duration",
            "unresolved",
        ):
            _invalid("an explicit pending value kind")

    @override
    def __repr__(self) -> str:
        return f"<UnresolvedDataType kind={self.kind}; use .show()>"

    def show(self) -> None:
        """Print the known kind. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].dtype.show()``.
        Constraints: Does not execute or infer a type from data.
        """
        print(repr(self))


FrameDataType: TypeAlias = DataType | UnresolvedDataType


def _invalid(received: str) -> None:
    reject(
        "a captured immutable Frame structure",
        received,
        "Read schema from the exact Frame whose fields you need.",
        "frame.schema",
        canonical_id="analysis_frame.schema",
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameField:
    """One exact-input slot, acquired from a FrameSchema directory or complete axis key."""

    slot_id: str
    name: str
    dtype: FrameDataType
    nullable: bool | None = field(
        metadata={
            "meaning": "Whether values may be Null; None means unknown and is allowed only for an unresolved pending field."
        }
    )
    unit: str | None = field(
        metadata={
            "meaning": "Declared unit; None means no unit was declared, while '1' means dimensionless."
        }
    )

    def __post_init__(self) -> None:
        require_data_text(self.slot_id, "frame.schema.slot_id")
        require_data_text(self.name, "frame.schema.name")
        if self.nullable is not None and type(self.nullable) is not bool:
            _invalid("nullability must be a boolean or explicitly unresolved")
        if self.nullable is None and not isinstance(self.dtype, UnresolvedDataType):
            _invalid("only unresolved pending fields may have unknown nullability")
        if self.unit is not None:
            require_data_text(self.unit, "frame.schema.unit")

    @override
    def __repr__(self) -> str:
        return f"<FrameField slot={self.slot_id[:64]!r} name={self.name[:64]!r}; use .show()>"

    def show(self) -> None:
        """Print captured slot facts. Args: None. Returns: None.

        Example: ``frame.schema.values['revenue'].show()``.
        Constraints: Names and slots belong only to this exact input.
        """
        print(repr(self))
        unit = None if self.unit is None else self.unit[:160]
        print(f"dtype={self.dtype!r} nullable={self.nullable} unit={unit!r}")


class _Fact(RenderableResult):
    def __post_init__(self) -> None:
        _validate_kind(self)

    @override
    def _repr_identity(self) -> str:
        assert is_dataclass(self)
        parts = [type(self).__name__]
        for item in fields(self):
            if item.name in (
                "kind",
                "output",
                "axis",
                "consumption_timezone",
                "role",
                "token",
                "axis_token",
                "name",
                "comparison_id",
                "alignment_id",
                "decomposition_id",
                "statistic",
                "row_scope",
                "unit",
                "step",
                "count",
                "endpoint",
                "group_id",
                "start",
                "end",
                "timezone",
                "level",
            ):
                parts.append(f"{item.name}={str(getattr(self, item.name))[:40]}")
            elif item.name in (
                "keys",
                "contributions",
                "groups",
                "along",
                "origins",
                "coordinates",
                "presence",
                "outputs",
                "values",
                "key",
            ):
                value = getattr(self, item.name)
                if isinstance(value, tuple):
                    parts.append(f"{item.name}={len(value)}")
        detail = self._identity_detail()
        if detail is not None:
            parts.append(detail)
        return " ".join(parts)[:220]

    def _identity_detail(self) -> str | None:
        return None

    @override
    def _card(self) -> Card:
        assert is_dataclass(self)
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, tuple):
                summary = ", ".join(repr(v)[:100] for v in value[:12])
                if len(value) > 12:
                    summary += f", ... {len(value) - 12} omitted; read .{item.name}"
            else:
                summary = repr(value)[:240]
            card.field(item.name, summary)
        return card


def _validate_kind(value: _Fact) -> None:
    assert is_dataclass(value)
    for descriptor in fields(value):
        if (
            descriptor.name == "kind"
            and descriptor.default is not MISSING
            and value.kind != descriptor.default
        ):
            _invalid("the exact kind tag of the closed result variant")


def _text(item: str) -> None:
    require_data_text(item, "frame.schema.fact")


def _instant(item: datetime | None) -> None:
    if item is not None and (type(item) is not datetime or item.tzinfo is not timezone.utc):
        _invalid("captured instants must be plain UTC datetimes")
